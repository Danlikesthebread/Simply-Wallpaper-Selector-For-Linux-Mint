#!/usr/bin/env bash
# Instala la IA del efecto «Contorno del sujeto» (todo en segundo plano, con caché):
#   - U²-Netp (4.7 MB): detección rápida, se usa mientras el recorte bueno no está listo.
#   - BiRefNet-lite (≈180 MB, 512 px): recorte de alta calidad, como hace pocket-dots. Se calcula UNA vez por
#     fondo, de forma discreta (baja prioridad, con límite de RAM) y queda guardado.
# Sin esto el efecto funciona igual, con un detector por color mucho menos preciso.
#
# Uso:  ./instalar-modelo.sh                       instala ambos
#       ./instalar-modelo.sh --rapido              solo U²-Netp (sin los ~180 MB de BiRefNet)
#       ./instalar-modelo.sh --file u2netp.onnx    usa un U²-Netp que ya tengas
#       ./instalar-modelo.sh --hq-file modelo.onnx usa un BiRefNet-lite que ya tengas
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="$HOME/.cache/wallpaper-switcher/models"
U2="$MODEL_DIR/u2netp.onnx"
HQ="$MODEL_DIR/birefnet-lite.onnx"
U2_URL="${MODEL_URL:-https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx}"
# Candidatos de BiRefNet-lite, en orden de preferencia (MIT). Los de 512 px gastan bastante menos RAM.
HQ_URLS=(
    "https://huggingface.co/h143N/birefnet-lite-512-chunked/resolve/main/onnx/model.onnx"
    "https://huggingface.co/studioludens/birefnet-lite-512/resolve/main/onnx/model.onnx"
    "https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/resolve/main/onnx/model.onnx"
)
[ -n "${HQ_URL:-}" ] && HQ_URLS=("$HQ_URL")
MIN_HQ_BYTES="${MIN_HQ_BYTES:-50000000}"

SRC_FILE=""; HQ_FILE=""; WANT_HQ=1
while [ $# -gt 0 ]; do
    case "$1" in
        --file) SRC_FILE="${2:?falta la ruta}"; shift 2 ;;
        --hq-file) HQ_FILE="${2:?falta la ruta}"; shift 2 ;;
        --rapido) WANT_HQ=0; shift ;;
        *) echo "Opción desconocida: $1" >&2; exit 1 ;;
    esac
done

echo "==> numpy"
/usr/bin/python3 -c "import numpy" 2>/dev/null || sudo apt install -y python3-numpy

echo "==> onnxruntime"
if ! /usr/bin/python3 -c "import onnxruntime" 2>/dev/null; then
    sudo apt install -y python3-pip >/dev/null 2>&1 || true
    /usr/bin/python3 -m pip install --user onnxruntime 2>/dev/null \
        || /usr/bin/python3 -m pip install --user --break-system-packages onnxruntime
fi
/usr/bin/python3 -c "import onnxruntime as o; print('onnxruntime', o.__version__)"

mkdir -p "$MODEL_DIR"
trap 'rm -f "$U2.tmp" "$HQ.tmp"' EXIT      # los .part (descargas a medias) se conservan para reanudar

