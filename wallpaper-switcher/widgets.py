"""Widgets del carrusel: tarjetas, barra de colores y panel HUD."""
import os
import math
from collections import OrderedDict

from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QPushButton)
from PyQt6.QtCore import (Qt, QSize, QRectF, QPointF, pyqtSignal, QObject, QTimer,
                          QElapsedTimer)
from PyQt6.QtGui import (QPainter, QPainterPath, QColor, QPen, QPixmap, QBrush,
                         QLinearGradient, QRadialGradient, QFont,
                         QFontMetrics)

# Importaciones del proyecto
from prefs import PREFS
from common import FAMILY_INFO, FAVORITES_KEY, MODE_KEY, SETTINGS_KEY, _ease_out, _lerp


def _build_bookmark_path(rect):
    """Icono de guardado tipo bookmark: cinta vertical con muesca en V abajo."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

    def pt(px, py):
        return QPointF(x + w * px, y + h * py)

    path = QPainterPath()
    path.moveTo(pt(0.22, 0.04))
    path.lineTo(pt(0.78, 0.04))
    path.lineTo(pt(0.78, 0.97))
    path.lineTo(pt(0.50, 0.73))
    path.lineTo(pt(0.22, 0.97))
    path.closeSubpath()
    return path


class AnimDriver(QObject):
    """Un único temporizador mueve todo lo que esté animándose. Los widgets se
    registran con touch() y deben implementar anim_step(dt_ms) -> bool (sigue
    animando). Así no hay un QVariantAnimation por tarjeta/botón."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = set()
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self._tick)

    def touch(self, widget):
        self._items.add(widget)
        if not self._timer.isActive():
            self._clock.start()
            self._timer.start()

    def stop(self):
        self._timer.stop()
        self._items.clear()

    def _tick(self):
        dt = max(1, min(self._clock.restart(), 50))
        for w in list(self._items):
            try:
                alive = w.anim_step(dt)
            except RuntimeError:       # widget ya destruido
                alive = False
            if not alive:
                self._items.discard(w)
        if not self._items:
            self._timer.stop()


def _step_toward(value, target, dt, duration_ms):
    """Mueve value hacia target a velocidad constante (duration_ms = recorrido 0..1)."""
    if value == target:
        return value
    step = 1.0 if duration_ms <= 0 else dt / float(duration_ms)
    return min(target, value + step) if value < target else max(target, value - step)


# ---------------------------------------------------------------------------
#  Tarjeta
# ---------------------------------------------------------------------------

