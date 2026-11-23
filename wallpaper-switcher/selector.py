import os
import threading
import math
from collections import OrderedDict

from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QScrollArea, QSizePolicy, QFileDialog)
from PyQt6.QtCore import (Qt, QRect, QPointF, QPoint, QVariantAnimation, QEasingCurve,
                          QFileSystemWatcher, QThreadPool, QTimer,
                          QElapsedTimer, QEvent, QAbstractAnimation)
from PyQt6.QtGui import (QPixmap, QKeyEvent, QCursor, QGuiApplication)

# Importaciones del proyecto
from wallpaper import (get_wallpapers, get_current_wallpaper, list_subdirs)
import livewall
from config import load_favorites, save_favorites, save_config
from prefs import PREFS
from prefs_panel import PrefsPanel
from widgets import AnimDriver, ColorFilterBar, HUDPanel, StripContainer, ThumbnailItem
from workers import ApplyWorker, ColorGroupingWorker, LoaderSignals, ThumbnailWorker, WallpaperSetWorker
from common import EASINGS, FAMILY_ORDER, FAVORITES_KEY, _clamp01, _ease_out

#  Ventana principal
# ---------------------------------------------------------------------------

def configure_metrics():
    """Vuelca las preferencias de tamaño/estilo a los atributos de clase que
    usan las tarjetas. Debe llamarse antes de crear/reconstruir el carrusel."""
    T = ThumbnailItem
    T.BASE_W = PREFS.get("card_width")
    T.BASE_H = PREFS.get("card_height")
    T.SLANT = PREFS.get("card_slant")
    T.GAP = PREFS.get("card_gap")
    T.EXPANDED_W = max(PREFS.get("expanded_width"), T.BASE_W + 20)
    T.GROW_SCALE = PREFS.get("grow_scale") / 100.0
    T.GROW_PUSH = PREFS.get("grow_spacing") / 100.0
    T.grow_mode = bool(PREFS.get("grow_mode"))
    T.INTRO_RISE = PREFS.get("intro_rise")
    T.INTRO_SCALE = PREFS.get("intro_scale") / 100.0
    T.INTRO_STYLE = PREFS.get("intro_style")

    overflow = int(round(T.BASE_H * (T.GROW_SCALE - 1.0) / 2))
    T.PAD_Y = max(overflow, int(max(T.INTRO_RISE, 10) * 1.5)) + 4

    # Estilo
    T.DIM = PREFS.get("dim_inactive") / 100.0
    T.BORDER_W = float(PREFS.get("border_active"))
    T.IDLE_BORDER_A = int(255 * PREFS.get("border_idle_opacity") / 100.0)
    T.ACCENT = PREFS.accent()
    T.SHOW_NAME = bool(PREFS.get("show_filename"))
    T.SHOW_BAND = bool(PREFS.get("show_band"))
    T.BAND_A = int(255 * PREFS.get("band_opacity") / 100.0)

    HUDPanel.HEIGHT = T.BASE_H + 2 * T.PAD_Y
    SelectorWindow.GROW_OVERFLOW = overflow
    SelectorWindow.STEP = T.BASE_W - T.SLANT + T.GAP


# ---------------------------------------------------------------------------
#  Workers
# ---------------------------------------------------------------------------


# Qué hay que rehacer cuando cambia cada preferencia
INTRO_KEYS = frozenset({"intro_style", "intro_stagger", "intro_card_ms", "intro_rise", "intro_scale"})
GEOMETRY_KEYS = frozenset({"card_width", "card_height", "card_slant", "card_gap",
                           "expanded_width", "grow_scale", "grow_spacing", "intro_rise"})
WINDOW_KEYS = frozenset({"hud_width_pct", "bar_position", "window_valign", "monitor"})
PAINT_KEYS = frozenset({"dim_inactive", "border_active", "border_idle_opacity", "accent",
                        "show_filename", "show_band", "band_opacity", "bar_opacity", "bar_shape", "intro_scale"})
THUMB_KEYS = frozenset({"card_height", "card_width", "expanded_width", "thumb_quality"})
RELOAD_KEYS = frozenset({"recursive", "sort_order", "infinite", "include_videos"})

TOOLTIP_QSS = ("QToolTip { color: #f1f1f4; background-color: #17171c; border: 1px solid rgba(255,255,255,45);"
               " border-radius: 6px; padding: 4px 9px; }")


