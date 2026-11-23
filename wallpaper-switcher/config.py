import os
import json

CONFIG_DIR = os.path.expanduser("~/.config/wallpaper-switcher")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
FAVORITES_FILE = os.path.join(CONFIG_DIR, "favorites.json")
CACHE_DIR = os.path.expanduser("~/.cache/wallpaper-switcher")


def init_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)


def _write_json_atomic(path, data):
    """Escribe en un temporal y lo renombra: un cierre brusco no deja el JSON a medias."""
    init_dirs()
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_config(wallpaper_dir):
    _write_json_atomic(CONFIG_FILE, {"wallpaper_dir": wallpaper_dir})


def load_favorites():
    """Devuelve un set con las rutas absolutas marcadas como favoritas."""
    if os.path.exists(FAVORITES_FILE):
        try:
            with open(FAVORITES_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                return set(data)
        except Exception:
            return set()
    return set()


def save_favorites(favorites_set):
    try:
        _write_json_atomic(FAVORITES_FILE, sorted(favorites_set))
    except Exception:
        pass
