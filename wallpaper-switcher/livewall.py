"""Fondos animados (video / GIF) para Cinnamon (Linux Mint, sesión X11).

Cinnamon no puede reproducir video como fondo por sí solo, así que el trabajo
lo hace un proceso aparte (player.py): abre una ventana por monitor, la coloca
en la capa del escritorio (detrás de los iconos de Nemo y de todas las
ventanas) y dentro incrusta mpv con `--wid`.

Este módulo es el "mando a distancia": arranca, sustituye y detiene ese
reproductor, guarda qué video está activo (para restaurarlo al iniciar sesión)
y, opcionalmente, crea una copia ligera del video con ffmpeg.

Optimización de video (resumen):
  * La copia ligera se adapta al monitor (no escala por encima de la pantalla),
    limita los fps (24/30/60 según preferencia) y el bitrate, y conserva el audio.
  * ffmpeg corre con la prioridad más baja (nice + ionice) y con la mitad de los
    núcleos, para no congelar el equipo mientras convierte.
  * Solo se convierte UN video a la vez; si eliges otro fondo, la conversión en
    curso se cancela en vez de acumular procesos.
  * Mientras la copia no está lista, el original se reproduce en "modo ligero"
    (decodificación rápida) si es pesado.
  * La caché de copias se poda por uso (preferencia "video_cache_mb").

No importa Qt a propósito: se puede usar desde cualquier hilo o script.
También se puede ejecutar como programa:

    python3 livewall.py --stop                 # detiene el fondo animado
    python3 livewall.py --info VIDEO           # muestra si el video se optimizaría y por qué
    python3 livewall.py --optimize VIDEO       # crea la copia ligera ahora (en primer plano)
    python3 livewall.py --prune                # poda la caché de copias optimizadas
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
PLAYER = os.path.join(HERE, "player.py")

CONFIG_DIR = os.path.expanduser("~/.config/wallpaper-switcher")
CACHE_DIR = os.path.expanduser("~/.cache/wallpaper-switcher")
STATE_FILE = os.path.join(CONFIG_DIR, "live.json")
PREFS_FILE = os.path.join(CONFIG_DIR, "preferences.json")
LOG_FILE = os.path.join(CACHE_DIR, "player.log")
OPT_DIR = os.path.join(CACHE_DIR, "video")

VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".gif")

MAX_W = 1920                   # tope de la copia optimizada: un fondo no necesita más y en CPUs
MAX_H = 1080                   # modestas (Celeron/Atom) 1440p+ cuesta mucho de decodificar y de pasar a la GPU
DEFAULT_FPS = 24
HEAVY_BITRATE = 6_000_000     # bits/s: un H.264 por encima de esto cuesta de leer y decodificar
OPT_VERSION = "v5"             # súbelo si cambias los parámetros de ffmpeg (invalida copias viejas)

_QUALITY_CRF = {"light": 27, "balanced": 23, "high": 20}


def is_video(path):
    return bool(path) and path.lower().endswith(VIDEO_EXTS)


# ---------------------------------------------------------------------------
#  Utilidades
# ---------------------------------------------------------------------------

def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def notify(title, body=""):
    if shutil.which("notify-send"):
        _run(["notify-send", "-a", "Wallpaper Switcher",
              "-i", "preferences-desktop-wallpaper", title, body])
    else:
        print(f"{title}: {body}")


def mpv_available():
    return bool(shutil.which("mpv"))


def is_x11_session():
    """El reproductor necesita X11 (Cinnamon en Wayland aún es experimental)."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"


def _have_ffmpeg():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# ---------------------------------------------------------------------------
#  Preferencias (se leen del JSON para no depender de Qt)
# ---------------------------------------------------------------------------

_prefs_cache = (None, {})


def read_prefs():
    """Contenido de preferences.json (con caché por fecha de modificación)."""
    global _prefs_cache
    try:
        mtime = os.path.getmtime(PREFS_FILE)
    except OSError:
        return {}
    if _prefs_cache[0] == mtime:
        return _prefs_cache[1]
    try:
        with open(PREFS_FILE) as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    _prefs_cache = (mtime, data)
    return data


def pref(key, default):
    return read_prefs().get(key, default)


def _max_fps():
    try:
        v = int(float(pref("video_max_fps", DEFAULT_FPS)))
    except (TypeError, ValueError):
        v = DEFAULT_FPS
    return v if v in (24, 30, 60) else DEFAULT_FPS


def _quality():
    q = pref("optimize_quality", "light")
    return q if q in _QUALITY_CRF else "light"


