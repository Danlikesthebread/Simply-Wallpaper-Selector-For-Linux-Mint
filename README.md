> Este proyecto se ha hecho prácticamente por completo con IA. No debería haber problemas con el código, pero no me
> hago responsable de ningún daño que este programa pueda causar. Eres libre de usarlo, modificarlo y publicarlo.

# Wallpaper Switcher — Linux Mint (Cinnamon) con fondos animados

Carrusel HUD translúcido (PyQt6, diseño *glassmorphism*) hecho **a medida para Linux Mint con el escritorio
Cinnamon**. Cambia el fondo de pantalla en tiempo real, funciona con **imágenes, videos y GIF**, genera la paleta
del sistema a partir del fondo (pywal, Starship, Kitty, tema GTK) y anima el cambio con transiciones dibujadas
dentro de Muffin, el compositor de Cinnamon.

Se integra con Mint de principio a fin: instala con `apt`, fija el fondo con `gsettings` (`org.cinnamon`), crea el
atajo en *Configuración del sistema → Teclado*, añade la entrada al menú, se activa como extensión de Cinnamon y
usa Nemo para los iconos del escritorio.

> **Solo probado en Linux Mint Cinnamon (sesión X11).** No me hago responsable de problemas en otras
> distribuciones, escritorios o sistemas operativos.

## Requisitos (Linux Mint)

| Requisito | Detalle |
|---|---|
| Sistema | Linux Mint con escritorio **Cinnamon** (la edición principal) |
| Versión de Cinnamon | 5.4 – 6.4 (rango declarado por la extensión) |
| Sesión | **Cinnamon (X11)**, la predeterminada. "Cinnamon on Wayland" no está soportada |
| Paquetes | Los instala `./instalar-dependencias.sh` con `apt`: `python3-pyqt6`, `python3-pil`, `python3-numpy`, `mpv`, `ffmpeg`, `x11-utils`, `libnotify-bin`, `vainfo`, `mesa-va-drivers` |
| Opcionales | `pywal` (paleta de colores), `onnxruntime` + modelos (efecto Contorno con IA) |

Para comprobar tu sesión: `echo $XDG_SESSION_TYPE` debe mostrar `x11`.

## Características

- **Carrusel infinito** con navegación por teclado y ratón, sin cortes visuales.
- **Fondos animados**: videos y GIF como fondo de escritorio (reproductor propio con mpv), con optimización
  automática en segundo plano.
- **Transiciones del fondo** mediante una extensión de Cinnamon (efectos al estilo Hyprland/swww), incluido el efecto
  **Contorno del sujeto** (con IA opcional).
- **Filtros por color**, **favoritos**, orden (nombre / recientes / antiguos / aleatorio) y subcarpetas.
- **Paleta del sistema**: pywal, Starship, Kitty y tema GTK a partir del fondo (o de un fotograma, si es video).
- **Carga asíncrona**: miniaturas y análisis de color en hilos (`QThreadPool`) para no congelar la interfaz.
- **Vigilancia de la carpeta** de fondos en tiempo real (`QFileSystemWatcher`).
- **Panel de preferencias** completo (`Ctrl+,`): animaciones, tamaño y estilo de las tarjetas, posición de la
  ventana, comportamiento, colores, archivos y rendimiento.
- **Una sola instancia**: si ya hay un selector abierto, el segundo lanzamiento sale al instante.

## Instalación rápida

```bash
cd wallpaper-switcher
chmod +x *.sh

./instalar-dependencias.sh   # PyQt6, Pillow, numpy, mpv, ffmpeg, xprop, VA-API (pide sudo)
./instalar-modelo.sh         # opcional: IA (U²-Netp + BiRefNet-lite) para detectar al sujeto en el efecto Contorno
./install.sh                 # lanzador + menú + atajo Ctrl+Alt+W + arranque del video + extensión de transiciones
```

Después abre **Wallpaper Switcher** desde el menú o pulsa **Ctrl+Alt+W**. La primera vez te pide la carpeta donde
tienes tus fondos (imágenes **y** videos pueden estar mezclados).

Opcional: `pywal` para la paleta de colores (`sudo apt install python3-pywal` o `pipx install pywal`).

Variantes de `install.sh`:

