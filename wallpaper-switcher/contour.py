"""Efecto "Contorno": prepara el archivo auxiliar que usa la extensión de Cinnamon.

Idea: el fondo nuevo nace de una línea que delinea al sujeto (una persona, un animal, el objeto que
más destaca) y crece desde ella hacia fuera y hacia dentro hasta cubrir la pantalla.

Qué hace este módulo (v2: numpy; opcionalmente un modelo de IA pequeño):

  1. DETECCIÓN, en tres niveles (como pocket-dots: el recorte bueno se hace UNA vez por fondo, en segundo
     plano, y se guarda en caché; aplicar un fondo nunca espera a la IA pesada):
       - BiRefNet-lite (≈180 MB, 512 px, ver instalar-modelo.sh): recorte de alta calidad. Corre en un proceso
         aparte, de baja prioridad y con vigilancia de memoria; el resultado queda en ~/.cache/.../masks.
         Cuando termina, el auxiliar del fondo se regenera solo con el recorte bueno.
       - U²-Netp (4.7 MB): rápido, se usa mientras el recorte bueno no está listo.
       - Detector clásico (color en Lab + prior de fondo + Otsu) si no hay ningún modelo.
  2. REFINADO DE BORDE. La máscara (de baja resolución) se ajusta a los bordes reales de la foto con un
     "guided filter": el contorno se pega al sujeto en vez de quedar con escalones.
  3. DISTANCIA EXACTA. Distancia euclídea exacta al contorno (no chamfer), suavizada a dos escalas: fiel al
     sujeto cerca de la línea y cada vez más redondeada al alejarse (el frente se ve limpio, sin "grano").
  4. CODIFICACIÓN (formato v5). Dos archivos:
        <md5>.c5.png  RGBA del tamaño EXACTO del monitor (Wc x Hc), pintado 1:1 por Clutter: RGB = imagen nueva
                      tal cual (exacta, nada de trucos de color) y A = distancia al contorno comprimida a 8 bits
                      (0..127 dentro, 128..255 fuera; basta: las celdas del avance son de ~30 px).
        <md5>.c5l.png la LÍNEA del contorno ya dibujada (RGBA: color de acento tomado de la foto, bordes suavizados).
                      La extensión solo la recorta de arriba abajo y la desvanece: no hace falta shader para ella.
     (El buffer del shader es del tamaño del actor en pantalla: la textura debe pintarse 1:1 y el actor quedar dentro
     de la pantalla, o los datos del alfa se corromperían.)
     Sin sujeto (paisajes, texturas) no hay línea: el shader hace crecer colonias desde puntos al azar.
     El .json lleva las profundidades máximas, la extensión vertical del sujeto y una semilla.

Archivos: ~/.cache/wallpaper-switcher/contour/<md5 de la ruta>.c5.png (+ .c5.json).
"""
import collections
import hashlib
import json
import math
import os
import select
import subprocess
import sys
import threading
import time

from PIL import Image, ImageOps

try:
    import numpy as np
except Exception:                       # sin numpy no hay efecto Contorno (la extensión usa «Barrido»)
    np = None

from config import CACHE_DIR

CONTOUR_DIR = os.path.join(CACHE_DIR, "contour")
MODEL_DIR = os.path.join(CACHE_DIR, "models")
MODEL_PATH = os.path.join(MODEL_DIR, "u2netp.onnx")
MASK_DIR = os.path.join(CACHE_DIR, "masks")                       # recortes de BiRefNet (PNG 8 bits)
HQ_MODEL_PATH = os.environ.get("WALLPAPER_HQ_MODEL") or os.path.join(MODEL_DIR, "birefnet-lite.onnx")
HQ_SIZE = int(os.environ.get("WALLPAPER_HQ_SIZE", "512"))         # solo si el modelo admite tamaño dinámico
HQ_MASK_SIDE = 768                                                # lado largo del recorte que se guarda
HQ_TIMEOUT = 240                                                  # s máx. por imagen antes de abortar
HQ_IDLE_EXIT = 20                                                 # s sin trabajo y el proceso se cierra (libera RAM)
HQ_QUEUE_MAX = 16
NOHQ_FLAG = os.path.join(MASK_DIR, "desactivado")                 # lo crea el vigilante si el modelo no cabe/falla
LINE_VER = 2                            # súbelo si cambia el dibujo de la línea (regenera los auxiliares)
SUFFIX = ".c5"                          # versión del formato; los archivos antiguos se ignoran y se podan
KEEP_FILES = 16
MAX_W = 3840                            # el auxiliar mide EXACTAMENTE lo que el monitor (hay un tope por seguridad)
GRID_W = 320                            # rejilla de análisis (máscara y distancia exacta)
GRID_W_HQ = 480                         # con recorte de BiRefNet se analiza más fino (corre en segundo plano)
WAIT_APPLY = 3.5                        # s máx. que aplicar un fondo espera al contorno antes de usar «Barrido»
LOG_FILE = os.path.join(CACHE_DIR, "contour.log")
MODEL_IN = 320                          # entrada de U²-Net

_session = None
_session_failed = False
LAST_INFO = {}                          # para --debug: qué detector se usó, etc.


def sidecar_path(image_path):
    key = hashlib.md5(os.path.abspath(image_path).encode("utf-8")).hexdigest()
    return os.path.join(CONTOUR_DIR, key + SUFFIX + ".png")


def _is_fresh(image_path, side):
    try:
        with open(side[:-4] + ".json") as f:
            if json.load(f).get("lv") != LINE_VER:
                return False
        return os.path.getmtime(side) >= os.path.getmtime(image_path)
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
#  Utilidades de imagen (numpy)
# ---------------------------------------------------------------------------

def _box(a, r):
    """Filtro de caja (media) de radio r sobre un array 2D, con bordes replicados. Usa sumas acumuladas."""
    if r <= 0:
        return a
    h, w = a.shape
    p = np.pad(a, ((r + 1, r), (r + 1, r)), mode="edge").astype(np.float64)
    c = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    s = c[k:k + h, k:k + w] - c[0:h, k:k + w] - c[k:k + h, 0:w] + c[0:h, 0:w]
    return (s / float(k * k)).astype(np.float32)


