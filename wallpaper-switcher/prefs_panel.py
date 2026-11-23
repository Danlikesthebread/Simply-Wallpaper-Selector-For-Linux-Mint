"""Panel de preferencias del selector.

Se abre desde el botón de la barra (o Ctrl+,). Los cambios se aplican en vivo
sobre el carrusel: el panel se coloca justo debajo y el carrusel sube para que
puedas ver el efecto mientras ajustas.
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QSlider, QComboBox, QFrame)
from PyQt6.QtCore import Qt, QRectF, QPointF, QVariantAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont, QFontMetrics, QLinearGradient

from prefs import PREFS, SCHEMA, ACCENTS

TEXT = "#ECECF1"
MUTED = "#8E8E9A"
MARGIN = 18          # margen transparente donde se dibuja la sombra

PAGES = [
    ("Animaciones", [
        ("sec", "General"), "anim_enabled", "anim_speed",
        ("sec", "Entrada y salida"), "intro_style", "intro_stagger", "intro_card_ms", "intro_rise",
        "intro_scale", "anim_filter", "anim_outro",
        ("sec", "Movimiento"), "hover_anim", "nav_duration", "nav_easing",
    ]),
    ("Carrusel", [
        ("sec", "Tarjeta activa"), "grow_mode", "grow_scale", "grow_spacing", "expanded_width",
        ("sec", "Tarjetas"), "card_width", "card_height", "card_slant", "card_gap",
        ("sec", "Estilo"), "accent", "dim_inactive", "border_active", "border_idle_opacity",
        "show_filename", "show_band", "band_opacity",
    ]),
    ("Ventana", [
        ("sec", "Posición"), "monitor", "window_valign", "hud_width_pct",
        ("sec", "Barra"), "bar_position", "bar_shape", "bar_opacity", "show_tooltips",
    ]),
    ("Comportamiento", [
        ("sec", "Navegación"), "infinite", "start_on_current", "wheel_nav", "wheel_invert", "nav_repeat_ms",
        ("sec", "Al aplicar un fondo"), "close_on_apply", "apply_on_navigate", "apply_delay",
        "single_click_apply",
        ("sec", "Transición del fondo de escritorio"), "wallpaper_fade", "fade_effect", "fade_direction", "fade_origin", "fade_ms",
        ("sec", "Ventana"), "close_on_blur",
    ]),
    ("Colores", [
        ("sec", "Filtros"), "color_filters",
        ("sec", "Paleta del sistema"), "pywal_enabled", "pywal_backend", "pywal_light",
        "palette_correct", "sat_scale", "sat_cap", "update_starship", "update_kitty", "sync_gtk",
    ]),
    ("Archivos", [
        ("sec", "Carpeta"), ("action", "folder"), "recursive", "sort_order",
        ("sec", "Fondos animados"), "include_videos", "stop_live_on_image", "video_screens",
        "video_mute", "video_hwdec", "video_compat", "video_layer",
        ("sec", "Optimización de video"), "optimize_videos", "video_max_fps", "optimize_quality",
        "video_pause_covered", "video_pause_battery", "video_cache_mb",
        ("sec", "Rendimiento"), "thumb_quality", "preload", "mem_cache", "disk_cache_mb", "threads",
        ("action", "cache"),
    ]),
]


def _accent():
    return PREFS.accent()


def _qss_scrollbar():
    return ("QScrollBar:vertical { background: transparent; width: 9px; margin: 4px 2px; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.16); border-radius: 3px; min-height: 36px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.28); }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")


# ---------------------------------------------------------------------------
#  Controles
# ---------------------------------------------------------------------------

class Toggle(QWidget):
    valueChanged = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._value = False
        self._pos = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._pos = float(v)
        self.update()

    def set_value(self, v, animate=False):
        v = bool(v)
        self._value = v
        target = 1.0 if v else 0.0
        if animate and PREFS.get("anim_enabled"):
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._pos = target
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.set_value(not self._value, animate=True)
            self.valueChanged.emit(self._value)

    # teclado
    def nudge(self, d, big=False):
        new = d > 0
        if new != self._value:
            self.set_value(new, animate=True)
            self.valueChanged.emit(new)

    def cycle(self):
        self.nudge(-1 if self._value else 1)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.35)
        acc = _accent()
        t = self._pos
        off = QColor(255, 255, 255, 40)
        on = QColor(acc.red(), acc.green(), acc.blue(), 235)
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                     int(off.blue() + (on.blue() - off.blue()) * t), int(off.alpha() + (on.alpha() - off.alpha()) * t))
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 10.5, 10.5)
        p.fillPath(path, col)
        # El tirador es oscuro sobre fondos claros (acento blanco) y claro en el resto
        luminous = (acc.red() * 299 + acc.green() * 587 + acc.blue() * 114) / 1000 > 150
        knob = QColor(20, 20, 26) if (luminous and t > 0.5) else QColor(255, 255, 255)
        r = 8.0
        x = 3 + r + (self.width() - 6 - 2 * r) * t
        p.setBrush(knob)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(x, self.height() / 2.0), r, r)


class Segmented(QWidget):
    valueChanged = pyqtSignal(object)

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self._choices = choices
        self._index = 0
        self._font = QFont()
        self._font.setPixelSize(12)
        self._font.setWeight(QFont.Weight.Medium)
        fm = QFontMetrics(self._font)
        self._widths = [fm.horizontalAdvance(lbl) + 24 for _, lbl in choices]
        self.setFixedSize(sum(self._widths) + 6, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hx = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _seg_x(self, i):
        return 3 + sum(self._widths[:i])

    def _on_anim(self, v):
        self._hx = float(v)
        self.update()

    def set_value(self, value, animate=False):
        for i, (v, _) in enumerate(self._choices):
            if v == value:
                self._index = i
                break
        target = float(self._seg_x(self._index))
        if animate and PREFS.get("anim_enabled"):
            self._anim.stop()
            self._anim.setStartValue(self._hx)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._hx = target
            self.update()

    def _go(self, i):
        if i != self._index:
            self.set_value(self._choices[i][0], animate=True)
            self.valueChanged.emit(self._choices[i][0])

    def nudge(self, d, big=False):
        self._go(max(0, min(len(self._choices) - 1, self._index + (1 if d > 0 else -1))))

    def cycle(self):
        self._go((self._index + 1) % len(self._choices))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        x = event.position().x() - 3
        acc = 0
        for i, w in enumerate(self._widths):
            if x < acc + w:
                if i != self._index:
                    self.set_value(self._choices[i][0], animate=True)
                    self.valueChanged.emit(self._choices[i][0])
                return
            acc += w

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.35)
        track = QPainterPath()
        track.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 9, 9)
        p.fillPath(track, QColor(255, 255, 255, 16))
        acc = _accent()
        hl = QPainterPath()
        hl.addRoundedRect(QRectF(self._hx, 3, self._widths[self._index], self.height() - 6), 7, 7)
        p.fillPath(hl, QColor(acc.red(), acc.green(), acc.blue(), 235))
        luminous = (acc.red() * 299 + acc.green() * 587 + acc.blue() * 114) / 1000 > 150
        p.setFont(self._font)
        for i, (_, label) in enumerate(self._choices):
            sel = (i == self._index)
            if sel:
                p.setPen(QColor(20, 20, 26) if luminous else QColor(255, 255, 255))
            else:
                p.setPen(QColor(200, 200, 210))
            p.drawText(QRectF(self._seg_x(i), 0, self._widths[i], self.height()), Qt.AlignmentFlag.AlignCenter, label)


class Combo(QComboBox):
    """QComboBox con chevron propio y sin cambio por rueda (para no alterar
    valores al desplazar la página)."""
    valueChanged = pyqtSignal(object)

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        for v, lbl in choices:
            self.addItem(lbl, v)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(30)
        fm = QFontMetrics(self.font())
        self.setMinimumWidth(max(fm.horizontalAdvance(lbl) for _, lbl in choices) + 56)
        self.setStyleSheet(
            "QComboBox { background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.10);"
            " border-radius: 9px; padding: 0 30px 0 12px; color: %s; }"
            "QComboBox:hover { background: rgba(255,255,255,0.11); }"
            "QComboBox:disabled { color: rgba(255,255,255,0.35); }"
            "QComboBox::drop-down { border: none; width: 0; }"
            "QComboBox QAbstractItemView { background: #1a1a21; color: %s; border: 1px solid rgba(255,255,255,0.12);"
            " padding: 4px; outline: 0; selection-background-color: rgba(255,255,255,0.14); selection-color: #fff; }"
            % (TEXT, TEXT))
        self.activated.connect(lambda i: self.valueChanged.emit(self.itemData(i)))

    def set_value(self, value, animate=False):
        i = self.findData(value)
        if i >= 0:
            self.setCurrentIndex(i)

    def wheelEvent(self, event):
        event.ignore()

    def _go(self, i):
        if 0 <= i < self.count() and i != self.currentIndex():
            self.setCurrentIndex(i)
            self.valueChanged.emit(self.itemData(i))

    def nudge(self, d, big=False):
        self._go(self.currentIndex() + (1 if d > 0 else -1))

    def cycle(self):
        self._go((self.currentIndex() + 1) % self.count())

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(200, 200, 210, 255 if self.isEnabled() else 90), 1.5,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        cx, cy = self.width() - 16, self.height() / 2.0
        p.drawLine(QPointF(cx - 3.5, cy - 1.5), QPointF(cx, cy + 2))
        p.drawLine(QPointF(cx, cy + 2), QPointF(cx + 3.5, cy - 1.5))


class _NoWheelSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


class SliderControl(QWidget):
    valueChanged = pyqtSignal(object)

    def __init__(self, pref, parent=None):
        super().__init__(parent)
        self._pref = pref
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.slider = _NoWheelSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(pref.lo, pref.hi)
        self.slider.setSingleStep(pref.step)
        self.slider.setPageStep(max(pref.step, (pref.hi - pref.lo) // 10))
        self.slider.setFixedWidth(150)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.valueChanged.connect(self._on_value)
        self.label = QLabel()
        self.label.setFixedWidth(62)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.label.setStyleSheet("color: %s; font-size: 12px; background: transparent;" % MUTED)
        lay.addWidget(self.slider)
        lay.addWidget(self.label)
        self.restyle()

    def restyle(self):
        acc = _accent().name()
        self.slider.setStyleSheet(
            "QSlider { min-height: 20px; max-height: 20px; background: transparent; }"
            "QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.13); border-radius: 2px; margin: 0; }"
            "QSlider::sub-page:horizontal { background: %s; height: 4px; border-radius: 2px; }"
            "QSlider::add-page:horizontal { background: rgba(255,255,255,0.13); height: 4px; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #ffffff; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }"
            "QSlider::sub-page:horizontal:disabled { background: rgba(255,255,255,0.25); }"
            "QSlider::handle:horizontal:disabled { background: rgba(255,255,255,0.4); }" % acc)

    def _fmt(self, v):
        p = self._pref
        return p.special.get(v, f"{v}{p.unit}")

    def _on_value(self, v):
        v = round(v / self._pref.step) * self._pref.step if self._pref.step > 1 else v
        self.label.setText(self._fmt(v))
        self.valueChanged.emit(v)

    def nudge(self, d, big=False):
        step = self._pref.step * (5 if big else 1)
        self.slider.setValue(max(self._pref.lo, min(self._pref.hi, self.slider.value() + (step if d > 0 else -step))))

    def cycle(self):
        pass

    def set_value(self, v, animate=False):
        self.slider.blockSignals(True)
        self.slider.setValue(int(v))
        self.slider.blockSignals(False)
        self.label.setText(self._fmt(int(v)))


class AccentPicker(QWidget):
    valueChanged = pyqtSignal(object)
    D = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = "white"
        self._hover = -1
        self.setMouseTracking(True)
        self.setFixedSize(len(ACCENTS) * (self.D + 8) + 2, self.D + 8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, v, animate=False):
        self._value = v
        self.update()

    def _go(self, i):
        if 0 <= i < len(ACCENTS) and ACCENTS[i][0] != self._value:
            self._value = ACCENTS[i][0]
            self.update()
            self.valueChanged.emit(self._value)

    def _cur(self):
        return next((i for i, a in enumerate(ACCENTS) if a[0] == self._value), 0)

    def nudge(self, d, big=False):
        self._go(self._cur() + (1 if d > 0 else -1))

    def cycle(self):
        self._go((self._cur() + 1) % len(ACCENTS))

    def _index_at(self, x):
        i = int((x - 1) // (self.D + 8))
        return i if 0 <= i < len(ACCENTS) else -1

    def mouseMoveEvent(self, event):
        i = self._index_at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        i = self._index_at(event.position().x())
        if event.button() == Qt.MouseButton.LeftButton and i >= 0 and self.isEnabled():
            self._value = ACCENTS[i][0]
            self.update()
            self.valueChanged.emit(self._value)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, (key, _, hexcol) in enumerate(ACCENTS):
            cx = 1 + i * (self.D + 8) + (self.D + 8) / 2.0
            cy = self.height() / 2.0
            r = 7.5 + (1.0 if i == self._hover else 0.0)
            p.setPen(QPen(QColor(0, 0, 0, 60), 1.0))
            p.setBrush(QColor(hexcol))
            p.drawEllipse(QPointF(cx, cy), r, r)
            if key == self._value:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 235), 1.6))
                p.drawEllipse(QPointF(cx, cy), r + 3.6, r + 3.6)


class ActionButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: %s; border: 1px solid rgba(255,255,255,0.10);"
            " border-radius: 9px; padding: 0 14px; font-size: 12px; font-weight: 500; }"
            "QPushButton:hover { background: rgba(255,255,255,0.14); }"
            "QPushButton:pressed { background: rgba(255,255,255,0.06); }" % TEXT)


# ---------------------------------------------------------------------------
#  Filas
# ---------------------------------------------------------------------------

def _label_pair(title, desc):
    box = QVBoxLayout()
    box.setSpacing(2)
    t = QLabel(title)
    t.setStyleSheet("color: %s; font-size: 13px; background: transparent;" % TEXT)
    box.addWidget(t)
    d = None
    if desc:
        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet("color: %s; font-size: 11px; background: transparent;" % MUTED)
        box.addWidget(d)
    return box, t, d


class FocusRow(QWidget):
    """Base de las filas: dibuja el resalte cuando tienen el foco de teclado."""
    _kb_focused = False

    def set_focused(self, flag):
        if self._kb_focused != flag:
            self._kb_focused = flag
            self.update()

    def paintEvent(self, event):
        if not self._kb_focused:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0, 3, 0, -3), 9, 9)
        p.fillPath(path, QColor(255, 255, 255, 15))
        acc = _accent()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(acc.red(), acc.green(), acc.blue(), 235))
        p.drawRoundedRect(QRectF(0, 12, 3, self.height() - 24), 1.5, 1.5)

    def kb_enabled(self):
        return True

    def adjust(self, d, big=False):
        pass

    def activate(self):
        pass


class PrefRow(FocusRow):
    def __init__(self, pref, parent=None):
        super().__init__(parent)
        self.pref = pref
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 11, 12, 11)
        lay.setSpacing(18)
        box, self._title, self._desc = _label_pair(pref.label, pref.desc)
        lay.addLayout(box, 1)

        if pref.kind == "accent":
            self.control = AccentPicker()
        elif pref.kind == "bool" and pref.choices:
            self.control = Segmented(pref.choices)
        elif pref.kind == "bool":
            self.control = Toggle()
        elif pref.kind == "int":
            self.control = SliderControl(pref)
        elif len(pref.choices) <= 3:
            self.control = Segmented(pref.choices)
        else:
            self.control = Combo(pref.choices)
        lay.addWidget(self.control, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self.control.valueChanged.connect(lambda v: PREFS.set(pref.key, v))
        self.refresh()

    def kb_enabled(self):
        return all(PREFS.get(r) for r in self.pref.requires)

    def adjust(self, d, big=False):
        if self.kb_enabled():
            self.control.nudge(d, big)

    def activate(self):
        if self.kb_enabled():
            self.control.cycle()

    def refresh(self):
        self.control.set_value(PREFS.get(self.pref.key))
        on = all(PREFS.get(r) for r in self.pref.requires)
        self.control.setEnabled(on)
        self._title.setStyleSheet("color: %s; font-size: 13px; background: transparent;" % (TEXT if on else "#5c5c68"))
        if self._desc is not None:
            self._desc.setStyleSheet("color: %s; font-size: 11px; background: transparent;" % (MUTED if on else "#4c4c57"))
        if isinstance(self.control, SliderControl):
            self.control.restyle()


class ActionRow(FocusRow):
    def __init__(self, title, button_text, callback, desc_fn=None, parent=None):
        super().__init__(parent)
        self._desc_fn = desc_fn
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 11, 12, 11)
        lay.setSpacing(18)
        box, self._title, self._desc = _label_pair(title, "…" if desc_fn else "")
        lay.addLayout(box, 1)
        self.button = ActionButton(button_text)
        self.button.clicked.connect(callback)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.refresh()

    def activate(self):
        self.button.click()

    def refresh(self):
        if self._desc_fn and self._desc is not None:
            fm = QFontMetrics(self._desc.font())
            self._desc.setText(fm.elidedText(self._desc_fn(), Qt.TextElideMode.ElideMiddle, 320))


class NavButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._text = text
        self.setCheckable(True)
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False
        self._kb = False

    def set_kb(self, flag):
        if self._kb != flag:
            self._kb = flag
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height())
        if self._kb:
            ring = QPainterPath()
            ring.addRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), 9, 9)
            p.setPen(QPen(QColor(255, 255, 255, 120), 1.2))
            p.drawPath(ring)
        if self.isChecked() or self._hover:
            path = QPainterPath()
            path.addRoundedRect(r, 9, 9)
            p.fillPath(path, QColor(255, 255, 255, 22 if self.isChecked() else 10))
        if self.isChecked():
            acc = _accent()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(acc.red(), acc.green(), acc.blue(), 235))
            p.drawRoundedRect(QRectF(0, 9, 3, self.height() - 18), 1.5, 1.5)
        f = QFont()
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.Medium if self.isChecked() else QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(TEXT) if self.isChecked() else QColor(170, 170, 182))
        p.drawText(QRectF(14, 0, self.width() - 14, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


class CloseButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Cerrar (Esc)")
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, 28, 28), 8, 8)
            p.fillPath(path, QColor(255, 255, 255, 26))
        p.setPen(QPen(QColor(220, 220, 230, 255 if self._hover else 170), 1.5,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(9.5, 9.5), QPointF(18.5, 18.5))
        p.drawLine(QPointF(18.5, 9.5), QPointF(9.5, 18.5))


# ---------------------------------------------------------------------------
#  Panel
# ---------------------------------------------------------------------------

class PrefsPanel(QWidget):
    closeRequested = pyqtSignal()

    W = 780
    H = 470

    def __init__(self, selector):
        super().__init__(selector, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.selector = selector
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._rows = []
        self._action_rows = []
        self._focus_rows = []     # por página: filas navegables con el teclado
        self._zone = "pages"      # "pages" (barra lateral) | "rows" (contenido)
        self._page = 0
        self._ridx = -1
        self._target = None
        self._anim = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(MARGIN + 14, MARGIN + 14, MARGIN + 6, MARGIN + 10)
        outer.setSpacing(14)

        # --- barra lateral ---
        side = QVBoxLayout()
        side.setSpacing(3)
        title = QLabel("Preferencias")
        title.setStyleSheet("color: %s; font-size: 16px; font-weight: 600; background: transparent;"
                            " padding: 2px 0 12px 14px;" % TEXT)
        side.addWidget(title)
        self._nav = []
        for i, (name, _) in enumerate(PAGES):
            b = NavButton(name)
            b.clicked.connect(lambda checked=False, idx=i: (self._set_zone("pages"), self.show_page(idx)))
            side.addWidget(b)
            self._nav.append(b)
        side.addStretch(1)
        hint = QLabel("↑ ↓  moverse\n← →  ajustar (Shift ×5)\nEnter  activar\nTab  cambiar de zona\nEsc  cerrar")
        hint.setStyleSheet("color: %s; font-size: 10px; background: transparent; padding: 0 0 10px 14px;" % MUTED)
        side.addWidget(hint)
        reset = ActionButton("Restablecer todo")
        reset.clicked.connect(self._reset_all)
        side.addWidget(reset)
        side_w = QWidget()
        side_w.setLayout(side)
        side_w.setFixedWidth(170)
        side_w.setStyleSheet("background: transparent;")
        outer.addWidget(side_w)

        div = QFrame()
        div.setFixedWidth(1)
        div.setStyleSheet("background: rgba(255,255,255,0.07);")
        outer.addWidget(div)

        # --- contenido ---
        content = QVBoxLayout()
        content.setSpacing(4)
        head = QHBoxLayout()
        self._page_title = QLabel("")
        self._page_title.setStyleSheet("color: %s; font-size: 15px; font-weight: 600; background: transparent;" % TEXT)
        head.addWidget(self._page_title, 1)
        close = CloseButton()
        close.clicked.connect(self.closeRequested.emit)
        head.addWidget(close)
        content.addLayout(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }"
                                   "QScrollArea > QWidget > QWidget { background: transparent; }" + _qss_scrollbar())
        content.addWidget(self._scroll, 1)
        outer.addLayout(content, 1)

        self._pages = []
        for _, items in PAGES:
            self._focus_rows.append([])
            self._pages.append(self._build_page(items, self._focus_rows[-1]))
        PREFS.changed.connect(self._on_prefs_changed)
        self.show_page(0)

    # ---- construcción ------------------------------------------------

    def _build_page(self, items, focus_list):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 10, 12)
        lay.setSpacing(0)
        first = True
        for it in items:
            if isinstance(it, tuple) and it[0] == "sec":
                lbl = QLabel(it[1])
                lbl.setStyleSheet("color: %s; font-size: 11px; font-weight: 600; background: transparent;"
                                  " padding: %dpx 0 4px 12px;" % (MUTED, 6 if first else 20))
                lay.addWidget(lbl)
                first = False
                continue
            first = False
            if isinstance(it, tuple) and it[0] == "action":
                row = self._make_action(it[1])
                self._action_rows.append(row)
            else:
                row = PrefRow(SCHEMA[it])
                self._rows.append(row)
            focus_list.append(row)
            lay.addWidget(row)
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet("background: rgba(255,255,255,0.055);")
            lay.addWidget(line)
        lay.addStretch(1)
        return page

    def _make_action(self, kind):
        sel = self.selector
        if kind == "folder":
            return ActionRow("Carpeta de fondos", "Cambiar…", self._pick_folder,
                             desc_fn=lambda: sel.wallpaper_dir or "—")
        from thumbnail_cache import cache_size_bytes

        def desc():
            return "Ocupa %.1f MB. Se regenera sola al navegar." % (cache_size_bytes() / 1048576.0)
        return ActionRow("Vaciar caché de miniaturas", "Vaciar", self._clear_cache, desc_fn=desc)

    # ---- acciones ----------------------------------------------------

    def _pick_folder(self):
        if self.selector.pick_wallpaper_dir():
            self._refresh_actions()
        self.activateWindow()
        self.setFocus()

    def _clear_cache(self):
        self.selector.clear_thumb_cache()
        self._refresh_actions()

    def _reset_all(self):
        PREFS.reset()
        self.refresh()

    def _refresh_actions(self):
        for r in self._action_rows:
            r.refresh()

    def refresh(self):
        for r in self._rows:
            r.refresh()
        self._refresh_actions()
        self.update()

    def _on_prefs_changed(self, keys):
        # Habilita/deshabilita filas dependientes y repinta con el nuevo acento
        for r in self._rows:
            if r.pref.key not in keys:
                r.refresh()
        if "accent" in keys:
            for b in self._nav:
                b.update()
            for r in self._rows:
                r.control.update()

    def show_page(self, idx):
        self._clear_row_focus()
        self._page = idx
        for i, b in enumerate(self._nav):
            b.setChecked(i == idx)
        self._update_nav_focus()
        self._page_title.setText(PAGES[idx][0])
        # takeWidget() cede la propiedad: se vuelve a poner el mismo objeto sin destruirlo
        old = self._scroll.takeWidget()
        if old is not None:
            old.setParent(None)
        self._scroll.setWidget(self._pages[idx])
        self._scroll.verticalScrollBar().setValue(0)

    # ---- navegación con teclado -----------------------------------------

    def _update_nav_focus(self):
        for i, b in enumerate(self._nav):
            b.set_kb(self._zone == "pages" and i == self._page)

    def _clear_row_focus(self):
        for row in self._focus_rows[self._page]:
            row.set_focused(False)
        self._ridx = -1

    def _set_zone(self, zone):
        self._zone = zone
        self._update_nav_focus()
        if zone == "pages":
            self._clear_row_focus()
        elif self._ridx < 0:
            self._move_row(1)

    def _focus_row(self, idx):
        rows = self._focus_rows[self._page]
        for r in rows:
            r.set_focused(False)
        self._ridx = idx
        if 0 <= idx < len(rows):
            rows[idx].set_focused(True)
            self._scroll.ensureWidgetVisible(rows[idx], 0, 48)

    def _move_row(self, d):
        """Siguiente fila editable en la dirección d (las desactivadas se saltan)."""
        rows = self._focus_rows[self._page]
        i = self._ridx
        while True:
            i += d
            if i < 0 or i >= len(rows):
                if self._ridx < 0 and rows:   # primera entrada: no hay donde quedarse
                    return
                return
            if rows[i].kb_enabled():
                self._focus_row(i)
                return

    def _edge_row(self, last):
        rows = self._focus_rows[self._page]
        order = range(len(rows) - 1, -1, -1) if last else range(len(rows))
        for i in order:
            if rows[i].kb_enabled():
                self._focus_row(i)
                return

    def _goto_page(self, idx):
        idx = max(0, min(len(PAGES) - 1, idx))
        if idx != self._page:
            self.show_page(idx)

    def _handle_key(self, event):
        key = event.key()
        big = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)

        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self._set_zone("rows" if self._zone == "pages" else "pages")
        elif key == Qt.Key.Key_PageDown:
            self._goto_page(self._page + 1)
        elif key == Qt.Key.Key_PageUp:
            self._goto_page(self._page - 1)
        elif self._zone == "pages":
            if key == Qt.Key.Key_Up:
                self._goto_page(self._page - 1)
            elif key == Qt.Key.Key_Down:
                self._goto_page(self._page + 1)
            elif key == Qt.Key.Key_Right or enter:
                self._set_zone("rows")
            else:
                return False
        else:
            rows = self._focus_rows[self._page]
            if key == Qt.Key.Key_Up:
                self._move_row(-1)
            elif key == Qt.Key.Key_Down:
                self._move_row(1)
            elif key == Qt.Key.Key_Home:
                self._edge_row(False)
            elif key == Qt.Key.Key_End:
                self._edge_row(True)
            elif key == Qt.Key.Key_Left:
                if 0 <= self._ridx < len(rows):
                    rows[self._ridx].adjust(-1, big)
            elif key == Qt.Key.Key_Right:
                if 0 <= self._ridx < len(rows):
                    rows[self._ridx].adjust(1, big)
            elif enter:
                if 0 <= self._ridx < len(rows):
                    rows[self._ridx].activate()
            else:
                return False
        return True

    # ---- tamaño / apertura -------------------------------------------

    def preferred_width(self):
        return self.W + 2 * MARGIN

    def preferred_height(self):
        return self.H + 2 * MARGIN

    def open_at(self, rect):
        self.refresh()
        self._set_zone("pages")
        self._target = rect
        self.setGeometry(rect)
        if self._anim is not None:
            self._anim.stop()
        dur = PREFS.ms(260)
        if dur <= 0:
            self.setWindowOpacity(1.0)
            self.show()
        else:
            self.setWindowOpacity(0.0)
            self.show()
            self._run_anim(0.0, 1.0, dur, QEasingCurve.Type.OutCubic, hide_at_end=False)
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def close_animated(self):
        if not self.isVisible():
            return
        dur = PREFS.ms(180)
        if self._anim is not None:
            self._anim.stop()
        if dur <= 0:
            self.hide()
            return
        self._run_anim(1.0, 0.0, dur, QEasingCurve.Type.InCubic, hide_at_end=True)

    def _run_anim(self, a, b, dur, curve, hide_at_end):
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(a)
        self._anim.setEndValue(b)
        self._anim.setDuration(dur)
        self._anim.setEasingCurve(curve)
        self._anim.valueChanged.connect(self._on_anim)
        if hide_at_end:
            self._anim.finished.connect(self.hide)
        self._anim.start()

    def _on_anim(self, v):
        v = float(v)
        self.setWindowOpacity(max(0.0, min(1.0, v)))
        if self._target is not None:
            self.move(self._target.x(), self._target.y() + int((1.0 - v) * 16))

    # ---- eventos / dibujo ----------------------------------------------

    def keyPressEvent(self, event):
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if event.key() == Qt.Key.Key_Escape or (ctrl and event.key() == Qt.Key.Key_Comma):
            self.closeRequested.emit()
        elif not self._handle_key(event):
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(MARGIN, MARGIN, self.width() - 2 * MARGIN, self.height() - 2 * MARGIN)
        # sombra suave: capas concéntricas
        for i in range(8, 0, -1):
            path = QPainterPath()
            path.addRoundedRect(body.adjusted(-i, -i + 4, i, i + 4), 18 + i, 18 + i)
            p.fillPath(path, QColor(0, 0, 0, 7))
        path = QPainterPath()
        path.addRoundedRect(body, 18, 18)
        bg = QLinearGradient(0, body.top(), 0, body.bottom())
        bg.setColorAt(0.0, QColor(30, 30, 37, 244))
        bg.setColorAt(1.0, QColor(20, 20, 26, 246))
        p.fillPath(path, QBrush(bg))
        border = QLinearGradient(0, body.top(), 0, body.bottom())
        border.setColorAt(0.0, QColor(255, 255, 255, 62))
        border.setColorAt(1.0, QColor(255, 255, 255, 20))
        p.setPen(QPen(QBrush(border), 1.0))
        p.drawPath(path)