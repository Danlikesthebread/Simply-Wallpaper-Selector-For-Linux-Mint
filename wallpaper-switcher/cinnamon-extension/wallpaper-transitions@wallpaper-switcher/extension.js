/* Wallpaper Transitions — extensión de Cinnamon.
 *
 * Cuando cambia `picture-uri` (org.cinnamon.desktop.background) dibuja una transición ENTRE el fondo
 * anterior y el nuevo dentro del propio compositor de Cinnamon: una capa de actores Clutter puesta
 * justo encima de los actores de fondo y debajo de todo lo demás. Por eso los desklets, los iconos y
 * las ventanas no se tocan, y no hay ventanas X extra.
 *
 * Funcionamiento:
 *   1. Se mantiene en memoria el fondo ACTUAL ya decodificado y recortado al monitor (ajuste "zoom").
 *   2. Al cambiar el fondo: se pone al instante una capa con el fondo VIEJO (cubre el fundido propio de
 *      Cinnamon), se decodifica el nuevo sin bloquear (async) y se anima por encima.
 *   3. Al terminar se mantiene la capa un instante hasta que acabe el fundido nativo y se destruye.
 *      En reposo no queda ningún actor.
 *
 * Efectos: wipe, wave, grow, outer, fade, slide, zoom, blinds, barn, dissolve, ink, glass, reveal, portal, pixels, random.
 * grow/outer/wave y el barrido en diagonal usan un shader (Clutter.ShaderEffect); si no está disponible
 * se sustituyen por equivalentes de recorte. Las opciones se leen de
 * ~/.config/wallpaper-switcher/preferences.json (las mismas del panel de Wallpaper Switcher).
 */
const Clutter = imports.gi.Clutter;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const GdkPixbuf = imports.gi.GdkPixbuf;
const Main = imports.ui.main;
let Cogl = null;
try { Cogl = imports.gi.Cogl; } catch (e) { /* se comprueba en enable() */ }

const BG_SCHEMA = 'org.cinnamon.desktop.background';
const PREFS_FILE = GLib.build_filenamev([GLib.get_user_config_dir(), 'wallpaper-switcher', 'preferences.json']);
const MAX_TEX_W = 1920;          // ancho máximo de textura (en pantallas mayores la GPU escala)
const NATIVE_FADE_MS = 1250;     // el fundido propio de Cinnamon debe haber terminado antes de quitar la capa
const TAU = Math.PI * 2;

const EFFECTS = ['contour', 'wipe', 'wave', 'grow', 'outer', 'fade', 'slide', 'zoom', 'blinds', 'barn', 'dissolve', 'ink', 'glass', 'reveal', 'portal', 'pixels'];
const SHADER_EFFECTS = ['contour', 'wave', 'grow', 'outer', 'ink', 'glass', 'reveal', 'portal', 'pixels'];   // = claves de SHADER_FX
const DIRS = {
    right: [1, 0], left: [-1, 0], down: [0, 1], up: [0, -1],
    diag: [Math.SQRT1_2, Math.SQRT1_2],
};

// Diagnóstico: ponlo a true solo para depurar (escribe ~/.cache/wallpaper-transitions.log sin límite de tamaño)
const DEBUG = false;
const LOG_FILE = GLib.build_filenamev([GLib.get_user_cache_dir(), 'wallpaper-transitions.log']);
function fileLog(line) {
    if (!DEBUG) return;
    try {
        const f = Gio.File.new_for_path(LOG_FILE);
        const st = f.append_to(Gio.FileCreateFlags.NONE, null);
        st.write_all((new Date().toISOString() + ' ' + line + '\n'), null);
        st.close(null);
    } catch (e) { /* sin log de archivo */ }
}
function log(msg) { if (DEBUG) { global.log('[wallpaper-transitions] ' + msg); fileLog(msg); } }
function logError(e, msg) { global.logError('[wallpaper-transitions] ' + (msg || '') + ': ' + e); fileLog('ERROR ' + (msg || '') + ': ' + e + (e && e.stack ? '\n' + e.stack : '')); }

/* Vuelca a ~/.cache/wallpaper-transitions.log el árbol de capas de Cinnamon (para diagnóstico). */
function dumpTree() {
    try {
        let n = 0;
        const wg = global.window_group;
        const walk = (a, depth, idx) => {
            if (n++ > 250) return;
            let tag = '';
            if (a === wg) tag += ' <== window_group';
            try { if (a === global.stage) tag += ' <== stage'; } catch (e) {}
            try { if (a === global.bottom_window_group) tag += ' <== bottom_window_group'; } catch (e) {}
            try { if (a === global.top_window_group) tag += ' <== top_window_group'; } catch (e) {}
            try { if (a === global.overlay_group) tag += ' <== overlay_group'; } catch (e) {}
            try { if (a === global.background_actor) tag += ' <== background_actor'; } catch (e) {}
            try { if (a === Main.layoutManager._backgroundGroup) tag += ' <== layoutManager._backgroundGroup'; } catch (e) {}
            let sz = '';
            try { const [w, h] = a.get_transformed_size(); const [x, y] = a.get_transformed_position(); sz = ` ${Math.round(w)}x${Math.round(h)}@${Math.round(x)},${Math.round(y)}`; } catch (e) {}
            fileLog('TREE ' + '  '.repeat(depth) + idx + ': ' + String(a) + sz + (a.visible === false ? ' (oculto)' : '') + tag);
            if (depth >= 4 || isBackgroundActor(a) || /WindowActor/.test(String(a))) return;
            let kids = [];
            try { kids = a.get_children(); } catch (e) {}
            kids.forEach((k, i) => walk(k, depth + 1, i));
        };
        fileLog('---- dumpTree ----');
        walk(global.stage, 0, 0);
    } catch (e) { fileLog('dumpTree falló: ' + e); }
}

// ---------------------------------------------------------------------------
//  Configuración (preferences.json del selector; sin archivo se usan estos valores)
// ---------------------------------------------------------------------------

const DEFAULTS = {
    enabled: true, effect: 'wipe', direction: 'random', origin: 'center', easing: 'smooth', ms: 1200,
};

function loadConfig() {
    const cfg = Object.assign({}, DEFAULTS);
    try {
        const [ok, bytes] = GLib.file_get_contents(PREFS_FILE);
        if (!ok) return cfg;
        const text = (typeof TextDecoder !== 'undefined')
            ? new TextDecoder().decode(bytes) : imports.byteArray.toString(bytes);
        const d = JSON.parse(text);
        if (typeof d.wallpaper_fade === 'boolean') cfg.enabled = d.wallpaper_fade;
        if (typeof d.fade_effect === 'string') cfg.effect = d.fade_effect;
        if (typeof d.fade_direction === 'string') cfg.direction = d.fade_direction;
        if (typeof d.fade_origin === 'string') cfg.origin = d.fade_origin;
        if (typeof d.fade_easing === 'string') cfg.easing = d.fade_easing;
        if (typeof d.fade_ms === 'number') cfg.ms = d.fade_ms;
    } catch (e) { /* sin archivo o ilegible: valores por defecto */ }
    cfg.ms = Math.max(100, Math.min(5000, cfg.ms | 0));
    return cfg;
}