def _gauss(a, sigma):
    """Gaussiana separable (numpy puro)."""
    if sigma <= 0.05:
        return a
    rad = max(1, int(math.ceil(sigma * 3)))
    x = np.arange(-rad, rad + 1, dtype=np.float32)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    k /= k.sum()
    p = np.pad(a, ((0, 0), (rad, rad)), mode="edge")
    out = np.zeros_like(a, dtype=np.float32)
    for i, kv in enumerate(k):
        out += kv * p[:, i:i + a.shape[1]]
    p = np.pad(out, ((rad, rad), (0, 0)), mode="edge")
    out2 = np.zeros_like(out)
    for i, kv in enumerate(k):
        out2 += kv * p[i:i + a.shape[0], :]
    return out2


def _resize_f(a, size, resample=Image.BICUBIC):
    """Redimensiona un array float32 2D a size=(w, h) con PIL (modo 'F')."""
    im = Image.fromarray(np.ascontiguousarray(a, dtype=np.float32))
    return np.asarray(im.resize(size, resample), dtype=np.float32)


def _srgb_to_lab(rgb):
    """rgb: float32 (h, w, 3) en 0..1 -> Lab aproximado (D65)."""
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
    xyz = lin @ m.T
    xyz /= np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.stack([116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]),
                     200.0 * (f[..., 1] - f[..., 2])], axis=-1).astype(np.float32)


def _otsu(v, bins=256):
    hist, edges = np.histogram(v, bins=bins, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    tot = hist.sum()
    centers = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(hist)
    w1 = tot - w0
    s0 = np.cumsum(hist * centers)
    with np.errstate(divide="ignore", invalid="ignore"):
        m0 = s0 / w0
        m1 = (s0[-1] - s0) / w1
        var = w0 * w1 * (m0 - m1) ** 2
    var = np.nan_to_num(var)
    return float(centers[int(np.argmax(var))])


def _guided_filter(guide, src, r, eps):
    """Guided filter (He et al.) con guía de 1 canal. Pega `src` a los bordes de `guide`."""
    mean_i = _box(guide, r)
    mean_p = _box(src, r)
    corr_ip = _box(guide * src, r)
    var_i = _box(guide * guide, r) - mean_i * mean_i
    cov_ip = corr_ip - mean_i * mean_p
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    return _box(a, r) * guide + _box(b, r)


# ---------------------------------------------------------------------------
#  Componentes conectados (4 vecinos) sin scipy
# ---------------------------------------------------------------------------

def _label(mask):
    """Etiqueta componentes 4-conexos de un array bool 2D. Devuelve (labels int32, n).
    Union-find por filas sobre runs: rápido incluso en Python para rejillas pequeñas."""
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    parent = [0]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    prev_runs = []
    for y in range(h):
        row = mask[y]
        padded = np.concatenate(([False], row, [False]))
        d = np.diff(padded.astype(np.int8))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)           # exclusivo
        runs = []
        for s, e in zip(starts.tolist(), ends.tolist()):
            lab = 0
            for ps, pe, pl in prev_runs:
                if ps < e and s < pe:            # se solapan en x -> misma componente
                    if lab == 0:
                        lab = find(pl)
                    else:
                        ra, rb = find(lab), find(pl)
                        if ra != rb:
                            parent[rb] = ra
            if lab == 0:
                parent.append(len(parent))
                lab = len(parent) - 1
            labels[y, s:e] = lab
            runs.append((s, e, lab))
        prev_runs = runs
    if len(parent) == 1:
        return labels, 0
    roots = np.array([find(i) for i in range(len(parent))], dtype=np.int32)
    uniq, inv = np.unique(roots[1:], return_inverse=True)
    remap = np.zeros(len(parent), dtype=np.int32)
    remap[1:] = inv + 1
    return remap[labels], len(uniq)


def _dilate(mask, r):
    """Dilatación cuadrada de radio r (bool 2D)."""
    if r <= 0:
        return mask
    return _box(mask.astype(np.float32), r) > 0.0


def _fill_small_holes(mask, max_frac):
    """Rellena los huecos interiores cuyo área sea < max_frac de la imagen."""
    h, w = mask.shape
    labels, n = _label(~mask)
    if n == 0:
        return mask
    border = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])).tolist())
    counts = np.bincount(labels.ravel(), minlength=n + 1)
    out = mask.copy()
    limit = max_frac * h * w
    for lab in range(1, n + 1):
        if lab not in border and counts[lab] < limit:
            out[labels == lab] = True
    return out


