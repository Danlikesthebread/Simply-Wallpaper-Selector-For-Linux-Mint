"""Preferencias del selector: esquema, valores por defecto y persistencia.

Todas las preferencias se declaran una sola vez en SCHEMA; el panel de
configuración (prefs_panel.py) construye sus controles a partir de él.
Se guardan en ~/.config/wallpaper-switcher/preferences.json
"""
import json
import os

from PyQt6.QtCore import QObject, QCoreApplication, QTimer, pyqtSignal
from PyQt6.QtGui import QColor

from config import CONFIG_DIR, init_dirs

PREFS_FILE = os.path.join(CONFIG_DIR, "preferences.json")
LEGACY_UI_FILE = os.path.expanduser("~/.cache/wallpaper_selector/ui_settings.json")

# (clave, nombre, color)
ACCENTS = [
    ("white", "Blanco", "#FFFFFF"),
    ("violet", "Violeta", "#A78BFA"),
    ("blue", "Azul", "#60A5FA"),
    ("cyan", "Cian", "#2DD4EF"),
    ("green", "Verde", "#4ADE80"),
    ("amber", "Ámbar", "#FBBF24"),
    ("rose", "Rosa", "#FB7185"),
]
_ACCENT_MAP = {k: c for k, _, c in ACCENTS}


class Pref:
    """Definición de una preferencia."""

    def __init__(self, key, default, kind, label, desc="", *, lo=0, hi=100, step=1,
                 unit="", choices=None, requires=(), special=None):
        self.key = key
        self.default = default
        self.kind = kind            # "bool" | "int" | "choice" | "accent"
        self.label = label
        self.desc = desc
        self.lo = lo
        self.hi = hi
        self.step = step
        self.unit = unit
        self.choices = choices or []   # [(valor, etiqueta)]
        self.requires = (requires,) if isinstance(requires, str) else tuple(requires)
        self.special = special or {}   # {valor: texto} p. ej. {0: "Auto"}


SCHEMA = {}


def _add(*args, **kwargs):
    p = Pref(*args, **kwargs)
    SCHEMA[p.key] = p


_ANIM = "anim_enabled"

# ---------------------------------------------------------------- Animaciones
_add("anim_enabled", True, "bool", "Animaciones",
     "Interruptor general. Desactívalo para una interfaz instantánea.")
_add("anim_speed", 100, "int", "Velocidad global",
     "Multiplica la velocidad de todas las animaciones.",
     lo=50, hi=200, step=5, unit="%", requires=_ANIM)
_add("intro_style", "cascade", "choice", "Animación de entrada",
     "Cascada: suben desde el centro. Barrido: entran de izquierda a derecha. Fundido: solo opacidad. "
     "Zoom: crecen desde el centro. Caída: bajan desde arriba.",
     choices=[("cascade", "Cascada"), ("sweep", "Barrido"), ("fade", "Fundido"), ("zoom", "Zoom"),
              ("drop", "Caída"), ("none", "Ninguna")],
     requires=_ANIM)
_add("intro_stagger", 32, "int", "Retardo entre tarjetas", "Separación temporal entre una tarjeta y la siguiente.",
     lo=5, hi=120, step=1, unit=" ms", requires=_ANIM)
_add("intro_card_ms", 420, "int", "Duración de cada tarjeta", "",
     lo=100, hi=1200, step=10, unit=" ms", requires=_ANIM)
_add("intro_rise", 22, "int", "Desplazamiento inicial", "Cuánto suben las tarjetas al aparecer.",
     lo=0, hi=80, step=1, unit=" px", requires=_ANIM)
_add("intro_scale", 92, "int", "Escala inicial", "Las tarjetas crecen desde este tamaño.",
     lo=70, hi=100, step=1, unit="%", requires=_ANIM)
_add("anim_filter", True, "bool", "Animar al cambiar de filtro",
     "Las tarjetas vuelven a entrar al filtrar por color o favoritos.", requires=_ANIM)
