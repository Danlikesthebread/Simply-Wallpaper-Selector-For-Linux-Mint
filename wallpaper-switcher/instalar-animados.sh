#!/usr/bin/env bash
# Instala SOLO lo necesario para los fondos animados (video / GIF): mpv, ffmpeg, xprop
# y los drivers VA-API (decodificación de video por GPU: la mayor mejora de rendimiento).
# (instalar-dependencias.sh ya lo incluye; este es por si ya tenías el resto.)
set -e
echo "==> Instalando dependencias (te pedirá la contraseña de sudo)"
sudo apt update
sudo apt install -y mpv ffmpeg x11-utils libnotify-bin vainfo mesa-va-drivers
# Driver VA-API según la GPU (sin él, mpv decodifica por CPU y un Celeron se satura)
if lspci 2>/dev/null | grep -Ei 'vga|display' | grep -qi intel; then
    echo "==> GPU Intel detectada: instalando intel-media-va-driver"
    sudo apt install -y intel-media-va-driver || sudo apt install -y i965-va-driver || true
fi
echo
if [ "${XDG_SESSION_TYPE:-x11}" = "wayland" ]; then
    echo "AVISO: estás en una sesión Wayland. Los fondos animados necesitan X11:"
    echo "       cierra sesión y elige 'Cinnamon' (no 'Cinnamon on Wayland') en el login."
fi
echo "Aceleración por hardware (VA-API): ejecuta  vainfo  -> si lista perfiles H264/HEVC, tu GPU decodifica video."
echo "  Intel (Broadwell o más nuevo): sudo apt install intel-media-va-driver   | Intel antiguo: i965-va-driver"
echo "  NVIDIA propietario: no usa VA-API; sin aceleración, activa Optimizar videos pesados (viene activado)."
echo "Listo. Pon videos (.mp4 .webm .mkv .mov) o .gif en tu carpeta de fondos y ábrelos con el carrusel."
