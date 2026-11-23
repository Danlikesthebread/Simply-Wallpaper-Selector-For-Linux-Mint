#!/usr/bin/env bash
# Instala y activa la extensión de Cinnamon "Wallpaper Transitions" (transiciones animadas del fondo).
# Copia la carpeta a ~/.local/share/cinnamon/extensions/ y la añade a las extensiones activas.
#
# Uso:  ./instalar-extension.sh            instala y activa
#       ./instalar-extension.sh --remove   desactiva y borra
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UUID="wallpaper-transitions@wallpaper-switcher"
SRC="$ROOT/cinnamon-extension/$UUID"
DEST="$HOME/.local/share/cinnamon/extensions/$UUID"
SCHEMA=org.cinnamon
KEY=enabled-extensions

toggle() {   # $1 = add | remove
    command -v gsettings >/dev/null 2>&1 || { echo "No encuentro gsettings: activa la extensión desde Configuración → Extensiones." >&2; return 1; }
    local current new
    current="$(gsettings get $SCHEMA $KEY)"
    new="$(CURRENT="$current" UUID="$UUID" ACTION="$1" python3 - <<'PY'
import ast, os
raw = os.environ["CURRENT"].replace("@as", "").strip()
lst = ast.literal_eval(raw) if raw else []
uuid, action = os.environ["UUID"], os.environ["ACTION"]
if action == "add" and uuid not in lst:
    lst.append(uuid)
elif action == "remove":
    lst = [x for x in lst if x != uuid]
print(repr(lst) if lst else "@as []")
PY
)"
    gsettings set $SCHEMA $KEY "$new"
}

if [ "${1:-}" = "--remove" ]; then
    toggle remove || true
    rm -rf "$DEST"
    echo "Extensión desinstalada."
    exit 0
fi

[ -f "$SRC/extension.js" ] || { echo "No encuentro $SRC/extension.js" >&2; exit 1; }

mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -a "$SRC" "$DEST"
echo "Copiada a:    $DEST"

toggle add
echo "Activada:     $UUID"

# Si Cinnamon ya la tenía cargada, se recarga sin reiniciar el escritorio (si falla, basta con reiniciar Cinnamon)
dbus-send --session --dest=org.Cinnamon.LookingGlass --type=method_call \
    /org/Cinnamon/LookingGlass org.Cinnamon.LookingGlass.ReloadExtension \
    string:"$UUID" string:"EXTENSION" >/dev/null 2>&1 || true

echo
echo "Listo. Si no ves las transiciones, reinicia Cinnamon: Ctrl+Alt+Esc (o ejecuta: cinnamon --replace &)."
echo "Las opciones (efecto, dirección, duración…) están en el selector: Ctrl+, → Comportamiento."
echo "Errores de la extensión:  journalctl --user -b | grep wallpaper-transitions   o   Looking Glass (Alt+F2 → lg)"