class ThumbnailItem(QWidget):
    # Los valores reales los fija configure_metrics() a partir de las preferencias
    SLANT = 60
    BASE_W = 200
    BASE_H = 240
    EXPANDED_W = 430
    GAP = 0
    HEART_SIZE = 26
    HEART_MARGIN = 10
    GROW_SCALE = 1.3
    GROW_PUSH = 1.0
    PAD_Y = 40
    INTRO_RISE = 22
    INTRO_SCALE = 0.92
    INTRO_STYLE = "cascade"
    DIM = 0.43
    BORDER_W = 2.0
    IDLE_BORDER_A = 30
    ACCENT = QColor(255, 255, 255)
    SHOW_NAME = False
    SHOW_BAND = True
    BAND_A = 77

    # Modo actual (compartido por todas las tarjetas)
    grow_mode = False

    # Tarjetas en reposo ya renderizadas (LRU), compartidas entre copias del mismo wallpaper
    _card_cache = OrderedDict()
    CARD_CACHE_MAX = 120
    _black_card = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.pixmap = QPixmap()
        self._scaled_cache = QPixmap()
        self.reset()

    def reset(self):
        """Deja la tarjeta como recién creada (permite reutilizar widgets)."""
        self.filepath = ""
        self.is_selected = False
        self.is_blacked_out = False
        self.item_index = 0
        self.virtual_index = 0
        self.base_w = ThumbnailItem.BASE_W
        self.base_h = ThumbnailItem.BASE_H
        self.slot_x = 0
        self.slot_y = ThumbnailItem.PAD_Y
        self.geom_dirty = True
        self.glow_progress = 0.0
        self.glow_target = 0.0
        self.glow_start = 0.0
        self.hover_progress = 0.0
        self.hover_target = 0.0
        self.intro = 1.0
        self.intro_dy = 0
        self.intro_dx = 0
        self.pop = 0.0
        self.flash = 0.0
        self.pixmap = QPixmap()
        self._scaled_cache = QPixmap()
        self._scaled_cache_key = None
        self._is_animating = False
        self._thumb_requested = False
        self.setGeometry(0, self.slot_y, self.base_w, self.base_h)

    # ---- estado ------------------------------------------------------

    def _driver(self):
        return getattr(self.window(), "anim_driver", None)

    def enterEvent(self, event):
        self.hover_target = 1.0
        self._kick_hover()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_target = 0.0
        self._kick_hover()
        super().leaveEvent(event)

    def _kick_hover(self):
        drv = self._driver()
        if drv is not None and PREFS.get("hover_anim") and PREFS.get("anim_enabled"):
            drv.touch(self)
        else:
            self.hover_progress = self.hover_target
            self.update()

    def anim_step(self, dt):
        if self.hover_progress == self.hover_target:
            return False
        self.hover_progress = _step_toward(self.hover_progress, self.hover_target, dt, PREFS.ms(150))
        self.update()
        return self.hover_progress != self.hover_target

    def update_content(self, filepath, is_selected, is_blacked_out):
        """Solo repinta si algo cambió realmente."""
        changed = False

        if self.is_blacked_out != is_blacked_out:
            self.is_blacked_out = is_blacked_out
            changed = True

        if self.filepath != filepath:
            self.filepath = filepath
            self.pixmap = QPixmap()
            self._scaled_cache_key = None
            self._thumb_requested = False
            changed = True

        if self.is_selected != is_selected:
            self.is_selected = is_selected
            self.glow_target = 1.0 if is_selected else 0.0
            if is_selected and not self.is_blacked_out:
                self.raise_()
            changed = True

        if changed:
            self.update()

    def ensure_thumb(self, request_thumb, priority):
        """Pide la miniatura solo si hace falta (carga perezosa)."""
        if self.filepath and not self._thumb_requested and self.pixmap.isNull():
            self._thumb_requested = True
            request_thumb(self, priority)

    def unload(self):
        """Libera la memoria de la miniatura (tarjetas lejos de la zona visible)."""
        if self.pixmap.isNull() and not self._thumb_requested:
            return
        self.pixmap = QPixmap()
        self._scaled_cache = QPixmap()
        self._scaled_cache_key = None
        self._thumb_requested = False

    def set_thumbnail(self, pixmap):
        self.pixmap = pixmap
        self._scaled_cache_key = None
        self.update()

    def set_intro(self, p):
        if p == self.intro:
            return
        self.intro = p
        T = ThumbnailItem
        q = 1.0 - p
        travel = max(T.INTRO_RISE, 10)
        style = T.INTRO_STYLE
        dx = dy = 0
        if style == "cascade":
            dy = T.INTRO_RISE * q                 # suben
        elif style == "sweep":
            dx = -travel * 2.5 * q                # entran desde la izquierda
        elif style == "drop":
            dy = -travel * 1.5 * q                # bajan desde arriba
        # fade y zoom no se desplazan
        dx, dy = int(round(dx)), int(round(dy))
        if dx != self.intro_dx or dy != self.intro_dy:
            self.intro_dx, self.intro_dy = dx, dy
            self.apply_geometry()
        self.update()

    def set_flash(self, v):
        self.flash = v
        self.update()

    def set_pop(self, v):
        self.pop = v
        self.update()

    # ---- geometría según el modo --------------------------------------

    @staticmethod
    def layout_width_for(glow):
        """Ancho que ocupa la tarjeta en la fila. En modo crecer, las vecinas se
        apartan lo justo para que la tarjeta agrandada no las tape (según
        GROW_PUSH); como los bordes son paralelos, la separación entre
        tarjetas se mantiene constante."""
        T = ThumbnailItem
        if T.grow_mode:
            return int(T.BASE_W + (T.GROW_SCALE - 1.0) * (T.BASE_W - T.SLANT) * T.GROW_PUSH * glow)
        return int(T.BASE_W + (T.EXPANDED_W - T.BASE_W) * glow)

    def layout_width(self):
        return ThumbnailItem.layout_width_for(self.glow_progress)

    def apply_geometry(self):
        g = self.glow_progress
        if ThumbnailItem.grow_mode:
            s = 1.0 + (ThumbnailItem.GROW_SCALE - 1.0) * g
            w = int(round(self.base_w * s))
            h = int(round(self.base_h * s))
            x = self.slot_x + (self.layout_width() - w) // 2   # centrada en su hueco
            y = self.slot_y - (h - self.base_h) // 2
        else:
            w = self.layout_width()
            h = self.base_h
            x = self.slot_x
            y = self.slot_y
        x += self.intro_dx
        y += self.intro_dy
        if (x, y, w, h) != (self.x(), self.y(), self.width(), self.height()):
            self.setGeometry(x, y, w, h)

    def set_glow(self, value):
        """Aplica un progreso de resalte (0..1) y la geometría correspondiente."""
        self.glow_progress = value
        self.apply_geometry()
        self.update()

    def set_active_instant(self, is_active):
        self._is_animating = False
        self.is_selected = is_active
        self.glow_target = 1.0 if is_active else 0.0
        if is_active and not self.is_blacked_out:
            self.raise_()
        self.set_glow(self.glow_target)

    def _slant(self):
        if ThumbnailItem.grow_mode:
            return ThumbnailItem.SLANT * (self.height() / self.base_h)
        return ThumbnailItem.SLANT

    def _heart_rect(self):
        size = ThumbnailItem.HEART_SIZE
        margin = ThumbnailItem.HEART_MARGIN
        return QRectF(self.width() - size - margin, margin, size, size)

    # ---- mouse -------------------------------------------------------

    def _local_card_path(self):
        return ThumbnailItem._card_path(self.width(), self.height(), self._slant())

    def handle_click(self, local_pos, double=False):
        if not self.filepath or self.is_blacked_out:
            return
        win = self.window()
        on_heart = self.is_selected and self._heart_rect().contains(local_pos)

        if double:
            if not on_heart:
                win.confirm_selection()
            return

        # El clic nunca debe dejar el foco en otro widget.
        win.focus_carousel()
        if on_heart:
            win.toggle_favorite(self.filepath)
            return
        if self.is_selected and PREFS.get("single_click_apply"):
            win.confirm_selection()
            return
        if win.active_filter is None:
            win.set_index_by_virtual_idx(self.virtual_index)
        else:
            win.set_index(self.item_index)

    def mousePressEvent(self, event):
        # Solo cuenta el clic dentro de la forma de la tarjeta; fuera de ella
        # (esquinas transparentes del rectángulo) se deja pasar al contenedor,
        # que busca la tarjeta que realmente está debajo.
        if event.button() == Qt.MouseButton.LeftButton and self._local_card_path().contains(event.position()):
            self.handle_click(event.position(), double=False)
        else:
            event.ignore()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._local_card_path().contains(event.position()):
            self.handle_click(event.position(), double=True)
        else:
            event.ignore()

    # ---- render ------------------------------------------------------

    @staticmethod
    def _card_path(w, h, slant):
        path = QPainterPath()
        path.moveTo(slant, 0)
        path.lineTo(w, 0)
        path.lineTo(w - slant, h)
        path.lineTo(0, h)
        path.closeSubpath()
        return path

    @staticmethod
    def _inset_path(w, h, slant, d):
        """El mismo paralelogramo desplazado hacia dentro `d` px (para que el
        borde se dibuje completo y no quede recortado por el widget)."""
        k = math.sqrt(1.0 + (slant / h) ** 2) if h else 1.0
        dx = d * k
        r = d / h if h else 0.0
        path = QPainterPath()
        path.moveTo(slant * (1 - r) + dx, d)
        path.lineTo(w - slant * r - dx, d)
        path.lineTo(w - slant * (1 - r) - dx, h - d)
        path.lineTo(slant * r + dx, h - d)
        path.closeSubpath()
        return path

    @staticmethod
    def _paint_card(painter, w, h, slant, glow, hover, scaled_pix, ratio=1.0, flash=0.0, caption=None):
        T = ThumbnailItem
        path = T._card_path(w, h, slant)

        painter.save()
        painter.setClipPath(path)
        if ratio == 1.0:
            px_x = int((w - scaled_pix.width()) / 2)
            px_y = int((h - scaled_pix.height()) / 2)
            painter.drawPixmap(px_x, px_y, scaled_pix)
        else:
            tw = scaled_pix.width() * ratio
            th = scaled_pix.height() * ratio
            painter.drawPixmap(QRectF((w - tw) / 2, (h - th) / 2, tw, th),
                               scaled_pix, QRectF(scaled_pix.rect()))

        dim_a = int(255 * T.DIM * (1.0 - glow) * (1.0 - 0.55 * hover))
        if dim_a > 0:
            painter.fillPath(path, QBrush(QColor(0, 0, 0, dim_a)))

        if caption and glow > 0.02:
            gh = min(h * 0.38, 70.0)
            grad = QLinearGradient(0, h - gh, 0, h)
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(1.0, QColor(0, 0, 0, int(185 * glow)))
            painter.fillRect(QRectF(0, h - gh, w, gh), QBrush(grad))
            font = QFont(painter.font())
            font.setPixelSize(max(11, int(12 * h / T.BASE_H)))
            font.setWeight(QFont.Weight.Medium)
            painter.setFont(font)
            avail = max(10, int(w - slant - 28))
            text = QFontMetrics(font).elidedText(caption, Qt.TextElideMode.ElideMiddle, avail)
            painter.setPen(QColor(255, 255, 255, int(240 * glow)))
            painter.drawText(QRectF(12, h - 34, w - slant - 24, 22), Qt.AlignmentFlag.AlignCenter, text)

        if flash > 0.0:
            painter.fillPath(path, QBrush(QColor(255, 255, 255, int(110 * flash))))
        painter.restore()

        # Borde: de 1 px tenue (reposo) a BORDER_W con el color de acento (activa)
        bw = 1.0 + (T.BORDER_W - 1.0) * glow
        if bw >= 0.5:
            idle_a = T.IDLE_BORDER_A + (115 - T.IDLE_BORDER_A) * hover
            alpha = idle_a + (200 - idle_a) * glow
            acc = T.ACCENT
            color = QColor(int(_lerp(255, acc.red(), glow)), int(_lerp(255, acc.green(), glow)),
                           int(_lerp(255, acc.blue(), glow)), int(min(255, alpha + 55 * flash)))
            painter.setPen(QPen(color, bw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(T._inset_path(w, h, slant, bw / 2.0))

    def _scaled_for(self, w, h):
        """Devuelve (pixmap escalado, ratio de dibujo). Se escala UNA vez por
        tamaño y se dibuja reducido según la animación (sin reescalar por cuadro)."""
        T = ThumbnailItem
        if T.grow_mode:
            gh = int(round(self.base_h * T.GROW_SCALE))
            gw = int(round(self.base_w * T.GROW_SCALE))
            key = ("grow", gw, gh)
            if self._scaled_cache_key != key:
                self._scaled_cache = self.pixmap.scaled(
                    QSize(max(gw, int(gh * 1.8)), gh),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                self._scaled_cache_key = key
            return self._scaled_cache, h / gh

        tw = max(T.EXPANDED_W, int(self.base_h * 1.8))
        key = ("exp", tw, self.base_h)
        if self._scaled_cache_key != key:
            self._scaled_cache = self.pixmap.scaled(
                QSize(tw, self.base_h), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            self._scaled_cache_key = key
        return self._scaled_cache, 1.0

    def _idle_card(self):
        cache = ThumbnailItem._card_cache
        card = cache.get(self.filepath)
        if card is not None:
            cache.move_to_end(self.filepath)
            return card
        w, h = self.base_w, self.base_h
        card = QPixmap(w, h)
        card.fill(Qt.GlobalColor.transparent)
        p = QPainter(card)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scaled = self.pixmap.scaled(
            QSize(max(w, int(h * 1.8)), h),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        ThumbnailItem._paint_card(p, w, h, ThumbnailItem.SLANT, 0.0, 0.0, scaled)
        p.end()
        cache[self.filepath] = card
        while len(cache) > ThumbnailItem.CARD_CACHE_MAX:
            cache.popitem(last=False)
        return card

    @staticmethod
    def _get_black_card(w, h):
        card = ThumbnailItem._black_card
        if card is None or card.width() != w or card.height() != h:
            card = QPixmap(w, h)
            card.fill(Qt.GlobalColor.transparent)
            p = QPainter(card)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillPath(ThumbnailItem._card_path(w, h, ThumbnailItem.SLANT), QBrush(Qt.GlobalColor.black))
            p.end()
            ThumbnailItem._black_card = card
        return card

    def paintEvent(self, event):
        if not self.filepath or self.pixmap.isNull() or self.intro <= 0.0:
            return

        painter = QPainter(self)
        w = self.width()
        h = self.height()

        if self.intro < 1.0:
            style = ThumbnailItem.INTRO_STYLE
            s0 = 1.0 if style in ("fade", "sweep", "drop") else (0.5 if style == "zoom" else ThumbnailItem.INTRO_SCALE)
            s = s0 + (1.0 - s0) * self.intro
            painter.setOpacity(self.intro)
            painter.translate(w / 2.0, h / 2.0)
            painter.scale(s, s)
            painter.translate(-w / 2.0, -h / 2.0)

        if self.is_blacked_out:
            painter.drawPixmap(0, 0, ThumbnailItem._get_black_card(w, h))
            return

        # Camino rápido: tarjeta en reposo (la inmensa mayoría)
        if (not self.is_selected and self.glow_progress == 0.0 and self.hover_progress == 0.0
                and self.flash == 0.0 and w == self.base_w and h == self.base_h):
            painter.drawPixmap(0, 0, self._idle_card())
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scaled, ratio = self._scaled_for(w, h)
        hv = 1.0 - (1.0 - self.hover_progress) ** 2
        caption = None
        if ThumbnailItem.SHOW_NAME and self.is_selected:
            caption = os.path.splitext(os.path.basename(self.filepath))[0]
        ThumbnailItem._paint_card(painter, w, h, self._slant(), self.glow_progress, hv, scaled,
                                  ratio, self.flash, caption)
        self._draw_favorite_heart(painter)

    def _draw_favorite_heart(self, painter):
        if not self.is_selected or self.glow_progress <= 0.0:
            return
        win = self.window()
        is_fav = hasattr(win, "favorites") and self.filepath in win.favorites
        rect = self._heart_rect()
        acc = ThumbnailItem.ACCENT

        painter.save()
        painter.setOpacity(painter.opacity() * self.glow_progress)
        if self.pop > 0.0:
            c = rect.center()
            painter.translate(c)
            painter.scale(1.0 + self.pop, 1.0 + self.pop)
            painter.translate(-c)
        heart_path = _build_bookmark_path(rect)

        if is_fav:
            painter.setBrush(QBrush(QColor(acc.red(), acc.green(), acc.blue(), 245)))
            painter.setPen(QPen(QColor(255, 255, 255, 210), 1.2, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        else:
            painter.setBrush(QBrush(QColor(0, 0, 0, 80)))
            painter.setPen(QPen(QColor(255, 255, 255, 215), 1.4, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(heart_path)

        # Foco de teclado (Tab): anillo punteado alrededor del marcador
        if getattr(win, "heart_focus", False):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 255), 1.6, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect.adjusted(-5, -5, 5, 5), 8, 8)
        painter.restore()


class StripContainer(QWidget):
    """Contenedor de las tarjetas: pinta la franja oscura de fondo y resuelve
    los clics que caen fuera de la forma de una tarjeta."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.band_progress = 1.0

    def set_band_progress(self, v):
        if v != self.band_progress:
            self.band_progress = v
            self.update()

    def paintEvent(self, event):
        T = ThumbnailItem
        bp = self.band_progress
        if not T.SHOW_BAND or bp <= 0.0 or T.BAND_A <= 0:
            return
        hh = T.BASE_H * bp           # la franja "se abre" desde el centro
        y = T.PAD_Y + (T.BASE_H - hh) / 2.0
        p = QPainter(self)
        p.fillRect(QRectF(0, y, self.width(), hh), QColor(0, 0, 0, int(T.BAND_A * min(1.0, bp * 1.5))))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().click_at(event.position(), double=False)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().click_at(event.position(), double=True)


# ---------------------------------------------------------------------------
#  Barra superior
# ---------------------------------------------------------------------------

class BarButton(QPushButton):
    """Botón de la barra. Tipos: color | all | fav | mode | settings.
    Todo se dibuja a mano con el mismo trazo (1.5 px, extremos redondeados)
    para que los iconos tengan el mismo peso visual."""
    SIZE = 30

    def __init__(self, kind, color_hex="", label="", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.color_hex = color_hex
        self._label = label
        self.is_active = False
        self.is_keyboard_focused = False
        self.hover_progress = 0.0
        self.hover_target = 0.0
        self.active_progress = 0.0
        self.active_target = 0.0
        self.intro = 1.0
        self.intro_target = 1.0
        self.setFixedSize(BarButton.SIZE, BarButton.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.set_tooltips(PREFS.get("show_tooltips"))

    def set_tooltips(self, flag):
        self.setToolTip(self._label if (flag and self._label) else "")

    # ---- animación ---------------------------------------------------

    def _kick(self):
        drv = getattr(self.window(), "anim_driver", None)
        if drv is not None and PREFS.get("anim_enabled") and self.isVisible():
            drv.touch(self)
        else:
            self.hover_progress = self.hover_target
            self.active_progress = self.active_target
            self.intro = self.intro_target
            self.update()

    def fade_in(self):
        self.intro = 0.0
        self.intro_target = 1.0
        self._kick()

    def anim_step(self, dt):
        self.hover_progress = _step_toward(self.hover_progress, self.hover_target, dt, PREFS.ms(150))
        self.active_progress = _step_toward(self.active_progress, self.active_target, dt, PREFS.ms(260))
        self.intro = _step_toward(self.intro, self.intro_target, dt, PREFS.ms(280))
        self.update()
        return (self.hover_progress != self.hover_target or self.active_progress != self.active_target
                or self.intro != self.intro_target)

    def enterEvent(self, event):
        self.hover_target = 1.0
        self._kick()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_target = 0.0
        self._kick()
        super().leaveEvent(event)

    def set_active(self, active):
        if self.is_active != active:
            self.is_active = active
            self.active_target = 1.0 if active else 0.0
            self._kick()

    def set_keyboard_focused(self, focused):
        if self.is_keyboard_focused != focused:
            self.is_keyboard_focused = focused
            self.update()

    # ---- dibujo ------------------------------------------------------

    @staticmethod
    def _chip_radius():
        return 6 if PREFS.get("bar_shape") == "rect" else 9

    def paintEvent(self, event):
        if self.intro <= 0.002:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        S = float(self.width())
        hv = 1.0 - (1.0 - self.hover_progress) ** 2
        ac = 1.0 - (1.0 - self.active_progress) ** 2
        cx = cy = S / 2.0

        p.setOpacity(self.intro)
        sc = (0.8 + 0.2 * self.intro) * (1.0 + 0.06 * hv) * (0.9 if self.isDown() else 1.0)
        p.translate(cx, cy)
        p.scale(sc, sc)
        p.translate(-cx, -cy)

        if self.kind == "color":
            self._paint_dot(p, cx, cy, ac)
        else:
            chip_a = int(255 * (0.075 * hv + 0.14 * ac))
            if chip_a > 0:
                chip = QPainterPath()
                chip.addRoundedRect(QRectF(1, 1, S - 2, S - 2), self._chip_radius(), self._chip_radius())
                p.fillPath(chip, QColor(255, 255, 255, chip_a))
            t = max(hv * 0.8, ac)
            col = QColor(int(172 + 83 * t), int(175 + 80 * t), int(182 + 73 * t), int(205 + 50 * t))
            pen = QPen(col, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if self.kind == "mode":
                self._paint_mode(p, cx, cy, ac, col)
            elif self.kind == "all":
                self._paint_home(p, cx, cy, ac, col)
            elif self.kind == "fav":
                self._paint_bookmark(p, cx, cy, ac, col)
            elif self.kind == "settings":
                self._paint_sliders(p, cx, cy, max(hv, ac))

        if self.is_keyboard_focused:
            p.setOpacity(1.0)
            p.setPen(QPen(QColor(255, 255, 255, 215), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(0.7, 0.7, S - 1.4, S - 1.4), self._chip_radius() + 0.5, self._chip_radius() + 0.5)

    def _paint_dot(self, p, cx, cy, ac):
        base = QColor(self.color_hex)
        r = 7.2
        grad = QRadialGradient(QPointF(cx - 2.4, cy - 3.0), r * 1.9)
        grad.setColorAt(0.0, base.lighter(128))
        grad.setColorAt(1.0, base.darker(112))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(0, 0, 0, 70), 1.0))
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1.0))
        p.drawEllipse(QPointF(cx, cy), r - 0.6, r - 0.6)
        if ac > 0.01:
            acc = ThumbnailItem.ACCENT
            ring = _lerp(r + 0.4, r + 3.4, ac)
            p.setPen(QPen(QColor(acc.red(), acc.green(), acc.blue(), int(240 * ac)), 1.6))
            p.drawEllipse(QPointF(cx, cy), ring, ring)

    def _paint_mode(self, p, cx, cy, ac, col):
        """Cuatro cuadrados que se funden en uno solo (contorno)."""
        size = 14.0
        x0, y0 = cx - size / 2, cy - size / 2
        gap = 2.4
        cell = (size - gap) / 2.0
        fill_a = int(col.alpha() * (1.0 - ac))
        if fill_a > 0:
            for r in range(2):
                for c in range(2):
                    rx = _lerp(x0 + c * (cell + gap), x0, ac)
                    ry = _lerp(y0 + r * (cell + gap), y0, ac)
                    rw = _lerp(cell, size, ac)
                    rad = _lerp(1.8, 3.2, ac)
                    path = QPainterPath()
                    path.addRoundedRect(QRectF(rx, ry, rw, rw), rad, rad)
                    p.fillPath(path, QColor(col.red(), col.green(), col.blue(), fill_a))
        if ac > 0.01:
            line = QColor(col.red(), col.green(), col.blue(), int(col.alpha() * ac))
            p.setPen(QPen(line, 1.5))
            p.drawRoundedRect(QRectF(x0 + 0.75, y0 + 0.75, size - 1.5, size - 1.5), 2.8, 2.8)

    def _paint_home(self, p, cx, cy, ac, col):
        x0, y0 = cx - 8, cy - 8
        if ac > 0.01:
            fill = QPainterPath()
            fill.moveTo(x0 + 3.2, y0 + 6.4)
            fill.lineTo(x0 + 8, y0 + 2.2)
            fill.lineTo(x0 + 12.8, y0 + 6.4)
            fill.lineTo(x0 + 12.8, y0 + 14.2)
            fill.lineTo(x0 + 3.2, y0 + 14.2)
            fill.closeSubpath()
            p.fillPath(fill, QColor(255, 255, 255, int(70 * ac)))
        roof = QPainterPath()
        roof.moveTo(x0 + 1.4, y0 + 7.4)
        roof.lineTo(x0 + 8, y0 + 1.6)
        roof.lineTo(x0 + 14.6, y0 + 7.4)
        p.drawPath(roof)
        body = QPainterPath()
        body.moveTo(x0 + 3.2, y0 + 6.2)
        body.lineTo(x0 + 3.2, y0 + 14.2)
        body.lineTo(x0 + 12.8, y0 + 14.2)
        body.lineTo(x0 + 12.8, y0 + 6.2)
        p.drawPath(body)
        door = QPainterPath()
        door.moveTo(x0 + 6.6, y0 + 14.2)
        door.lineTo(x0 + 6.6, y0 + 10.0)
        door.lineTo(x0 + 9.4, y0 + 10.0)
        door.lineTo(x0 + 9.4, y0 + 14.2)
        p.drawPath(door)

    def _paint_bookmark(self, p, cx, cy, ac, col):
        path = _build_bookmark_path(QRectF(cx - 8.5, cy - 8.5, 17, 17))
        if ac > 0.01:
            p.fillPath(path, QColor(col.red(), col.green(), col.blue(), int(235 * ac)))
        p.drawPath(path)

    def _paint_sliders(self, p, cx, cy, m):
        """Dos controles deslizantes; los tiradores se cruzan al pasar el ratón."""
        x0, y0 = cx - 8, cy - 8
        y1, y2 = y0 + 4.6, y0 + 11.4
        k1 = _lerp(x0 + 4.8, x0 + 11.2, m)
        k2 = _lerp(x0 + 11.2, x0 + 4.8, m)
        p.drawLine(QPointF(x0 + 1.4, y1), QPointF(x0 + 14.6, y1))
        p.drawLine(QPointF(x0 + 1.4, y2), QPointF(x0 + 14.6, y2))
        p.setBrush(QColor(22, 22, 27, 255))
        p.drawEllipse(QPointF(k1, y1), 2.4, 2.4)
        p.drawEllipse(QPointF(k2, y2), 2.4, 2.4)


class BarSeparator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(9, BarButton.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.intro = 1.0
        self.intro_target = 1.0

    def paintEvent(self, event):
        if self.intro <= 0.002:
            return
        p = QPainter(self)
        p.setOpacity(self.intro)
        p.fillRect(QRectF(self.width() / 2.0 - 0.5, (self.height() - 14) / 2.0, 1, 14), QColor(255, 255, 255, 36))


class ColorFilterBar(QWidget):
    filterChanged = pyqtSignal(object)
    modeToggled = pyqtSignal()
    settingsRequested = pyqtSignal()

    HEIGHT = 44
    SPACING = 2
    MARGIN = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.family_swatches = {}
        self.active_key = None
        self.intro = 1.0

        self.has_keyboard_focus = False
        self.focus_keys_order = [MODE_KEY, None, FAVORITES_KEY, SETTINGS_KEY]
        self.current_focus_idx = 1

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(ColorFilterBar.MARGIN, 0, ColorFilterBar.MARGIN, 0)
        self.layout.setSpacing(ColorFilterBar.SPACING)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Orden: [modo] | [todos] [favoritos] [colores...] | [preferencias]
        self.mode_btn = BarButton("mode", label="Modo: expandir / crecer")
        self.mode_btn.clicked.connect(self._on_mode_clicked)
        self.layout.addWidget(self.mode_btn)

        self.sep1 = BarSeparator()
        self.layout.addWidget(self.sep1)

        self.all_swatch = BarButton("all", label="Todos")
        self.all_swatch.clicked.connect(lambda: self._on_swatch_clicked(None))
        self.all_swatch.set_active(True)
        self.layout.addWidget(self.all_swatch)

        self.heart_swatch = BarButton("fav", label="Favoritos")
        self.heart_swatch.clicked.connect(lambda: self._on_swatch_clicked(FAVORITES_KEY))
        self.layout.addWidget(self.heart_swatch)

        self.sep2 = BarSeparator()
        self.layout.addWidget(self.sep2)

        self.settings_btn = BarButton("settings", label="Preferencias")
        self.settings_btn.clicked.connect(self._on_settings_clicked)
        self.layout.addWidget(self.settings_btn)

        self._parts = []
        self._rebuild_parts()
        self.setFixedHeight(ColorFilterBar.HEIGHT)
        self._update_bar_width()

    def _rebuild_parts(self):
        self._parts = [self.layout.itemAt(i).widget() for i in range(self.layout.count())]

    def set_groups(self, ordered_keys):
        for key, swatch in list(self.family_swatches.items()):
            self.layout.removeWidget(swatch)
            swatch.setParent(None)
            swatch.deleteLater()
        self.family_swatches = {}

        self.focus_keys_order = [MODE_KEY, None, FAVORITES_KEY, *ordered_keys, SETTINGS_KEY]
        self.current_focus_idx = 1

        # Los colores van entre "favoritos" y el segundo separador
        for i, key in enumerate(ordered_keys):
            info = FAMILY_INFO[key]
            swatch = BarButton("color", info["color"], info["label"])
            swatch.clicked.connect(lambda checked=False, k=key: self._on_swatch_clicked(k))
            self.layout.insertWidget(4 + i, swatch)
            swatch.set_active(key == self.active_key)
            swatch.show()
            swatch.fade_in()
            self.family_swatches[key] = swatch

        self._rebuild_parts()
        self._update_bar_width()
        self._update_focus_visuals()

    def set_tooltips(self, flag):
        for w in self._parts:
            if isinstance(w, BarButton):
                w.set_tooltips(flag)

    def _update_bar_width(self):
        n = self.layout.count()
        if n > 0:
            widths = sum(self.layout.itemAt(i).widget().width() for i in range(n))
            self.setFixedWidth(2 * ColorFilterBar.MARGIN + widths + (n - 1) * ColorFilterBar.SPACING)

    # ---- animación de entrada ------------------------------------------

    def prepare_intro(self):
        self.intro = 0.0
        for w in self._parts:
            w.intro = 0.0
            w.update()
        self.update()

    def apply_intro(self, t, start, dur, stagger):
        """t, start, dur y stagger en ms. Devuelve el progreso (0..1) de la barra."""
        dur = max(1, dur)
        self.intro = _ease_out((t - start) / dur)
        for i, w in enumerate(self._parts):
            w.intro = _ease_out((t - start - 50 - i * stagger) / dur)
            w.update()
        self.update()
        return self.intro

    def set_progress(self, p):
        """Fija de golpe la visibilidad de la barra y sus botones (salida)."""
        self.intro = p
        for w in self._parts:
            w.intro = p
            w.update()
        self.update()

    def finish_intro(self):
        self.intro = 1.0
        for w in self._parts:
            w.intro = 1.0
            w.intro_target = 1.0
        self.update()
        for w in self._parts:
            w.update()

    # ---- estado ------------------------------------------------------

    def set_active_key(self, key):
        self.active_key = key
        self.all_swatch.set_active(key is None)
        self.heart_swatch.set_active(key == FAVORITES_KEY)
        for k, swatch in self.family_swatches.items():
            swatch.set_active(k == key)

    def set_mode_active(self, active):
        self.mode_btn.set_active(active)

    def set_settings_active(self, active):
        self.settings_btn.set_active(active)

    def _swatch_for_key(self, key):
        if key is None:
            return self.all_swatch
        if key == FAVORITES_KEY:
            return self.heart_swatch
        if key == MODE_KEY:
            return self.mode_btn
        if key == SETTINGS_KEY:
            return self.settings_btn
        return self.family_swatches[key]

    def set_keyboard_focus(self, focused):
        self.has_keyboard_focus = focused
        self._update_focus_visuals()

    def _update_focus_visuals(self):
        for i, key in enumerate(self.focus_keys_order):
            is_focused = self.has_keyboard_focus and (i == self.current_focus_idx)
            self._swatch_for_key(key).set_keyboard_focused(is_focused)

    def move_focus(self, step):
        if not self.focus_keys_order:
            return
        self.current_focus_idx = (self.current_focus_idx + step) % len(self.focus_keys_order)
        self._update_focus_visuals()

    def apply_focused(self):
        if 0 <= self.current_focus_idx < len(self.focus_keys_order):
            key = self.focus_keys_order[self.current_focus_idx]
            if key == MODE_KEY:
                self._on_mode_clicked()
            elif key == SETTINGS_KEY:
                self._on_settings_clicked()
            else:
                self._on_swatch_clicked(key)

    def _focus_key(self, key):
        try:
            self.current_focus_idx = self.focus_keys_order.index(key)
            self._update_focus_visuals()
        except ValueError:
            pass

    def _on_mode_clicked(self):
        self._focus_key(MODE_KEY)
        self.modeToggled.emit()
        if not self.has_keyboard_focus:
            self.window().setFocus()

    def _on_settings_clicked(self):
        self._focus_key(SETTINGS_KEY)
        if not self.has_keyboard_focus:
            self.window().setFocus()
        self.settingsRequested.emit()

    def _on_swatch_clicked(self, key):
        if key is not None and key == self.active_key:
            key = None
        self.set_active_key(key)
        self._focus_key(key)
        self.filterChanged.emit(key)

        if not self.has_keyboard_focus:
            self.window().setFocus()

    def paintEvent(self, event):
        if self.intro <= 0.002:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        rect = QRectF(0.5, 0.5, w - 1, h - 1)
        shape = PREFS.get("bar_shape")
        radius = {"rect": 7.0, "round": 15.0}.get(shape, rect.height() / 2.0)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        a = PREFS.get("bar_opacity") / 100.0 * self.intro
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, QColor(36, 36, 42, int(255 * a * 0.95)))
        bg.setColorAt(1.0, QColor(14, 14, 18, int(255 * a)))
        p.fillPath(path, QBrush(bg))

        border = QLinearGradient(0, 0, 0, h)
        border.setColorAt(0.0, QColor(255, 255, 255, int(78 * self.intro)))
        border.setColorAt(1.0, QColor(255, 255, 255, int(24 * self.intro)))
        p.setPen(QPen(QBrush(border), 1.0))
        p.drawPath(path)


class HUDPanel(QWidget):
    # Alto del carrusel: las tarjetas + espacio arriba y abajo (lo fija configure_metrics)
    HEIGHT = 320

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(HUDPanel.HEIGHT)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)


# ---------------------------------------------------------------------------
