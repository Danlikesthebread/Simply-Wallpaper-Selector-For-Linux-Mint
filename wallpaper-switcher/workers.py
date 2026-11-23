"""Tareas en segundo plano (miniaturas, agrupación por color, aplicar fondo)."""
import os
import json
import threading
import subprocess
import shutil
import hashlib

from PyQt6.QtCore import (pyqtSignal, QRunnable, QObject)
from PyQt6.QtGui import (QImage)

# Importaciones del proyecto
from wallpaper import (apply_wallpaper, sync_gtk_theme, static_image_for)
from thumbnail_cache import create_thumbnail
import livewall
from prefs import PREFS
from common import FAMILY_ORDER
from colors import _read_small_image, analyze_color_family, correct_and_apply_palette

def _lower_thread_priority(level=8):
    """Baja la prioridad del hilo actual (Linux: nice es por hilo): el trabajo de fondo no debe
    competir con el escritorio ni con el navegador."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), level)
    except (AttributeError, OSError):
        pass


class LoaderSignals(QObject):
    thumbLoaded = pyqtSignal(str, QImage)


class ThumbnailWorker(QRunnable):
    """Genera/carga una miniatura en un hilo. Se carga como QImage (seguro en
    hilos); la conversión a QPixmap la hace el hilo de la UI."""
    def __init__(self, filepath, signals):
        super().__init__()
        self.filepath = filepath
        self.signals = signals

    def run(self):
        _lower_thread_priority()
        img = QImage()
        try:
            box = PREFS.thumb_box()
            thumb_path = create_thumbnail(self.filepath, box)
            if thumb_path and thumb_path != self.filepath:
                img = QImage(thumb_path)
            if img.isNull():
                # Formatos que Pillow no lee: se decodifica ya reducido con Qt
                img = _read_small_image(self.filepath, box[0])
        except Exception as e:
            print(f"Error creando miniatura: {e}")
        self.signals.thumbLoaded.emit(self.filepath, img)


class GroupingSignals(QObject):
    groupsReady = pyqtSignal(dict)


class ColorGroupingWorker(QRunnable):
    def __init__(self, filepaths):
        super().__init__()
        self.filepaths = filepaths
        self.signals = GroupingSignals()
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        _lower_thread_priority(15)
        cache_dir = os.path.expanduser("~/.cache/wallpaper_selector")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, "color_groups.json")

        cache_data = {}
        if os.path.exists(cache_file):
            try:
                with open(cache_file) as f:
                    cache_data = json.load(f)
            except Exception:
                pass

        groups = {k: [] for k in FAMILY_ORDER}
        updated = False

        for path in self.filepaths:
            if self._cancel:
                break
            try:
                file_stat = os.stat(path)
            except OSError:
                continue
            file_key = f"{path}_{file_stat.st_mtime}_{file_stat.st_size}"
            file_hash = hashlib.md5(file_key.encode()).hexdigest()

            if file_hash in cache_data:
                family = cache_data[file_hash]
            else:
                family = analyze_color_family(path)
                cache_data[file_hash] = family
                updated = True

            if family in groups:
                groups[family].append(path)

        if updated:
            try:
                with open(cache_file, 'w') as f:
                    json.dump(cache_data, f)
            except Exception:
                pass

        # Si se canceló (el selector se cerró) se conserva lo analizado en el
        # cache pero no se notifica a una ventana que ya no existe.
        if not self._cancel:
            self.signals.groupsReady.emit(groups)


def _video_opts():
    """Preferencias del fondo animado, en el formato que espera livewall.start_video."""
    return {"layer": PREFS.get("video_layer"), "screens": PREFS.get("video_screens"),
            "mute": PREFS.get("video_mute"), "hwdec": PREFS.get("video_hwdec"),
            "compat": PREFS.get("video_compat")}


class WallpaperSetWorker(QRunnable):
    """Cambia el fondo (gsettings + tema GTK) fuera del hilo de la interfaz.
    Corre en un pool de 1 hilo para respetar el orden de las peticiones."""
    def __init__(self, image_path):
        super().__init__()
        self.image_path = image_path

    def run(self):
        try:
            # El tema GTK NO se sincroniza aquí: se hace al final de ApplyWorker, cuando
            # la paleta de pywal ya está generada y corregida.
            apply_wallpaper(self.image_path, sync_theme=False,
                            stop_live=PREFS.get("stop_live_on_image"),
                            optimize=PREFS.get("optimize_videos"),
                            video_opts=_video_opts())
        except Exception as e:
            print(f"Error aplicando fondo: {e}")


class ApplySignals(QObject):
    finished = pyqtSignal()


class ApplyWorker(QRunnable):
    """Genera la paleta con pywal en segundo plano."""
    def __init__(self, image_path):
        super().__init__()
        self.image_path = image_path
        self.signals = ApplySignals()

    def run(self):
        image_path = self.image_path
        is_live = livewall.is_video(image_path)
        try:
            if is_live:
                # pywal y el tema GTK no leen video: se usa un fotograma (el mismo que
                # queda como fondo estático de respaldo)
                image_path = static_image_for(image_path)
                if not image_path:
                    return
            if PREFS.get("pywal_enabled"):
                cmd = ["wal", "-i", image_path, "-q"]
                if PREFS.get("wallpaper_fade") or is_live:
                    cmd.append("-n")   # el fondo ya lo gestiona la transición / el reproductor
                backend = PREFS.get("pywal_backend")
                if backend != "wal":
                    cmd += ["--backend", backend]
                if PREFS.get("pywal_light"):
                    cmd.append("-l")
                subprocess.run(["nice", "-n", "10", *cmd] if shutil.which("nice") else cmd, check=True, timeout=90)
                correct_and_apply_palette(image_path)
        except Exception as e:
            print(f"Error aplicando pywal en background: {e}")
        finally:
            try:
                if PREFS.get("sync_gtk") and image_path:
                    sync_gtk_theme(image_path)   # con la paleta ya definitiva
            except Exception as e:
                print(f"Error sincronizando tema: {e}")
            self.signals.finished.emit()


# ---------------------------------------------------------------------------
#  Motor de animación compartido (hover, selección, fundidos de la barra)
# ---------------------------------------------------------------------------