// ---------------------------------------------------------------------------
//  Curvas de progreso (0..1 -> 0..1)
// ---------------------------------------------------------------------------

function cubicBezier(x1, y1, x2, y2) {
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const X = s => ((ax * s + bx) * s + cx) * s;
    const Y = s => ((ay * s + by) * s + cy) * s;
    const dX = s => (3 * ax * s + 2 * bx) * s + cx;
    return function (t) {
        if (t <= 0) return 0;
        if (t >= 1) return 1;
        let s = t;
        for (let i = 0; i < 8; i++) {               // Newton
            const e = X(s) - t;
            if (Math.abs(e) < 1e-6) return Y(s);
            const d = dX(s);
            if (Math.abs(d) < 1e-6) break;
            s -= e / d;
        }
        let lo = 0, hi = 1; s = t;                  // bisección (respaldo)
        for (let i = 0; i < 32; i++) {
            const e = X(s) - t;
            if (Math.abs(e) < 1e-6) break;
            if (e > 0) hi = s; else lo = s;
            s = (lo + hi) / 2;
        }
        return Y(s);
    };
}

function makeEasing(name) {
    if (name === 'linear') return t => t;
    if (name === 'out') return t => 1 - Math.pow(1 - t, 3);
    return cubicBezier(0.54, 0.0, 0.34, 0.99);      // la curva por defecto de swww
}

// ---------------------------------------------------------------------------
//  Imágenes: decodificación asíncrona, recorte "zoom" y textura Clutter
// ---------------------------------------------------------------------------

function uriToPath(uri) {
    try { return Gio.File.new_for_uri(uri).get_path(); } catch (e) { return null; }
}

function textureSize(w, h) {
    const s = (typeof global.ui_scale === 'number' && global.ui_scale > 0) ? global.ui_scale : 1;
    let W = Math.max(1, Math.round(w * s)), H = Math.max(1, Math.round(h * s));
    if (W > MAX_TEX_W) { H = Math.max(1, Math.round(H * MAX_TEX_W / W)); W = MAX_TEX_W; }
    return [W, H];
}

function pixelFormat(hasAlpha) {
    return hasAlpha ? Cogl.PixelFormat.RGBA_8888 : Cogl.PixelFormat.RGB_888;
}

/* Pixbuf -> {image: Clutter.Image, w, h} cubriendo W x H y recortado al centro. */
function makeContent(pb, W, H) {
    try {
        const rotated = pb.apply_embedded_orientation();
        if (rotated) pb = rotated;
        if (pb.get_width() < W || pb.get_height() < H) {
            const f = Math.max(W / pb.get_width(), H / pb.get_height());
            pb = pb.scale_simple(Math.ceil(pb.get_width() * f), Math.ceil(pb.get_height() * f),
                                 GdkPixbuf.InterpType.BILINEAR);
        }
        if (pb.get_width() !== W || pb.get_height() !== H) {
            const x = Math.floor((pb.get_width() - W) / 2), y = Math.floor((pb.get_height() - H) / 2);
            pb = pb.new_subpixbuf(x, y, W, H).copy();
        }
        const image = new Clutter.Image();
        const fmt = pixelFormat(pb.get_has_alpha());
        try {
            image.set_data(pb.get_pixels(), fmt, W, H, pb.get_rowstride());
        } catch (e) {
            image.set_bytes(pb.read_pixel_bytes(), fmt, W, H, pb.get_rowstride());
        }
        return { image: image, w: W, h: H };
    } catch (e) {
        logError(e, 'makeContent');
        return null;
    }
}

/* Decodifica `path` a W x H (cover) sin bloquear Cinnamon. Devuelve una función que cancela. */
function loadCover(path, W, H, done) {
    let info = null;
    try { info = GdkPixbuf.Pixbuf.get_file_info(path); } catch (e) { /* ilegible */ }
    if (!info || !info[0] || info[1] <= 0 || info[2] <= 0) {
        GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { done(null); return GLib.SOURCE_REMOVE; });
        return () => {};
    }
    const f = Math.max(W / info[1], H / info[2]);
    const tw = Math.max(W, Math.ceil(info[1] * f)), th = Math.max(H, Math.ceil(info[2] * f));
    const cancellable = new Gio.Cancellable();
    Gio.File.new_for_path(path).read_async(GLib.PRIORITY_LOW, cancellable, (file, res) => {
        let stream;
        try { stream = file.read_finish(res); } catch (e) { done(null); return; }
        GdkPixbuf.Pixbuf.new_from_stream_at_scale_async(stream, tw, th, true, cancellable, (_s, res2) => {
            let pb = null;
            try { pb = GdkPixbuf.Pixbuf.new_from_stream_finish(res2); } catch (e) { /* formato no soportado */ }
            try { stream.close(null); } catch (e) { /* nada */ }
            if (!pb || cancellable.is_cancelled()) { done(null); return; }
            done(makeContent(pb, W, H));
        });
    });
    return () => cancellable.cancel();
}

/* Archivos auxiliares del efecto "contorno" (los crea contour.py antes de cambiar el fondo; formato v5):
 *  <md5>.c5.png  RGBA del tamaño EXACTO del monitor: RGB = imagen nueva exacta, A = distancia al contorno comprimida
 *                a 8 bits (0..127 dentro, 128..255 fuera). El alfa son DATOS, no transparencia.
 *  <md5>.c5l.png la línea del contorno ya dibujada, con un color de la foto (solo si hay sujeto).
 *  <md5>.c5.json profundidades máximas, extensión vertical del sujeto, si hay sujeto y una semilla.
 * Devuelve { png, linePng, wc, hc, mi, mo, yt, yb, sub, seed } o null si no existe o está desactualizado. */
function sidecarFor(path) {
    try {
        const md5 = GLib.compute_checksum_for_string(GLib.ChecksumType.MD5, path, -1);
        const base = GLib.build_filenamev([GLib.get_user_cache_dir(), 'wallpaper-switcher', 'contour', md5 + '.c5']);
        const png = base + '.png', meta = base + '.json', linePng = base + 'l.png';
        const mod = (p) => Gio.File.new_for_path(p).query_info('time::modified', Gio.FileQueryInfoFlags.NONE, null)
            .get_attribute_uint64('time::modified');
        if (mod(png) < mod(path)) return null;
        const [ok, bytes] = GLib.file_get_contents(meta);
        if (!ok) return null;
        const text = (typeof TextDecoder !== 'undefined')
            ? new TextDecoder().decode(bytes) : imports.byteArray.toString(bytes);
        const m = JSON.parse(text);
        if (m.v !== 5 || !(m.wc > 0) || !(m.hc > 0) || !(m.maxout >= 0) || !(m.maxin >= 0)) return null;
        const sub = m.subject !== false;
        return { png: png, linePng: (sub && GLib.file_test(linePng, GLib.FileTest.EXISTS)) ? linePng : null,
                 wc: m.wc, hc: m.hc, mi: m.maxin, mo: m.maxout, yt: m.yt, yb: m.yb, sub: sub,
                 seed: (typeof m.seed === 'number') ? m.seed : 0 };
    } catch (e) { return null; }
}