_add("anim_outro", True, "bool", "Animación de salida", "Fundido al cerrar el selector.", requires=_ANIM)
_add("hover_anim", True, "bool", "Transiciones al pasar el ratón",
     "Resaltado suave en tarjetas y botones.", requires=_ANIM)
_add("nav_duration", 260, "int", "Duración al navegar", "Tiempo que tarda el carrusel en desplazarse.",
     lo=60, hi=700, step=10, unit=" ms", requires=_ANIM)
_add("nav_easing", "out_cubic", "choice", "Curva de movimiento", "",
     choices=[("out_cubic", "Suave"), ("out_quart", "Muy suave"), ("out_expo", "Rápida"),
              ("in_out_cubic", "Simétrica"), ("out_back", "Con rebote"), ("linear", "Lineal")],
     requires=_ANIM)

# ------------------------------------------------------------------- Carrusel
_add("grow_mode", False, "bool", "Modo de la tarjeta activa",
     "Expandir ensancha la tarjeta; Crecer la agranda sin empujar a las demás.",
     choices=[(False, "Expandir"), (True, "Crecer")])
_add("grow_scale", 130, "int", "Tamaño en modo Crecer", "", lo=110, hi=170, step=1, unit="%")
_add("grow_spacing", 100, "int", "Separar vecinas al crecer",
     "Aparta las tarjetas de al lado para que la activa no las tape (0 = se solapan).",
     lo=0, hi=100, step=5, unit="%")
_add("expanded_width", 430, "int", "Ancho en modo Expandir", "", lo=260, hi=640, step=5, unit=" px")
_add("card_width", 200, "int", "Ancho de tarjeta", "", lo=140, hi=320, step=2, unit=" px")
_add("card_height", 240, "int", "Alto de tarjeta", "", lo=160, hi=360, step=2, unit=" px")
_add("card_slant", 60, "int", "Inclinación", "0 = rectángulo.", lo=0, hi=100, step=1, unit=" px")
_add("card_gap", 0, "int", "Separación", "", lo=0, hi=40, step=1, unit=" px")
_add("dim_inactive", 43, "int", "Oscurecer inactivas", "Opacidad del velo sobre las tarjetas no activas.",
     lo=0, hi=90, step=1, unit="%")
_add("border_active", 2, "int", "Borde de la activa", "", lo=0, hi=6, step=1, unit=" px")
_add("border_idle_opacity", 12, "int", "Borde de las inactivas", "", lo=0, hi=60, step=1, unit="%")
_add("accent", "white", "accent", "Color de acento", "Borde activo, selección y controles.")
_add("show_filename", False, "bool", "Mostrar nombre del archivo", "En la parte inferior de la tarjeta activa.")
_add("show_band", True, "bool", "Franja de fondo", "Banda oscura detrás del carrusel.")
_add("band_opacity", 30, "int", "Opacidad de la franja", "", lo=0, hi=100, step=1, unit="%", requires="show_band")

# -------------------------------------------------------------------- Ventana
_add("hud_width_pct", 100, "int", "Ancho del carrusel", "Porcentaje del ancho de pantalla.",
     lo=50, hi=100, step=1, unit="%")
_add("window_valign", "center", "choice", "Posición vertical", "",
     choices=[("center", "Centro"), ("top", "Arriba"), ("bottom", "Abajo")])
_add("monitor", "cursor", "choice", "Pantalla", "Dónde se abre con varios monitores.",
     choices=[("cursor", "Donde está el cursor"), ("primary", "Principal")])
_add("bar_position", "top", "choice", "Posición de la barra", "",
     choices=[("top", "Arriba"), ("bottom", "Abajo")])
_add("bar_shape", "rect", "choice", "Forma de la barra", "",
     choices=[("rect", "Rectangular"), ("round", "Redondeada"), ("pill", "Píldora")])
_add("bar_opacity", 62, "int", "Opacidad de la barra", "", lo=0, hi=100, step=1, unit="%")
_add("show_tooltips", True, "bool", "Mostrar descripciones", "Texto emergente en los botones de la barra.")