| Comando | Efecto |
|---|---|
| `./install.sh --shortcut '<Super>w'` | Otro atajo (sintaxis GTK: `<Super>`, `<Control>`, `<Alt>`, `<Shift>`) |
| `./install.sh --no-shortcut` | No crear atajo |
| `./install.sh --no-autostart` | No restaurar el video al iniciar sesión |
| `./install.sh --no-extension` | No instalar la extensión de transiciones del fondo |

Ejecución manual sin instalar: `python3 main.py` (con `--settings` para volver a elegir la carpeta).
Si prefieres crear el atajo a mano: *Menú → Configuración del sistema → Teclado → Atajos → Atajos personalizados*,
con el comando `~/.local/bin/wallpaper-switcher` (o `python3 /ruta/al/proyecto/main.py`).
Desinstalar: `./uninstall.sh` (no borra tus fondos ni tus preferencias).

> **No muevas la carpeta** después de instalar; si lo haces, vuelve a ejecutar `./install.sh`.

## Controles

| Acción | Teclas / ratón |
|---|---|
| Avanzar | `→` `D` · rueda abajo |
| Retroceder | `←` `A` · rueda arriba |
| Seleccionar una tarjeta | Clic sobre ella |
| Aplicar el fondo | `Enter` o doble clic (con *Aplicar con un clic*, basta un clic sobre la tarjeta activa) |
| Ir a la barra de colores | `↑` (después `←` `→` / `A` `D` para moverte y `Enter` para aplicar el filtro) |
| Volver al carrusel | `↓` |
| Marcar / quitar favorito | `Tab` (foco en el corazón de la tarjeta) y `Enter`, o clic en el corazón |
| Preferencias | `Ctrl+,` |
| Cerrar | `Esc` (si las preferencias están abiertas, las cierra primero) |

Mantener pulsada una tecla de dirección repite la navegación (la velocidad se ajusta en Preferencias). La rueda
solo mueve el carrusel mientras el foco está en él, y puede desactivarse o invertirse.

## Preferencias (`Ctrl+,`)

Se guardan en `~/.config/wallpaper-switcher/preferences.json`.

| Pestaña | Qué contiene |
|---|---|
| Animaciones | Animación de entrada (cascada, barrido, fundido, zoom, caída), velocidad global, duración y curva de movimiento |
| Carrusel | Modo de la tarjeta activa (Expandir / Crecer), tamaño, inclinación, separación, color de acento, franja de fondo, nombre del archivo |
| Ventana | Pantalla donde se abre, posición vertical, ancho, posición/forma/opacidad de la barra |
| Comportamiento | Carrusel infinito, empezar en el fondo actual, rueda, cerrar al aplicar o al perder el foco, **aplicar al navegar** (vista previa en vivo), transición del fondo |
| Colores | Filtros por color, pywal (motor, tema claro, corrección de saturación), Starship, Kitty, tema GTK |
| Archivos | Carpeta, subcarpetas, orden, fondos animados, optimización de video, miniaturas y cachés |

Otros archivos de datos: `config.json` (carpeta de fondos), `favorites.json` y `live.json` (video activo, para
restaurarlo al iniciar sesión), todos en `~/.config/wallpaper-switcher/`. Las cachés están en
`~/.cache/wallpaper-switcher/`.

## Fondos animados (videos y GIF)

Los archivos `.mp4 .webm .mkv .mov .avi .m4v .gif` aparecen en el carrusel con un **▶** sobre la miniatura.
Al aplicarlos, el video se reproduce como fondo de escritorio, **detrás de los iconos y de las ventanas**, en
bucle y sin sonido. Al aplicar una imagen normal, el video se detiene.

**Cómo funciona.** Cinnamon no puede reproducir video como fondo por sí mismo (a diferencia de GNOME, no tiene una extensión
equivalente a Hanabi). Aquí un pequeño proceso aparte
(`player.py`) abre una ventana por monitor, la marca como ventana de escritorio (`_NET_WM_WINDOW_TYPE_DESKTOP`)
y le incrusta **mpv**. Detalles útiles:

- Sigue funcionando aunque cierres el carrusel, y se **restaura solo al iniciar sesión**.
- Además del video, se deja un **fotograma como fondo estático** (se ve en la pantalla de bloqueo y mientras
  arranca el video).
- **pywal, Starship, Kitty y el tema GTK** se generan a partir de ese fotograma, igual que con una imagen.
- Los filtros por color también analizan el fotograma del video.
- Funciona con varios monitores (por defecto el video se reproduce solo en el principal).
- El video llena la pantalla recortando los bordes (equivale al ajuste "zoom").

