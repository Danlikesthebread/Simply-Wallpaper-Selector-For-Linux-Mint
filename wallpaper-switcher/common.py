"""Constantes y utilidades matemáticas compartidas por el selector."""

from PyQt6.QtCore import (QEasingCurve)

# Importaciones del proyecto

FAVORITES_KEY = "favorites"
MODE_KEY = "__mode__"
SETTINGS_KEY = "__settings__"

# --- FAMILIAS DE COLOR (análisis interno, sin depender de Pywal) ---
FAMILY_ORDER = ["red", "orange", "yellow", "green", "cyan", "blue", "purple", "monochrome"]
FAMILY_INFO = {
    "red": {"label": "Rojos", "color": "#F25C54"},
    "orange": {"label": "Naranjas", "color": "#F79B3E"},
    "yellow": {"label": "Amarillos", "color": "#F5D547"},
    "green": {"label": "Verdes", "color": "#4ADE80"},
    "cyan": {"label": "Cyan", "color": "#38D5E8"},
    "blue": {"label": "Azules", "color": "#5B8CFF"},
    "purple": {"label": "Morados", "color": "#A66BFF"},
    "monochrome": {"label": "Monocromo", "color": "#9AA0A6"},
}

EASINGS = {
    "out_cubic": QEasingCurve.Type.OutCubic,
    "out_quart": QEasingCurve.Type.OutQuart,
    "out_expo": QEasingCurve.Type.OutExpo,
    "in_out_cubic": QEasingCurve.Type.InOutCubic,
    "out_back": QEasingCurve.Type.OutBack,
    "linear": QEasingCurve.Type.Linear,
}


def _clamp01(v):
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _ease_out(t):
    t = _clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def _lerp(a, b, t):
    return a + (b - a) * t


# ---------------------------------------------------------------------------
#  Análisis de color y paleta pywal
# ---------------------------------------------------------------------------