/* Decodifica un PNG a su tamaño real, sin escalar ni recortar. cb(pixbuf | null). */
function loadPixbuf(path, cancellable, cb) {
    Gio.File.new_for_path(path).read_async(GLib.PRIORITY_LOW, cancellable, (file, res) => {
        let stream;
        try { stream = file.read_finish(res); } catch (e) { cb(null); return; }
        GdkPixbuf.Pixbuf.new_from_stream_async(stream, cancellable, (_s, res2) => {
            let pb = null;
            try { pb = GdkPixbuf.Pixbuf.new_from_stream_finish(res2); } catch (e) { /* ilegible */ }
            try { stream.close(null); } catch (e) { /* nada */ }
            cb(pb);
        });
    });
}

function imageFromPixbuf(pb, fmt) {
    const image = new Clutter.Image();
    const W = pb.get_width(), H = pb.get_height();
    try {
        image.set_data(pb.get_pixels(), fmt, W, H, pb.get_rowstride());
    } catch (e) {
        image.set_bytes(pb.read_pixel_bytes(), fmt, W, H, pb.get_rowstride());
    }
    return image;
}

/* Carga el auxiliar (y la línea) y los sube como texturas. Devuelve una función que cancela. */
function loadRaw(sc, done) {
    const cancellable = new Gio.Cancellable();
    loadPixbuf(sc.png, cancellable, (pb) => {
        if (!pb || cancellable.is_cancelled()) { done(null); return; }
        try {
            const W = pb.get_width(), H = pb.get_height();
            if (W !== sc.wc || H !== sc.hc || !pb.get_has_alpha()) { done(null); return; }
            // RGBA_8888_PRE: Cogl no multiplica el color por el alfa al subir la textura, así que el shader recibe
            // los bytes exactos (aquí el alfa NO es transparencia: es la distancia al contorno)
            const image = imageFromPixbuf(pb, Cogl.PixelFormat.RGBA_8888_PRE);
            const finish = (lineImage) => done({ image: image, lineImage: lineImage, w: W, h: H, wc: sc.wc, hc: sc.hc,
                mi: sc.mi, mo: sc.mo, yt: sc.yt, yb: sc.yb, sub: sc.sub, seed: sc.seed });
            if (!sc.linePng) { finish(null); return; }
            loadPixbuf(sc.linePng, cancellable, (lpb) => {
                if (cancellable.is_cancelled()) { done(null); return; }
                let li = null;
                try {
                    if (lpb && lpb.get_width() === W && lpb.get_height() === H && lpb.get_has_alpha())
                        li = imageFromPixbuf(lpb, Cogl.PixelFormat.RGBA_8888);       // imagen normal: Cogl premultiplica
                } catch (e) { logError(e, 'línea del contorno'); }
                finish(li);                                                          // sin línea el efecto sigue sirviendo
            });
        } catch (e) {
            logError(e, 'loadRaw');
            done(null);
        }
    });
    return () => cancellable.cancel();
}

// ---------------------------------------------------------------------------
//  Capas de fondo: buscar los actores de fondo de cada monitor
// ---------------------------------------------------------------------------

function isBackgroundActor(a) {
    return /BackgroundActor/.test(String(a));
}

/* Grupo que contiene los fondos de escritorio: el más bajo de todos, por debajo de las ventanas. */
function backgroundRoot() {
    try {
        const g = Main.layoutManager._backgroundGroup;
        if (g) return g;
    } catch (e) { /* se prueba el siguiente */ }
    try { if (global.background_actor) return global.background_actor; } catch (e) { /* nada */ }
    return null;
}

/* ¿`actor` está POR ENCIMA de alguna ventana? Solo devuelve true si se comprueba con seguridad;
 * ante cualquier duda devuelve false (así nunca se desactiva la extensión por un falso positivo). */
function isAboveWindows(actor) {
    try {
        const wg = global.window_group;
        if (!wg) return false;
        const chain = (a) => { const c = []; for (; a; a = a.get_parent()) c.unshift(a); return c; };
        const ca = chain(actor), cw = chain(wg);
        let i = 0;
        while (i < ca.length && i < cw.length && ca[i] === cw[i]) i++;
        if (i >= ca.length) return false;
        if (i >= cw.length) {
            // Está dentro de window_group: debe quedar por debajo de todos los actores de ventana
            const kids = wg.get_children();
            const mine = kids.indexOf(ca[i]);
            return kids.some((k, idx) => k !== ca[i] && /WindowActor/.test(String(k)) && idx < mine);
        }
        const kids = ca[i - 1].get_children();
        return kids.indexOf(ca[i]) > kids.indexOf(cw[i]);
    } catch (e) {
        return false;
    }
}

/* Un objetivo por monitor, con su geometría en coordenadas del escenario. */
function findTargets() {
    const out = [];
    let mons = [];
    try { mons = Main.layoutManager.monitors; } catch (e) { /* nada */ }
    for (const m of mons) {
        const w = m.width, h = m.height;
        if (!(w > 0 && h > 0)) continue;
        const [tw, th] = textureSize(w, h);
        out.push({ x: m.x, y: m.y, w: w, h: h, key: `${tw}x${th}`, tw: tw, th: th });
    }
    log(`findTargets: monitores=${out.length}`);
    return out;
}

// ---------------------------------------------------------------------------
//  Efectos. Cada constructor recibe ctx y devuelve { update(t) } con t ya suavizado.
//  ctx: holder, oldA, w, h, dir, origin, layer() -> actor nuevo (imagen nueva) encima.
// ---------------------------------------------------------------------------