class SelectorWindow(QWidget):
    WINDOW_MARGIN = 20
    # Separación entre la barra y la parte más alta que alcanza una tarjeta
    # (en modo crecer la tarjeta activa sobresale por encima de la franja).
    BAR_GAP = 6
    PANEL_GAP = 14
    GROW_OVERFLOW = 36     # los fija configure_metrics()
    STEP = 140

    def __init__(self, wallpaper_dir):
        super().__init__()
        self.wallpaper_dir = wallpaper_dir
        configure_metrics()

        self.anim_driver = AnimDriver(self)
        self._screen_obj = self._pick_screen()
        self._closing = False
        self._modal_open = False
        self.panel = None
        self._prefs_open = False
        self._y_shift = 0
        self._base_y = 0
        self._bar_home = QPoint(0, 0)
        self._bar_dy = 0
        self._wheel_acc = 0
        self._shift_anim = None
        self._outro_anim = None
        self._outro_items = []
        self._outro_total = 1
        self._outro_card_ms = 1
        self._flash_anim = None
        self._flash_slot = None
        self._pop_anim = None
        self._pop_slot = None
        self._confirm_clock = QElapsedTimer()
        self._shown_clock = QElapsedTimer()

        self.all_wallpapers = self._scan_wallpapers()
        self.wallpapers = list(self.all_wallpapers)
        self.active_filter = None
        self.favorites = load_favorites()
        self.color_groups = {FAVORITES_KEY: list(self.favorites)}
        self.current_idx = self._initial_index()

        self.repeat_factor = self._compute_repeat_factor(len(self.wallpapers))
        self.virtual_wallpapers = []
        self.virtual_current_idx = 0

        self.focus_layer = 0
        self.heart_focus = False   # Tab: el foco está en el marcador de la tarjeta activa

        # Cache del filtro activo (evita recalcular listas en cada pulsación)
        self._filter_set = set()
        self._filter_min = 0
        self._filter_max = 0
        self._filter_matching = 0

        self._applying = False
        self._pending_apply = None
        self._quit_when_done = False
        self._last_applied = None

        # Miniaturas: una sola petición por archivo (las copias del carrusel
        # infinito comparten la misma miniatura). Caché LRU en memoria.
        self._thumb_pix = OrderedDict()
        self._thumb_waiting = {}
        self._loaded_idx = set()
        self._thumb_signals = LoaderSignals()
        self._thumb_signals.thumbLoaded.connect(self._on_thumb_loaded)
        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(self._thumb_thread_count())

        # El análisis de color va en su propio pool de 1 hilo para que nunca
        # compita con las miniaturas.
        self.group_pool = QThreadPool()
        self.group_pool.setMaxThreadCount(1)
        self._group_worker = None

        # Cambiar el fondo (gsettings/tema GTK) en un hilo aparte, en orden
        self.apply_pool = QThreadPool()
        self.apply_pool.setMaxThreadCount(1)

        # Una sola animación mueve el resalte de las tarjetas y el scroll.
        self._moving = []
        self._scroll_start = 0
        self._scroll_end = 0

        # Tool: no aparece en la barra de tareas ni en Alt+Tab
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Las ventanas Tool no cierran la app por defecto: se fuerza.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, True)
        self.setStyleSheet(TOOLTIP_QSS)

        self.hud = HUDPanel(self)
        self.hud_layout = self.hud.layout

        self.scroll_area = QScrollArea(self.hud)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.scroll_area.viewport().setContentsMargins(0, 0, 0, 0)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.viewport().installEventFilter(self)   # rueda del ratón
        self.hud_layout.addWidget(self.scroll_area, 1)

        self.scroll_container = StripContainer()
        self.scroll_container.setContentsMargins(0, 0, 0, 0)
        self.scroll_area.setWidget(self.scroll_container)

        self.palette_bar = ColorFilterBar(self)
        self.palette_bar.filterChanged.connect(self.apply_color_filter)
        self.palette_bar.modeToggled.connect(self._on_mode_toggled)
        self.palette_bar.settingsRequested.connect(self.toggle_prefs)
        self.palette_bar.set_mode_active(ThumbnailItem.grow_mode)

        self.scroll_anim = QVariantAnimation(self)
        self.scroll_anim.valueChanged.connect(self._on_transition_tick)
        self.scroll_anim.finished.connect(self._on_transition_finished)

        # Animación de entrada (apertura y cambio de filtro)
        self.intro_anim = QVariantAnimation(self)
        self.intro_anim.setStartValue(0.0)
        self.intro_anim.setEndValue(1.0)
        self.intro_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self.intro_anim.valueChanged.connect(self._on_intro_tick)
        self.intro_anim.finished.connect(self._on_intro_finished)
        self._intro_items = []
        self._intro_total = 1
        self._intro_card_ms = 1
        self._intro_full = False
        self._intro_bar = (0, 1, 1)
        self._intro_ready = False

        self._nav_direction = 0
        self.nav_timer = QTimer(self)
        self.nav_timer.setInterval(PREFS.get("nav_repeat_ms"))
        self.nav_timer.timeout.connect(self._nav_repeat)

        # Temporizadores de "agrupar cambios"
        self._prefs_pending = set()
        self._prefs_timer = QTimer(self)
        self._prefs_timer.setSingleShot(True)
        self._prefs_timer.timeout.connect(self._apply_prefs_now)
        PREFS.changed.connect(self._on_prefs_changed)

        self._dir_timer = QTimer(self)
        self._dir_timer.setSingleShot(True)
        self._dir_timer.setInterval(250)
        self._dir_timer.timeout.connect(self.handle_directory_mutations)

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._live_apply)
        self._prefetch_timer = QTimer(self)               # precalcula el contorno de la miniatura resaltada
        self._prefetch_timer.setSingleShot(True)
        self._prefetch_timer.timeout.connect(self._contour_prefetch)

        self.visible_slots = []
        self._empty_label = None
        self._full_content = True
        self._last_active = None

        if self.wallpapers:
            base_offset = (self.repeat_factor // 2) * len(self.wallpapers)
            self.virtual_current_idx = base_offset + self.current_idx
            self.virtual_wallpapers = self.wallpapers * self.repeat_factor

        self.position_hud()
        self.init_carousel()
        self.update_focused_view(animate_scroll=False)
        self._start_color_grouping()

        self.watcher = QFileSystemWatcher(self)
        self._refresh_watch()
        self.watcher.directoryChanged.connect(self._schedule_dir_reload)

        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(3000)
        self.watch_timer.timeout.connect(self.ensure_watcher_resilience)
        self.watch_timer.start()

        # Todo oculto antes de mostrar la ventana; la entrada arranca al finalizar
        self._intro_ready = self._intro_prepare(full=True)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.show()
        self._shown_clock.start()
        self.raise_()
        self.activateWindow()
        self.setFocus()

        QTimer.singleShot(0, self._finalize_initial_position)

    # ------------------------------------------------------------------
    #  Arranque / foco
    # ------------------------------------------------------------------

    def _finalize_initial_position(self):
        self.position_hud(force=True)
        self.update_focused_view(animate_scroll=False)
        if self._intro_ready:
            self._intro_ready = False
            self.intro_anim.start()

    def _pick_screen(self):
        if PREFS.get("monitor") == "cursor":
            s = QGuiApplication.screenAt(QCursor.pos())
            if s is not None:
                return s
        return QGuiApplication.primaryScreen()

    def _scan_wallpapers(self):
        return get_wallpapers(self.wallpaper_dir, PREFS.get("recursive"), PREFS.get("sort_order"),
                              PREFS.get("include_videos"))

    def _initial_index(self):
        if not self.wallpapers or not PREFS.get("start_on_current"):
            return 0
        cur = get_current_wallpaper()
        if not cur:
            return 0
        for i, p in enumerate(self.wallpapers):
            if os.path.normpath(p) == cur:
                return i
        return 0

    def _thumb_thread_count(self):
        return PREFS.get("threads") or max(1, min(3, (os.cpu_count() or 2) // 2))

    def focus_carousel(self):
        self.nav_timer.stop()
        self.focus_layer = 0
        self.palette_bar.set_keyboard_focus(False)
        self._set_heart_focus(False)
        self.setFocus()

    def _set_heart_focus(self, flag):
        if self.heart_focus != flag:
            self.heart_focus = flag
            for slot in self.visible_slots:
                if slot.is_selected:
                    slot.update()

    def _toggle_heart_focus(self):
        if not self.wallpapers or self._is_blacked_out(self.current_idx):
            return
        self.nav_timer.stop()
        if self.focus_layer == 1:
            self.focus_layer = 0
            self.palette_bar.set_keyboard_focus(False)
            self._set_heart_focus(True)
        else:
            self._set_heart_focus(not self.heart_focus)

    def _toggle_current_favorite(self):
        if not self.wallpapers or not (0 <= self.current_idx < len(self.wallpapers)):
            return
        if self._is_blacked_out(self.current_idx):
            return
        self.toggle_favorite(self.wallpapers[self.current_idx])

    def focusNextPrevChild(self, next_):
        return False

    # ---- observar directorio ------------------------------------------

    def _refresh_watch(self):
        old = self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        dirs = []
        if os.path.isdir(self.wallpaper_dir):
            dirs.append(self.wallpaper_dir)
            if PREFS.get("recursive"):
                dirs += list_subdirs(self.wallpaper_dir)
        if dirs:
            self.watcher.addPaths(dirs)

    def _schedule_dir_reload(self, *_):
        self._dir_timer.start()   # agrupa ráfagas de cambios (copiar muchos archivos)

    def ensure_watcher_resilience(self):
        if os.path.exists(self.wallpaper_dir):
            if self.wallpaper_dir not in self.watcher.directories():
                self._refresh_watch()
                self._schedule_dir_reload()

    # ------------------------------------------------------------------
    #  Modo de animación
    # ------------------------------------------------------------------

    def _on_mode_toggled(self):
        flag = not PREFS.get("grow_mode")
        self.palette_bar.set_mode_active(flag)       # el botón responde al instante
        PREFS.set("grow_mode", flag)

    def _apply_grow_mode(self):
        # Recalcular la geometría de todas las tarjetas con el modo nuevo
        for slot in self.visible_slots:
            slot._scaled_cache_key = None
            slot.geom_dirty = True
        self.palette_bar.set_mode_active(ThumbnailItem.grow_mode)
        self.update_focused_view(animate_scroll=False)

    # ------------------------------------------------------------------
    #  Geometría
    # ------------------------------------------------------------------

    def _hud_width(self):
        sg = self._screen_obj.geometry()
        return max(400, int(sg.width() * PREFS.get("hud_width_pct") / 100.0) - 40)

    def _compute_repeat_factor(self, n):
        """Copias del carrusel infinito: lo justo para cubrir ~3 pantallas
        (siempre impar, mínimo 3). Sin carrusel infinito: una sola copia."""
        if n <= 0 or not PREFS.get("infinite"):
            return 1
        visible = self._hud_width() // max(1, SelectorWindow.STEP) + 2
        needed = -(-visible * 3 // n)
        r = max(3, needed)
        if r % 2 == 0:
            r += 1
        return r

    def _compute_layout(self):
        screen = self._screen_obj or QApplication.primaryScreen()
        sg = screen.geometry()
        margin = SelectorWindow.WINDOW_MARGIN
        gap = SelectorWindow.BAR_GAP

        hud_width = self._hud_width()
        bar_w = self.palette_bar.width()
        bar_h = self.palette_bar.height()
        hud_h = self.hud.height()
        pad = ThumbnailItem.PAD_Y - SelectorWindow.GROW_OVERFLOW   # espacio sobrante sobre la tarjeta crecida

        if PREFS.get("bar_position") == "top":
            bar_y = margin
            hud_y = margin + bar_h + gap - pad
            win_h = hud_y + hud_h + margin
        else:
            hud_y = margin - pad
            bar_y = hud_y + hud_h - pad + gap
            win_h = bar_y + bar_h + margin
            if hud_y < 0:
                hud_y, bar_y, win_h = hud_y - hud_y, bar_y - hud_y, win_h - hud_y

        win_w = max(hud_width, bar_w) + margin * 2
        valign = PREFS.get("window_valign")
        edge = int(sg.height() * 0.06)
        if valign == "top":
            win_y = sg.y() + edge - margin
        elif valign == "bottom":
            win_y = sg.y() + sg.height() - edge - win_h + margin
        else:
            win_y = sg.y() + (sg.height() - win_h) // 2
        win_x = sg.x() + (sg.width() - win_w) // 2
        return win_x, win_y, win_w, win_h, hud_width, hud_y, bar_y, bar_w

    def position_hud(self, force=False):
        """Dimensiona la ventana al contenido y la coloca en la pantalla.
        Devuelve True si cambió el ancho del carrusel."""
        win_x, win_y, win_w, win_h, hud_width, hud_y, bar_y, bar_w = self._compute_layout()
        self._base_y = win_y
        rect = QRect(win_x, win_y + self._y_shift, win_w, win_h)
        if force or self.geometry() != rect:
            self.setGeometry(rect)

        width_changed = self.hud.width() != hud_width
        if width_changed:
            self.hud.setFixedWidth(hud_width)
        self.hud.move((win_w - hud_width) // 2, hud_y)
        self._bar_home = QPoint((win_w - bar_w) // 2, bar_y)
        self._place_bar()
        return width_changed

    def _place_bar(self):
        self.palette_bar.move(self._bar_home.x(), self._bar_home.y() + self._bar_dy)

    # ------------------------------------------------------------------
    #  Miniaturas (carga perezosa + caché LRU)
    # ------------------------------------------------------------------

    def _request_thumb(self, item, priority):
        path = item.filepath
        pix = self._thumb_pix.get(path)
        if pix is not None:
            self._thumb_pix.move_to_end(path)
            item.set_thumbnail(pix)
            return
        waiting = self._thumb_waiting.get(path)
        if waiting is not None:
            waiting.append(item)
            return
        self._thumb_waiting[path] = [item]
        self.threadpool.start(ThumbnailWorker(path, self._thumb_signals), priority)

    def _on_thumb_loaded(self, path, img):
        pix = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
        self._thumb_pix[path] = pix
        cap = PREFS.get("mem_cache")
        while len(self._thumb_pix) > cap:
            self._thumb_pix.popitem(last=False)
        for item in self._thumb_waiting.pop(path, []):
            try:
                if item.filepath == path and item._thumb_requested:
                    item.set_thumbnail(pix)
            except RuntimeError:
                pass  # el widget ya fue destruido

    def _refresh_thumb_window(self, active_idx):
        """Solo se cargan las miniaturas cercanas a la tarjeta activa; las
        lejanas se liberan. Así el arranque y la memoria no dependen de
        cuántos fondos haya."""
        slots = self.visible_slots
        n = len(slots)
        if not n:
            return
        vis = self.hud.width() // max(1, SelectorWindow.STEP) + 2
        r = vis // 2 + PREFS.get("preload")
        lo = max(0, active_idx - r)
        hi = min(n - 1, active_idx + r)
        for i in range(lo, hi + 1):
            slots[i].ensure_thumb(self._request_thumb, -abs(i - active_idx))
            self._loaded_idx.add(i)
        far_lo, far_hi = lo - r, hi + r
        for i in [i for i in self._loaded_idx if i < far_lo or i > far_hi]:
            if i < n:
                slots[i].unload()
            self._loaded_idx.discard(i)

    # ------------------------------------------------------------------
    #  Carrusel
    # ------------------------------------------------------------------

    def _active_index(self):
        return self.virtual_current_idx if self.active_filter is None else self.current_idx

    def _active_slot(self):
        idx = self._active_index()
        if 0 <= idx < len(self.visible_slots):
            return self.visible_slots[idx]
        return None

    def init_carousel(self):
        """Prepara las tarjetas. Reutiliza los widgets existentes (crear
        cientos de widgets es lo más caro de reconstruir el carrusel)."""
        self._intro_cancel()
        self.scroll_anim.stop()
        self._moving = []
        self._loaded_idx = set()
        self._full_content = True
        self._last_active = None
        if self._empty_label is not None:
            self._empty_label.setParent(None)
            self._empty_label.deleteLater()
            self._empty_label = None

        active_list = self.virtual_wallpapers if self.active_filter is None else self.wallpapers
        slots = self.visible_slots

        if not active_list:
            for widget in slots:
                widget.setParent(None)
                widget.deleteLater()
            self.visible_slots = []
            empty = QLabel("Vacío", self.scroll_container)
            empty.setStyleSheet("color: rgba(255,255,255,0.3); font-size: 13px; font-weight: 300;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setGeometry(0, ThumbnailItem.PAD_Y, 300, ThumbnailItem.BASE_H)
            empty.show()
            self._empty_label = empty
            self.scroll_container.setFixedSize(300, HUDPanel.HEIGHT)
            return

        n = len(active_list)
        while len(slots) > n:
            widget = slots.pop()
            widget.setParent(None)
            widget.deleteLater()
        for i in range(n):
            if i < len(slots):
                thumb = slots[i]
                thumb.reset()
            else:
                thumb = ThumbnailItem(self.scroll_container)
                thumb.show()
                slots.append(thumb)
            thumb.virtual_index = i
            thumb.item_index = i
        self._reflow_slots(full=True)

    def _reflow_slots(self, full=True):
        """Posiciona las tarjetas una tras otra.

        Durante la animación (full=False) solo se actualizan las tarjetas que
        están en (o cerca de) la zona visible; el resto se acomoda al terminar.
        """
        T = ThumbnailItem
        slant = T.SLANT
        gap = T.GAP
        base = T.BASE_W
        grow = T.grow_mode
        delta = ((T.GROW_SCALE - 1.0) * (T.BASE_W - T.SLANT) * T.GROW_PUSH) if grow else (T.EXPANDED_W - T.BASE_W)

        if full:
            lo = hi = 0
        else:
            vw = self.hud.width()
            val = self.scroll_area.horizontalScrollBar().value()
            lo = val - 2 * T.EXPANDED_W
            hi = val + vw + 2 * T.EXPANDED_W

        x = 0
        for slot in self.visible_slots:
            g = slot.glow_progress
            lw = base if g == 0.0 else int(base + delta * g)
            if slot.slot_x != x:
                slot.slot_x = x
                slot.geom_dirty = True
            if slot.geom_dirty:
                if full:
                    apply = True
                else:
                    cur = slot.x()
                    apply = ((x + lw >= lo and x <= hi) or (cur + slot.width() >= lo and cur <= hi))
                if apply:
                    slot.apply_geometry()
                    slot.geom_dirty = False
            x += lw - slant + gap

        total_w = (x + slant - gap) if self.visible_slots else 1
        total_w = max(total_w, 1)
        if self.scroll_container.width() != total_w or self.scroll_container.height() != HUDPanel.HEIGHT:
            self.scroll_container.setFixedSize(total_w, HUDPanel.HEIGHT)

    def _scroll_target(self, idx):
        """Scroll que deja centrado el item `idx` ya resaltado. Se calcula con
        la geometría FINAL (no depende de en qué punto está la animación)."""
        n = len(self.visible_slots)
        vw = self.hud.width()
        step = SelectorWindow.STEP
        active_w = ThumbnailItem.layout_width_for(1.0)
        scroll_to = idx * step - (vw - active_w) // 2
        total = (n - 1) * step + active_w
        return max(0, min(scroll_to, max(0, total - vw)))

    # ---- transición (una sola animación para resalte + scroll) --------

    def _begin_transition(self, scroll_to, animate, candidates=None):
        bar = self.scroll_area.horizontalScrollBar()
        self.scroll_anim.stop()

        dur = PREFS.ms(PREFS.get("nav_duration"))
        animate = animate and dur > 0

        prev = self._moving
        for s in prev:
            s._is_animating = False
        if candidates is None:
            pool = self.visible_slots
        else:
            pool = set(prev)
            for i in candidates:
                if i is not None and 0 <= i < len(self.visible_slots):
                    pool.add(self.visible_slots[i])
        moving = [s for s in pool if s.glow_progress != s.glow_target]
        # Apilado coherente: la que encoge queda debajo y la que crece, encima
        for s in sorted(moving, key=lambda m: m.glow_target):
            s.raise_()

        if animate and (moving or bar.value() != scroll_to):
            for s in moving:
                s.glow_start = s.glow_progress
                s._is_animating = True
            self._moving = moving
            self._scroll_start = bar.value()
            self._scroll_end = scroll_to
            self.scroll_anim.setDuration(dur)
            self.scroll_anim.setEasingCurve(EASINGS.get(PREFS.get("nav_easing"), QEasingCurve.Type.OutCubic))
            self.scroll_anim.setStartValue(0.0)
            self.scroll_anim.setEndValue(1.0)
            self.scroll_anim.start()
        else:
            for s in moving:
                s.set_glow(s.glow_target)
            self._moving = []
            self._reflow_slots(full=True)
            bar.setValue(scroll_to)

    def _on_transition_tick(self, t):
        t = float(t)
        g = _clamp01(t)        # con curva "rebote" el scroll se pasa, el resalte no
        for s in self._moving:
            s.set_glow(s.glow_start + (s.glow_target - s.glow_start) * g)
        # En modo crecer sin separación no hay empuje: no hace falta recolocar
        if not ThumbnailItem.grow_mode or ThumbnailItem.GROW_PUSH > 0:
            self._reflow_slots(full=False)
        self.scroll_area.horizontalScrollBar().setValue(
            int(self._scroll_start + (self._scroll_end - self._scroll_start) * t))

    def _on_transition_finished(self):
        for s in self._moving:
            s.set_glow(s.glow_target)
            s._is_animating = False
            s.update()
        self._moving = []
        self._reflow_slots(full=True)
        self.scroll_area.horizontalScrollBar().setValue(self._scroll_end)
        self.normalize_virtual_index()

    # ---- clics ---------------------------------------------------------

    def click_at(self, pos, double=False):
        """Resuelve un clic en el contenedor: busca, de arriba hacia abajo, la
        tarjeta cuya forma contiene el punto."""
        p = pos.toPoint()
        for child in reversed(self.scroll_container.children()):
            if isinstance(child, ThumbnailItem) and child.geometry().contains(p):
                local = QPointF(p.x() - child.x(), p.y() - child.y())
                if child._local_card_path().contains(local):
                    child.handle_click(local, double)
                    return

    # ---- rueda del ratón -----------------------------------------------

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and PREFS.get("wheel_nav"):
            self._handle_wheel(event)
            return True
        return super().eventFilter(obj, event)

    def _handle_wheel(self, event):
        if self.focus_layer != 0 or not self.wallpapers:
            return
        d = event.angleDelta()
        delta = d.y() or d.x()
        if PREFS.get("wheel_invert"):
            delta = -delta
        self._wheel_acc += delta
        # Un "clic" de rueda = 120; el touchpad acumula hasta llegar a ese valor
        while self._wheel_acc <= -120:
            self._wheel_acc += 120
            self._set_heart_focus(False)
            self.advance()
        while self._wheel_acc >= 120:
            self._wheel_acc -= 120
            self._set_heart_focus(False)
            self.retreat()

    # ---- grupos de color ---------------------------------------------

    def _start_color_grouping(self):
        if self._group_worker is not None:
            self._group_worker.cancel()
            self._group_worker = None
        if not self.all_wallpapers or not PREFS.get("color_filters"):
            self.palette_bar.set_groups([])
            return
        worker = ColorGroupingWorker(list(self.all_wallpapers))
        worker.setAutoDelete(False)
        worker.signals.groupsReady.connect(self._on_groups_ready)
        self._group_worker = worker
        self.group_pool.start(worker)

    def _on_groups_ready(self, groups):
        if not PREFS.get("color_filters"):
            return
        groups[FAVORITES_KEY] = list(self.favorites)
        self.color_groups = groups
        ordered_keys = [key for key in FAMILY_ORDER if groups.get(key)]
        self.palette_bar.set_groups(ordered_keys)
        if self.active_filter is not None:
            self._rebuild_filter_cache()
        if self.position_hud():
            self.update_focused_view(animate_scroll=False)

    def _on_color_filters_toggled(self):
        if PREFS.get("color_filters"):
            self._start_color_grouping()
            return
        if self._group_worker is not None:
            self._group_worker.cancel()
            self._group_worker = None
        self.palette_bar.set_groups([])
        if self.active_filter not in (None, FAVORITES_KEY):
            self.palette_bar.set_active_key(None)
            self.apply_color_filter(None)
        self.position_hud()

    def _rebuild_filter_cache(self):
        if self.active_filter is None:
            self._filter_set = set()
            self._filter_min = self._filter_max = self._filter_matching = 0
            return
        self._filter_set = set(self.color_groups.get(self.active_filter, []))
        matching = sum(1 for p in self.wallpapers if p in self._filter_set)
        half = (len(self.wallpapers) - matching) // 2
        self._filter_matching = matching
        self._filter_min = half
        self._filter_max = half + matching - 1 if matching else 0

    def toggle_favorite(self, filepath):
        if not filepath:
            return
        if filepath in self.favorites:
            self.favorites.discard(filepath)
        else:
            self.favorites.add(filepath)
        save_favorites(self.favorites)
        self.color_groups[FAVORITES_KEY] = list(self.favorites)
        self.refresh_favorite_visuals()
        self._play_pop(self._active_slot())
        if self.active_filter == FAVORITES_KEY:
            self.apply_color_filter(FAVORITES_KEY)

    def refresh_favorite_visuals(self):
        for slot in self.visible_slots:
            if slot.is_selected:
                slot.update()

    def _is_blacked_out(self, idx):
        if self.active_filter is None:
            return False
        if not self.wallpapers or idx >= len(self.wallpapers):
            return False
        return self.wallpapers[idx] not in self._filter_set

    def _current_wallpaper(self):
        if self.wallpapers and 0 <= self.current_idx < len(self.wallpapers):
            return self.wallpapers[self.current_idx]
        return None

    def _arrange_for_filter(self, current_wp):
        """Reconstruye self.wallpapers según el filtro activo."""
        if self.active_filter is None:
            self.wallpapers = list(self.all_wallpapers)
            self.repeat_factor = self._compute_repeat_factor(len(self.wallpapers))
            base_offset = (self.repeat_factor // 2) * len(self.wallpapers)
            self.virtual_wallpapers = self.wallpapers * self.repeat_factor
            if current_wp in self.wallpapers:
                self.current_idx = self.wallpapers.index(current_wp)
            else:
                self.current_idx = 0
            self.virtual_current_idx = base_offset + self.current_idx
        else:
            group_set = set(self.color_groups.get(self.active_filter, []))
            matching = [p for p in self.all_wallpapers if p in group_set]
            non_matching = [p for p in self.all_wallpapers if p not in group_set]

            half_non = len(non_matching) // 2
            left_non = non_matching[:half_non]
            right_non = non_matching[half_non:]

            self.wallpapers = left_non + matching + right_non
            if matching:
                if current_wp in matching:
                    self.current_idx = self.wallpapers.index(current_wp)
                else:
                    self.current_idx = len(left_non)
            else:
                self.current_idx = 0
            self.virtual_current_idx = self.current_idx
        self._rebuild_filter_cache()

    def apply_color_filter(self, family_key):
        current_wp = self._current_wallpaper()

        self.active_filter = family_key
        self._set_heart_focus(False)
        self._arrange_for_filter(current_wp)

        self.init_carousel()
        self.update_focused_view(animate_scroll=False)
        if self._intro_prepare(full=False):      # las tarjetas vuelven a entrar
            self.intro_anim.start()

    # ---- recarga de archivos -------------------------------------------

    def _prune_favorites(self):
        alive = {f for f in self.favorites if os.path.exists(f)}
        if alive != self.favorites:
            self.favorites = alive
            save_favorites(self.favorites)
        self.color_groups[FAVORITES_KEY] = list(self.favorites)

    def _reload_wallpapers(self, clear_caches=True):
        cur = self._current_wallpaper()
        self.all_wallpapers = self._scan_wallpapers()
        if clear_caches:
            # Los archivos pudieron cambiar: descartar miniaturas y tarjetas en cache
            self._thumb_pix.clear()
            ThumbnailItem._card_cache.clear()
        self._prune_favorites()
        self._arrange_for_filter(cur)
        self.init_carousel()
        self.update_focused_view(animate_scroll=False)
        self._refresh_watch()
        self._start_color_grouping()

    def handle_directory_mutations(self):
        self._reload_wallpapers(clear_caches=True)

    def pick_wallpaper_dir(self):
        """Selector de carpeta (lo usa el panel de preferencias)."""
        parent = self.panel if self.panel is not None else self
        dlg = QFileDialog(parent, "Seleccionar carpeta de fondos", self.wallpaper_dir or os.path.expanduser("~"))
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._modal_open = True
        try:
            ok = dlg.exec()
        finally:
            self._modal_open = False
        if ok and dlg.selectedFiles():
            self.change_wallpaper_dir(dlg.selectedFiles()[0])
            return True
        return False

    def change_wallpaper_dir(self, folder):
        if not folder or not os.path.isdir(folder) or folder == self.wallpaper_dir:
            return
        save_config(folder)
        self.wallpaper_dir = folder
        self.active_filter = None
        self.palette_bar.set_active_key(None)
        self._reload_wallpapers(clear_caches=True)

    def clear_thumb_cache(self):
        from thumbnail_cache import clear_cache
        freed = clear_cache()
        self._thumb_pix.clear()
        ThumbnailItem._card_cache.clear()
        for slot in self.visible_slots:
            slot.unload()
        self._loaded_idx = set()
        self.update_focused_view(animate_scroll=False)
        return freed

    # ------------------------------------------------------------------
    #  Vista / navegación
    # ------------------------------------------------------------------

    def update_focused_view(self, animate_scroll=True):
        if not self.wallpapers or not self.visible_slots:
            return

        if self.active_filter is None:
            active_idx = self.virtual_current_idx
            paths = self.virtual_wallpapers
        else:
            self.current_idx = max(self._filter_min, min(self.current_idx, self._filter_max))
            active_idx = self.current_idx
            paths = self.wallpapers

        filtering = self.active_filter is not None
        slots = self.visible_slots

        if self._full_content or len(slots) != len(paths):
            for i, slot in enumerate(slots):
                slot.update_content(paths[i], i == active_idx, filtering and self._is_blacked_out(i))
            self._full_content = False
            candidates = None
        else:
            # Solo cambian la tarjeta que deja de ser activa y la nueva
            candidates = (self._last_active, active_idx)
            for i in set(candidates):
                if i is not None and 0 <= i < len(slots):
                    slots[i].update_content(paths[i], i == active_idx, filtering and self._is_blacked_out(i))
        self._last_active = active_idx

        self._refresh_thumb_window(active_idx)
        self._begin_transition(self._scroll_target(active_idx), animate_scroll, candidates)
        if animate_scroll:
            self._schedule_live_apply()
        self._schedule_contour_prefetch()

    def advance(self):
        if not self.wallpapers:
            return

        if self.active_filter is None:
            if not PREFS.get("infinite") and self.virtual_current_idx >= len(self.wallpapers) - 1:
                return
            self.virtual_current_idx += 1
            self.current_idx = self.virtual_current_idx % len(self.wallpapers)
            self.update_focused_view(animate_scroll=True)
        else:
            if self._filter_matching and self.current_idx < self._filter_max:
                self.current_idx += 1
                self.virtual_current_idx = self.current_idx
                self.update_focused_view(animate_scroll=True)

    def retreat(self):
        if not self.wallpapers:
            return

        if self.active_filter is None:
            if not PREFS.get("infinite") and self.virtual_current_idx <= 0:
                return
            self.virtual_current_idx -= 1
            self.current_idx = self.virtual_current_idx % len(self.wallpapers)
            self.update_focused_view(animate_scroll=True)
        else:
            if self.current_idx > self._filter_min:
                self.current_idx -= 1
                self.virtual_current_idx = self.current_idx
                self.update_focused_view(animate_scroll=True)

    def normalize_virtual_index(self):
        """Devuelve el item activo al bloque central del carrusel infinito."""
        if self.active_filter is not None or not self.wallpapers:
            return
        N = len(self.wallpapers)
        if N == 0 or len(self.visible_slots) <= N:
            return

        middle_block = self.repeat_factor // 2
        current_block = self.virtual_current_idx // N
        if current_block == middle_block:
            return

        local_idx = self.virtual_current_idx % N
        old_active = self._last_active
        self.virtual_current_idx = (middle_block * N) + local_idx
        self.current_idx = local_idx

        # Solo cambian de estado la tarjeta que dejó de ser activa y la nueva
        for i in {old_active, self.virtual_current_idx}:
            if i is None or not (0 <= i < len(self.visible_slots)):
                continue
            slot = self.visible_slots[i]
            is_active = (i == self.virtual_current_idx)
            target = 1.0 if is_active else 0.0
            if slot.is_selected != is_active or slot.glow_progress != target:
                slot.set_active_instant(is_active)

        self._last_active = self.virtual_current_idx
        self._loaded_idx = set()
        for slot in self.visible_slots:
            if slot.glow_progress != slot.glow_target:
                slot.set_active_instant(slot.glow_target >= 1.0)
        self._reflow_slots(full=True)
        self.scroll_area.horizontalScrollBar().setValue(self._scroll_target(self.virtual_current_idx))
        self._refresh_thumb_window(self.virtual_current_idx)

    def set_index_by_virtual_idx(self, virtual_idx):
        if not self.wallpapers:
            return
        self.virtual_current_idx = virtual_idx
        self.current_idx = virtual_idx % len(self.wallpapers)
        self.update_focused_view(animate_scroll=True)

    def set_index(self, idx):
        if not self.wallpapers:
            return
        if self._filter_min <= idx <= self._filter_max and not self._is_blacked_out(idx):
            self.current_idx = idx
            self.virtual_current_idx = idx
            self.update_focused_view(animate_scroll=True)

    # ------------------------------------------------------------------
    #  Aplicar fondo
    # ------------------------------------------------------------------

    def confirm_selection(self, from_live=False):
        if not self.wallpapers:
            return
        if self.active_filter is not None and self._is_blacked_out(self.current_idx):
            return
        if self.current_idx < 0 or self.current_idx >= len(self.wallpapers):
            return
        if not from_live:
            # Un doble clic llega como dos eventos: se aplica una sola vez
            if self._confirm_clock.isValid() and self._confirm_clock.elapsed() < 300:
                return
            self._confirm_clock.restart()
        selected = self.wallpapers[self.current_idx]
        if from_live and selected == self._last_applied:
            return
        self._last_applied = selected

        self.apply_pool.start(WallpaperSetWorker(selected))   # sin bloquear la interfaz
        self._queue_pywal(selected)

        if not from_live:
            self._play_flash()
            if PREFS.get("close_on_apply"):
                self.request_close()

    def _schedule_contour_prefetch(self):
        if not hasattr(self, "_prefetch_timer"):
            return
        if PREFS.get("wallpaper_fade") and PREFS.get("fade_effect") in ("contour", "random"):
            self._prefetch_timer.start(300)

    @staticmethod
    def _prefetch_video_frame(path):
        try:
            from wallpaper import livewall_frame
            import contour
            frame = livewall_frame(path)
            if frame:
                contour.prefetch(frame)
        except Exception as e:
            print(f"Contorno (fotograma del video): {e}")

    def _contour_prefetch(self):
        if self._closing or not self.wallpapers or not (0 <= self.current_idx < len(self.wallpapers)):
            return
        path = self.wallpapers[self.current_idx]
        if livewall.is_video(path):
            # Fondo animado: el efecto se hace sobre su fotograma (extraerlo puede tardar: hilo aparte)
            threading.Thread(target=self._prefetch_video_frame, args=(path,), daemon=True).start()
            return
        # Vecinas más cercanas primero (lo más probable es que el usuario siga navegando hacia ellas)
        n = len(self.wallpapers)
        neighbors = []
        for step in (1, 2, 3, 4):
            for idx in (self.current_idx + step, self.current_idx - step):
                p = self.wallpapers[idx % n]
                if p != path and p not in neighbors and not livewall.is_video(p):
                    neighbors.append(p)
        try:
            import contour
            contour.prefetch(path, neighbors=neighbors)   # hilo propio, prioridad baja: no bloquea la interfaz
        except Exception as e:
            print(f"Contorno (precálculo): {e}")

    def _schedule_live_apply(self):
        if PREFS.get("apply_on_navigate"):
            self._apply_timer.start(PREFS.get("apply_delay"))

    def _live_apply(self):
        if PREFS.get("apply_on_navigate") and not self._closing:
            self.confirm_selection(from_live=True)

    def _queue_pywal(self, image_path):
        if self._applying:
            self._pending_apply = image_path
            return
        self._applying = True
        worker = ApplyWorker(image_path)
        worker.signals.finished.connect(self._on_apply_finished)
        QThreadPool.globalInstance().start(worker)

    def _on_apply_finished(self):
        self._applying = False
        if self._pending_apply:
            path = self._pending_apply
            self._pending_apply = None
            self._queue_pywal(path)
            return
        if self._quit_when_done:
            self.apply_pool.waitForDone(5000)
            QApplication.instance().quit()

    # ------------------------------------------------------------------
    #  Animaciones: entrada, pulso al aplicar, "pop" del marcador, salida
    # ------------------------------------------------------------------

    def _intro_prepare(self, full):
        """Deja todo oculto y calcula el calendario de la animación.
        full=True: apertura (franja, barra y tarjetas). False: solo tarjetas."""
        self._intro_cancel()
        style = PREFS.get("intro_style")
        if (not PREFS.get("anim_enabled") or style == "none" or not self.visible_slots
                or not self.wallpapers or (not full and not PREFS.get("anim_filter"))):
            return False

        slots = self.visible_slots
        n = len(slots)
        active = self._active_index()
        half = self.hud.width() // max(1, SelectorWindow.STEP) // 2 + 3
        lo, hi = max(0, active - half), min(n - 1, active + half)

        stagger = PREFS.ms(PREFS.get("intro_stagger"))
        card_ms = max(1, PREFS.ms(PREFS.get("intro_card_ms")))
        base = PREFS.ms(80) if full else 0
        if not full:
            stagger = int(stagger * 0.6)
            card_ms = max(1, int(card_ms * 0.8))
        if style == "sweep":
            stagger = int(stagger * 0.5)
        elif style == "zoom":
            stagger = int(stagger * 0.4)
            card_ms = int(card_ms * 1.1)

        items = []
        for i in range(lo, hi + 1):
            if style == "sweep":
                rank = i - lo                      # de izquierda a derecha
            elif style == "fade":
                rank = 0                           # todas a la vez
            else:
                rank = abs(i - active)             # del centro hacia fuera
            items.append((slots[i], base + rank * stagger))
        for slot, _ in items:
            slot.set_intro(0.0)

        end = max(d for _, d in items) + card_ms
        self._intro_items = items
        self._intro_card_ms = card_ms
        self._intro_full = full

        if full:
            self.scroll_container.set_band_progress(0.0)
            self.palette_bar.prepare_intro()
            self._bar_dy = -10 if PREFS.get("bar_position") == "top" else 10
            self._place_bar()
            bar_start = PREFS.ms(30)
            bar_dur = max(1, int(card_ms * 0.75))
            bar_stag = PREFS.ms(26)
            self._intro_bar = (bar_start, bar_dur, bar_stag)
            end = max(end, bar_start + 50 + len(self.palette_bar._parts) * bar_stag + bar_dur)
        self._intro_total = max(1, end)
        self.intro_anim.setDuration(self._intro_total)
        return True

    def _on_intro_tick(self, v):
        t = float(v) * self._intro_total
        cm = self._intro_card_ms
        for slot, delay in self._intro_items:
            slot.set_intro(_ease_out((t - delay) / cm))
        if self._intro_full:
            self.scroll_container.set_band_progress(_ease_out(t / cm))
            start, dur, stag = self._intro_bar
            p = self.palette_bar.apply_intro(t, start, dur, stag)
            self._bar_dy = int(round((-10 if PREFS.get("bar_position") == "top" else 10) * (1.0 - p)))
            self._place_bar()

    def _intro_cancel(self):
        """Detiene la entrada y deja todo en su estado final."""
        if self.intro_anim.state() == QAbstractAnimation.State.Running:
            self.intro_anim.stop()
        for slot, _ in self._intro_items:
            try:
                slot.set_intro(1.0)
            except RuntimeError:
                pass
        had_full = self._intro_full
        self._intro_items = []
        self._intro_full = False
        if had_full or self._bar_dy != 0:
            self.scroll_container.set_band_progress(1.0)
            self.palette_bar.finish_intro()
            self._bar_dy = 0
            self._place_bar()

    def _on_intro_finished(self):
        self._intro_cancel()

    def _active_slot_or_none(self):
        return self._active_slot() if self.visible_slots else None

    def _play_flash(self):
        slot = self._active_slot_or_none()
        if slot is None or not PREFS.get("anim_enabled"):
            return
        if self._flash_anim is None:
            self._flash_anim = QVariantAnimation(self)
            self._flash_anim.setStartValue(1.0)
            self._flash_anim.setEndValue(0.0)
            self._flash_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._flash_anim.valueChanged.connect(self._on_flash_tick)
        self._flash_anim.stop()
        if self._flash_slot is not None:
            try:
                self._flash_slot.set_flash(0.0)
            except RuntimeError:
                pass
        self._flash_slot = slot
        self._flash_anim.setDuration(PREFS.ms(520))
        self._flash_anim.start()

    def _on_flash_tick(self, v):
        try:
            self._flash_slot.set_flash(float(v))
        except (RuntimeError, AttributeError):
            pass

    def _play_pop(self, slot):
        if slot is None or not PREFS.get("anim_enabled"):
            return
        if self._pop_anim is None:
            self._pop_anim = QVariantAnimation(self)
            self._pop_anim.setStartValue(0.0)
            self._pop_anim.setEndValue(1.0)
            self._pop_anim.valueChanged.connect(self._on_pop_tick)
        self._pop_anim.stop()
        self._pop_slot = slot
        self._pop_anim.setDuration(PREFS.ms(360))
        self._pop_anim.start()

    def _on_pop_tick(self, v):
        try:
            self._pop_slot.set_pop(0.42 * math.sin(math.pi * float(v)))
        except (RuntimeError, AttributeError):
            pass

    def request_close(self):
        """Cierra con la animación de salida (si está activada). La salida se
        dibuja con las mismas piezas que la entrada (tarjetas, franja y barra),
        porque la opacidad de ventana no la respetan todos los compositores."""
        if self._closing:
            return
        self._closing = True
        self.nav_timer.stop()
        self._apply_timer.stop()
        if self._prefs_open and self.panel is not None:
            self.panel.hide()
        dur = PREFS.ms(260) if PREFS.get("anim_outro") else 0
        if dur <= 0 or not self.isVisible() or not self.visible_slots:
            self.close()
            return

        self._intro_cancel()
        for anim in (self.scroll_anim, self._flash_anim, self._pop_anim):
            if anim is not None:
                anim.stop()
        self.anim_driver.stop()

        slots = self.visible_slots
        n = len(slots)
        active = self._active_index()
        half = self.hud.width() // max(1, SelectorWindow.STEP) // 2 + 3
        lo, hi = max(0, active - half), min(n - 1, active + half)
        maxrank = max(abs(i - active) for i in range(lo, hi + 1))
        stag = max(1, int(dur * 0.035))
        self._outro_card_ms = max(1, int(dur * 0.62))
        # Salen primero las tarjetas de los extremos y al final la activa
        self._outro_items = [(slots[i], (maxrank - abs(i - active)) * stag) for i in range(lo, hi + 1)]
        self._outro_total = max(1, maxrank * stag + self._outro_card_ms)

        self._outro_anim = QVariantAnimation(self)
        self._outro_anim.setStartValue(0.0)
        self._outro_anim.setEndValue(1.0)
        self._outro_anim.setDuration(self._outro_total)
        self._outro_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._outro_anim.valueChanged.connect(self._on_outro_tick)
        self._outro_anim.finished.connect(self.close)
        self._outro_anim.start()

    def _on_outro_tick(self, v):
        t = float(v) * self._outro_total
        cm = self._outro_card_ms
        for slot, delay in self._outro_items:
            q = _clamp01((t - delay) / cm)
            slot.set_intro(1.0 - q * q)
        self.scroll_container.set_band_progress(1.0 - _clamp01(t / self._outro_total) ** 2)
        bq = _clamp01((t - 0.2 * self._outro_total) / (0.8 * self._outro_total))
        p = 1.0 - bq * bq
        self.palette_bar.set_progress(p)
        self._bar_dy = int(round((-10 if PREFS.get("bar_position") == "top" else 10) * (1.0 - p)))
        self._place_bar()

    # ------------------------------------------------------------------
    #  Preferencias
    # ------------------------------------------------------------------

    def _on_prefs_changed(self, keys):
        self._prefs_pending |= set(keys)
        fast = self._prefs_pending == {"grow_mode"}
        self._prefs_timer.start(0 if fast else 70)   # agrupa los cambios de un slider

    def _apply_prefs_now(self):
        keys = self._prefs_pending
        self._prefs_pending = set()
        if not keys or self._closing:
            return
        configure_metrics()
        ThumbnailItem.CARD_CACHE_MAX = PREFS.get("mem_cache")
        cur = self._current_wallpaper()

        if keys & (GEOMETRY_KEYS | PAINT_KEYS | THUMB_KEYS | {"grow_mode"}):
            ThumbnailItem._card_cache.clear()
            ThumbnailItem._black_card = None
        if keys & THUMB_KEYS:
            self._thumb_pix.clear()
            for slot in self.visible_slots:
                slot.unload()
            self._loaded_idx = set()

        if "monitor" in keys:
            self._screen_obj = self._pick_screen()
        if "color_filters" in keys:
            self._on_color_filters_toggled()
        if "threads" in keys:
            self.threadpool.setMaxThreadCount(self._thumb_thread_count())
        if "nav_repeat_ms" in keys:
            self.nav_timer.setInterval(PREFS.get("nav_repeat_ms"))
        if "show_tooltips" in keys:
            self.palette_bar.set_tooltips(PREFS.get("show_tooltips"))
        if "apply_on_navigate" in keys and not PREFS.get("apply_on_navigate"):
            self._apply_timer.stop()

        if keys & RELOAD_KEYS:
            self.hud.setFixedHeight(HUDPanel.HEIGHT)
            self.position_hud(force=True)
            self._reload_wallpapers(clear_caches=False)
        elif keys & GEOMETRY_KEYS:
            self.hud.setFixedHeight(HUDPanel.HEIGHT)
            self._arrange_for_filter(cur)
            self.init_carousel()
            self.position_hud(force=True)
            self.update_focused_view(animate_scroll=False)
        else:
            if "grow_mode" in keys:
                self._apply_grow_mode()
            if keys & WINDOW_KEYS:
                self.position_hud(force=True)
                self.update_focused_view(animate_scroll=False)
            elif keys & THUMB_KEYS:
                self.update_focused_view(animate_scroll=False)
            if keys & PAINT_KEYS:
                for slot in self.visible_slots:
                    slot.update()
                self.scroll_container.update()
                self.palette_bar.update()
                for w in self.palette_bar._parts:
                    w.update()
                if self.panel is not None:
                    self.panel.update()
        if self._prefs_open and self.panel is not None:
            self.panel.update()

        # Vista previa: al tocar un ajuste de la animación de entrada se reproduce
        if keys & INTRO_KEYS and self._intro_prepare(full=True):
            self.intro_anim.start()

    def toggle_prefs(self):
        if self._prefs_open:
            self.close_prefs()
        else:
            self.open_prefs()

    def open_prefs(self):
        if self._closing or self._prefs_open:
            return
        if self.panel is None:
            self.panel = PrefsPanel(self)
            self.panel.closeRequested.connect(self.close_prefs)
        self._prefs_open = True
        self.nav_timer.stop()
        self.palette_bar.set_settings_active(True)

        sg = self._screen_obj.availableGeometry()
        margin = SelectorWindow.WINDOW_MARGIN
        vis_top = self._base_y + margin
        vis_bottom = self._base_y + self.height() - margin
        gap = SelectorWindow.PANEL_GAP
        avail = sg.height() - 32 - (vis_bottom - vis_top) - gap
        panel_h = max(280, min(self.panel.preferred_height(), avail))
        panel_w = min(self.panel.preferred_width(), sg.width() - 40)

        # El grupo (carrusel + panel) se centra en la pantalla
        group_h = (vis_bottom - vis_top) + gap + panel_h
        top = max(sg.y() + 8, sg.y() + (sg.height() - group_h) // 2)
        self._animate_shift(top - vis_top)
        px = sg.x() + (sg.width() - panel_w) // 2
        py = top + (vis_bottom - vis_top) + gap
        self.panel.open_at(QRect(px, py, panel_w, panel_h))

    def close_prefs(self):
        if not self._prefs_open:
            return
        self._prefs_open = False
        self.palette_bar.set_settings_active(False)
        if self.panel is not None:
            self.panel.close_animated()
        self._animate_shift(0)
        self.activateWindow()
        self.setFocus()

    def _animate_shift(self, target):
        if self._shift_anim is not None:
            self._shift_anim.stop()
        dur = PREFS.ms(340)
        if dur <= 0:
            self._y_shift = target
            self.move(self.x(), self._base_y + target)
            return
        self._shift_anim = QVariantAnimation(self)
        self._shift_anim.setStartValue(float(self._y_shift))
        self._shift_anim.setEndValue(float(target))
        self._shift_anim.setDuration(dur)
        self._shift_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._shift_anim.valueChanged.connect(self._on_shift_tick)
        self._shift_anim.start()

    def _on_shift_tick(self, v):
        self._y_shift = int(round(float(v)))
        self.move(self.x(), self._base_y + self._y_shift)

    # ------------------------------------------------------------------
    #  Cierre y teclado
    # ------------------------------------------------------------------

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and PREFS.get("close_on_blur"):
            if not self.isActiveWindow():
                QTimer.singleShot(180, self._check_blur)
        super().changeEvent(event)

    def _check_blur(self):
        if (self._closing or self._modal_open or not self.isVisible()
                or (self._shown_clock.isValid() and self._shown_clock.elapsed() < 900)):
            return
        if QApplication.activePopupWidget() is not None:
            return
        active = QApplication.activeWindow()
        if active is self or (self.panel is not None and active is self.panel):
            return
        self.request_close()

    def closeEvent(self, event):
        self.nav_timer.stop()
        self.watch_timer.stop()
        self.scroll_anim.stop()
        self.intro_anim.stop()
        self._apply_timer.stop()
        self.anim_driver.stop()
        PREFS.flush()

        # El análisis de color se cancela (conserva lo ya analizado en su cache)
        if self._group_worker is not None:
            self._group_worker.cancel()

        if self._applying or self._pending_apply:
            self._quit_when_done = True
            event.ignore()
            self.hide()
            return

        self.hide()      # la espera de abajo (p. ej. fin de la transición del fondo) no debe verse
        self.threadpool.clear()
        self.threadpool.waitForDone()
        self.group_pool.clear()
        self.group_pool.waitForDone()
        self.apply_pool.waitForDone(5000)

        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def keyPressEvent(self, event: QKeyEvent):
        if self._closing:
            return
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

        if key == Qt.Key.Key_Escape:
            if self._prefs_open:
                self.close_prefs()
            else:
                self.request_close()

        elif ctrl and key == Qt.Key.Key_Comma:
            self.toggle_prefs()

        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self._toggle_heart_focus()

        elif key == Qt.Key.Key_Up:
            self.nav_timer.stop()
            self._set_heart_focus(False)
            self.focus_layer = 1
            self.palette_bar.set_keyboard_focus(True)
        elif key == Qt.Key.Key_Down:
            self.nav_timer.stop()
            self._set_heart_focus(False)
            self.focus_layer = 0
            self.palette_bar.set_keyboard_focus(False)

        elif self.focus_layer == 0:
            if key in (Qt.Key.Key_Right, Qt.Key.Key_D):
                if not event.isAutoRepeat():
                    self._set_heart_focus(False)
                    self._nav_direction = 1
                    self.advance()
                    self.nav_timer.start()
            elif key in (Qt.Key.Key_Left, Qt.Key.Key_A):
                if not event.isAutoRepeat():
                    self._set_heart_focus(False)
                    self._nav_direction = -1
                    self.retreat()
                    self.nav_timer.start()
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.heart_focus:
                    self._toggle_current_favorite()   # Enter sobre el marcador: favorito sí/no
                else:
                    self.confirm_selection()

        elif self.focus_layer == 1:
            if key in (Qt.Key.Key_Right, Qt.Key.Key_D):
                self.palette_bar.move_focus(1)
            elif key in (Qt.Key.Key_Left, Qt.Key.Key_A):
                self.palette_bar.move_focus(-1)
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.palette_bar.apply_focused()

    def keyReleaseEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_D, Qt.Key.Key_Left, Qt.Key.Key_A):
            self.nav_timer.stop()
        super().keyReleaseEvent(event)

    def _nav_repeat(self):
        if self.focus_layer != 0:
            self.nav_timer.stop()
            return
        if self._nav_direction > 0:
            self.advance()
        elif self._nav_direction < 0:
            self.retreat()
