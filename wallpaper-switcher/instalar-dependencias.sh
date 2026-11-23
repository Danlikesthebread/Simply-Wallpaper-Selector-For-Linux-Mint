#!/usr/bin/env bash
# Instala las dependencias de Wallpaper Switcher en Linux Mint (Cinnamon).
# Incluye lo necesario para los fondos animados (mpv, ffmpeg, xprop).
set -e
echo "==> Instalando dependencias (te pedirá la contraseña de sudo)"
sudo apt update
sudo apt install -y python3 python3-pyqt6 python3-pil python3-numpy libxcb-cursor0 \
    mpv ffmpeg x11-utils libnotify-bin vainfo mesa-va-drivers
# Driver VA-API según la GPU (sin él, mpv decodifica por CPU y un Celeron se satura)
if lspci 2>/dev/null | grep -Ei 'vga|display' | grep -qi intel; then
    echo "==> GPU Intel detectada: instalando intel-media-va-driver"
    sudo apt install -y intel-media-va-driver || sudo apt install -y i965-va-driver || true
fi
echo
echo "Listo. Opcional: pywal para la paleta de colores (wal) -> sudo apt install python3-pywal"
echo "                 o: pipx install pywal"
echo "Opcional (recomendado) para que el efecto Contorno detecte bien al sujeto: ./instalar-modelo.sh"
echo "Ahora ejecuta ./install.sh para crear el lanzador, el atajo de teclado y el arranque del fondo animado."