const SHADER_SRC = `
uniform sampler2D tex;
uniform float mode;
uniform float t;
uniform float sx;
uniform float sy;
uniform float ox;
uniform float oy;
uniform float dx;
uniform float dy;
uniform float rmax;
uniform float lo;
uniform float hi;
uniform float vlo;
uniform float span;
uniform float amp;
uniform float phase;
uniform float tw;
uniform float th;
uniform float dgc;
uniform float mi;
uniform float mo;
uniform float yt;
uniform float yb;
uniform float seed;
uniform float nosub;
uniform float c1x;
uniform float c1y;
uniform float c2x;
uniform float c2y;
uniform float c3x;
uniform float c3y;
float hsh(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
// Forma de una celda (círculo o cuadrado) que crece con g (0..1); devuelve su cobertura con borde suave de ~1.4 px
float cellShape(vec2 P, float cs, float g, float rnd) {
    vec2 p = (fract(P / cs) - 0.5) * 2.0;
    float sq = step(0.5, rnd);
    float dist = mix(length(p), max(abs(p.x), abs(p.y)), sq);
    float rho = g * mix(1.62, 1.20, sq);
    float aaw = 1.4 / (0.5 * cs);
    return smoothstep(-aaw, aaw, rho - dist - aaw);
}
// 1 si el texel de esa posición está dentro del sujeto (alfa < 128)
float insAt(vec2 q) { return 1.0 - step(127.5, texture2D(tex, q).a * 255.0); }

float vn(vec2 p) {
    vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hsh(i), hsh(i + vec2(1.0, 0.0)), f.x), mix(hsh(i + vec2(0.0, 1.0)), hsh(i + vec2(1.0, 1.0)), f.x), f.y);
}
// Momento (0..1) en que llega la colonia a un punto: distancia al sujeto (o a los focos) + ruido de baja frecuencia = frente lobulado
float arrive(vec2 Cn, float s) {
    float tb;
    if (nosub > 0.5) {
        float d1 = length(Cn - vec2(c1x, c1y));
        float d2 = length(Cn - vec2(c2x, c2y)) + 0.10 * dgc;
        float d3 = length(Cn - vec2(c3x, c3y)) + 0.20 * dgc;
        tb = min(d1, min(d2, d3)) / max(mo, 1.0);
    } else {
        vec2 cc = clamp(floor(Cn), vec2(0.0), vec2(tw - 1.0, th - 1.0));
        float uc = texture2D(tex, (cc + 0.5) / vec2(tw, th)).a * 255.0;
        tb = ((uc < 127.5) ? 0.0 : (uc - 128.0) / 127.0 * mo) / max(mo, 1.0);
    }
    tb += (vn(Cn / (170.0 * s) + seed) - 0.5) * 0.14 + (vn(Cn / (55.0 * s) + seed * 2.0) - 0.5) * 0.05;
    return clamp(tb, 0.0, 1.0);
}
// Colonia de manchas redondas: centros con jitter, tamaños distintos, se fusionan al crecer (aspecto orgánico)
float colonyAt(vec2 P, float cs, float tn, float s) {
    vec2 g0 = floor(P / cs);
    vec2 sg = step(0.5, fract(P / cs)) * 2.0 - 1.0;
    float best = 0.0;
    for (int i = 0; i < 4; i++) {
        vec2 n = g0 + vec2(mod(float(i), 2.0), floor(float(i) / 2.0)) * sg;
        vec2 jit = (vec2(hsh(n * 1.3 + seed + 2.0), hsh(n * 0.7 + seed + 9.0)) - 0.5) * 0.5;
        vec2 Cn = (n + 0.5 + jit) * cs;
        float g = clamp((tn - (arrive(Cn, s) * 0.55 + hsh(n + seed) * 0.06)) / 0.22, 0.0, 1.0);
        g = g * g * (3.0 - 2.0 * g);
        float rad = cs * (1.05 + 0.35 * hsh(n * 2.1 + seed + 4.0)) * g;
        best = max(best, 1.0 - smoothstep(rad - 1.4, rad + 1.4, length(P - Cn)));
    }
    return best;
}
// Hash sin sin(): más barato en GPU integradas (los efectos nuevos; el contorno conserva hsh para no cambiar su aspecto)
float hq(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
vec2 hq2(vec2 p) { return vec2(hq(p), hq(p + 37.0)); }
float vnq(vec2 p) {
    vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hq(i), hq(i + vec2(1.0, 0.0)), f.x), mix(hq(i + vec2(0.0, 1.0)), hq(i + vec2(1.0, 1.0)), f.x), f.y);
}
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 4; i++) { v += a * vnq(p); p = p * 2.03 + 17.0; a *= 0.5; }
    return v;
}
// Efectos nuevos (mode 4..8): escriben cogl_color_out premultiplicado
void newFx() {
    vec2 uv = cogl_tex_coord_in[0].xy;
    vec2 P = uv * vec2(sx, sy);
    vec2 R = vec2(sx, sy);
    float s = sy / 1080.0;
    vec4 c = texture2D(tex, uv);
    float a = 1.0;
    float fin = smoothstep(0.97, 1.0, t);
    if (mode < 4.5) {                         // TINTA
        float f = fbm(P / (260.0 * s) + seed);
        float d = min(length(P - vec2(c1x, c1y)), min(length(P - vec2(c2x, c2y)) + 0.05 * rmax, length(P - vec2(c3x, c3y)) + 0.10 * rmax)) / rmax;
        float front = t * 1.3 - (d + (f - 0.5) * 0.5);
        float x = front;
        if (abs(front) < 0.22) {                  // las venas solo existen junto al frente: lejos no se calcula el segundo fbm
            float v = fbm(P / (40.0 * s) + seed * 2.0);
            x -= (v - 0.5) * 0.14 * (1.0 - smoothstep(0.0, 0.22, abs(front)));
        }
        a = max(smoothstep(0.0, 0.06, x), fin);
        float border = a * (1.0 - smoothstep(0.0, 0.18, x)) * (1.0 - fin);
        c.rgb *= mix(1.0, 0.85, border);
        cogl_color_out = vec4(c.rgb * a, a);
    } else if (mode < 5.5) {                  // GRIETA DE CRISTAL
        float cs = 150.0 * s;
        vec2 g0 = floor(P / cs);
        float d1 = 1e9, d2 = 1e9; vec2 C = P, id = g0;
        for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) {
            vec2 n = g0 + vec2(float(i), float(j));
            vec2 Cn = (n + 0.5 + (hq2(n + seed) - 0.5) * 0.8) * cs;
            float dd = length(P - Cn);
            if (dd < d1) { d2 = d1; d1 = dd; C = Cn; id = n; }
            else if (dd < d2) { d2 = dd; }
        }
        float delay = length(C - vec2(ox, oy)) / rmax * 0.6 + hq(id + seed) * 0.2;
        a = max(smoothstep(delay, delay + 0.15, t), fin);
        vec2 off = (hq2(id + 5.0) - 0.5) * 30.0 * s * (1.0 - a);       // la esquirla "encaja" al llegar
        c = texture2D(tex, clamp((P + off) / R, 0.0, 1.0));
        float crack = (1.0 - smoothstep(0.0, 1.5 * s, d2 - d1)) * (1.0 - a) * 0.75;
        cogl_color_out = vec4(c.rgb * a * (1.0 - crack), a + crack * (1.0 - a));
    } else if (mode < 6.5) {                  // REVELADO POR LUZ
        float sp = dot(P, vec2(dx, dy));
        float w = 200.0 * s;
        a = smoothstep(-w, 0.0, lo - w + (hi - lo + w) * t - sp);
        c.rgb += vec3(1.0, 0.96, 0.85) * 0.45 * (1.0 - a) * a * 4.0;
        cogl_color_out = vec4(c.rgb * a, a);
    } else if (mode < 7.5) {                  // PORTAL
        vec2 o = vec2(ox, oy);
        float d = length(P - o) - rmax * t;
        a = smoothstep(-2.0, 2.0, -d);
        float band = 60.0 * s;
        vec2 dir = normalize(P - o + 1e-4);
        vec2 q = P - dir * 40.0 * s * smoothstep(band, 0.0, abs(d));
        c = texture2D(tex, clamp(q / R, 0.0, 1.0));
        float ring = exp(-pow(abs(d) / (4.0 * s), 2.0)) * smoothstep(0.0, 0.05, t) * (1.0 - fin);
        cogl_color_out = vec4(c.rgb * a + vec3(0.7, 0.9, 1.0) * ring * 0.8, max(a, ring));
    } else {                                  // PIXELES A COLOR
        float bs = mix(96.0, 1.0, t * t) * s;
        vec2 cell = floor(P / bs);
        c = texture2D(tex, clamp((cell + 0.5) * bs / R, 0.0, 1.0));
        a = max(step(hq(cell + seed), t * 1.2), fin);
        cogl_color_out = vec4(c.rgb * a, a);
    }
}
void main() {
    if (mode > 3.5) { newFx(); return; }
    if (mode > 2.5) {
        // CONTORNO (formato v5). Buffer 1:1 con la textura (w x h): RGB = imagen nueva exacta; A = distancia al
        // contorno comprimida (0..127 dentro, 128..255 fuera). La línea del contorno es otra capa (imagen).
        vec2 uv = cogl_tex_coord_in[0].xy;
        vec2 P = uv * vec2(tw, th);
        vec4 c0 = texture2D(tex, uv);
        float s = th / 1080.0;
        float cs = 30.0 * s;                                         // celdas pequeñas: círculos y cuadrados
        float dim = 0.5 * smoothstep(0.0, 0.10, t);
        float a;
        if (nosub > 0.5) {
            // SIN SUJETO: colonias desde tres puntos al azar (los otros dos arrancan algo después)
            a = colonyAt(P, cs, t, s);
        } else {
            vec2 ix = vec2(1.0 / tw, 0.0);
            vec2 iy = vec2(0.0, 1.0 / th);
            float u0 = c0.a * 255.0;
            float ins = 0.5 * (1.0 - step(127.5, u0))
                      + 0.125 * (insAt(uv - ix) + insAt(uv + ix) + insAt(uv - iy) + insAt(uv + iy));   // borde exacto y suave
            // Fase 2: el sujeto se rellena desde la línea hacia dentro (siguiendo el trazo de arriba abajo)
            float lt = clamp(t / 0.30, 0.0, 1.0);
            float swp = lt * lt * (3.0 - 2.0 * lt);
            float sweepY = (yt - 10.0 * s) + (yb - yt + 20.0 * s) * swp;
            float lag = 40.0 * s * (1.0 - lt);
            float gateIn = 1.0 - smoothstep(sweepY - lag, sweepY - lag + 24.0 * s, P.y);
            float fi = smoothstep(0.14, 0.55, t);
            float din = (127.0 - u0) / 127.0 * mi;
            float aIn = smoothstep(-0.9, 0.9, (-2.0 + (mi + 4.2) * fi) - din) * gateIn;
            // Fase 3: el resto avanza en celdas pequeñas, de más cerca a más lejos del sujeto
            float tn = clamp((t - 0.30) / 0.70, 0.0, 1.0);
            a = ins * aIn + (1.0 - ins) * colonyAt(P, cs, tn, s);
        }
        a = max(a, smoothstep(0.90, 0.985, t));            // sin salto al terminar
        // premultiplicado: el fondo viejo se oscurece un poco
        cogl_color_out = vec4(c0.rgb * a, a + dim * (1.0 - a));
        return;
    }
    vec4 c = texture2D(tex, cogl_tex_coord_in[0].xy);
    vec2 p = cogl_tex_coord_in[0].xy * vec2(sx, sy);
    float d;
    if (mode < 0.5) {
        d = rmax * t - length(p - vec2(ox, oy));
    } else if (mode < 1.5) {
        d = length(p - vec2(ox, oy)) - rmax * (1.0 - t);
    } else {
        float s = dot(p, vec2(dx, dy));
        float v = dot(p, vec2(-dy, dx)) - vlo;
        float front = lo - amp + (hi - lo + 2.0 * amp) * t + amp * sin(6.2831853 * 3.0 * v / span + phase);
        d = front - s;
    }
    float a = clamp(d + 0.5, 0.0, 1.0);
    cogl_color_out = c * a;
}`;