### Requisitos

- Sesión **X11** de Cinnamon (ver *Requisitos*): el programa avisará con una notificación si detecta Wayland.
- `mpv`, `ffmpeg` (miniaturas y optimización) y `x11-utils` (`xprop`) → los instala `./instalar-dependencias.sh`
  (o solo ellos, más los drivers VA-API, con `./instalar-animados.sh`).

### Opciones (Ctrl+, → Archivos → Fondos animados)

| Opción | Valor por defecto | Para qué sirve |
|---|---|---|
| Incluir fondos animados | Sí | Muestra u oculta videos y GIF en el carrusel |
| Detener animado al elegir imagen | Sí | Al aplicar una imagen fija se apaga el video |
| Monitores con video | Solo el principal | Todos, o solo el principal (menos consumo) |
| Sin sonido | Sí | Desactívalo si quieres oír el audio del video |
| Aceleración por hardware | Sí | Usa la GPU para decodificar (menos CPU) |
| Modo compatibilidad | No | Dibuja por software (`--vo=x11`). Úsalo si hay pantalla negra o ventana suelta |
| Capa del video | Escritorio | "Escritorio" (tras los iconos) o "Debajo de ventanas" |

### Optimización de video (Ctrl+, → Archivos → Optimización de video)

| Opción | Valor por defecto | Para qué sirve |
|---|---|---|
| Optimizar videos pesados | Sí | Crea en segundo plano una copia H.264 adaptada a tu monitor y cambia a ella (los GIF se convierten siempre que esté activada) |
| Límite de fotogramas por segundo | 24 fps | 24, 30 o 60 fps. Los videos con más fps se reducen al optimizar |
| Calidad de la copia optimizada | Ligera | Ligera / Equilibrada / Alta |
| Pausar si una ventana tapa el escritorio | Sí | Pausa el video del monitor cubierto por una ventana maximizada o a pantalla completa y lo reanuda al descubrirlo |
| Pausar con batería | No | Congela el fondo animado mientras el portátil no está enchufado |
| Caché de videos optimizados | 1000 MB | Espacio máximo de las copias (se borran las menos usadas) |

Qué hace la optimización:

- La copia **no supera la resolución de tu monitor ni 1920×1080** (1600×900 en CPU de 4 núcleos o menos) y nunca
  amplía el original.
- Limita fps y bitrate, y **conserva el audio** si el video lo tiene.
- `ffmpeg` corre con la **prioridad más baja** (`nice 19` + `ionice` clase 3) y con pocos hilos (1 en CPU de
  4 núcleos o menos): el equipo no se congela mientras convierte.
- Solo se convierte **un video a la vez**. Si eliges otro fondo, la conversión en curso se **cancela** (no se
  acumulan procesos al navegar rápido).
- Mientras la copia no está lista, el original se reproduce en **modo ligero** (decodificación rápida).
- Los videos que ya son ligeros (H.264, dentro del límite de fps, resolución ajustada y bitrate de hasta
  6 Mbit/s) no se tocan.

Las copias optimizadas se guardan en `~/.cache/wallpaper-switcher/video/` (se pueden borrar sin problema).
Diagnóstico de un video concreto: `python3 livewall.py --info mi_video.mp4` (indica si se optimizaría y por qué).

### Consejos de rendimiento

- **Aceleración por hardware (lo que más pesa):** ejecuta `vainfo`. Si lista perfiles H264/HEVC, tu GPU decodifica
  el video y el consumo de CPU es mínimo (`./instalar-animados.sh` instala `vainfo` y `mesa-va-drivers`; en GPU
  Intel añade `intel-media-va-driver`, o `i965-va-driver` en las antiguas). Si falla, mpv cae solo a software.
- Con **Optimizar videos pesados** activo, un 4K/60 fps pasa a ser un 1080p/24 ligero tras unos segundos o
  minutos (depende de su duración); verás un breve periodo en modo ligero y luego el cambio.
- Con varios monitores, cada uno decodifica el video por separado: mantén **Monitores con video → Solo el
  principal** si notas carga.
- El fondo **se pausa solo** cuando una ventana maximizada o a pantalla completa tapa el escritorio, y
  opcionalmente con batería.
- NVIDIA propietario no usa VA-API: sin aceleración, deja activada la optimización de videos.