# ---------------------------------------------------------------------------
#  Estado persistente (para restaurar al iniciar sesión)
# ---------------------------------------------------------------------------

def read_state():
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_state(state):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print(f"No se pudo guardar el estado del fondo animado: {e}")


def clear_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
#  Procesos del reproductor
# ---------------------------------------------------------------------------

def player_pids(older_than=None):
    """PIDs de los reproductores en marcha. Con `older_than` (marca de tiempo en ms)
    solo devuelve los que arrancaron antes: así el reproductor más nuevo sustituye
    a los anteriores sin que dos arranques simultáneos se maten entre sí."""
    out = []
    me = os.getpid()
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        # python3 /ruta/player.py ...  (así no se confunde con un editor que tenga el archivo abierto)
        if len(args) < 2 or b"python" not in os.path.basename(args[0]) or not args[1].endswith(b"player.py"):
            continue
        stamp = 0
        if b"--stamp" in args:
            try:
                stamp = int(args[args.index(b"--stamp") + 1])
            except (ValueError, IndexError):
                pass
        if older_than is None or stamp < older_than:
            out.append(int(name))
    return out


def current_video():
    """Ruta del video que está sonando como fondo, o None."""
    if not player_pids():
        return None
    p = read_state().get("video")
    return os.path.normpath(p) if p and os.path.exists(p) else None