// GJS convierte los números enteros en gint: los uniforms float deben llegar como no enteros.
function F(v) { return Number.isInteger(v) ? v + 1e-6 : v; }

const rnd = (a, b) => a + Math.random() * (b - a);
const smooth = (a, b, x) => { const u = Math.min(1, Math.max(0, (x - a) / (b - a))); return u * u * (3 - 2 * u); };

/* Posición del cursor relativa al actor `holder`, o null si está fuera de él. */
function pointerIn(holder, w, h) {
    try {
        const [px, py] = global.get_pointer();
        const [gx, gy] = holder.get_transformed_position();
        if (px >= gx && px <= gx + w && py >= gy && py <= gy + h) return [px - gx, py - gy];
    } catch (e) { /* sin cursor */ }
    return null;
}

function shaderAvailable() {
    return typeof Clutter.ShaderEffect === 'function' && Clutter.ShaderType !== undefined;
}

function shaderLayer(ctx, mode, ampFrac) {
    const isC = (mode > 2.5 && mode < 3.5);
    const a = ctx.layer(isC);
    const eff = new Clutter.ShaderEffect({ shader_type: Clutter.ShaderType.FRAGMENT_SHADER });
    eff.set_shader_source(SHADER_SRC);
    a.add_effect(eff);
    const set = (name, v) => eff.set_uniform_value(name, F(v));
    try { eff.set_uniform_value('tex', 0); } catch (e) { /* el sampler se enlaza solo en algunas versiones */ }

    const { w, h } = ctx;
    const [ux, uy] = ctx.dir, vx = -uy, vy = ux;
    const corners = [[0, 0], [w, 0], [0, h], [w, h]];
    const pr = corners.map(c => c[0] * ux + c[1] * uy);
    const pe = corners.map(c => c[0] * vx + c[1] * vy);
    const lo = Math.min(...pr), hi = Math.max(...pr), vlo = Math.min(...pe), vhi = Math.max(...pe);
    const rmax = Math.max(...corners.map(c => Math.hypot(c[0] - ctx.origin[0], c[1] - ctx.origin[1]))) + 1;

    // La línea del contorno es una capa de imagen propia encima del shader (se anima con un recorte y la opacidad)
    let lineA = null;
    if (isC && ctx.raw && ctx.raw.sub && ctx.raw.lineImage) {
        lineA = ctx.line(ctx.raw.lineImage);
        lineA.opacity = 0;
    }
    set('mode', mode);
    set('sx', w); set('sy', h);
    set('ox', ctx.origin[0]); set('oy', ctx.origin[1]);
    set('dx', ux); set('dy', uy);
    set('rmax', rmax);
    set('lo', lo); set('hi', hi); set('vlo', vlo);
    set('span', Math.max(1, vhi - vlo));
    set('amp', ampFrac * Math.min(w, h));
    if (mode > 3.5) {       // efectos nuevos: focos al azar y semilla
        set('seed', rnd(1, 80));
        set('c1x', rnd(0.15, 0.85) * w); set('c1y', rnd(0.15, 0.85) * h);
        set('c2x', rnd(0.1, 0.9) * w); set('c2y', rnd(0.1, 0.9) * h);
        set('c3x', rnd(0.1, 0.9) * w); set('c3y', rnd(0.1, 0.9) * h);
    }
    if (isC && ctx.raw) {
        const r = ctx.raw;
        set('tw', r.w); set('th', r.h);
        set('dgc', Math.hypot(r.wc, r.hc));
        set('mi', r.mi); set('mo', r.mo); set('yt', r.yt); set('yb', r.yb);
        set('seed', r.seed); set('nosub', r.sub ? 0.0 : 1.0);
        // sin sujeto: tres puntos al azar (el primero manda; los otros dos arrancan un poco después)
        const px = [rnd(0.15, 0.85) * r.wc], py = [rnd(0.15, 0.85) * r.hc];
        for (let i = 1; i < 3; i++) {
            let x, y, tries = 0;
            do { x = rnd(0.08, 0.92) * r.wc; y = rnd(0.08, 0.92) * r.hc; tries++; }
            while (tries < 20 && Math.hypot(x - px[0], y - py[0]) < 0.25 * Math.hypot(r.wc, r.hc));
            px.push(x); py.push(y);
        }
        set('c1x', px[0]); set('c1y', py[0]); set('c2x', px[1]); set('c2y', py[1]); set('c3x', px[2]); set('c3y', py[2]);
        if (!r.sub) {      // radio que hace falta para llegar a la esquina más lejana (así el avance usa todo el tiempo)
            const dgcv = Math.hypot(r.wc, r.hc);
            let need = 0;
            for (const [cx, cy] of [[0, 0], [r.wc, 0], [0, r.hc], [r.wc, r.hc]]) {
                need = Math.max(need, Math.min(Math.hypot(cx - px[0], cy - py[0]),
                    Math.hypot(cx - px[1], cy - py[1]) + 0.10 * dgcv, Math.hypot(cx - px[2], cy - py[2]) + 0.20 * dgcv));
            }
            set('mo', need);
        }
    }
    return {
        update(t) {
            set('t', t);
            if (mode > 1.5 && mode < 2.5) set('phase', t * TAU * 1.5);   // solo la ola lo usa
            if (lineA) {                       // trazo de arriba abajo (0..0.30) y se desvanece (0.50..0.70)
                const r = ctx.raw, sc = r.hc / 1080;
                const sw = smooth(0, 1, Math.min(1, t / 0.30));
                const sweepY = (r.yt - 10 * sc) + (r.yb - r.yt + 20 * sc) * sw;
                const ch = Math.min(h, sweepY);
                if (t <= 0 || ch < 1) { lineA.opacity = 0; return; }
                lineA.set_clip(0, 0, w, ch);
                lineA.opacity = Math.round(255 * (1 - smooth(0.50, 0.70, t)));
            }
        },
    };
}

