"""Análisis de color de los fondos y sincronización de paletas (starship, kitty)."""
import os
import json
import re

from PyQt6.QtCore import (Qt)
from PyQt6.QtGui import (QColor, QImageReader)

# Importaciones del proyecto
from thumbnail_cache import create_thumbnail, get_thumbnail_path
import livewall
from prefs import PREFS

def _read_small_image(filepath, size=32):
    """Lee la imagen ya reducida (el decodificador escala al leer: mucho más
    rápido que cargar un wallpaper 4K completo y reducirlo después)."""
    reader = QImageReader(filepath)
    reader.setAutoTransform(True)
    native = reader.size()
    if native.isValid():
        reader.setScaledSize(native.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


def analyze_color_family(filepath):
    """Analiza el color predominante usando QImage nativo sin tocar Pywal."""
    if livewall.is_video(filepath):
        filepath = create_thumbnail(filepath)      # miniatura = fotograma del video
    else:
        thumb = get_thumbnail_path(filepath, PREFS.thumb_box())
        if os.path.exists(thumb):                  # la miniatura en caché es ~10x más pequeña que el original
            filepath = thumb
    img = _read_small_image(filepath)
    if img.isNull():
        return "monochrome"

    avg = img.scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation).pixelColor(0, 0)
    h, s, v, _ = avg.getHsv()

    if s < 35 or v < 40:
        return "monochrome"

    if h < 15 or h >= 345: return "red"
    if h < 45: return "orange"
    if h < 75: return "yellow"
    if h < 165: return "green"
    if h < 195: return "cyan"
    if h < 265: return "blue"
    if h < 315: return "purple"
    return "monochrome"


def _get_image_max_saturation(image_path, max_dim=64, sample_step=4, percentile=90):
    """Devuelve el percentil de saturación de la imagen."""
    # Igual que la versión original: imagen completa reducida con muestreo
    # directo (no promediado), para que la medida sea la misma de siempre.
    img = _read_small_image(image_path, max_dim * 2)      # el decodificador escala al leer (JPEG: muy barato)
    if img.isNull():
        return 140
    img = img.scaled(max_dim, max_dim, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.FastTransformation)

    saturations = []
    for y in range(0, img.height(), sample_step):
        for x in range(0, img.width(), sample_step):
            s = QColor.fromRgb(img.pixel(x, y)).saturation()
            if s > 10:
                saturations.append(s)

    if not saturations:
        return 140

    saturations.sort()
    idx = min(int(len(saturations) * (percentile / 100.0)), len(saturations) - 1)
    return saturations[idx]


def _build_corrected_palette(colors_dict, image_path):
    """Reduce la saturación de los colores sin cambiar el matiz."""
    real_max_sat = _get_image_max_saturation(image_path, percentile=90)

    sat_scale = PREFS.get("sat_scale") / 100.0
    max_sat_limit = PREFS.get("sat_cap") / 100.0

    corrected = {}
    for i in range(16):
        hex_val = colors_dict.get(f"color{i}", "#ffffff")
        color = QColor(hex_val)
        if not color.isValid() or color.saturation() < 30:
            corrected[f"color{i}"] = hex_val
            continue

        h, s, v, a = color.getHsv()
        new_s = int(s * sat_scale)
        new_s = min(new_s, int(real_max_sat * max_sat_limit))
        new_s = max(0, min(new_s, 255))
        corrected[f"color{i}"] = QColor.fromHsv(h, new_s, v).name()
    return corrected


def correct_and_apply_palette(image_path):
    """Lee el colors.json de pywal, lo corrige según las preferencias y
    actualiza starship.toml y kitty."""
    cache_json = os.path.expanduser("~/.cache/wal/colors.json")
    if not os.path.exists(cache_json) or not os.path.exists(image_path):
        return

    try:
        with open(cache_json) as f:
            data = json.load(f)

        if PREFS.get("palette_correct"):
            corrected = _build_corrected_palette(data.get("colors", {}), image_path)
            data["colors"] = corrected
            with open(cache_json, "w") as f:
                json.dump(data, f, indent=4)
            if PREFS.get("update_starship"):
                update_starship_toml()
            if PREFS.get("update_kitty"):
                update_kitty_colors(corrected)
    except Exception as e:
        print(f"Error al corregir y aplicar paleta: {e}")


def update_starship_toml():
    """Inyecta la paleta (ya corregida) en starship.toml."""
    cache_json = os.path.expanduser("~/.cache/wal/colors.json")
    starship_path = os.path.expanduser("~/.config/starship.toml")

    if not os.path.exists(cache_json) or not os.path.exists(starship_path):
        return

    try:
        with open(cache_json) as f:
            colors_dict = json.load(f).get("colors", {})

        with open(starship_path) as f:
            content = f.read()

        lines = ["[palettes.wal]"]
        for i in range(16):
            lines.append(f'color{i} = "{colors_dict.get(f"color{i}", "#ffffff")}"')
        new_palette_str = "\n".join(lines)

        if "[palettes.wal]" in content:
            updated_content = content.split("[palettes.wal]")[0] + new_palette_str
        else:
            updated_content = content + "\n\n" + new_palette_str

        with open(starship_path, "w") as f:
            f.write(updated_content)
    except Exception as e:
        print(f"Error actualizando starship.toml: {e}")


def update_kitty_colors(colors_dict):
    """Reemplaza solo las líneas colorN del colors-kitty.conf."""
    kitty_path = os.path.expanduser("~/.cache/wal/colors-kitty.conf")
    if not os.path.exists(kitty_path):
        return

    try:
        with open(kitty_path) as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            match = re.match(r'^(color(\d{1,2})\s+)#[0-9a-fA-F]{6}\s*$', line)
            if match:
                idx = int(match.group(2))
                hex_val = colors_dict.get(f"color{idx}")
                if 0 <= idx <= 15 and hex_val:
                    new_lines.append(f"{match.group(1)}{hex_val}\n")
                    continue
            new_lines.append(line)

        with open(kitty_path, "w") as f:
            f.writelines(new_lines)
    except Exception as e:
        print(f"Error actualizando colors-kitty.conf: {e}")


