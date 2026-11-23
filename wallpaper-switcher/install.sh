#!/usr/bin/env bash
# Crea el lanzador, la entrada de menú, el atajo de teclado (Cinnamon) y el arranque
# automático del fondo animado.
#
# Uso:  ./install.sh                         (atajo por defecto: Ctrl+Alt+W)
#       ./install.sh --shortcut '<Super>w'   (otro atajo, sintaxis de Cinnamon/GTK)
#       ./install.sh --no-shortcut           (sin atajo)
#       ./install.sh --no-autostart          (no restaurar el video al iniciar sesión)
#       ./install.sh --no-extension          (no instalar la extensión de transiciones del fondo)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/wallpaper-switcher"
DESKTOP_FILE="$HOME/.local/share/applications/wallpaper-switcher.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/wallpaper-switcher-live.desktop"
SHORTCUT='<Control><Alt>w'
AUTOSTART=1
EXTENSION=1

while [ $# -gt 0 ]; do
    case "$1" in
        --shortcut) SHORTCUT="${2:?falta el atajo}"; shift 2 ;;
        --no-shortcut) SHORTCUT=""; shift ;;
        --no-autostart) AUTOSTART=0; shift ;;
        --no-extension) EXTENSION=0; shift ;;
        *) echo "Opción desconocida: $1" >&2; exit 1 ;;
    esac
done

[ -f "$APP_DIR/main.py" ] || { echo "No encuentro $APP_DIR/main.py" >&2; exit 1; }

# Comprobación rápida de dependencias (no instala nada)
/usr/bin/python3 - <<'PY' || { echo "Faltan dependencias: ejecuta ./instalar-dependencias.sh primero." >&2; exit 1; }
import PyQt6.QtWidgets, PIL
PY

for cmd in mpv ffmpeg xprop; do
    command -v "$cmd" >/dev/null 2>&1 || echo "Aviso: falta '$cmd' (los fondos animados no funcionarán): ./instalar-animados.sh"
done

mkdir -p "$BIN_DIR" "$(dirname "$DESKTOP_FILE")"

cat > "$LAUNCHER" <<LAUNCH
#!/usr/bin/env bash
export PATH="\$HOME/.local/bin:\$PATH"
exec /usr/bin/python3 "$APP_DIR/main.py" "\$@"
LAUNCH
chmod +x "$LAUNCHER"

cat > "$DESKTOP_FILE" <<DESK
[Desktop Entry]
Type=Application
Name=Wallpaper Switcher
Comment=Carrusel para cambiar el fondo de pantalla (imágenes y videos)
Exec=$LAUNCHER
Icon=preferences-desktop-wallpaper
Terminal=false
Categories=Utility;Settings;
StartupNotify=false
StartupWMClass=wallpaper-switcher
DESK
update-desktop-database "$(dirname "$DESKTOP_FILE")" >/dev/null 2>&1 || true

echo "Lanzador:      $LAUNCHER"
echo "Entrada menú:  $DESKTOP_FILE"

# --- Restaurar el fondo animado al iniciar sesión (no hace nada si no hay uno activo) ---
if [ "$AUTOSTART" = 1 ]; then
    mkdir -p "$(dirname "$AUTOSTART_FILE")"
    cat > "$AUTOSTART_FILE" <<AUTO
[Desktop Entry]
Type=Application
Name=Wallpaper Switcher (fondo animado)
Comment=Restaura el video de fondo al iniciar sesión
Exec=/usr/bin/python3 $APP_DIR/player.py --restore --delay 5
Icon=preferences-desktop-wallpaper
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
AUTO
    echo "Autoarranque:  $AUTOSTART_FILE"
fi

# --- Atajo de teclado en Cinnamon ---
if [ -n "$SHORTCUT" ]; then
    SCHEMA=org.cinnamon.desktop.keybindings
    KB_ID=wallpaper-switcher
    KB_PATH="/org/cinnamon/desktop/keybindings/custom-keybindings/$KB_ID/"
    if gsettings list-schemas 2>/dev/null | grep -x "$SCHEMA" >/dev/null; then
        current="$(gsettings get $SCHEMA custom-list)"
        new="$(CURRENT="$current" KB_ID="$KB_ID" python3 - <<'PY'
import ast, os
raw = os.environ["CURRENT"].replace("@as", "").strip()
lst = ast.literal_eval(raw) if raw else []
if os.environ["KB_ID"] not in lst:
    lst.append(os.environ["KB_ID"])
print(repr(lst))
PY
)"
        gsettings set $SCHEMA custom-list "$new"
        KB="org.cinnamon.desktop.keybindings.custom-keybinding:$KB_PATH"
        gsettings set "$KB" name 'Wallpaper Switcher'
        gsettings set "$KB" command "$LAUNCHER"
        gsettings set "$KB" binding "['$SHORTCUT']"
        echo "Atajo creado:  $SHORTCUT  (Configuración → Teclado → Atajos → Personalizados)"
    else
        echo "No se encontró el esquema de atajos de Cinnamon: crea el atajo a mano con el comando $LAUNCHER"
    fi
fi

# --- Extensión de Cinnamon con las transiciones animadas del fondo ---
if [ "$EXTENSION" = 1 ] && [ -x "$ROOT/instalar-extension.sh" ]; then
    echo
    "$ROOT/instalar-extension.sh" || echo "Aviso: no se pudo instalar la extensión (puedes reintentar con ./instalar-extension.sh)."
fi

echo
echo "Hecho. No muevas esta carpeta de sitio; si lo haces, vuelve a ejecutar ./install.sh."
echo "La primera vez que lo abras te pedirá la carpeta de fondos (la que contiene tus imágenes y videos)."