const BUILDERS = {
    fade(ctx) {
        const a = ctx.layer();
        return { update(t) { a.opacity = Math.round(255 * t); } };
    },

    zoom(ctx) {
        const a = ctx.layer();
        a.set_pivot_point(0.5, 0.5);
        return {
            update(t) {
                a.opacity = Math.round(255 * t);
                const s = 1 + 0.12 * (1 - t);
                a.set_scale(s, s);
            },
        };
    },

    slide(ctx) {
        const a = ctx.layer();
        const { w, h, oldA } = ctx;
        const horiz = Math.abs(ctx.dir[0]) >= Math.abs(ctx.dir[1]);
        const sgn = (horiz ? ctx.dir[0] : ctx.dir[1]) >= 0 ? 1 : -1;
        return {
            update(t) {
                if (horiz) {
                    const x = Math.round(t * w * sgn);
                    oldA.set_position(x, 0);
                    a.set_position(x - w * sgn, 0);
                } else {
                    const y = Math.round(t * h * sgn);
                    oldA.set_position(0, y);
                    a.set_position(0, y - h * sgn);
                }
            },
        };
    },

    // Barrido por recorte (ejes); el diagonal usa el shader
    wipeClip(ctx) {
        const a = ctx.layer();
        const { w, h } = ctx;
        const horiz = Math.abs(ctx.dir[0]) >= Math.abs(ctx.dir[1]);
        const fwd = (horiz ? ctx.dir[0] : ctx.dir[1]) >= 0;
        return {
            update(t) {
                const len = (horiz ? w : h) * t;
                if (len < 1) { a.visible = false; return; }
                a.visible = true;
                if (horiz) a.set_clip(fwd ? 0 : w - len, 0, len, h);
                else a.set_clip(0, fwd ? 0 : h - len, w, len);
            },
        };
    },

    barn(ctx) {
        const a = ctx.layer();
        const { w, h } = ctx;
        const ax = Math.abs(ctx.dir[0]), ay = Math.abs(ctx.dir[1]);
        return {
            update(t) {
                let cw = w * t, ch = h * t;
                if (Math.abs(ax - ay) >= 0.01) { if (ax > ay) ch = h; else cw = w; }
                if (cw < 1 || ch < 1) { a.visible = false; return; }
                a.visible = true;
                a.set_clip((w - cw) / 2, (h - ch) / 2, cw, ch);
            },
        };
    },

    blinds(ctx, slats = 10) {
        const { w, h } = ctx;
        const horiz = Math.abs(ctx.dir[0]) >= Math.abs(ctx.dir[1]);
        const fwd = (horiz ? ctx.dir[0] : ctx.dir[1]) >= 0;
        const sz = (horiz ? w : h) / slats;
        const layers = [];
        for (let i = 0; i < slats; i++) layers.push(ctx.layer());
        return {
            update(t) {
                const len = sz * t;
                layers.forEach((a, i) => {
                    if (len < 0.5) { a.visible = false; return; }
                    a.visible = true;
                    const p0 = i * sz + (fwd ? 0 : sz - len);
                    if (horiz) a.set_clip(p0, 0, len, h);
                    else a.set_clip(0, p0, w, len);
                });
            },
        };
    },

    dissolve(ctx) {
        const { w, h } = ctx;
        const ts = Math.max(64, Math.round(Math.max(w, h) / 16));
        const cols = Math.ceil(w / ts), rows = Math.ceil(h / ts);
        const tiles = [];
        for (let i = 0; i < cols * rows; i++) {
            const a = ctx.layer();
            const x = (i % cols) * ts, y = Math.floor(i / cols) * ts;
            a.set_clip(x, y, Math.min(ts, w - x), Math.min(ts, h - y));
            a.visible = false;
            tiles.push(a);
        }
        const order = tiles.map((_, i) => i);
        for (let i = order.length - 1; i > 0; i--) {          // Fisher-Yates
            const j = Math.floor(Math.random() * (i + 1));
            [order[i], order[j]] = [order[j], order[i]];
        }
        let shown = 0;
        return {
            update(t) {
                const target = Math.min(order.length, Math.round(t * order.length));
                for (; shown < target; shown++) tiles[order[shown]].visible = true;
            },
        };
    },

    wipe(ctx) {
        if (shaderAvailable() && Math.abs(Math.abs(ctx.dir[0]) - Math.abs(ctx.dir[1])) < 0.01)
            return shaderLayer(ctx, 2, 0.0);           // diagonal
        return BUILDERS.wipeClip(ctx);
    },
};

// Efectos de shader: nombre -> [mode, amplitud, builder de respaldo sin shader]
const SHADER_FX = {
    contour: [3, 0.0, 'wipeClip'], wave: [2, 0.07, 'wipeClip'], grow: [0, 0.0, 'barn'], outer: [1, 0.0, 'barn'],
    ink: [4, 0.0, 'wipeClip'], glass: [5, 0.0, 'wipeClip'], reveal: [6, 0.0, 'wipeClip'],
    portal: [7, 0.0, 'wipeClip'], pixels: [8, 0.0, 'wipeClip'],
};
for (const name of Object.keys(SHADER_FX)) {
    const [mode, amp, fallback] = SHADER_FX[name];
    BUILDERS[name] = (ctx) => {
        if (!shaderAvailable()) return BUILDERS[fallback](ctx);
        if (name === 'portal') ctx.origin = pointerIn(ctx.holder, ctx.w, ctx.h) || ctx.origin;   // el portal se abre bajo el cursor
        return shaderLayer(ctx, mode, amp);
    };
}