### Equipos modestos (Celeron, Atom…)

Los valores por defecto están pensados para CPU de gama baja: copia optimizada ligera a 24 fps, video solo en el
monitor principal, mpv con prioridad reducida (`nice 5`, para ceder CPU al navegador y al resto de apps), pausa
por ventana que tapa el escritorio calculada en un hilo aparte y pocas miniaturas en memoria. Si ya tenías un
`~/.config/wallpaper-switcher/preferences.json`, tus valores antiguos se respetan: bórralo (o usa *Restablecer
todo* en Preferencias) para adoptar los nuevos.

## Transiciones del fondo (estilo Hyprland / swww)

Cinnamon solo cambia de imagen con su propio fundido. La extensión **Wallpaper Transitions**
(`cinnamon-extension/wallpaper-transitions@wallpaper-switcher`, v2.0, para Cinnamon 5.4–6.4) dibuja efectos
propios **dentro del compositor de Cinnamon**: una capa de actores Clutter justo encima del fondo y debajo de
todo lo demás. Por eso los **desklets, los iconos y las ventanas no se ven afectados** (no hay ventanas X extra)
y en reposo no queda ningún actor.

```bash
./instalar-extension.sh          # copia, activa y recarga la extensión (también lo hace ./install.sh)
./instalar-extension.sh --remove
```

Puedes comprobar que está activa en *Configuración del sistema → Extensiones* (aparece como *Wallpaper Transitions*).
Si no ves nada tras instalar: reinicia Cinnamon (`Ctrl+Alt+Esc`, o `cinnamon --replace &` desde una terminal). Errores: Looking Glass (`Alt+F2` → `lg`) o
`journalctl --user -b | grep wallpaper-transitions`.

Se configura en **Ctrl+, → Comportamiento → Transición del fondo de escritorio** (la extensión lee
`~/.config/wallpaper-switcher/preferences.json`; con el interruptor desactivado, o sin ese archivo, usa valores
por defecto). Anima **cualquier** cambio de `picture-uri`, también si lo haces desde otra herramienta.

| Opción | Valores |
|---|---|
| Efecto | Contorno del sujeto, **Barrido** (por defecto), Ola, Círculo (crece / se cierra), Fundido, Deslizar, Zoom, Persianas, Puertas, Mosaico, Tinta, Grieta de cristal, Revelado por luz, Portal (se abre bajo el cursor), Píxeles a color, Aleatorio |
| Dirección | Aleatoria, derecha, izquierda, abajo, arriba, diagonal (para barrido, ola, deslizar, persianas y puertas) |
| Origen (efectos circulares) | Centro, cursor, aleatorio |
| Duración | 300–3000 ms (1200 por defecto) |

Ola, Círculo, Tinta, Grieta de cristal, Revelado por luz, Portal, Píxeles a color y Contorno usan un shader de
Clutter; si no está disponible, se sustituyen por equivalentes de recorte (o por Fundido).

### Efecto «Contorno del sujeto»