# --------------------------------------------------------------- Comportamiento
_add("infinite", True, "bool", "Carrusel infinito", "Al llegar al final vuelve al principio.")
_add("start_on_current", True, "bool", "Empezar en el fondo actual", "Al abrir, selecciona el fondo que tienes puesto.")
_add("close_on_apply", False, "bool", "Cerrar al aplicar", "Se cierra tras confirmar un fondo.")
_add("close_on_blur", False, "bool", "Cerrar al perder el foco", "Se cierra al hacer clic fuera de la ventana.")
_add("apply_on_navigate", False, "bool", "Aplicar al navegar", "Vista previa en vivo: aplica el fondo tras detenerte en él.")
_add("apply_delay", 800, "int", "Espera antes de aplicar", "", lo=200, hi=3000, step=50, unit=" ms",
     requires="apply_on_navigate")
_add("single_click_apply", False, "bool", "Aplicar con un clic", "Un clic sobre la tarjeta activa confirma el fondo.")
_add("wallpaper_fade", True, "bool", "Transición animada del fondo",
     "Efectos estilo Hyprland/swww al cambiar de fondo. Los dibuja la extensión de Cinnamon "
     "'Wallpaper Transitions' (./instalar-extension.sh): sin ella el fondo cambia con el fundido normal. "
     "Solo con el ajuste de imagen 'zoom'.")
_add("fade_effect", "wipe", "choice", "Efecto", "Aleatorio elige uno distinto cada vez.",
     choices=[("contour", "Contorno del sujeto"), ("wipe", "Barrido"), ("wave", "Ola"), ("grow", "Círculo (crece)"), ("outer", "Círculo (se cierra)"),
              ("fade", "Fundido"), ("slide", "Deslizar"), ("zoom", "Zoom"), ("blinds", "Persianas"),
              ("barn", "Puertas"), ("dissolve", "Mosaico"), ("ink", "Tinta"), ("glass", "Grieta de cristal"),
              ("reveal", "Revelado por luz"), ("portal", "Portal (cursor)"), ("pixels", "Píxeles a color"), ("random", "Aleatorio")],
     requires="wallpaper_fade")
_add("fade_direction", "random", "choice", "Dirección", "Para barrido, ola, deslizar, persianas y puertas.",
     choices=[("random", "Aleatoria"), ("right", "Derecha"), ("left", "Izquierda"), ("down", "Abajo"),
              ("up", "Arriba"), ("diag", "Diagonal")],
     requires="wallpaper_fade")
_add("fade_origin", "center", "choice", "Origen", "Centro de los efectos circulares.",
     choices=[("center", "Centro"), ("cursor", "Cursor"), ("random", "Aleatorio")],
     requires="wallpaper_fade")
_add("fade_ms", 1200, "int", "Duración de la transición", "", lo=300, hi=3000, step=50, unit=" ms",
     requires="wallpaper_fade")
_add("wheel_nav", True, "bool", "Navegar con la rueda", "")
_add("wheel_invert", False, "bool", "Invertir la rueda", "", requires="wheel_nav")
_add("nav_repeat_ms", 300, "int", "Repetición al mantener tecla", "Menos tiempo = navegación más rápida.",
     lo=60, hi=600, step=10, unit=" ms")

# --------------------------------------------------------------------- Colores
_add("color_filters", True, "bool", "Filtros por color", "Analiza los fondos y muestra los colores en la barra.")
_add("pywal_enabled", True, "bool", "Generar paleta con pywal", "Ejecuta wal al aplicar un fondo.")
_add("pywal_backend", "wal", "choice", "Motor de pywal", "Debe estar instalado en el sistema.",
     choices=[("wal", "wal"), ("colorz", "colorz"), ("colorthief", "colorthief"),
              ("haishoku", "haishoku"), ("schemer2", "schemer2")], requires="pywal_enabled")
_add("pywal_light", False, "bool", "Tema claro", "Equivale a wal -l.", requires="pywal_enabled")
_add("palette_correct", True, "bool", "Corregir saturación", "Suaviza los colores de la paleta.",
     requires="pywal_enabled")
_add("sat_scale", 70, "int", "Saturación de la paleta", "", lo=30, hi=100, step=1, unit="%",
     requires=("pywal_enabled", "palette_correct"))