// ---------------------------------------------------------------------------
//  Una transición en curso (una capa por monitor)
// ---------------------------------------------------------------------------

class Transition {
    /* targets: [{anchor, w, h, key}]; oldMap: key -> contenido del fondo viejo. */
    constructor(targets, oldMap, startedAt, onDone) {
        this._startedAt = startedAt;      // instante (µs) en que cambió el fondo real
        this._onDone = onDone;
        this._timeline = null;
        this._holdId = 0;
        this._keepId = 0;
        this._needKeep = false;
        this._dead = false;
        this._fx = [];
        this._targets = [];
        for (const t of targets) {
            try {
                // Colocación: dentro de bottom_window_group (capa de escritorio), al fondo de todo. Muffin no
                // reordena ese grupo (solo reordena los hijos directos de window_group), y queda por debajo de los
                // desklets y de las ventanas, justo encima del fondo real: se comporta como un fondo normal.
                const bg = global.background_actor;
                let parent = global.bottom_window_group;
                let useBottom = !!parent;
                if (!parent) parent = bg.get_parent();
                const holder = new Clutter.Actor({ width: t.w, height: t.h, clip_to_allocation: true });
                const [px, py] = parent.get_transformed_position();
                holder.set_position(t.x - px, t.y - py);
                const oldA = new Clutter.Actor({ width: t.w, height: t.h, content: oldMap.get(t.key).image });
                holder.add_child(oldA);
                if (useBottom) parent.insert_child_at_index(holder, 0);
                else { parent.insert_child_above(holder, bg); this._needKeep = true; }
                log(`capa colocada en ${parent} (${useBottom ? 'bottom_window_group' : 'respaldo window_group'})`);
                const sid = 0;
                this._targets.push({ t: t, parent: parent, holder: holder, oldA: oldA, sid: sid, layers: [] });
            } catch (e) {
                logError(e, 'crear capa');
            }
        }
        if (this._targets.length === 0) this._dead = true;
        else if (this._needKeep) this._keepId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => { this.keepDown(); return this._dead ? GLib.SOURCE_REMOVE : GLib.SOURCE_CONTINUE; });
    }

    get ok() { return !this._dead; }

    /* Muffin reordena los hijos de window_group al cambiar las ventanas y puede subir la capa por encima
     * de ellas: se vuelve a dejar justo encima del fondo real. No hace nada si ya está bien colocada. */
    keepDown() {
        if (this._dead) return;
        const bg = global.background_actor;
        for (const tg of this._targets) {
            try {
                const kids = tg.parent.get_children();
                if (kids.indexOf(tg.holder) !== kids.indexOf(bg) + 1)
                    tg.parent.set_child_above_sibling(tg.holder, bg);
            } catch (e) { /* ya destruida */ }
        }
    }

    dumpOrder() {
        try {
            const kids = (global.bottom_window_group || global.window_group).get_children();
            const hs = this._targets.map(tg => tg.holder);
            kids.forEach((k, i) => fileLog(`ORDER ${i}: ${hs.indexOf(k) >= 0 ? '*** HOLDER ***' : String(k)}${k.visible === false ? ' (oculto)' : ''}`));
        } catch (e) { fileLog('dumpOrder falló: ' + e); }
    }

    run(newMap, cfg, rawMap) {
        if (this._dead) return;
        if (DEBUG) GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => { if (!this._dead) this.dumpOrder(); return GLib.SOURCE_REMOVE; });
        const ease = makeEasing(cfg.easing);
        let name = cfg.effect;
        const haveContour = !!rawMap && shaderAvailable();
        let pool = shaderAvailable() ? EFFECTS : EFFECTS.filter(e => !SHADER_EFFECTS.includes(e));
        if (!haveContour) pool = pool.filter(e => e !== 'contour');     // sin archivo auxiliar no hay contorno
        if (name === 'random' || EFFECTS.indexOf(name) < 0) name = pool[Math.floor(Math.random() * pool.length)];
        else if (name === 'contour' && !haveContour) name = 'wipe';
        const dkeys = Object.keys(DIRS);
        const dname = DIRS[cfg.direction] ? cfg.direction : dkeys[Math.floor(Math.random() * dkeys.length)];
        const dir = DIRS[dname];

        for (const tg of this._targets) {
            const { w, h } = tg.t;
            const newImage = newMap.get(tg.t.key).image;
            let origin = [w / 2, h / 2];
            if (cfg.origin === 'random') {
                origin = [rnd(0.15, 0.85) * w, rnd(0.15, 0.85) * h];
            } else if (cfg.origin === 'cursor') {
                origin = pointerIn(tg.holder, w, h) || origin;
            }
            const ctx = {
                holder: tg.holder, oldA: tg.oldA, w: w, h: h, dir: dir, origin: origin,
                raw: (rawMap && rawMap.has(tg.t.key)) ? rawMap.get(tg.t.key) : null,
                line: (img) => {
                    const a = new Clutter.Actor({ width: w, height: h, content: img });
                    tg.holder.add_child(a);
                    tg.layers.push(a);
                    return a;
                },
                layer: (raw) => {
                    const img = (raw && rawMap && rawMap.has(tg.t.key)) ? rawMap.get(tg.t.key).image : newImage;
                    // la textura del contorno mide justo w x h: se pinta 1:1 y el actor queda DENTRO de la pantalla
                    // (si un actor sobresale, Clutter desplaza el contenido de su buffer y el shader lee basura)
                    const a = new Clutter.Actor({ width: w, height: h, content: img });
                    tg.holder.add_child(a);
                    tg.layers.push(a);
                    return a;
                },
            };
            tg.ctx = ctx;
            tg.name = name;
            tg.fx = this._build(tg, name);
        }

        // El contorno tiene tres fases suavizadas dentro del shader: tiempo lineal y algo más largo que el resto
        const isContour = (name === 'contour');
        const ms = isContour ? Math.round(cfg.ms * 1.6) : cfg.ms;
        this._update(0);
        this._timeline = new Clutter.Timeline({ duration: ms });
        this._timeline.connect('new-frame', (_tl, msecs) => {
            if (!this._dead) this._update(isContour ? Math.min(1, msecs / ms) : ease(Math.min(1, msecs / ms)));
        });
        this._timeline.connect('completed', () => this._finish());
        this._timeline.start();
    }

    _clearLayers(tg) {
        for (const a of tg.layers) { try { a.destroy(); } catch (e) { /* nada */ } }
        tg.layers = [];
    }

    _build(tg, name) {
        try {
            return BUILDERS[name](tg.ctx);
        } catch (e) {
            logError(e, `efecto ${name}; se usa fundido`);
            this._clearLayers(tg);
            return BUILDERS.fade(tg.ctx);
        }
    }

    _update(t) {
        for (const tg of this._targets) {
            try {
                tg.fx.update(t);
            } catch (e) {                       // p. ej. el shader no acepta un uniform: se degrada a fundido
                logError(e, `actualizar ${tg.name}; se usa fundido`);
                this._clearLayers(tg);
                tg.name = 'fade';
                tg.fx = BUILDERS.fade(tg.ctx);
                try { tg.fx.update(t); } catch (e2) { /* nada */ }
            }
        }
    }

    _finish() {
        if (this._dead || this._holdId) return;
        // Estado final limpio: una sola capa con el fondo nuevo, sin shaders ni recortes
        for (const tg of this._targets) {
            this._clearLayers(tg);
            try { tg.oldA.set_position(0, 0); tg.ctx.layer(); } catch (e) { /* nada */ }
        }
        const elapsed = (GLib.get_monotonic_time() - this._startedAt) / 1000;
        const hold = Math.max(50, NATIVE_FADE_MS - elapsed);
        this._holdId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.round(hold), () => {
            this._holdId = 0;
            this.destroy();
            if (this._onDone) this._onDone(this);
            return GLib.SOURCE_REMOVE;
        });
    }

    destroy() {
        this._dead = true;
        if (this._keepId) { GLib.source_remove(this._keepId); this._keepId = 0; }
        if (this._holdId) { GLib.source_remove(this._holdId); this._holdId = 0; }
        if (this._timeline) {
            try { this._timeline.stop(); } catch (e) { /* nada */ }
            this._timeline = null;
        }
        for (const tg of this._targets) {
            try { tg.holder.destroy(); } catch (e) { /* nada */ }
        }
        this._targets = [];
    }
}