def _pick_subject(prob, thr, min_frac=0.004):
    """De la probabilidad `prob` (0..1) devuelve una máscara bool con el/los sujetos principales:
    el bloque más 'masivo' (área x probabilidad, favorecido el centro) y los que tengan al menos 35% de su masa."""
    h, w = prob.shape
    cw = 160                                   # las componentes se calculan en una rejilla gruesa
    ch = max(8, int(round(cw * h / float(w))))
    coarse = _resize_f(prob, (cw, ch), Image.BILINEAR) > thr
    labels, n = _label(coarse)
    if n == 0:
        return None
    yy, xx = np.mgrid[0:ch, 0:cw]
    scores = []
    for lab in range(1, n + 1):
        m = labels == lab
        area = m.sum() / float(cw * ch)
        if area < min_frac:
            continue
        cx, cy = xx[m].mean() / cw, yy[m].mean() / ch
        off = math.hypot(cx - 0.5, cy - 0.5)
        scores.append((area * (1.0 - 0.7 * min(1.0, off * 1.6)), lab, area))
    if not scores:
        return None
    scores.sort(reverse=True)
    best = scores[0][0]
    keep = np.zeros((ch, cw), dtype=bool)
    total = 0.0
    for sc, lab, area in scores:
        if sc >= 0.35 * best:
            keep |= labels == lab
            total += area
    if total > 0.80:                           # casi toda la imagen: no es un sujeto
        return None
    big = _resize_f(keep.astype(np.float32), (w, h), Image.BILINEAR) > 0.0
    return _dilate(big, max(2, w // 160))      # margen para no recortar el borde fino


# ---------------------------------------------------------------------------
#  Detección: modelo de IA (opcional)
# ---------------------------------------------------------------------------

def _get_session():
    global _session, _session_failed
    if _session is not None or _session_failed:
        return _session
    if not os.path.exists(MODEL_PATH):
        _session_failed = True
        return None
    try:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 4)
        opts.log_severity_level = 3
        _session = ort.InferenceSession(MODEL_PATH, opts, providers=["CPUExecutionProvider"])
    except Exception as e:
        print(f"Contorno: no se pudo cargar el modelo ({e}); se usa el detector clásico")
        _session_failed = True
    return _session


def model_available():
    return _get_session() is not None


def _run_model(small_rgb):
    """small_rgb: PIL RGB. Devuelve probabilidad float32 (MODEL_IN x MODEL_IN) en 0..1, o None."""
    sess = _get_session()
    if sess is None:
        return None
    try:
        im = small_rgb.resize((MODEL_IN, MODEL_IN), Image.LANCZOS)
        a = np.asarray(im, dtype=np.float32)
        a = a / max(float(a.max()), 1e-6)
        a = (a - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
        x = a.transpose(2, 0, 1)[None].astype(np.float32)
        name = sess.get_inputs()[0].name
        out = sess.run(None, {name: x})[0]
        p = np.asarray(out)[0, 0]
        if (os.cpu_count() or 1) >= 4:       # promedio con la imagen volteada: más estable, pero cuesta x2
            out2 = sess.run(None, {name: x[:, :, :, ::-1].copy()})[0][..., ::-1]
            p = (p + np.asarray(out2)[0, 0]) / 2.0
        lo, hi = float(p.min()), float(p.max())
        if hi - lo < 1e-6:
            return None
        return ((p - lo) / (hi - lo)).astype(np.float32)
    except Exception as e:
        print(f"Contorno: falló el modelo ({e}); se usa el detector clásico")
        return None


# ---------------------------------------------------------------------------
#  Detección clásica mejorada (sin modelo)
# ---------------------------------------------------------------------------

def _classic_prob(rgb01):
    """Prominencia 0..1 a partir de color Lab: contraste global (Achanta) + prior de fondo por los bordes."""
    h, w, _ = rgb01.shape
    lab = _srgb_to_lab(rgb01)
    # Contraste respecto al color medio de la imagen, sobre una versión suavizada
    blur = np.stack([_gauss(lab[..., c], 1.5) for c in range(3)], axis=-1)
    mean = blur.reshape(-1, 3).mean(0)
    s_global = np.sqrt(((blur - mean) ** 2).sum(-1))
    # Fondo: lo que se parece al color de los bordes (los sujetos casi nunca tocan los 4 lados)
    bw = max(2, int(round(min(h, w) * 0.05)))
    edge = np.concatenate([lab[:bw].reshape(-1, 3), lab[-bw:].reshape(-1, 3),
                           lab[:, :bw].reshape(-1, 3), lab[:, -bw:].reshape(-1, 3)])
    # Representantes del fondo: cuantización gruesa del color de los bordes (los 6 más frecuentes)
    q = np.round(edge / 12.0).astype(np.int32)
    keys, inv, cnt = np.unique(q, axis=0, return_inverse=True, return_counts=True)
    order = np.argsort(cnt)[::-1][:6]
    protos = np.stack([edge[inv.ravel() == k].mean(0) for k in order])
    dist = np.sqrt(((blur[:, :, None, :] - protos[None, None]) ** 2).sum(-1)).min(-1)
    # Sesgo suave hacia el centro
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cen = np.exp(-(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) * 1.2)
    sal = (0.5 * s_global / (s_global.max() + 1e-6) + 0.5 * dist / (dist.max() + 1e-6)) * (0.55 + 0.45 * cen)
    sal = _gauss(sal, 1.2)
    sal -= sal.min()
    return (sal / (sal.max() + 1e-6)).astype(np.float32)


def _ellipse_prob(w, h):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return (((xx - w / 2) / (w * 0.2)) ** 2 + ((yy - h / 2) / (h * 0.34)) ** 2 <= 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
#  Máscara final
# ---------------------------------------------------------------------------

def find_subject(small_img, use_model=True, hq_prob=None):
    """small_img: PIL RGB (tamaño de la rejilla). hq_prob: recorte de BiRefNet (float32 0..1 del mismo tamaño) o None.
    Devuelve (prob float32 (h,w) refinada 0..1 o None si no hay sujeto, detector:str)."""
    w, h = small_img.size
    rgb = np.asarray(small_img, dtype=np.float32) / 255.0
    gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)

    if hq_prob is not None:
        # Recorte de alta calidad: ya es fiel al borde, solo se suaviza un poco. Si BiRefNet no ve ningún sujeto
        # (paisaje, textura) el fondo nuevo crece desde el centro en vez de inventarse un contorno.
        p = np.clip(hq_prob, 0.0, 1.0).astype(np.float32)
        sel = _pick_subject(p, 0.5, min_frac=0.003)
        if sel is None:
            return None, "sin sujeto (BiRefNet) -> colonias desde puntos al azar"
        p = p * sel
        m = _fill_small_holes(p > 0.5, 0.002)
        p = np.maximum(p, m.astype(np.float32) * 0.55) if m.any() else p
        return _gauss(p, 0.8), "BiRefNet"

    prob, name = None, ""
    if use_model:
        p = _run_model(small_img)
        if p is not None:
            prob = _resize_f(p, (w, h), Image.BICUBIC).clip(0.0, 1.0)
            thr = 0.5
            sel = _pick_subject(prob, thr)
            if sel is not None:
                prob = prob * sel
                name = "modelo U2-Netp"
            else:
                prob = None
    if prob is None:
        c = _classic_prob(rgb)
        thr = max(0.15, min(0.7, _otsu(c)))
        sel = _pick_subject(c, thr)
        if sel is None:                                   # sin sujeto claro: lo más prominente
            hi = float(np.quantile(c, 0.92))
            peak = np.unravel_index(int(np.argmax(c)), c.shape)
            strong = c >= hi
            labels, n = _label(strong)
            lab = labels[peak]
            sel = (labels == lab) if lab > 0 else None
            if sel is not None and sel.mean() < 0.01:
                sel = None
            if sel is not None:
                sel = _dilate(sel, 2)
        if sel is None:
            return None, "sin sujeto -> colonias desde puntos al azar"
        prob = (c >= thr).astype(np.float32) * sel
        name = "clásico (color)"

    # Rellenar huecos pequeños y refinar con guided filter para pegar el borde a la foto
    m = _fill_small_holes(prob > 0.5, 0.004)
    prob = np.maximum(prob, m.astype(np.float32) * 0.55) if m.any() else prob
    prob = _gauss(prob, 1.0)
    r = max(2, int(round(w / 120)))
    refined = _guided_filter(gray, prob, r, 1e-3).clip(0.0, 1.0)
    # La guía solo puede mover el borde unos píxeles: se combina con la versión sin refinar
    refined = 0.75 * refined + 0.25 * prob
    return refined, name


# ---------------------------------------------------------------------------
#  Distancia euclídea exacta y campo de tiempo
# ---------------------------------------------------------------------------

def _edt(flag):
    """Distancia euclídea exacta (en celdas) de cada celda al punto con flag=True más cercano.
    Primera pasada horizontal en O(n) con acumulados; segunda vertical por fuerza bruta en bloques."""
    h, w = flag.shape
    if not flag.any():
        return np.full((h, w), float(max(h, w)), dtype=np.float32)
    idx = np.arange(w, dtype=np.int32)[None, :]
    big = 10 ** 6
    left = np.where(flag, idx, -big)
    left = np.maximum.accumulate(left, axis=1)
    right = np.where(flag, idx, big)
    right = np.minimum.accumulate(right[:, ::-1], axis=1)[:, ::-1]
    dx = np.minimum(idx - left, right - idx).astype(np.float32)
    g = np.where(dx > w * 2, np.float32(1e9), dx * dx).astype(np.float32)       # (h, w) distancia² horizontal
    ys = np.arange(h, dtype=np.float32)
    dy2 = (ys[:, None] - ys[None, :]) ** 2                                       # (h, h)
    out = np.empty((h, w), dtype=np.float32)
    step = 64
    for x0 in range(0, w, step):
        blk = g[:, x0:x0 + step]                                                 # (h, b)
        out[:, x0:x0 + step] = (blk[None, :, :] + dy2[:, :, None]).min(axis=1)
    return np.sqrt(out)


def _signed_field(mask):
    """Distancia con signo en celdas: >0 fuera del sujeto, <0 dentro, con medio píxel de corrección."""
    d_out = _edt(mask)
    d_in = _edt(~mask)
    return np.where(mask, -(d_in - 0.5), d_out - 0.5).astype(np.float32)


def build_field(prob):
    """prob (h,w) 0..1 -> distancia CON SIGNO suavizada, en celdas de la rejilla (>0 fuera del sujeto, <0 dentro).
    Cerca de la línea se suaviza lo justo para que el contorno quede limpio (sin pelos ni escalones); al alejarse se
    redondea cada vez más, para que el avance del fondo sea fluido."""
    h, w = prob.shape
    sd = _signed_field(prob > 0.5)
    near = _gauss(sd, max(2.4, w / 140.0))
    far = _gauss(sd, max(4.0, w / 40.0))
    wt = np.clip(np.abs(near) / (w * 0.08), 0.0, 1.0)
    wt = wt * wt * (3 - 2 * wt)
    mix = near + wt * (far - near)
    return np.where(np.sign(mix) == np.sign(near), mix, near).astype(np.float32)   # el signo no cambia: sin islas nuevas


# ---------------------------------------------------------------------------
#  API
# ---------------------------------------------------------------------------

def accent_color(full_rgb):
    """Un color vivo de la propia foto para la línea: el tono más presente (ponderado por saturación y brillo),
    aclarado para que se vea sobre el fondo oscurecido. Devuelve (r, g, b) 0..255."""
    import colorsys
    a = np.asarray(full_rgb.resize((96, 54), Image.BOX), dtype=np.float32) / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx, mn = a.max(-1), a.min(-1)
    d = mx - mn + 1e-6
    hue = np.where(mx == r, ((g - b) / d) % 6.0, np.where(mx == g, (b - r) / d + 2.0, (r - g) / d + 4.0)) / 6.0
    sat = (mx - mn) / (mx + 1e-6)
    w = sat * sat * mx * (mx > 0.15)
    bins = np.minimum((hue * 24).astype(int), 23)
    hist = np.bincount(bins.ravel(), weights=w.ravel(), minlength=24)
    hist = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))
    if hist.max() < 1.0:                                          # foto casi sin color: un blanco cálido/frío suave
        m = a.reshape(-1, 3).mean(0)
        hh, ll, ss = colorsys.rgb_to_hls(*m)
        rr, gg, bb = colorsys.hls_to_rgb(hh, 0.85, min(0.35, ss))
    else:
        sel = (bins == int(hist.argmax())) * w
        col = (a * sel[..., None]).sum((0, 1)) / max(sel.sum(), 1e-6)
        hh, ll, ss = colorsys.rgb_to_hls(*col)
        rr, gg, bb = colorsys.hls_to_rgb(hh, min(0.74, max(0.60, ll)), max(0.65, ss))
    return tuple(int(round(v * 255)) for v in (rr, gg, bb))