Pensado para fondos con un sujeto claro (una persona, un animal, un objeto). Se desarrolla en tres fases:
**(1)** una línea fina, de un color tomado de la propia foto, dibuja el contorno del sujeto de arriba abajo
mientras el fondo viejo se oscurece un poco; **(2)** el sujeto se rellena con el fondo nuevo desde esa línea
hacia dentro; **(3)** el resto del fondo avanza desde el sujeto en celdas pequeñas (~30 px a 1080p), cada una un
círculo o un cuadrado que crece, con borde suave. Dura 1.6 veces lo que configures en «duración» y va a velocidad
lineal. Con un video, el efecto se calcula sobre su primer fotograma. Está inspirado en el efecto de
[pocket-dots](https://github.com/diegoMalagrida/pocket-dots) (`wallfx`), con la misma idea de fondo: **el recorte
bueno se hace con IA una sola vez por fondo, en segundo plano, y queda en caché; aplicar un fondo nunca espera a
la IA pesada.**

Detección, en tres niveles (se usa el mejor disponible):

1. **BiRefNet-lite** (≈180 MB, `./instalar-modelo.sh`). Recorte de alta calidad. Corre en un proceso aparte con
   prioridad baja, con vigilancia de RAM y de tiempo (si no cabe o tarda demasiado se mata el proceso, se
   desactiva solo y queda anotado en el registro) y se cierra solo tras 20 s sin trabajo. El recorte se guarda en
   `~/.cache/wallpaper-switcher/masks/` y, al terminar, el auxiliar del fondo se regenera con él.
2. **U²-Netp** (4.7 MB). Rápido; se usa mientras el recorte bueno aún no está listo.
3. **Detector clásico** (color en Lab + prior de fondo + Otsu) si no hay ningún modelo. Funciona con sujetos que
   contrastan con el fondo; falla con primeros planos o fondos parecidos al sujeto.

Si no hay sujeto definido (un paisaje, un bosque, una textura) no se dibuja ninguna línea: el nuevo fondo nace de
tres puntos al azar (distintos en cada cambio) y crece como colonias de esas mismas celdas, que se fusionan.

**Cuándo se calcula.** El selector precalcula el contorno de la miniatura resaltada (300 ms después de pararte;
en un video, el de su fotograma) y pone en la cola de BiRefNet esa imagen y las 8 vecinas más cercanas. Al
aplicar, si el auxiliar no está listo se espera como máximo 3.5 s (los cálculos en segundo plano no lo
retrasan); pasado ese tiempo se aplica con «Barrido» y el cálculo sigue en segundo plano para la próxima vez.

Comandos útiles (desde la carpeta del programa):

```bash
python3 contour.py --status                  # qué modelos hay, cuáles se usan, últimas líneas del registro
python3 contour.py --warm ~/Imágenes/Fondos  # precalcula los recortes de toda una carpeta (queda en caché)
python3 contour.py --bench foto.jpg          # cuánto tarda BiRefNet en TU equipo y cuánta RAM usa
python3 contour.py --debug foto.jpg          # dibuja el contorno detectado en rojo en foto_debug.png
python3 contour.py --debug foto.jpg --no-model   # solo el detector clásico, para comparar
```

Si BiRefNet se desactivó solo (RAM o tiempo), borra `~/.cache/wallpaper-switcher/masks/desactivado` para
reintentar. El registro está en `~/.cache/wallpaper-switcher/contour.log`. Variables de entorno:
`WALLPAPER_HQ_MODEL` (otro modelo ONNX) y `WALLPAPER_HQ_SIZE` (resolución si el modelo la admite; 512 por defecto).
Opciones de `instalar-modelo.sh`: `--rapido` (solo U²-Netp), `--file` y `--hq-file` (usar un modelo que ya tengas).

<details>
<summary>Formato de los auxiliares (v5) y por qué importa</summary>

Los auxiliares están en `~/.cache/wallpaper-switcher/contour/`. `<md5>.c5.png` es un RGBA del tamaño *exacto* del
monitor: el RGB es la imagen nueva tal cual (sin trucos de color) y el alfa guarda la distancia al contorno en
8 bits (basta: las celdas son de ~30 px). `<md5>.c5l.png` es la línea del contorno ya dibujada, con su color de
acento, y `<md5>.c5.json` lleva las profundidades y la extensión vertical del sujeto.

**Importante:** Clutter ejecuta el shader sobre un buffer del tamaño que tiene el actor en pantalla. Por eso el
auxiliar mide exactamente lo que el monitor y el actor queda dentro de la pantalla: si se escalara, o sobresaliera,
los datos del alfa se corromperían.

Si el auxiliar no está (cambias el fondo desde otra herramienta), no mide exactamente lo mismo que el monitor
(p. ej. varios monitores de distinto tamaño), usas escala HiDPI o no hay shader, la extensión usa «Barrido» (el
motivo queda en el registro de la extensión). Requiere `python3-numpy` (lo instala `./instalar-dependencias.sh`);
sin numpy el efecto Contorno cae a «Barrido».

</details>

### Cómo funciona y cómo está optimizado

La extensión mantiene el fondo actual ya decodificado y recortado al monitor; al cambiar pone al instante una capa
con el fondo viejo (tapa el fundido nativo), decodifica el nuevo **sin bloquear** el escritorio y anima por GPU
(recortes y un shader para círculos/ola/diagonal; si el shader falla degrada a fundido). Las pantallas de más de
2560 px usan una textura de 2560 y la GPU escala.

### Limitaciones

- Solo funciona con el ajuste de imagen **zoom** (el de Cinnamon por defecto).
- Las transiciones se dibujan sobre el fondo estático de Cinnamon; un video que ya se esté reproduciendo va
  encima y puede taparlas.

## Solución de problemas

| Síntoma | Qué hacer |
|---|---|
| Notificación "Falta mpv" | `./instalar-animados.sh` |
| Notificación "Sesión Wayland no compatible" | Cierra sesión y, en la pantalla de inicio (LightDM), elige la sesión **Cinnamon** (no "Cinnamon on Wayland") |
| Pantalla negra, o el video sale en una ventana normal | Activa **Modo compatibilidad** en las preferencias |
| El video tapa los iconos del escritorio | Los iconos los dibuja Nemo: reinícialo (`nemo -q && nemo-desktop &`) o prueba **Capa del video → Debajo de ventanas** |
| Los desklets o el panel se ven raros tras instalar la extensión | Desactívala en *Configuración del sistema → Extensiones* o con `./instalar-extension.sh --remove` |
| Colores raros o cortes | Desactiva **Aceleración por hardware** |
| El video no vuelve al iniciar sesión | Comprueba que existe `~/.config/autostart/wallpaper-switcher-live.desktop` (se crea con `./install.sh`) |
| El selector no se abre | Puede haber otra instancia activa (solo se permite una); ciérrala o espera unos segundos |
| La ventana del carrusel no responde al teclado | Lánzala con el atajo de teclado (así recibe el foco) |
| Las transiciones no se ven | `./instalar-extension.sh`, comprueba que el ajuste de imagen sea *zoom* y reinicia Cinnamon (`Ctrl+Alt+Esc`) |
| El efecto Contorno cae siempre a «Barrido» | `python3 contour.py --status` y revisa `~/.cache/wallpaper-switcher/contour.log` |
| Quiero detener el video desde la terminal | `python3 livewall.py --stop` |
| Quiero ver errores del reproductor | `cat ~/.cache/wallpaper-switcher/player.log` |
| El equipo sigue yendo justo con un video | `python3 livewall.py --info VIDEO`; baja el límite a 24 fps / calidad Ligera; revisa `vainfo` |
| Quiero liberar el espacio de las copias | `rm -rf ~/.cache/wallpaper-switcher/video/` (o baja "Caché de videos optimizados") |

## Estructura del proyecto

```
main.py                Punto de entrada (instancia única, primera configuración)
selector.py            Interfaz del carrusel (filtros, favoritos, pywal, aplicar)
widgets.py             Tarjetas, barra de colores y panel HUD del carrusel
workers.py             Tareas en segundo plano (miniaturas, agrupación por color, aplicar)
colors.py              Análisis de color y paletas (Starship, Kitty)
common.py              Constantes y utilidades compartidas
prefs.py               Esquema, valores por defecto y persistencia de las preferencias
prefs_panel.py         Panel de preferencias (Ctrl+,)
settings.py            Ventana de primera configuración (elegir carpeta)
config.py              Rutas y lectura/escritura de config.json y favoritos
wallpaper.py           Listar y aplicar fondos (gsettings de Cinnamon)
thumbnail_cache.py     Miniaturas (imágenes y fotogramas de video) + caché con poda LRU
livewall.py            Control del fondo animado: iniciar / detener / optimizar / estado
player.py              Reproductor: ventanas de escritorio + mpv + pausa inteligente (lo lanza livewall.py)
contour.py             Detección del sujeto y campo de distancia del efecto Contorno (numpy; IA opcional)
cinnamon-extension/    Extensión de Cinnamon con las transiciones del fondo (JavaScript, dentro del compositor)
install.sh             Lanzador, menú, atajo (Cinnamon), autoarranque y extensión
uninstall.sh           Lo deshace todo (no borra tus fondos ni tus preferencias)
instalar-dependencias.sh   Paquetes apt necesarios (incluye mpv, ffmpeg, xprop, VA-API)
instalar-animados.sh   Solo lo necesario para los fondos animados
instalar-extension.sh  Instala/activa/elimina la extensión de transiciones
instalar-modelo.sh     Instala onnxruntime, U²-Netp (rápido) y BiRefNet-lite (calidad, ≈180 MB)
readme.md              Esta documentación
```

## Notas

Pensado y probado únicamente para Linux Mint Cinnamon; no me hago responsable de problemas en otros entornos. La
parte de fondos animados depende de cómo Cinnamon (Muffin y Nemo) apile las ventanas de escritorio: si en tu equipo
se comporta distinto, mira la tabla de *Solución de problemas* (Capa del video y Modo compatibilidad).
