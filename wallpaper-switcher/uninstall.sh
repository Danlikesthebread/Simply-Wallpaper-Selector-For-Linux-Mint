#!/usr/bin/env bash
# Quita lanzador, entrada de menú, autoarranque, atajo de teclado y detiene el fondo animado.
# No borra la carpeta del proyecto, tus fondos, ni ~/.config/wallpaper-switcher.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detiene el video de fondo (y olvida cuál era, para que no vuelva al iniciar sesión)
/usr/bin/python3 "$ROOT/livewall.py" --stop 2>/dev/null || true

"$ROOT/instalar-extension.sh" --remove 2>/dev/null || true

rm -f "$HOME/.local/bin/wallpaper-switcher" \
      "$HOME/.local/share/applications/wallpaper-switcher.desktop" \
      "$HOME/.config/autostart/wallpaper-switcher-live.desktop"

SCHEMA=org.cinnamon.desktop.keybindings
KB_ID=wallpaper-switcher
KB_PATH="/org/cinnamon/desktop/keybindings/custom-keybindings/$KB_ID/"
if gsettings list-schemas | grep -qx "$SCHEMA"; then
    current="$(gsettings get $SCHEMA custom-list)"
    new="$(CURRENT="$current" KB_ID="$KB_ID" python3 - <<'PY'
import ast, os
raw = os.environ["CURRENT"].replace("@as", "").strip()
lst = ast.literal_eval(raw) if raw else []
lst = [x for x in lst if x != os.environ["KB_ID"]]
print(repr(lst) if lst else "@as []")
PY
)"
    gsettings set $SCHEMA custom-list "$new"
    gsettings reset-recursively "$SCHEMA.custom-keybinding:$KB_PATH" 2>/dev/null || true
fi
echo "Desinstalado. Si tenías un video de fondo, el fondo estático de respaldo se queda puesto."