def _smooth(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def make_line(sd_px, size, color):
    """Imagen RGBA de la línea del contorno: trazo fino (~2.3 px a 1080p) con un halo muy tenue, del color `color`."""
    wc, hc = size
    sc = hc / 1080.0
    hw = max(0.9, 1.15 * sc)
    gy, gx = np.gradient(sd_px)
    ad = np.abs(sd_px) / np.clip(np.hypot(gx, gy), 0.5, 2.0)      # ancho constante aunque la distancia se deforme
    line = 1.0 - _smooth(hw - 0.8, hw + 0.8, ad)
    halo = 0.20 * (1.0 - _smooth(0.0, 7.0 * sc, ad))
    alpha = np.clip(line + halo * (1.0 - line), 0.0, 1.0)
    out = np.zeros((hc, wc, 4), dtype=np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = color
    out[..., 3] = np.rint(alpha * 255.0).astype(np.uint8)
    return Image.fromarray(out)


def compose(full_rgb, sd_cells, grid_size, mask):
    """full_rgb: PIL RGB (Wc x Hc). sd_cells: distancia con signo en celdas de la rejilla `grid_size` (None = sin sujeto).
    mask: bool (rejilla) del sujeto. Devuelve (PIL RGBA de Wc x Hc, meta dict con 'line_img' o None)."""
    wc, hc = full_rgb.size
    out = np.zeros((hc, wc, 4), dtype=np.uint8)
    out[..., :3] = np.asarray(full_rgb, dtype=np.uint8)           # el color va EXACTO
    if sd_cells is None:
        out[..., 3] = 128
        return Image.fromarray(out), {"maxin": 0.0, "maxout": 0.0, "yt": 0.0, "yb": float(hc),
                                      "subject": False, "line_img": None}
    gw, gh = grid_size
    px = _resize_f(sd_cells * (wc / float(gw)), (wc, hc), Image.BICUBIC)         # distancia con signo, en píxeles
    mi = float(max(1.0, -px.min()))
    mo = float(max(1.0, px.max()))
    inside = px <= 0.0
    u_in = 127.0 - np.rint(127.0 * np.clip(-px / mi, 0.0, 1.0))                  # 0..127 (127 = el contorno)
    u_out = 128.0 + np.rint(127.0 * np.clip(px / mo, 0.0, 1.0))                  # 128..255
    out[..., 3] = np.where(inside, u_in, u_out).astype(np.uint8)
    rows = np.flatnonzero(mask.any(axis=1))
    yt, yb = (rows[0] / float(gh) * hc, (rows[-1] + 1) / float(gh) * hc) if len(rows) else (0.0, float(hc))
    line = make_line(px, (wc, hc), accent_color(full_rgb))
    return Image.fromarray(out), {"maxin": mi, "maxout": mo, "yt": float(yt), "yb": float(yb),
                                  "subject": True, "line_img": line}


def _work(image_path, size, use_model=True):
    tw, th = max(64, int(size[0])), max(64, int(size[1]))         # EXACTAMENTE el tamaño del monitor
    if tw > MAX_W:                                                # tope de seguridad (la extensión usará «Barrido»)
        th = max(64, int(round(th * MAX_W / float(tw))))
        tw = MAX_W
    with Image.open(image_path) as im:
        im.draft("RGB", (tw, th))
        im = ImageOps.exif_transpose(im).convert("RGB")
        full = ImageOps.fit(im, (tw, th), Image.BILINEAR)         # cubre el monitor, recortado al centro
    hq_img = load_hq_mask(image_path)
    gw = GRID_W_HQ if hq_img is not None else GRID_W
    gh = max(16, int(round(gw * th / float(tw))))
    small = full.resize((gw, gh), Image.LANCZOS)
    hq_prob = None
    if hq_img is not None:               # el recorte es de la imagen completa: mismo recorte centrado que `full`
        hq_prob = np.asarray(ImageOps.fit(hq_img, (gw, gh), Image.BICUBIC), dtype=np.float32) / 255.0
    prob, name = find_subject(small, use_model, hq_prob)
    if prob is None:
        tex, meta = compose(full, None, (gw, gh), None)
    else:
        tex, meta = compose(full, build_field(prob), (gw, gh), prob > 0.5)
    meta["seed"] = int(_key(image_path)[:6], 16) % 1000 / 10.0     # para el ruido de las colonias (por imagen)
    LAST_INFO.update(detector=name, grid=(gw, gh), size=(tw, th), meta=meta, prob=prob, full=full)
    return tex, meta, (tw, th)


# ---------------------------------------------------------------------------
#  BiRefNet: recorte de alta calidad, en segundo plano y con caché
# ---------------------------------------------------------------------------

def _key(image_path):
    return hashlib.md5(os.path.abspath(image_path).encode("utf-8")).hexdigest()


def mask_path(image_path):
    return os.path.join(MASK_DIR, _key(image_path) + ".png")


def _fail_path(image_path):
    return os.path.join(MASK_DIR, _key(image_path) + ".fail")


def hq_mask_fresh(image_path):
    try:
        return os.path.getmtime(mask_path(image_path)) >= os.path.getmtime(image_path)
    except OSError:
        return False


def _hq_failed(image_path):
    try:
        return os.path.getmtime(_fail_path(image_path)) >= os.path.getmtime(image_path)
    except OSError:
        return False


def load_hq_mask(image_path):
    """Recorte de BiRefNet de la imagen completa (PIL 'L') o None si aún no existe."""
    if not hq_mask_fresh(image_path):
        return None
    try:
        with Image.open(mask_path(image_path)) as m:
            return m.convert("L").copy()
    except Exception:
        return None


def hq_available():
    """¿Se puede usar BiRefNet? Hace falta numpy, onnxruntime, el modelo, y que el vigilante no lo haya desactivado."""
    if np is None or not os.path.exists(HQ_MODEL_PATH) or os.path.exists(NOHQ_FLAG):
        return False
    try:
        import importlib.util
        return importlib.util.find_spec("onnxruntime") is not None
    except Exception:
        return False


_hq_sess = None


def _hq_session():
    global _hq_sess
    if _hq_sess is None:
        import onnxruntime as ort
        o = ort.SessionOptions()
        o.log_severity_level = 3
        o.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        o.enable_cpu_mem_arena = False          # devuelve la memoria al terminar cada capa: menor pico
        o.enable_mem_pattern = False
        _hq_sess = ort.InferenceSession(HQ_MODEL_PATH, o, providers=["CPUExecutionProvider"])
    return _hq_sess


def hq_infer(image_path, save=True):
    """Ejecuta BiRefNet sobre la imagen completa. Devuelve (mascara uint8 (h,w) al lado largo HQ_MASK_SIDE, segundos
    de inferencia). Con save=True la guarda en la caché de recortes."""
    sess = _hq_session()
    inp = sess.get_inputs()[0]
    shp = inp.shape
    sh, sw = (shp[2], shp[3]) if (len(shp) == 4 and isinstance(shp[2], int) and isinstance(shp[3], int)
                                  and shp[2] > 0 and shp[3] > 0) else (HQ_SIZE, HQ_SIZE)
    with Image.open(image_path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        W, H = im.size
        x = im.resize((sw, sh), Image.LANCZOS)
    a = np.asarray(x, dtype=np.float32) / 255.0
    a = (a - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
    a = np.ascontiguousarray(a.transpose(2, 0, 1)[None])
    t0 = time.time()
    outs = sess.run(None, {inp.name: a})
    dt = time.time() - t0
    out = None
    for o in reversed(outs):                    # la predicción final es la última salida con el tamaño de la entrada
        o = np.asarray(o)
        if o.ndim >= 2 and o.shape[-2:] == (sh, sw):
            out = o
            break
    if out is None:
        out = np.asarray(outs[-1])
    p = out.reshape(sh, sw).astype(np.float32) if out.size == sh * sw else out.reshape(out.shape[-2:]).astype(np.float32)
    if p.min() < 0.0 or p.max() > 1.0:          # el export da logits: sigmoide
        p = 1.0 / (1.0 + np.exp(-np.clip(p, -30.0, 30.0)))
    side = min(HQ_MASK_SIDE, max(W, H))
    mw, mh = (side, max(1, int(round(side * H / float(W))))) if W >= H else (max(1, int(round(side * W / float(H)))), side)
    m = Image.fromarray((np.clip(p, 0, 1) * 255.0).astype(np.uint8)).resize((mw, mh), Image.BICUBIC)
    arr = np.asarray(m, dtype=np.uint8)
    if save:
        os.makedirs(MASK_DIR, exist_ok=True)
        dst = mask_path(image_path)
        tmp = f"{dst}.{os.getpid()}.tmp"
        Image.fromarray(arr).save(tmp, "PNG", compress_level=6)
        os.replace(tmp, dst)
        try:
            os.remove(_fail_path(image_path))
        except OSError:
            pass
    return arr, dt


def _hq_worker():
    """Proceso hijo: lee rutas por stdin, guarda el recorte y responde OK/FAIL. Prioridad baja."""
    try:
        os.nice(15)
    except OSError:
        pass
    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            hq_infer(path)
            print("OK", flush=True)
        except Exception as e:                  # incluye MemoryError de onnxruntime
            print("FAIL " + str(e).replace("\n", " ")[:200], flush=True)


def _rss_bytes(pid):
    try:
        with open(f"/proc/{pid}/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def _mem_limit():
    """Memoria máxima que se le deja al proceso de IA: el 60 % de la RAM total, con tope de 4 GB."""
    try:
        with open("/proc/meminfo") as f:
            total = int(f.readline().split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        total = 4 * 1024 ** 3
    return int(min(4 * 1024 ** 3, total * 0.6))


_hq_lock = threading.Lock()
_hq_queue = collections.deque()
_hq_current = None
_hq_thread = None
_hq_bad = 0                                      # procesos abortados seguidos (memoria / tiempo)


def hq_pending():
    with _hq_lock:
        return len(_hq_queue) + (1 if _hq_current else 0)


def hq_enqueue(image_path, front=False, limit=HQ_QUEUE_MAX):
    """Pone en cola el recorte de BiRefNet de `image_path` (no bloquea). front=True lo adelanta."""
    global _hq_thread
    if not hq_available() or not image_path or not os.path.exists(image_path):
        return False
    key = os.path.abspath(image_path)
    if hq_mask_fresh(key) or _hq_failed(key):
        return False
    with _hq_lock:
        if key == _hq_current:
            return False
        if key in _hq_queue:
            if front:
                _hq_queue.remove(key)
                _hq_queue.appendleft(key)
            return True
        (_hq_queue.appendleft if front else _hq_queue.append)(key)
        while limit and len(_hq_queue) > limit:
            _hq_queue.pop()
        if _hq_thread is None or not _hq_thread.is_alive():
            _hq_thread = threading.Thread(target=_hq_loop, daemon=True)
            _hq_thread.start()
    return True


def _spawn_worker():
    return subprocess.Popen([sys.executable, os.path.abspath(__file__), "--hq-worker"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1)


def _worker_do(proc, path):
    """Envía una imagen al proceso de IA y espera. Devuelve 'OK', 'FAIL' o el motivo por el que se abortó."""
    try:
        proc.stdin.write(path + "\n")
        proc.stdin.flush()
    except (OSError, ValueError):
        return "MUERTO"
    t0 = time.time()
    limit = _mem_limit()
    while True:
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
        except (OSError, ValueError):
            return "MUERTO"
        if ready:
            line = proc.stdout.readline()
            if not line:
                return "MUERTO"
            return "OK" if line.startswith("OK") else "FAIL"
        if proc.poll() is not None:
            return "MUERTO"
        motivo = "TIEMPO" if time.time() - t0 > HQ_TIMEOUT else ("MEMORIA" if _rss_bytes(proc.pid) > limit else None)
        if motivo:
            proc.kill()
            try:
                proc.wait(timeout=3)             # sin dejar un proceso zombi
            except subprocess.TimeoutExpired:
                pass
            return motivo


def _hq_loop():
    global _hq_current, _hq_bad
    proc = None
    size = None
    idle_since = None
    try:
        while True:
            with _hq_lock:
                path = _hq_queue.popleft() if _hq_queue else None
                _hq_current = path
            if path is None:
                idle_since = idle_since or time.time()
                if time.time() - idle_since > HQ_IDLE_EXIT:
                    break
                time.sleep(0.5)
                continue
            idle_since = None
            if hq_mask_fresh(path) or not os.path.exists(path):
                continue
            if proc is None or proc.poll() is not None:
                proc = _spawn_worker()
            t0 = time.time()
            res = _worker_do(proc, path)
            if res == "OK":
                _hq_bad = 0
                _log("BiRefNet %s: recorte listo en %.1fs" % (os.path.basename(path), time.time() - t0))
                if size is None:
                    try:
                        import livewall
                        size = livewall.native_size()
                    except Exception:
                        size = (1920, 1080)
                with _heavy:                    # el auxiliar se regenera ya con el recorte bueno
                    _compute(path, size, True)
            else:
                _log("BiRefNet %s: %s" % (os.path.basename(path), res))
                try:
                    os.makedirs(MASK_DIR, exist_ok=True)
                    with open(_fail_path(path), "w") as f:
                        f.write(res)
                except OSError:
                    pass
                if res in ("MEMORIA", "TIEMPO", "MUERTO"):
                    proc = None
                    _hq_bad += 1
                    if _hq_bad >= 2:            # no cabe o es demasiado lento aquí: se desactiva y se avisa en el registro
                        try:
                            with open(NOHQ_FLAG, "w") as f:
                                f.write("BiRefNet desactivado tras fallos seguidos (%s). Borra este archivo para reintentar." % res)
                        except OSError:
                            pass
                        _log("BiRefNet DESACTIVADO (%s dos veces seguidas). Se usa U2-Netp / detector clásico." % res)
                        with _hq_lock:
                            _hq_queue.clear()
                        break
    finally:
        with _hq_lock:
            _hq_current = None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def _log(msg):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 100000:
            os.remove(LOG_FILE)
        with open(LOG_FILE, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def _compute(image_path, size, use_model=True):
    """Genera el PNG + JSON auxiliares. Devuelve la ruta o None."""
    side = sidecar_path(image_path)
    t0 = time.time()
    try:
        tex, meta_d, (wc, hc) = _work(image_path, size, use_model)
        t1 = time.time()
        os.makedirs(CONTOUR_DIR, exist_ok=True)
        line_img = meta_d.pop("line_img", None)
        line_path = side[:-4] + "l.png"
        if line_img is not None:                                  # la línea va antes: el PNG principal marca «listo»
            ltmp = f"{line_path}.{os.getpid()}.{threading.get_native_id()}.tmp"
            line_img.save(ltmp, "PNG", compress_level=1)
            os.replace(ltmp, line_path)
        else:
            try:
                os.remove(line_path)
            except OSError:
                pass
        tmp = f"{side}.{os.getpid()}.{threading.get_native_id()}.tmp"
        tex.save(tmp, "PNG", compress_level=1)                    # rápido; RGBA exacto (sin tocar el color según el alfa)
        meta = side[:-4] + ".json"
        mtmp = f"{meta}.{os.getpid()}.{threading.get_native_id()}.tmp"
        with open(mtmp, "w") as f:
            json.dump({"v": 5, "lv": LINE_VER, "wc": wc, "hc": hc, "maxin": round(meta_d["maxin"], 2), "maxout": round(meta_d["maxout"], 2),
                       "yt": round(meta_d["yt"], 1), "yb": round(meta_d["yb"], 1), "subject": bool(meta_d["subject"]),
                       "seed": meta_d["seed"], "detector": LAST_INFO.get("detector", "")}, f)
        os.replace(mtmp, meta)
        os.replace(tmp, side)                                     # el PNG va al final: marca «listo»
        prune()
        _log("%s | %s | análisis %.2fs + guardado %.2fs" % (
            os.path.basename(image_path), LAST_INFO.get("detector", "?"), t1 - t0, time.time() - t1))
        return side
    except Exception as e:
        _log(f"ERROR {os.path.basename(image_path)}: {e}")
        print(f"Contorno: no se pudo preparar {image_path}: {e}")
        return None


class _Job:
    def __init__(self, seq, wanted):
        self.event = threading.Event()
        self.seq = seq
        self.wanted = wanted               # alguien espera el resultado (aplicar): no se puede descartar


_jobs = {}
_jobs_lock = threading.Lock()
_heavy = threading.Lock()                  # un solo cálculo pesado a la vez
_prefetch_seq = 0


def _run_job(key, image_path, size, use_model, job):
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (AttributeError, OSError):
        pass
    got = False
    try:
        # Los trabajos en segundo plano se turnan; uno que alguien espera (aplicar el fondo) NO hace cola detrás de ellos
        while not job.wanted:
            if _heavy.acquire(timeout=0.2):
                got = True
                break
        # Un precálculo se descarta si el usuario ya se movió a otra miniatura y nadie lo espera
        if job.wanted or job.seq == _prefetch_seq:
            if size is None:
                try:
                    import livewall
                    size = livewall.native_size()
                except Exception:
                    size = (1920, 1080)
            _compute(image_path, size, use_model)
    finally:
        if got:
            _heavy.release()
        with _jobs_lock:
            if _jobs.get(key) is job:
                del _jobs[key]
        job.event.set()


def _start(image_path, size, use_model, wanted):
    """Arranca (o reutiliza) el cálculo en segundo plano. Devuelve el _Job."""
    global _prefetch_seq
    key = os.path.abspath(image_path)
    with _jobs_lock:
        if not wanted:
            _prefetch_seq += 1
        job = _jobs.get(key)
        if job is not None:
            if wanted:
                job.wanted = True
            return job
        job = _Job(_prefetch_seq, wanted)
        _jobs[key] = job
    threading.Thread(target=_run_job, args=(key, image_path, size, use_model, job), daemon=True).start()
    return job


def prefetch(image_path, size=None, use_model=True, neighbors=()):
    """Precalcula en segundo plano (prioridad baja) el auxiliar de `image_path`. No bloquea.
    Además pide el recorte de BiRefNet de esa imagen (primero en la cola) y de sus vecinas (al final)."""
    if np is None or not image_path or not os.path.exists(image_path):
        return
    for n in neighbors:                          # al final de la cola, la más cercana primero
        hq_enqueue(n, front=False)
    hq_enqueue(image_path, front=True)
    if _is_fresh(image_path, sidecar_path(image_path)):
        return
    _start(image_path, size, use_model, wanted=False)


def prepare(image_path, size, use_model=True, wait=None, hq=True):
    """Devuelve la ruta del auxiliar de `image_path` (lo crea si hace falta) o None.
    wait=None: espera hasta que esté listo. wait=N: espera como mucho N segundos y devuelve None si aún no
    está (el cálculo sigue en segundo plano y quedará en caché para la próxima vez)."""
    if np is None or not image_path or not os.path.exists(image_path):
        return None
    side = sidecar_path(image_path)
    if _is_fresh(image_path, side):
        try:
            os.utime(side, None)
        except OSError:
            pass
        return side
    if hq:
        hq_enqueue(image_path, front=True)       # la próxima vez ya usará el recorte bueno
    job = _start(image_path, size, use_model, wanted=True)
    if not job.event.wait(wait):
        _log("%s: el auxiliar no estuvo listo en %.1fs; se aplica con Barrido (queda en caché para la próxima)" % (
            os.path.basename(image_path), wait))
        return None
    return side if _is_fresh(image_path, side) else None


def prune(keep=KEEP_FILES):
    """Conserva los `keep` auxiliares más recientes (PNG + JSON) y borra el resto, incluidos los del formato antiguo."""
    try:
        entries = list(os.scandir(CONTOUR_DIR))
    except OSError:
        return
    pngs = sorted(((e.stat().st_mtime, e.name) for e in entries if e.name.endswith(SUFFIX + ".png")), reverse=True)
    keep_names = set()
    for _, name in pngs[:keep]:
        keep_names.add(name)
        keep_names.add(name[:-4] + ".json")
        keep_names.add(name[:-4] + "l.png")
    for e in entries:
        if e.name in keep_names or e.name.endswith(".tmp"):
            continue
        try:
            os.remove(e.path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
#  Depuración:  python3 contour.py --debug foto.jpg [--no-model] [--size 1920x1080]
# ---------------------------------------------------------------------------

def _debug(argv):
    path = argv[0]
    use_model = "--no-model" not in argv
    size = (1920, 1080)
    if "--size" in argv:
        a, b = argv[argv.index("--size") + 1].lower().split("x")
        size = (int(a), int(b))
    if use_model and hq_available() and not hq_mask_fresh(path):
        print("Calculando el recorte de BiRefNet (la primera vez tarda; queda en caché)...")
        _, dt = hq_infer(path)
        print("  listo en %.1f s" % dt)
    tex, md, (wc, hc) = _work(path, size, use_model)
    info = LAST_INFO
    print("Detector: %s | profundidad dentro %.0f px, fuera %.0f px | sujeto entre y=%.0f y y=%.0f" % (
        info["detector"], md["maxin"], md["maxout"], md["yt"], md["yb"]))
    full = info["full"]
    arr = np.asarray(full).copy()
    if info["prob"] is None:
        print("Sin sujeto: el efecto hará crecer colonias desde puntos al azar.")
    else:
        mask = _resize_f((info["prob"] > 0.5).astype(np.float32), full.size, Image.BILINEAR) > 0.5
        edge = _dilate(mask, 1) & ~mask
        arr[edge] = (255, 40, 40)
    out = os.path.splitext(os.path.basename(path))[0] + "_debug.png"
    Image.fromarray(arr).save(out)
    print("Guardado:", out, "(contorno detectado en rojo)")


IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


def _status():
    print("numpy:", "sí" if np is not None else "NO (sin numpy no hay efecto Contorno)")
    try:
        import onnxruntime
        print("onnxruntime:", onnxruntime.__version__)
    except Exception as e:
        print("onnxruntime: NO instalado (%s)" % e)
    print("U2-Netp (rápido):", ("%.1f MB" % (os.path.getsize(MODEL_PATH) / 1e6)) if os.path.exists(MODEL_PATH) else "no instalado")
    hq = ("%.0f MB" % (os.path.getsize(HQ_MODEL_PATH) / 1e6)) if os.path.exists(HQ_MODEL_PATH) else "no instalado"
    print("BiRefNet-lite (calidad):", hq, "->", "ACTIVO" if hq_available() else "inactivo")
    if os.path.exists(NOHQ_FLAG):
        print("  ATENCIÓN:", open(NOHQ_FLAG).read().strip())
    try:
        n = len([f for f in os.listdir(MASK_DIR) if f.endswith(".png")])
        print("recortes en caché:", n)
    except OSError:
        print("recortes en caché: 0")
    print("se usará:", "BiRefNet" if hq_available() else ("U2-Netp" if model_available() else "detector clásico (menos preciso)"))
    if os.path.exists(LOG_FILE):
        print("\nÚltimas líneas de", LOG_FILE)
        print("".join(open(LOG_FILE).readlines()[-8:]))


def _rss_mb():
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


def _check_hq(path):
    """Prueba un modelo de recorte con una imagen sintética: carga, tamaño de entrada, tiempo y RAM. Sale 0 si va bien."""
    global HQ_MODEL_PATH
    HQ_MODEL_PATH = path
    yy, xx = np.mgrid[0:540, 0:960]
    img = np.zeros((540, 960, 3), dtype=np.uint8)
    img[...] = (40, 60, 90)
    img[((xx - 480) / 150.0) ** 2 + ((yy - 280) / 200.0) ** 2 < 1] = (230, 200, 170)
    tmp = os.path.join(CACHE_DIR, "check-hq.png")
    os.makedirs(CACHE_DIR, exist_ok=True)
    Image.fromarray(img).save(tmp)
    sess = _hq_session()
    inp = sess.get_inputs()[0]
    arr, dt = hq_infer(tmp, save=False)
    print("entrada:", inp.name, inp.shape, "| salida:", [o.shape for o in sess.get_outputs()])
    print("inferencia: %.1f s | RAM pico: %.0f MB | recorte %dx%d | cobertura %.0f%%" % (
        dt, _rss_mb(), arr.shape[1], arr.shape[0], 100.0 * (arr > 127).mean()))
    os.remove(tmp)
    return 0


def _warm(args):
    """Precalcula los recortes (y los auxiliares) de una carpeta o lista de imágenes, en primer plano."""
    files = []
    for a in args:
        if os.path.isdir(a):
            files += sorted(os.path.join(a, f) for f in os.listdir(a) if f.lower().endswith(IMG_EXT))
        elif os.path.isfile(a):
            files.append(a)
    if not hq_available():
        print("BiRefNet no está disponible (ejecuta ./instalar-modelo.sh, o mira python3 contour.py --status).")
        return 1
    todo = [f for f in files if not hq_mask_fresh(f)]
    print("%d imágenes, %d por procesar (el resto ya está en caché)" % (len(files), len(todo)))
    t0 = time.time()
    for f in todo:
        hq_enqueue(f, limit=0)
    last = -1
    while hq_pending():
        done = len(todo) - hq_pending()
        if done != last:
            last = done
            print("  %d/%d  (%.0fs)" % (done, len(todo), time.time() - t0), flush=True)
        time.sleep(1)
    ok = len([f for f in todo if hq_mask_fresh(f)])
    print("Listo: %d recortes nuevos en %.0fs" % (ok, time.time() - t0))
    fails = [f for f in todo if _hq_failed(f)]
    if fails:
        print("Fallaron %d (mira %s)" % (len(fails), LOG_FILE))
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--debug":
        _debug(sys.argv[2:])
    elif len(sys.argv) >= 2 and sys.argv[1] == "--status":
        _status()
    elif len(sys.argv) >= 2 and sys.argv[1] == "--hq-worker":
        _hq_worker()
    elif len(sys.argv) >= 3 and sys.argv[1] == "--check-hq":
        sys.exit(_check_hq(sys.argv[2]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "--warm":
        sys.exit(_warm(sys.argv[2:]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "--bench":
        arr, dt = hq_infer(sys.argv[2], save=False)
        print("BiRefNet: %.1f s de inferencia, RAM pico %.0f MB" % (dt, _rss_mb()))
    else:
        print(__doc__)