_add("sat_cap", 85, "int", "Límite de saturación", "Respecto a la saturación real de la imagen.",
     lo=50, hi=100, step=1, unit="%", requires=("pywal_enabled", "palette_correct"))
_add("update_starship", True, "bool", "Actualizar Starship", "Escribe la paleta en starship.toml.",
     requires=("pywal_enabled", "palette_correct"))
_add("update_kitty", True, "bool", "Actualizar Kitty", "Reescribe colors-kitty.conf.",
     requires=("pywal_enabled", "palette_correct"))
_add("sync_gtk", True, "bool", "Sincronizar tema GTK", "Ejecuta ~/.local/bin/sync-andromeda.sh si existe.")

# -------------------------------------------------------- Archivos y rendimiento
_add("recursive", False, "bool", "Incluir subcarpetas", "")
_add("sort_order", "name", "choice", "Orden", "",
     choices=[("name", "Nombre"), ("newest", "Más recientes"), ("oldest", "Más antiguos"), ("random", "Aleatorio")])
_add("include_videos", True, "bool", "Incluir fondos animados",
     "Videos (mp4, webm, mkv, mov…) y GIF. Necesitan mpv (./instalar-animados.sh) y una sesión X11.")
_add("stop_live_on_image", True, "bool", "Detener animado al elegir imagen",
     "Al aplicar una imagen fija se apaga el video de fondo.", requires="include_videos")
_add("video_screens", "primary", "choice", "Monitores con video",
     "En cuáles se reproduce el fondo animado.",
     choices=[("all", "Todos"), ("primary", "Solo el principal")], requires="include_videos")
_add("video_mute", True, "bool", "Sin sonido", "Desactívalo si quieres oír el audio del video.",
     requires="include_videos")
_add("video_hwdec", True, "bool", "Aceleración por hardware",
     "Usa la GPU para decodificar (menos CPU). Desactívala si ves cortes o colores raros.",
     requires="include_videos")
_add("video_compat", False, "bool", "Modo compatibilidad",
     "Si ves una ventana suelta o pantalla negra: dibuja por software (--vo=x11). Usa más CPU.",
     requires="include_videos")
_add("optimize_videos", True, "bool", "Optimizar videos pesados",
     "Crea en segundo plano una copia H.264 adaptada a tu monitor y cambia a ella (incluye los GIF). "
     "Mientras tanto reproduce el original en modo ligero. Necesita ffmpeg con libx264.",
     requires="include_videos")
_add("video_max_fps", "24", "choice", "Límite de fotogramas por segundo",
     "Los videos con más fps se reducen a este valor al optimizar. 24 = mínimo consumo.",
     choices=[("24", "24 fps"), ("30", "30 fps"), ("60", "60 fps")], requires=("include_videos", "optimize_videos"))
_add("optimize_quality", "light", "choice", "Calidad de la copia optimizada",
     "Ligera = archivo y decodificación más pequeños. Alta = más fiel al original.",
     choices=[("light", "Ligera"), ("balanced", "Equilibrada"), ("high", "Alta")],
     requires=("include_videos", "optimize_videos"))
_add("video_pause_covered", True, "bool", "Pausar si una ventana tapa el escritorio",
     "Detiene el video del monitor cuyo escritorio queda cubierto por una ventana maximizada o a "
     "pantalla completa, y lo reanuda al descubrirlo. Ahorra CPU/GPU y batería.",
     requires="include_videos")
_add("video_pause_battery", False, "bool", "Pausar con batería",
     "Congela el fondo animado mientras el portátil no está enchufado.", requires="include_videos")
_add("video_cache_mb", 1000, "int", "Caché de videos optimizados",
     "Espacio máximo para las copias optimizadas (se borran las menos usadas).",
     lo=200, hi=20000, step=100, unit=" MB", requires="include_videos")
_add("video_layer", "desktop", "choice", "Capa del video",
     "Si el video tapa los iconos del escritorio, prueba 'Debajo de ventanas'.",
     choices=[("desktop", "Escritorio (tras los iconos)"), ("below", "Debajo de ventanas")],
     requires="include_videos")