// ---------------------------------------------------------------------------
//  Controlador
// ---------------------------------------------------------------------------

class WallpaperTransitions {
    enable() {
        this._settings = new Gio.Settings({ schema_id: BG_SCHEMA });
        this._uri = this._settings.get_string('picture-uri');
        this._cache = null;               // { uri, map: key -> contenido }
        this._active = null;
        this._gen = 0;
        this._cancels = new Set();
        this._sig = this._settings.connect('changed::picture-uri', () => this._onChanged());
        // El fondo actual se prepara en cuanto el escritorio está listo (no bloquea el arranque)
        this._startId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
            this._startId = 0;
            this._preload(this._uri);
            return GLib.SOURCE_REMOVE;
        });
    }

    disable() {
        if (this._startId) { GLib.source_remove(this._startId); this._startId = 0; }
        if (this._sig) { this._settings.disconnect(this._sig); this._sig = 0; }
        this._gen++;
        for (const c of this._cancels) c();
        this._cancels.clear();
        if (this._active) { this._active.destroy(); this._active = null; }
        this._cache = null;
        this._settings = null;
    }

    _load(path, targets, map, gen, onAll) {
        const keys = new Map();
        for (const t of targets) keys.set(t.key, t);
        let pending = keys.size, failed = false;
        if (pending === 0) { onAll(true); return; }
        for (const [key, t] of keys) {
            let cancel = null;
            cancel = loadCover(path, t.tw, t.th, (content) => {
                this._cancels.delete(cancel);
                if (gen !== this._gen) return;
                if (content) map.set(key, content); else failed = true;
                if (--pending === 0) onAll(!failed);
            });
            this._cancels.add(cancel);
        }
    }

    _preload(uri) {
        const path = uriToPath(uri);
        const targets = findTargets();
        if (!path || targets.length === 0) { this._cache = null; return; }
        const cache = { uri: uri, map: new Map() };
        this._cache = cache;
        this._load(path, targets, cache.map, this._gen, () => {});
    }

    _onChanged() {
        const prev = this._uri;
        const uri = this._settings.get_string('picture-uri');
        if (uri === prev) return;
        this._uri = uri;
        const startedAt = GLib.get_monotonic_time();
        this._gen++;
        const gen = this._gen;
        for (const c of this._cancels) c();
        this._cancels.clear();
        if (this._active) { this._active.destroy(); this._active = null; }

        const oldCache = (this._cache && this._cache.uri === prev) ? this._cache : null;
        this._cache = null;
        const cfg = loadConfig();
        const path = uriToPath(uri);
        const targets = findTargets();
        const usable = cfg.enabled && oldCache && path && targets.length > 0
            && this._settings.get_string('picture-options') === 'zoom'
            && targets.every(t => oldCache.map.has(t.key));
        if (!usable) {                    // cambio normal de Cinnamon; se deja listo el nuevo fondo
            this._preload(uri);
            return;
        }

        // Capa con el fondo viejo ya puesta: tapa el fundido nativo mientras se decodifica el nuevo
        const tr = new Transition(targets, oldCache.map, startedAt, (t) => { if (this._active === t) this._active = null; });
        if (!tr.ok) { this._preload(uri); return; }
        this._active = tr;

        const cache = { uri: uri, map: new Map() };
        this._cache = cache;
        this._load(path, targets, cache.map, gen, (ok) => {
            if (gen !== this._gen) return;
            if (!ok) {                    // no se pudo leer la imagen: se quita la capa y queda el cambio nativo
                tr.destroy();
                this._active = null;
                this._cache = null;
                return;
            }
            const sc = (cfg.effect === 'contour' || cfg.effect === 'random') ? sidecarFor(path) : null;
            if (!sc) { tr.run(cache.map, cfg, null); return; }
            const cancelRaw = loadRaw(sc, (raw) => {          // imagen + campo de distancia de 16 bits
                this._cancels.delete(cancelRaw);
                if (gen !== this._gen) return;
                let rawMap = null;
                if (raw) {
                    rawMap = new Map();
                    const scale = (typeof global.ui_scale === 'number' && global.ui_scale > 0) ? global.ui_scale : 1;
                    for (const t of targets) {
                        // El shader necesita que la textura se pinte 1:1 (si Clutter la escala, los datos del alfa se
                        // corrompen): el auxiliar debe medir EXACTAMENTE lo que el monitor y sin HiDPI.
                        if (scale !== 1 || raw.wc !== Math.round(t.w) || raw.hc !== Math.round(t.h)) {
                            log(`contorno: auxiliar ${raw.wc}x${raw.hc} no encaja con el monitor ${t.w}x${t.h} (escala ${scale}); se usa Barrido`);
                            rawMap = null;
                            break;
                        }
                        rawMap.set(t.key, raw);
                    }
                }
                if (rawMap) log(`contorno: usando auxiliar ${raw.wc}x${raw.hc} (${raw.sub ? 'con sujeto' : 'sin sujeto: colonias al azar'})`);
                tr.run(cache.map, cfg, rawMap);
            });
            this._cancels.add(cancelRaw);
        });
    }
}

// ---------------------------------------------------------------------------
//  Puntos de entrada de Cinnamon
// ---------------------------------------------------------------------------

let _ext = null;

function init(_meta) {}

function enable() {
    if (!Cogl || !Cogl.PixelFormat) {
        log('Falta Cogl: la extensión no se activa');
        global.logError('[wallpaper-transitions] Falta Cogl');
        return;
    }
    try {
        log('enable() v2.0');
        _ext = new WallpaperTransitions();
        _ext.enable();
        if (DEBUG) GLib.timeout_add(GLib.PRIORITY_DEFAULT, 3000, () => { dumpTree(); return GLib.SOURCE_REMOVE; });
    } catch (e) {
        logError(e, 'enable');
        _ext = null;
    }
}

function disable() {
    if (_ext) {
        try { _ext.disable(); } catch (e) { logError(e, 'disable'); }
        _ext = null;
    }
}