def _spawn_player(delay=0.0):
    """Lanza un reproductor nuevo que lee el estado guardado. El anterior se
    cierra solo cuando el nuevo ya está dibujando (sin parpadeo negro)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        if os.path.getsize(LOG_FILE) > 512 * 1024:      # el log no crece sin límite
            os.remove(LOG_FILE)
    except OSError:
        pass
    try:
        log = open(LOG_FILE, "a")
    except OSError:
        log = subprocess.DEVNULL
    extra = ["--delay", f"{delay:.2f}"] if delay > 0 else []
    subprocess.Popen([sys.executable, PLAYER, "--restore", "--stamp", str(int(time.time() * 1000)), *extra],
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=log)


# ---------------------------------------------------------------------------
#  Entorno: batería, monitores y ventanas que tapan el escritorio
# ---------------------------------------------------------------------------

def on_battery():
    """True si el equipo tiene batería propia y no está enchufado."""
    base = "/sys/class/power_supply"
    try:
        names = os.listdir(base)
    except OSError:
        return False

    def read(name, attr):
        try:
            with open(f"{base}/{name}/{attr}") as f:
                return f.read().strip()
        except OSError:
            return ""

    has_battery = on_ac = False
    for n in names:
        kind = read(n, "type")
        if kind == "Battery" and read(n, "scope") != "Device":   # ignora ratones/mandos
            has_battery = True
        elif kind == "Mains" and read(n, "online") == "1":
            on_ac = True
    return has_battery and not on_ac


_XRANDR_RE = re.compile(r"^(\S+) connected( primary)? (\d+)x(\d+)\+(-?\d+)\+(-?\d+)", re.M)


def parse_monitors(xrandr_output):
    """[(nombre, es_principal, ancho, alto)] a partir de la salida de `xrandr --current`."""
    return [(m.group(1), bool(m.group(2)), int(m.group(3)), int(m.group(4)))
            for m in _XRANDR_RE.finditer(xrandr_output or "")]


@lru_cache(maxsize=4)
def _target_size(primary_only):
    r = _run(["xrandr", "--current"], timeout=3)
    mons = parse_monitors(r.stdout if r else "")
    if primary_only and mons:
        mons = [m for m in mons if m[1]] or mons[:1]
    if not mons:
        return 1920, 1080
    mw, mh = (MAX_W, MAX_H) if (os.cpu_count() or 2) > 4 else (1600, 900)    # CPU modesta: menos píxeles que decodificar
    return min(max(m[2] for m in mons), mw), min(max(m[3] for m in mons), mh)


@lru_cache(maxsize=1)
def _native_size():
    r = _run(["xrandr", "--current"], timeout=3)
    mons = parse_monitors(r.stdout if r else "")
    if not mons:
        return 1920, 1080
    m = next((m for m in mons if m[1]), mons[0])
    return m[2], m[3]


def native_size():
    """Tamaño EXACTO (sin tope) del monitor principal: el efecto Contorno necesita que el auxiliar
    mida justo lo que el monitor (con el tope de target_size() nunca coincidía en pantallas > 1080p)."""
    return _native_size()


def target_size():
    """(ancho, alto) que debe cubrir la copia optimizada: el monitor más grande
    (o el principal), limitado a 2560x1440. No se amplía nunca el video original."""
    return _target_size(pref("video_screens", "all") == "primary")


_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_GEO_RE = {k: re.compile(rx) for k, rx in (
    ("x", r"Absolute upper-left X:\s+(-?\d+)"), ("y", r"Absolute upper-left Y:\s+(-?\d+)"),
    ("w", r"Width:\s+(\d+)"), ("h", r"Height:\s+(\d+)"))}


def window_is_cover(xprop_output):
    """¿Esta ventana tapa el escritorio? (a pantalla completa o maximizada, y no minimizada)."""
    out = xprop_output or ""
    if "_NET_WM_STATE_HIDDEN" in out:
        return False
    if "_NET_WM_WINDOW_TYPE_DESKTOP" in out or "_NET_WM_WINDOW_TYPE_DOCK" in out:
        return False
    if "_NET_WM_STATE_FULLSCREEN" in out:
        return True
    return "_NET_WM_STATE_MAXIMIZED_VERT" in out and "_NET_WM_STATE_MAXIMIZED_HORZ" in out


def parse_geometry(xwininfo_output):
    """(x, y, ancho, alto) absolutos a partir de la salida de `xwininfo -id`, o None."""
    try:
        v = {k: int(rx.search(xwininfo_output).group(1)) for k, rx in _GEO_RE.items()}
        return v["x"], v["y"], v["w"], v["h"]
    except Exception:
        return None


def covering_window():
    """Ventana activa que tapa el escritorio.

    Devuelve None (nada lo tapa), (x, y, ancho, alto) en píxeles nativos, o "all"
    si tapa algo pero no se pudo medir (se asume que tapa todos los monitores)."""
    r = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], timeout=1.0)
    m = _HEX_RE.search(r.stdout) if r else None
    if not m or int(m.group(0), 16) == 0:
        return None
    wid = m.group(0)
    r = _run(["xprop", "-id", wid, "_NET_WM_STATE", "_NET_WM_WINDOW_TYPE"], timeout=1.0)
    if not r or not window_is_cover(r.stdout):
        return None
    r = _run(["xwininfo", "-id", wid], timeout=1.0)
    return parse_geometry(r.stdout if r else "") or "all"


# ---------------------------------------------------------------------------
#  API pública
# ---------------------------------------------------------------------------

def start_video(path, optimize=False, layer="desktop", screens="all", mute=True, hwdec=True,
                compat=False, delay=0.0):
    """Pone `path` como fondo animado. Devuelve True si lo lanzó.

    optimize: si el video es pesado, arranca ya con el original (en modo ligero) y en
    segundo plano crea una copia optimizada a la que cambia al terminar."""
    path = os.path.abspath(path)
    optimize = True                  # un fondo siempre se reproduce desde la copia ligera (si hace falta)
    if not mpv_available():
        notify("Falta mpv", "Ejecuta ./instalar-animados.sh para instalar los fondos animados.")
        return False
    if not is_x11_session():
        notify("Sesión Wayland no compatible",
               "Los fondos animados necesitan una sesión X11 de Cinnamon.")
        return False
    if not shutil.which("xprop"):
        notify("Falta xprop", "Instala x11-utils (./instalar-animados.sh).")
        return False

    play, heavy, spawn_job = path, False, False
    if _have_ffmpeg():
        needs = _needs_optimizing(path, _probe(path))
        if needs and optimize:
            opt = optimized_path(path)
            if os.path.exists(opt):
                play = opt
                _touch(opt)                      # marca de uso para la poda LRU
            else:
                spawn_job = True
        heavy = needs and play == path           # se reproduce el original pesado: modo ligero

    write_state({"video": path, "play": play, "layer": layer, "screens": screens,
                 "mute": bool(mute), "hwdec": bool(hwdec), "compat": bool(compat),
                 "heavy": bool(heavy)})
    _spawn_player(delay)       # el reproductor espera `delay` s él mismo: sobrevive al cierre del selector

    if spawn_job:
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--optimize", path, "--watch"],
                             start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Optimización omitida: {e}")
    return True


def kill_players():
    """Cierra los reproductores en marcha SIN olvidar el estado (el que se lance después lo reemplaza).
    Se usa al cambiar a otro video con transición: así la animación no queda tapada por el video anterior."""
    import signal
    for pid in player_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def stop_video():
    """Detiene el fondo animado y olvida el estado (no se restaurará al iniciar sesión)."""
    clear_state()
    import signal
    for pid in player_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _touch(path):
    try:
        os.utime(path, None)
    except OSError:
        pass


# ---------------------------------------------------------------------------
#  Optimización (H.264 8 bits, fps y resolución acordes al monitor)
# ---------------------------------------------------------------------------

def _ratio(s):
    try:
        n, d = str(s).split("/")
        n, d = float(n), float(d)
        return n / d if d else 0.0
    except Exception:
        return 0.0


def _probe(path):
    """dict(codec, pix, fps, w, h, bitrate, audio) del primer video, o None."""
    r = _run(["ffprobe", "-v", "error", "-of", "json", "-show_entries",
              "stream=codec_type,codec_name,pix_fmt,r_frame_rate,avg_frame_rate,width,height,bit_rate"
              ":format=bit_rate", path], timeout=15)
    try:
        data = json.loads(r.stdout)
        streams = data.get("streams", [])
        v = next(s for s in streams if s.get("codec_type") == "video")
        fps = _ratio(v.get("r_frame_rate"))
        if not 1 <= fps <= 240:                  # los GIF a veces declaran valores absurdos
            fps = _ratio(v.get("avg_frame_rate"))
        if not 1 <= fps <= 240:
            fps = float(DEFAULT_FPS)
        try:
            bitrate = int(v.get("bit_rate") or data.get("format", {}).get("bit_rate") or 0)
        except (TypeError, ValueError):
            bitrate = 0
        return {"codec": v.get("codec_name"), "pix": v.get("pix_fmt"), "fps": fps,
                "w": int(v["width"]), "h": int(v["height"]), "bitrate": bitrate,
                "audio": any(s.get("codec_type") == "audio" for s in streams)}
    except Exception:
        return None


@lru_cache(maxsize=1)
def _h264_encoder():
    r = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout=10)
    out = r.stdout if r else ""
    for enc in ("libx264", "libopenh264"):
        if f" {enc} " in out:
            return enc
    return None


def optimized_path(path):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    tw, th = target_size()
    key = f"{OPT_VERSION}:{os.path.abspath(path)}:{mtime}:{_max_fps()}:{_quality()}:{tw}x{th}"
    return os.path.join(OPT_DIR, f"{hashlib.md5(key.encode()).hexdigest()}.mp4")


def _needs_optimizing(path, info):
    """¿Compensa reconvertir este video? (siempre los GIF)."""
    if path.lower().endswith(".gif"):
        return True
    if not info:
        return False
    tw, th = target_size()
    cover = max(tw / info["w"], th / info["h"])          # <1: el video es mayor que la pantalla
    return (info["codec"] != "h264" or info["pix"] != "yuv420p"
            or info["fps"] > _max_fps() + 0.5
            or cover < 0.95
            or info["bitrate"] > HEAVY_BITRATE)


def explain(path):
    """Texto legible con el diagnóstico de un video (para --info)."""
    info = _probe(path)
    tw, th = target_size()
    lines = [f"Archivo:  {path}", f"Monitor:  {tw}x{th} (destino de la copia)",
             f"Límite:   {_max_fps()} fps, calidad {_quality()}"]
    if not info:
        lines.append("ffprobe no pudo leer el video.")
    else:
        lines.append(f"Video:    {info['codec']} {info['pix']} {info['w']}x{info['h']} "
                     f"{info['fps']:.2f} fps {info['bitrate'] / 1e6:.1f} Mb/s"
                     f"{' (con audio)' if info['audio'] else ''}")
    lines.append("Se optimizaría: " + ("sí" if _needs_optimizing(path, info) else "no (ya es ligero)"))
    return "\n".join(lines)


def _ffmpeg_cmd(src, dst, enc, info):
    tw, th = target_size()
    fps = info["fps"] if info else DEFAULT_FPS
    vf = []
    if fps > _max_fps() + 0.5:
        vf.append(f"fps={_max_fps()}")
    # Cubre el monitor sin ampliar nunca el original; anchos pares y proporción intacta
    vf.append(f"scale=w='max(2,trunc(iw*min(1,max({tw}/iw,{th}/ih))/2)*2)':h=-2:flags=bicubic")
    vf.append("format=yuv420p")
    has_audio = bool(info and info["audio"])

    cmd = ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-i", src, "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0?"]
    cmd += ["-vf", ",".join(vf), "-c:v", enc]

    max_mbps = max(4, round(8 * tw * th / (1920 * 1080)))   # tope de bitrate: lectura y decodificación fluidas
    if enc == "libx264":
        threads = 1 if (os.cpu_count() or 2) <= 4 else (os.cpu_count() or 2) // 4   # poco CPU: no compite con el video
        cmd += ["-preset", "veryfast", "-crf", str(_QUALITY_CRF[_quality()]), "-profile:v", "main", "-tune", "fastdecode", "-bf", "0", "-refs", "2", "-g", "48",
                "-maxrate", f"{max_mbps}M", "-bufsize", f"{max_mbps * 2}M", "-threads", str(threads)]
    else:
        cmd += ["-b:v", f"{max_mbps}M"]
    cmd += ["-c:a", "aac", "-b:a", "96k"] if has_audio else ["-an"]
    return [*cmd, "-sn", "-dn", "-movflags", "+faststart", dst]


def _low_priority():
    try:
        os.nice(19)
    except OSError:
        pass


def _still_wanted(src):
    """¿Sigue siendo `src` el fondo elegido? (si no, la conversión ya no sirve)."""
    return read_state().get("video") == src


def _encode(src, tmp, enc, info, watch):
    """Ejecuta ffmpeg a baja prioridad. Devuelve True si terminó bien.
    Con watch=True se cancela si el usuario elige otro fondo."""
    cmd = _ffmpeg_cmd(src, tmp, enc, info)
    if shutil.which("ionice"):
        cmd = ["ionice", "-c", "3", *cmd]
    try:
        log = open(LOG_FILE, "a")
    except OSError:
        log = subprocess.DEVNULL
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            preexec_fn=_low_priority)
    while True:
        try:
            return proc.wait(timeout=1.0) == 0
        except subprocess.TimeoutExpired:
            if watch and not _still_wanted(src):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False


def _optimize_job(src, watch=False):
    """Crea la copia optimizada. Con watch=True (lanzado por start_video) se ejecuta
    en un proceso aparte que sobrevive al carrusel y respeta la cola de un solo ffmpeg."""
    if not _have_ffmpeg():
        return
    enc = _h264_encoder()
    info = _probe(src)
    if not enc or not _needs_optimizing(src, info):
        return
    opt = optimized_path(src)
    if os.path.exists(opt):
        return
    os.makedirs(OPT_DIR, exist_ok=True)
    if watch:                         # no convertir justo al arrancar el video: se notaban tirones
        for _ in range(20):
            time.sleep(1)
            if not _still_wanted(src):
                return

    # Un único ffmpeg a la vez: los demás esperan aquí y, al llegar su turno,
    # comprueban que su video siga siendo el elegido.
    with open(os.path.join(OPT_DIR, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (watch and not _still_wanted(src)) or os.path.exists(opt):
            return
        tmp = opt + ".part.mp4"
        ok = False
        try:
            ok = _encode(src, tmp, enc, info, watch)
            if ok and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, opt)
            else:
                ok = False
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    if not ok:
        return
    # Solo se cambia a la copia ligera si el usuario no ha elegido otro fondo mientras tanto
    st = read_state()
    if st.get("video") == src and player_pids():
        st["play"] = opt
        st["heavy"] = False
        write_state(st)
        _spawn_player()


def prune_optimized(max_bytes=None):
    """Borra las copias menos usadas cuando la caché supera el límite (queda al 80 %).
    Nunca borra la que está sonando ahora."""
    if max_bytes is None:
        try:
            max_bytes = int(pref("video_cache_mb", 2000)) * 1024 * 1024
        except (TypeError, ValueError):
            max_bytes = 2000 * 1024 * 1024
    play = read_state().get("play")
    keep = os.path.normpath(play) if play else None
    now = time.time()
    files = []
    try:
        with os.scandir(OPT_DIR) as it:
            for e in it:
                try:
                    st = e.stat()
                except OSError:
                    continue
                if not e.is_file() or not e.name.endswith(".mp4"):
                    continue
                if e.name.endswith(".part.mp4"):          # restos de una conversión interrumpida
                    if now - st.st_mtime > 3600:
                        os.remove(e.path)
                    continue
                files.append((e.path, max(st.st_atime, st.st_mtime), st.st_size))
    except OSError:
        return
    total = sum(f[2] for f in files)
    if total <= max_bytes:
        return
    target = int(max_bytes * 0.8)
    for path, _, size in sorted(files, key=lambda f: f[1]):
        if os.path.normpath(path) == keep:
            continue
        try:
            os.remove(path)
            total -= size
        except OSError:
            continue
        if total <= target:
            break


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--stop"]:
        stop_video()
    elif args[:1] == ["--optimize"] and len(args) > 1:
        _optimize_job(os.path.abspath(args[1]), watch="--watch" in args)
    elif args[:1] == ["--info"] and len(args) > 1:
        print(explain(os.path.abspath(args[1])))
    elif args[:1] == ["--prune"]:
        prune_optimized()
    else:
        print(__doc__)