_add("thumb_quality", "1", "choice", "Calidad de miniaturas", "Más calidad = más memoria y disco.",
     choices=[("1", "Normal"), ("1.5", "Alta"), ("2", "Máxima")])
_add("preload", 6, "int", "Tarjetas precargadas", "A cada lado de la visible. Menos = menos memoria.",
     lo=2, hi=40, step=1)
_add("mem_cache", 100, "int", "Miniaturas en memoria", "", lo=60, hi=1500, step=10)
_add("disk_cache_mb", 300, "int", "Caché en disco", "", lo=100, hi=4000, step=50, unit=" MB")
_add("threads", 0, "int", "Hilos de miniaturas", "", lo=0, hi=8, step=1, special={0: "Auto"})


class Preferences(QObject):
    changed = pyqtSignal(object)   # frozenset con las claves modificadas

    def __init__(self):
        super().__init__()
        self._v = {k: p.default for k, p in SCHEMA.items()}
        self._dirty = False
        self._load()

    # ---- acceso ----------------------------------------------------------
    def get(self, key):
        return self._v[key]

    __getitem__ = get

    def ms(self, base):
        """Duración en ms ajustada a la velocidad global (0 si no hay animaciones)."""
        if not self._v["anim_enabled"]:
            return 0
        return max(0, int(round(base * 100.0 / max(25, self._v["anim_speed"]))))

    def accent(self):
        return QColor(_ACCENT_MAP.get(self._v["accent"], "#FFFFFF"))

    def thumb_box(self):
        """Tamaño (px) de las miniaturas en disco según el tamaño de tarjeta."""
        h = self._v["card_height"]
        q = float(self._v["thumb_quality"])
        w = max(int(h * 1.8), self._v["expanded_width"], self._v["card_width"])
        return int(w * q), int(h * q)

    # ---- escritura ---------------------------------------------------------
    @staticmethod
    def _coerce(p, value):
        if p.kind == "bool":
            return bool(value)
        if p.kind == "int":
            try:
                value = int(round(float(value)))
            except (TypeError, ValueError):
                return p.default
            return max(p.lo, min(p.hi, value))
        valid = [c[0] for c in p.choices] if p.kind == "choice" else [a[0] for a in ACCENTS]
        return value if value in valid else p.default

    def set(self, key, value):
        return self.set_many({key: value})

    def set_many(self, mapping):
        changed = set()
        for key, value in mapping.items():
            p = SCHEMA.get(key)
            if p is None:
                continue
            value = self._coerce(p, value)
            if self._v[key] != value:
                self._v[key] = value
                changed.add(key)
        if changed:
            self._schedule_save()
            self.changed.emit(frozenset(changed))
        return bool(changed)

    def reset(self, keys=None):
        keys = keys or list(SCHEMA)
        return self.set_many({k: SCHEMA[k].default for k in keys})

    # ---- persistencia ------------------------------------------------------
    def _load(self):
        data = None
        try:
            with open(PREFS_FILE) as f:
                data = json.load(f)
        except Exception:
            data = None
        if not isinstance(data, dict):
            data = {}
            # Migración: el modo de animación se guardaba en otro archivo
            try:
                with open(LEGACY_UI_FILE) as f:
                    legacy = json.load(f)
                if isinstance(legacy, dict) and "grow_mode" in legacy:
                    data["grow_mode"] = legacy["grow_mode"]
            except Exception:
                pass
        for k, v in data.items():
            p = SCHEMA.get(k)
            if p is not None:
                self._v[k] = self._coerce(p, v)

    def _schedule_save(self):
        if self._dirty:
            return
        self._dirty = True
        if QCoreApplication.instance() is not None:
            QTimer.singleShot(400, self.flush)
        else:
            self.flush()

    def flush(self):
        if not self._dirty:
            return
        self._dirty = False
        try:
            init_dirs()
            tmp = PREFS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._v, f, indent=2)
            os.replace(tmp, PREFS_FILE)
        except Exception as e:
            print(f"Error guardando preferencias: {e}")


PREFS = Preferences()