download() {   # download URL DESTINO : con reanudación, tiempo límite por lectura y reintentos
    /usr/bin/python3 - "$1" "$2" <<'PY'
import os, sys, time, urllib.request, urllib.error
url, dst = sys.argv[1], sys.argv[2]
part = dst + ".part"          # el parcial se conserva entre intentos (y entre ejecuciones del script)
TIMEOUT, TRIES = 30, 40
last_pct = -1
for attempt in range(1, TRIES + 1):
    have = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(url, headers={"User-Agent": "wallpaper-switcher", **({"Range": "bytes=%d-" % have} if have else {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            code = r.getcode()
            if have and code != 206:          # el servidor no admite reanudar: se empieza de cero
                have = 0
            total = r.headers.get("Content-Range", "").split("/")[-1] if code == 206 else r.headers.get("Content-Length", "")
            total = int(total) if str(total).isdigit() else 0
            with open(part, "ab" if have else "wb") as f:
                while True:
                    chunk = r.read(256 * 1024)       # si no llega nada en TIMEOUT s salta una excepción
                    if not chunk:
                        break
                    f.write(chunk); have += len(chunk)
                    if total:
                        pct = have * 100 // total
                        if pct // 5 != last_pct // 5:
                            last_pct = pct
                            print("   %d%% (%d MB)" % (pct, have // 1048576), flush=True)
        if total and have < total:
            raise OSError("descarga incompleta (%d de %d bytes)" % (have, total))
        os.replace(part, dst)
        sys.exit(0)
    except urllib.error.HTTPError as e:
        if e.code == 416 and os.path.exists(part):    # el parcial ya estaba completo
            os.replace(part, dst); sys.exit(0)
        print("   intento %d/%d: HTTP %s" % (attempt, TRIES, e.code), flush=True)
        if e.code in (401, 403, 404):
            sys.exit("Error de descarga: HTTP %s" % e.code)
    except Exception as e:
        print("   intento %d/%d: %s; reintentando (se retoma desde %d MB)..." % (attempt, TRIES, e, (os.path.getsize(part) if os.path.exists(part) else 0) // 1048576), flush=True)
    time.sleep(min(2 * attempt, 15))
sys.exit("Error de descarga: demasiados intentos. Vuelve a ejecutar el script: retomará desde donde quedó.")
PY
}

# ---------------------------------------------------------------- U²-Netp (rápido)
if [ ! -f "$U2" ] || [ -n "$SRC_FILE" ]; then
    echo "==> U²-Netp (~4.7 MB)"
    if [ -n "$SRC_FILE" ]; then cp "$SRC_FILE" "$U2.tmp"
    elif ! download "$U2_URL" "$U2.tmp"; then
        echo "No se pudo descargar U²-Netp. Bájalo a mano y usa: ./instalar-modelo.sh --file /ruta/u2netp.onnx" >&2
        rm -f "$U2.tmp"
    fi
    if [ -f "$U2.tmp" ]; then
        /usr/bin/python3 - "$U2.tmp" <<'PY'
import os, sys
import numpy as np
import onnxruntime as ort
p = sys.argv[1]; size = os.path.getsize(p)
if not 3_000_000 < size < 12_000_000:
    sys.exit(f"El archivo pesa {size} bytes: no parece U2-Netp (esperado ~4.7 MB)")
s = ort.InferenceSession(p, providers=["CPUExecutionProvider"])
out = s.run(None, {s.get_inputs()[0].name: np.zeros((1, 3, 320, 320), np.float32)})[0]
if out.shape[-2:] != (320, 320): sys.exit(f"Salida inesperada {out.shape}")
print("U²-Netp verificado:", round(size / 1e6, 1), "MB")
PY
        mv "$U2.tmp" "$U2"
    fi
else
    echo "==> U²-Netp ya instalado"
fi

# ---------------------------------------------------------------- BiRefNet-lite (calidad)
if [ "$WANT_HQ" = 1 ]; then
    if [ -f "$HQ" ] && [ -z "$HQ_FILE" ]; then
        echo "==> BiRefNet-lite ya instalado"
    else
        echo "==> BiRefNet-lite (~180 MB; recorte de alta calidad)"
        free -m | awk 'NR==2 {printf "   RAM total: %d MB, libre: %d MB\n", $2, $7}'
        ok=0
        candidates=("${HQ_URLS[@]}")
        [ -n "$HQ_FILE" ] && candidates=("file:$HQ_FILE")
        for url in "${candidates[@]}"; do
            echo "-> $url"
            if [ "${url#file:}" != "$url" ]; then cp "${url#file:}" "$HQ.tmp" || continue
            elif ! download "$url" "$HQ.tmp"; then rm -f "$HQ.tmp"; continue; fi
            size=$(stat -c %s "$HQ.tmp")
            if [ "$size" -lt "$MIN_HQ_BYTES" ]; then echo "   archivo demasiado pequeño ($size bytes), se descarta"; rm -f "$HQ.tmp"; continue; fi
            echo "   verificando en este equipo (carga + una inferencia; puede tardar un rato)..."
            if /usr/bin/python3 "$DIR/contour.py" --check-hq "$HQ.tmp"; then
                mv "$HQ.tmp" "$HQ"; ok=1; break
            fi
            echo "   no funciona aquí, se prueba el siguiente candidato"; rm -f "$HQ.tmp" "$HQ.tmp.part"
        done
        if [ "$ok" = 1 ]; then
            rm -f "$HOME/.cache/wallpaper-switcher/masks/desactivado"
            echo "BiRefNet-lite instalado: $HQ"
        else
            echo "No se pudo instalar BiRefNet-lite. El efecto usará U²-Netp (instalado arriba)." >&2
            echo "Puedes bajar un BiRefNet-lite ONNX a mano y usar: ./instalar-modelo.sh --hq-file /ruta/modelo.onnx" >&2
        fi
    fi
fi

rm -rf "$HOME/.cache/wallpaper-switcher/contour"      # los auxiliares viejos se regeneran con los modelos nuevos
echo
/usr/bin/python3 "$DIR/contour.py" --status | head -8
echo
echo "Para precalcular los recortes de una carpeta entera (opcional, queda en caché):"
echo "  python3 $DIR/contour.py --warm ~/Imágenes/Fondos"
echo "Ver qué detecta en una foto:  python3 $DIR/contour.py --debug foto.jpg"
