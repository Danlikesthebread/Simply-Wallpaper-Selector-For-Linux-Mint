import os
import re
import random
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

from PyQt6.QtGui import QImageReader

import livewall

# Semilla fija durante la sesión: el orden aleatorio no cambia al recargar la carpeta
_SESSION_SEED = int(time.time())


@lru_cache(maxsize=1)
def get_supported_extensions():
    exts = set()
    for fmt in QImageReader.supportedImageFormats():
        exts.add(f".{fmt.data().decode().lower()}")
    exts.update(['.jpg', '.jpeg', '.png', '.webp'])
    exts.discard('.gif')            # los GIF se tratan como fondos animados (livewall.VIDEO_EXTS)
    return tuple(exts)


def _natural_key(path):
    name = os.path.basename(path).casefold()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)] + [path]


def get_wallpapers(directory, recursive=False, sort="name", include_videos=True):
    if not directory or not os.path.isdir(directory):
        return []
    valid_exts = get_supported_extensions()
    if include_videos:
        valid_exts = tuple(set(valid_exts) | set(livewall.VIDEO_EXTS))
    found = []

    def scan(path, depth):
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if recursive and depth < 6 and not e.name.startswith("."):
                                scan(e.path, depth + 1)
                        elif e.name.lower().endswith(valid_exts):
                            found.append(e.path)
                    except OSError:
                        continue
        except OSError:
            pass

    scan(directory, 0)

    if sort in ("newest", "oldest"):
        def mtime(p):
            try:
                return os.path.getmtime(p)
            except OSError:
                return 0
        found.sort(key=mtime, reverse=(sort == "newest"))
    elif sort == "random":
        found.sort()
        random.Random(_SESSION_SEED).shuffle(found)
    else:
        found.sort(key=_natural_key)
    return found


def list_subdirs(directory, limit=200):
    """Subcarpetas (para vigilarlas con QFileSystemWatcher)."""
    out = []
    for root, dirs, _ in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for d in dirs:
            out.append(os.path.join(root, d))
            if len(out) >= limit:
                return out
    return out


def _uri(path):
    """URI file:// correctamente codificada (espacios, tildes, #, etc.)."""
    return Path(path).absolute().as_uri()


def get_current_wallpaper():
    """Ruta del fondo actual de Cinnamon (el video si hay un fondo animado) o None."""
    live = livewall.current_video()
    if live:
        return live
    try:
        r = subprocess.run(["gsettings", "get", "org.cinnamon.desktop.background", "picture-uri"],
                           capture_output=True, text=True, timeout=1.0)
        uri = r.stdout.strip().strip("'\"")
        if uri.startswith("file://"):
            return os.path.normpath(unquote(uri[7:]))
    except Exception:
        pass
    return None


def sync_gtk_theme(image_path):
    """Sincronizar el tema GTK con el wallpaper usando el script de Andromeda"""
    script_path = os.path.expanduser("~/.local/bin/sync-andromeda.sh")
    if os.path.exists(script_path):
        try:
            subprocess.run([script_path, image_path], check=False, capture_output=True, timeout=30)
        except Exception as e:
            print(f"Error sincronizando tema: {e}")


def _gs_get(key):
    try:
        r = subprocess.run(["gsettings", "get", "org.cinnamon.desktop.background", key],
                           capture_output=True, text=True, timeout=1.0)
        return r.stdout.strip().strip("'\"")
    except Exception:
        return ""


def _gs_set(key, value):
    try:
        subprocess.run(["gsettings", "set", "org.cinnamon.desktop.background", key, value],
                       check=False, capture_output=True, timeout=5)
    except subprocess.TimeoutExpired:
        pass


def livewall_frame(video_path):
    """Primer fotograma del video a tamaño completo (caché), o None."""
    from thumbnail_cache import video_frame
    return video_frame(video_path, full=True)


def static_image_for(path):
    """Imagen que representa a `path`: la propia imagen o, si es un video/GIF, un fotograma.
    Sirve para pywal, el tema GTK y el fondo de respaldo (pantalla de bloqueo)."""
    if livewall.is_video(path):
        return livewall_frame(path)
    return path


def _prepare_contour(path, video=False):
    """Efecto 'Contorno': el archivo auxiliar debe existir antes de cambiar picture-uri (la extensión lo
    busca en ese instante). Normalmente ya está precalculado (el selector lo hace al resaltar la miniatura); si no, espera WAIT_APPLY s como máximo."""
    if not livewall.pref("wallpaper_fade", True) or livewall.pref("fade_effect", "wipe") not in ("contour", "random"):
        return
    try:
        import contour
        # Espera como mucho unos segundos: si no está listo se aplica con «Barrido» y queda en caché
        contour.prepare(path, livewall.native_size(), wait=contour.WAIT_APPLY, hq=not video)   # video: sin IA pesada (evita tirones al arrancar)
    except Exception as e:
        print(f"Contorno: {e}")


_video_timer = None


def _cancel_video_timer():
    global _video_timer
    if _video_timer is not None:
        _video_timer.cancel()
        _video_timer = None


def _transition_delay():
    """Segundos que dura la transición (con margen): la ventana del video nuevo tapa el fondo de Cinnamon, que es donde
    la extensión dibuja el efecto, así que el video no debe arrancar hasta que termine."""
    if not livewall.pref("wallpaper_fade", True):
        return 0.0
    try:
        ms = int(float(livewall.pref("fade_ms", 1200)))
    except (TypeError, ValueError):
        ms = 1200
    if livewall.pref("fade_effect", "wipe") in ("contour", "random"):
        ms = int(ms * 1.6)                  # el efecto Contorno dura 1.6 veces la duración configurada
    return (ms + 250) / 1000.0


def _start_video_later(path, optimize, video_opts, delay):
    """El retraso lo espera el propio reproductor (proceso aparte): un temporizador dentro de este
    proceso moría si el selector se cerraba al elegir el fondo y el video no arrancaba nunca."""
    try:
        livewall.start_video(path, optimize=optimize, delay=delay, **(video_opts or {}))
    except Exception as e:
        print(f"No se pudo iniciar el fondo animado: {e}")


def apply_wallpaper(image_path, sync_theme=True, stop_live=True, optimize=False, video_opts=None):
    """Fija el fondo. La transición animada la dibuja la extensión de Cinnamon
    (cinnamon-extension/wallpaper-transitions@wallpaper-switcher) al cambiar `picture-uri`.

    Si `image_path` es un video/GIF se lanza como fondo animado (livewall/player.py) y se
    deja un fotograma suyo como fondo estático de respaldo. Al volver a una imagen fija
    se detiene el fondo animado (si stop_live)."""
    if not os.path.exists(image_path):
        return
    _cancel_video_timer()                    # un video pendiente de un cambio anterior ya no debe arrancar

    if livewall.is_video(image_path):
        frame = livewall_frame(image_path)
        if frame:
            _prepare_contour(frame, video=True)
            for key in ("picture-uri", "picture-uri-dark"):
                _gs_set(key, _uri(frame))
        if livewall.pref("wallpaper_fade", True):
            livewall.kill_players()          # el video anterior no debe tapar la transición
        _start_video_later(image_path, optimize, video_opts, _transition_delay() if frame else 0.0)
        if sync_theme and frame:
            sync_gtk_theme(frame)
        return

    if stop_live:
        livewall.stop_video()

    _prepare_contour(image_path)
    file_uri = _uri(image_path)
    for key in ("picture-uri", "picture-uri-dark"):
        _gs_set(key, file_uri)
    if sync_theme:
        sync_gtk_theme(image_path)
