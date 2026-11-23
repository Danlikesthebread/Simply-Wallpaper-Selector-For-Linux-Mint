import time
import os
import hashlib
import shutil
import subprocess
import threading
from PIL import Image, ImageDraw
from config import CACHE_DIR
from prefs import PREFS
import livewall

try:
    RESAMPLE_MODE = Image.Resampling.BICUBIC
except AttributeError:
    RESAMPLE_MODE = Image.BICUBIC

# .png = miniaturas de versiones anteriores (se siguen podando)
CACHE_EXTS = (".jpg", ".png")


def get_thumbnail_path(image_path, box):
    try:
        mtime = os.path.getmtime(image_path)
    except OSError:
        mtime = 0
    hash_src = f"{image_path}:{mtime}:{box[0]}x{box[1]}"
    hash_name = hashlib.md5(hash_src.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{hash_name}.jpg")


def _extract_frame(video_path, out_path, width=None):
    """Guarda un fotograma del video en out_path (JPEG). True si lo consigue.
    Prueba ffmpeg, luego ffmpegthumbnailer; los GIF se leen con Pillow."""
    tmp = f"{out_path}.{os.getpid()}.{threading.get_ident()}.tmp.jpg"
    try:
        if video_path.lower().endswith(".gif"):
            with Image.open(video_path) as im:
                im.seek(0)
                im = im.convert("RGB")
                if width and im.width > width:
                    im = im.resize((width, int(im.height * width / im.width)))
                im.save(tmp, "JPEG", quality=92)
        elif shutil.which("ffmpeg"):
            vf = ["-vf", f"scale='min(iw,{width})':-2"] if width else []
            # -ss 1 evita el típico primer fotograma negro; si el video es más corto, se reintenta en 0
            for ss in ("1", "0"):
                # -threads 1: con muchas miniaturas a la vez no se disparan todos los núcleos
                subprocess.run(["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-threads", "1",
                                "-ss", ss, "-i", video_path, "-frames:v", "1", "-an", "-sn",
                                *vf, "-q:v", "3", tmp],
                               capture_output=True, timeout=30)
                if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                    break
        elif shutil.which("ffmpegthumbnailer"):
            subprocess.run(["ffmpegthumbnailer", "-i", video_path, "-o", tmp, "-s", str(width or 0),
                            "-t", "10%", "-f"], capture_output=True, timeout=30)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, out_path)
            return True
    except Exception:
        pass
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def video_frame(video_path, full=False):
    """Fotograma del video en caché (tamaño completo hasta 2560 px). None si no se pudo."""
    try:
        mtime = os.path.getmtime(video_path)
    except OSError:
        return None
    name = hashlib.md5(f"{video_path}:{mtime}:frame".encode()).hexdigest()
    out = os.path.join(CACHE_DIR, f"{name}.frame.jpg")
    if os.path.exists(out):
        try:
            os.utime(out, (time.time(), os.path.getmtime(out)))      # el fondo estático de respaldo apunta aquí: que la poda LRU no lo borre
        except OSError:
            pass
        return out
    if _extract_frame(video_path, out, width=2560):
        return out
    return None


def _play_badge(img):
    """Dibuja un pequeño ▶ en la esquina para distinguir los fondos animados."""
    d = ImageDraw.Draw(img, "RGBA")
    r = max(10, img.height // 12)
    cx, cy = img.width // 2, img.height // 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 0, 0, 140), outline=(255, 255, 255, 200), width=2)
    k = r * 0.45
    d.polygon([(cx - k * 0.6, cy - k), (cx - k * 0.6, cy + k), (cx + k, cy)], fill=(255, 255, 255, 230))


def _create_video_thumbnail(video_path, box, thumb_path):
    frame = video_frame(video_path)
    try:
        if frame:
            with Image.open(frame) as img:
                img = img.convert("RGB")
                img.thumbnail(box, RESAMPLE_MODE, reducing_gap=2.0)
        else:                                   # sin ffmpeg: tarjeta oscura con el ▶
            img = Image.new("RGB", box, (24, 22, 36))
        _play_badge(img)
        tmp_path = f"{thumb_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        img.save(tmp_path, "JPEG", quality=90)
        os.replace(tmp_path, thumb_path)
        return thumb_path
    except Exception:
        return video_path


def create_thumbnail(image_path, box=None):
    """Devuelve la ruta de la miniatura (la crea si hace falta). Si no se puede
    generar devuelve la ruta original y el llamador decide qué hacer."""
    box = box or PREFS.thumb_box()
    thumb_path = get_thumbnail_path(image_path, box)
    if os.path.exists(thumb_path):
        try:
            os.utime(thumb_path, None)   # marca de uso para la poda LRU (atime no es fiable)
        except OSError:
            pass
        return thumb_path

    if livewall.is_video(image_path):
        return _create_video_thumbnail(image_path, box, thumb_path)

    tmp_path = f"{thumb_path}.{os.getpid()}.tmp"
    try:
        with Image.open(image_path) as img:
            img.draft("RGB", box)        # JPEG: decodifica ya a escala reducida (mucho más rápido)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail(box, RESAMPLE_MODE, reducing_gap=2.0)
            img.save(tmp_path, "JPEG", quality=90)
        os.replace(tmp_path, thumb_path)   # escritura atómica: nunca se lee a medias
        return thumb_path
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return image_path


def _cache_files():
    out = []
    try:
        with os.scandir(CACHE_DIR) as it:
            for e in it:
                if e.name.endswith(CACHE_EXTS):
                    try:
                        st = e.stat()
                        out.append((e.path, max(st.st_atime, st.st_mtime), st.st_size))
                    except OSError:
                        pass
    except OSError:
        pass
    return out


def cache_size_bytes():
    return sum(f[2] for f in _cache_files())


def clear_cache():
    """Borra todas las miniaturas. Devuelve los bytes liberados."""
    freed = 0
    for path, _, size in _cache_files():
        try:
            os.remove(path)
            freed += size
        except OSError:
            pass
    return freed


def prune_cache_lru():
    """Si la caché supera el límite, borra las menos usadas hasta quedar al 80 %.
    También poda las copias de video optimizadas."""
    try:
        livewall.prune_optimized()       # copias de video optimizadas (límite propio: video_cache_mb)
    except Exception as e:
        print(f"No se pudo podar la caché de videos: {e}")
    try:
        import contour
        contour.prune()
    except Exception:
        pass
    max_bytes = PREFS.get("disk_cache_mb") * 1024 * 1024
    files = _cache_files()
    total = sum(f[2] for f in files)
    if total <= max_bytes:
        return
    target = int(max_bytes * 0.8)
    files.sort(key=lambda x: x[1])
    for path, _, size in files:
        try:
            os.remove(path)
            total -= size
            if total <= target:
                break
        except OSError:
            pass