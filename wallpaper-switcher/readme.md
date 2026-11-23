# Wallpaper Switcher — Linux Mint (Cinnamon) con fondos animados

Carrusel HUD translúcido (PyQt6) para cambiar el fondo de pantalla en tiempo real.
Esta versión conserva todo lo de Cinnamon (pywal, Starship, Kitty, tema GTK) y añade
**videos y GIF como fondo de escritorio**, adaptado desde la versión de Fedora.

## Instalación rápida

```bash
cd wallpaper-switcher
chmod +x *.sh

./instalar-dependencias.sh   # PyQt6, Pillow, numpy, mpv, ffmpeg, xprop (pide sudo)
./instalar-modelo.sh         # opcional: IA (U²-Netp + BiRefNet-lite) para detectar al sujeto en el efecto Contorno
./install.sh                 # lanzador + menú + atajo Ctrl+Alt+W + arranque del video
```

Después abre **Wallpaper Switcher** desde el menú o pulsa **Ctrl+Alt+W**. La primera vez te pide la
carpeta donde tienes tus fondos (imágenes **y** videos pueden estar mezclados).

Variantes de `install.sh`:

| Comando | Efecto |
|---|---|
| `./install.sh --shortcut '<Super>w'` | Otro atajo (sintaxis GTK: `<Super>`, `<Control>`, `<Alt>`, `<Shift>`) |
| `./install.sh --no-shortcut` | No crear atajo |
| `./install.sh --no-autostart` | No restaurar el video al iniciar sesión |

Ejecución manual sin instalar: `python3 main.py` (con `--settings` para volver a elegir la carpeta).
Desinstalar: `./uninstall.sh` (no borra tus fondos ni tus preferencias).

> **No muevas la carpeta** después de instalar; si lo haces, vuelve a ejecutar `./install.sh`.

## Controles

| Acción | Teclas / ratón |
|---|---|
| Avanzar | `→` `D` `↓` `S` · rueda abajo |
| Retroceder | `←` `A` `↑` `W` · rueda arriba |
| Aplicar el fondo | `Enter` o doble clic |
| Cerrar | `Esc` |
| Preferencias | `Ctrl+,` |

El resto de funciones (carrusel infinito, filtros por color, favoritos, modo "Expandir", aplicar al
navegar, subcarpetas, orden por nombre/recientes/antiguos/aleatorio, caché de miniaturas) funcionan como siempre.

## Fondos animados (videos y GIF)

Los archivos `.mp4 .webm .mkv .mov .avi .m4v .gif` aparecen en el carrusel con un **▶** sobre la miniatura.
Al aplicarlos, el video se reproduce como fondo de escritorio, **detrás de los iconos y de las ventanas**, en
bucle y sin sonido. Al aplicar una imagen normal, el video se detiene.

**Cómo funciona.** Cinnamon no puede reproducir video como fondo por sí mismo (la versión de Fedora usaba
la extensión Hanabi de GNOME, que no existe en Cinnamon). Aquí un pequeño proceso aparte (`player.py`) abre
una ventana por monitor, la marca como ventana de escritorio y le incrusta **mpv**. Detalles útiles:

- Sigue funcionando aunque cierres el carrusel, y se **restaura solo al iniciar sesión**.
- Además del video, se deja un **fotograma como fondo estático** (se ve en la pantalla de bloqueo y
  mientras arranca el video).
- **pywal, Starship, Kitty y el tema GTK** se generan a partir de ese fotograma, igual que con una imagen.
- Los filtros por color también analizan el fotograma del video.
- Funciona con varios monitores (por defecto el video se reproduce en todos).
- El video llena la pantalla recortando los bordes (equivale al ajuste "zoom").

### Requisitos

- Sesión **X11** de Cinnamon (la predeterminada en Linux Mint). La sesión experimental "Cinnamon on Wayland"
  no está soportada: el programa avisará con una notificación.
- `mpv`, `ffmpeg` (miniaturas) y `x11-utils` (`xprop`) → los instala `./instalar-dependencias.sh`
  (o solo ellos con `./instalar-animados.sh`).

### Preferencias (Ctrl+, → Archivos → Fondos animados)

| Opción | Para qué sirve |
|---|---|
| Incluir fondos animados | Muestra u oculta videos y GIF en el carrusel |
| Detener animado al elegir imagen | Al aplicar una imagen fija se apaga el video |
| Monitores con video | Todos, o solo el principal (menos consumo) |
| Sin sonido | Desactívalo si quieres oír el audio del video |
| Aceleración por hardware | Usa la GPU para decodificar (menos CPU) |
| Modo compatibilidad | Dibuja por software (`--vo=x11`). Úsalo si hay pantalla negra o ventana suelta |
| Capa del video | "Escritorio" (tras los iconos, por defecto) o "Debajo de ventanas" |

#### Optimización de video (Ctrl+, → Archivos → Optimización de video)

| Opción | Para qué sirve |
|---|---|
| Optimizar videos pesados (**activada por defecto**) | Crea en segundo plano una copia H.264 adaptada a tu monitor y cambia a ella. Los GIF se convierten siempre que esté activada |
| Límite de fotogramas por segundo | 24, 30 (defecto) o 60 fps. Los videos con más fps se reducen al optimizar |
| Calidad de la copia optimizada | Ligera / Equilibrada / Alta |
| Pausar si una ventana tapa el escritorio | Pausa el video del monitor cubierto por una ventana maximizada o a pantalla completa y lo reanuda al descubrirlo |
| Pausar con batería | Congela el fondo animado mientras el portátil no está enchufado |
| Caché de videos optimizados | Espacio máximo de las copias (se borran las menos usadas; defecto 2000 MB) |

Qué hace la optimización:

- La copia **no supera la resolución de tu monitor** (ni 2560×1440) y nunca amplía el original.
- Limita fps y bitrate, y **conserva el audio** si el video lo tiene.
- `ffmpeg` corre con la **prioridad más baja** (nice + ionice) y con la mitad de los núcleos: el equipo no se congela mientras convierte.
- Solo se convierte **un video a la vez**. Si eliges otro fondo, la conversión en curso se **cancela** (no se acumulan procesos al navegar rápido).
- Mientras la copia no está lista, el original se reproduce en **modo ligero** (decodificación rápida).
- Los videos que ya son ligeros (H.264, ≤ límite de fps, resolución ajustada y bitrate normal) no se tocan.

Las copias optimizadas se guardan en `~/.cache/wallpaper-switcher/video/` (se pueden borrar sin problema).
Diagnóstico de un video concreto: `python3 livewall.py --info mi_video.mp4` (indica si se optimizaría y por qué).

### Consejos de rendimiento

- **Aceleración por hardware (lo que más pesa):** ejecuta `vainfo`. Si lista perfiles H264/HEVC, tu GPU decodifica el video
  y el consumo de CPU es mínimo (`./instalar-animados.sh` instala `vainfo` y `mesa-va-drivers`; en Intel moderno añade
  `intel-media-va-driver`). Si falla, mpv cae solo a software.
- Con **Optimizar videos pesados** activo, un 4K/60 fps pasa a ser un 1080p/30 ligero tras unos segundos o minutos
  (depende de su duración); verás un breve periodo en modo ligero y luego el cambio.
- Con varios monitores, cada uno decodifica el video por separado: usa **Monitores con video → Solo el principal** si notas carga.
- El fondo **se pausa solo** cuando una ventana maximizada o a pantalla completa tapa el escritorio, y opcionalmente con batería.
- Para el mínimo consumo: 24 fps, calidad Ligera y 1080p (o menos).

### Equipos modestos (Celeron, Atom…)

Los valores por defecto están ajustados para CPU de gama baja: copia optimizada máx. 1080p / 24 fps / calidad
Ligera, video solo en el monitor principal, mpv con prioridad `SCHED_IDLE` (cede la CPU al navegador y al resto de
apps), pausa por ventana que tapa el escritorio calculada en un hilo aparte, y menos miniaturas en memoria.
Si ya tenías `~/.config/wallpaper-switcher/preferences.json`, tus valores antiguos se respetan: bórralo (o usa el
botón de restaurar en Preferencias) para adoptar los nuevos. Si `vainfo` no lista perfiles H264, instala
`intel-media-va-driver` (lo hace `./instalar-animados.sh` en GPU Intel).

## Transiciones del fondo (estilo Hyprland / swww)

Cinnamon solo cambia de imagen con su propio fundido. La extensión **Wallpaper Transitions**
(`cinnamon-extension/wallpaper-transitions@wallpaper-switcher`) dibuja efectos propios **dentro del compositor
de Cinnamon**: una capa de actores Clutter justo encima del fondo y debajo de todo lo demás. Por eso los
**desklets, los iconos y las ventanas no se ven afectados** (no hay ventanas X extra) y en reposo no queda
ningún actor.

```bash
./instalar-extension.sh          # copia, activa y recarga la extensión (también lo hace ./install.sh)
./instalar-extension.sh --remove
```

Si no ves nada tras instalar: reinicia Cinnamon (`Ctrl+Alt+Esc`). Errores: Looking Glass (`Alt+F2` → `lg`) o
`journalctl --user -b | grep wallpaper-transitions`.

Se configura en **Ctrl+, → Comportamiento → Transición del fondo de escritorio** (la extensión lee
`~/.config/wallpaper-switcher/preferences.json`; con el interruptor desactivado, o sin ese archivo, usa
valores por defecto). Anima **cualquier** cambio de `picture-uri`, también si lo haces desde otra herramienta.

| Opción | Valores |
|---|---|
| Efecto | **Contorno del sujeto**, Barrido, Ola, Círculo (crece / se cierra), Fundido, Deslizar, Zoom, Persianas, Puertas, Mosaico, Aleatorio |
| Dirección | Aleatoria, derecha, izquierda, abajo, arriba, diagonal |
| Origen (efectos circulares) | Centro, cursor, aleatorio |
| Duración | 300–3000 ms |

**Efecto «Contorno del sujeto».** Pensado para fondos con un sujeto claro (una persona, un animal, un objeto). Se desarrolla
en tres fases: **(1)** una línea fina, de un color tomado de la propia foto, dibuja el contorno del sujeto de arriba abajo
mientras el fondo viejo se oscurece un poco; **(2)** el sujeto se rellena con el fondo nuevo desde esa línea hacia dentro;
**(3)** el resto del fondo avanza desde el sujeto en celdas pequeñas (~30 px a 1080p), cada una un círculo o un cuadrado que
crece, con borde suave. Dura 1.6 veces lo que configures en «duración» y va a velocidad lineal. Funciona también con fondos
animados (se hace sobre su primer fotograma y el video arranca cuando termina la transición). Está inspirado en el efecto de
[pocket-dots](https://github.com/diegoMalagrida/pocket-dots) (`wallfx`), con la misma idea de fondo: **el recorte bueno
se hace con IA una sola vez por fondo, en segundo plano, y queda en caché; aplicar un fondo nunca espera a la IA pesada.**

Detección, en tres niveles (se usa el mejor disponible):

1. **BiRefNet-lite** (≈180 MB, `./instalar-modelo.sh`). Recorte de alta calidad. Corre en un proceso aparte con
   prioridad baja, con vigilancia de RAM y de tiempo (si no cabe o tarda demasiado se mata el proceso, se desactiva solo y
   queda anotado en el registro) y se cierra solo tras 20 s sin trabajo. El recorte se guarda en
   `~/.cache/wallpaper-switcher/masks/` y, al terminar, el auxiliar del fondo se regenera con él.
2. **U²-Netp** (4.7 MB). Rápido; se usa mientras el recorte bueno aún no está listo.
3. **Detector clásico** (color en Lab + prior de fondo + Otsu) si no hay ningún modelo. Funciona con sujetos que
   contrastan con el fondo; falla con primeros planos o fondos parecidos al sujeto.

Si no hay sujeto definido (un paisaje, un bosque, una textura) no se dibuja ninguna línea: el nuevo fondo nace de tres
puntos al azar (distintos en cada cambio) y crece como colonias de esas mismas celdas, que se fusionan.
al azar (distintos en cada cambio) y crece como colonias que se fusionan, con el mismo borde liso.

Cuándo se calcula: el selector precalcula el contorno de la miniatura resaltada (300 ms después de pararte; en un video, el de su
fotograma) y pone en la
cola de BiRefNet esa imagen y las 8 vecinas más cercanas. Al aplicar, si el auxiliar no está listo se espera como máximo
4 s (`WAIT_APPLY`; los cálculos en segundo plano no lo retrasan); pasado ese tiempo se aplica con «Barrido» y el cálculo sigue en segundo plano para la próxima vez.

Comandos útiles (desde la carpeta del programa):

```bash
python3 contour.py --status                  # qué modelos hay, cuáles se usan, últimas líneas del registro
python3 contour.py --warm ~/Imágenes/Fondos  # precalcula los recortes de toda una carpeta (queda en caché)
python3 contour.py --bench foto.jpg          # cuánto tarda BiRefNet en TU equipo y cuánta RAM usa
python3 contour.py --debug foto.jpg          # dibuja el contorno detectado en rojo en foto_debug.png
python3 contour.py --debug foto.jpg --no-model   # solo el detector clásico, para comparar
```

Si BiRefNet se desactivó solo (RAM o tiempo), borra `~/.cache/wallpaper-switcher/masks/desactivado` para reintentar. El
registro está en `~/.cache/wallpaper-switcher/contour.log`. Variables: `WALLPAPER_HQ_MODEL` (otro modelo ONNX) y
`WALLPAPER_HQ_SIZE` (resolución si el modelo la admite; 512 por defecto).

Formato de los auxiliares (v5, en `~/.cache/wallpaper-switcher/contour/`): `<md5>.c5.png` es un RGBA del tamaño *exacto* del
monitor: el RGB es la imagen nueva tal cual (sin trucos de color) y el alfa guarda la distancia al contorno en 8 bits
(basta: las celdas son de ~30 px). `<md5>.c5l.png` es la línea del contorno ya dibujada, con su color de acento, y
`<md5>.c5.json` lleva las profundidades y la extensión vertical del sujeto. **Importante:** Clutter ejecuta el shader sobre un
buffer del tamaño que tiene el actor en pantalla. Por eso el auxiliar mide exactamente lo que el monitor y el actor queda
dentro de la pantalla: si se escalara, o sobresaliera, los datos del alfa se corromperían.

Si el auxiliar no está (cambias el fondo desde otra herramienta), no mide exactamente lo mismo que el monitor (p. ej. varios monitores de
distinto tamaño), usas escala HiDPI o no hay shader, la extensión usa «Barrido» (el motivo queda en el registro de la
extensión). Requiere `python3-numpy` (lo instala `./instalar-dependencias.sh`); sin numpy el efecto
Contorno cae a «Barrido».

Cómo funciona y cómo está optimizado: mantiene el fondo actual ya decodificado y recortado al monitor; al cambiar
pone al instante una capa con el fondo viejo (tapa el fundido nativo), decodifica el nuevo **sin bloquear** el
escritorio y anima por GPU (recortes y un shader para círculos/ola/diagonal; si el shader falla degrada a
fundido). Pantallas de más de 2560 px usan textura de 2560 y la GPU escala.

Limitaciones: solo con el ajuste de imagen **zoom** (el de Cinnamon por defecto); si hay un fondo animado
(video) la transición no se ve; versiones probadas en teoría: Cinnamon 5.4–6.4.

## Solución de problemas

| Síntoma | Qué hacer |
|---|---|
| Notificación "Falta mpv" | `./instalar-animados.sh` |
| Notificación "Sesión Wayland no compatible" | Cierra sesión y entra con la sesión **Cinnamon** (X11) |
| Pantalla negra, o el video sale en una ventana normal | Activa **Modo compatibilidad** en las preferencias |
| El video tapa los iconos del escritorio | Prueba **Capa del video → Debajo de ventanas**, o reinicia Nemo (`nemo -q && nemo-desktop &`) |
| Colores raros o cortes | Desactiva **Aceleración por hardware** |
| El video no vuelve al iniciar sesión | Comprueba que existe `~/.config/autostart/wallpaper-switcher-live.desktop` (se crea con `./install.sh`) |
| La ventana del carrusel no responde al teclado | Lánzala con el atajo de teclado (así recibe el foco) |
| Quiero detener el video desde la terminal | `python3 livewall.py --stop` |
| Quiero ver errores del reproductor | `cat ~/.cache/wallpaper-switcher/player.log` |
| El equipo sigue yendo justo con un video | `python3 livewall.py --info VIDEO`; baja el límite a 24 fps / calidad Ligera; revisa `vainfo` |
| Quiero liberar el espacio de las copias | `rm -rf ~/.cache/wallpaper-switcher/video/` (o baja "Caché de videos optimizados") |

## Estructura del proyecto

```
main.py              Punto de entrada
selector.py          Interfaz del carrusel (filtros, favoritos, pywal, aplicar)
widgets.py           Tarjetas, barra de colores y panel HUD del carrusel
workers.py           Tareas en segundo plano (miniaturas, agrupación por color, aplicar)
colors.py            Análisis de color y paletas (starship, kitty)
common.py            Constantes y utilidades compartidas
prefs.py / prefs_panel.py   Preferencias (Ctrl+,)
wallpaper.py         Listar y aplicar fondos (gsettings de Cinnamon)
thumbnail_cache.py   Miniaturas (imágenes y fotogramas de video) + caché
livewall.py          Control del fondo animado: iniciar / detener / optimizar (cola, cancelación, poda) / estado
cinnamon-extension/ Extensión de Cinnamon con las transiciones del fondo (JavaScript, dentro del compositor)
instalar-extension.sh  Instala/activa/elimina esa extensión
contour.py           Detección del sujeto y campo de distancia del efecto Contorno (numpy; IA opcional en 2.º plano)
instalar-modelo.sh   Instala onnxruntime, U²-Netp (rápido) y BiRefNet-lite (calidad, ≈180 MB)
player.py            Reproductor: ventanas de escritorio + mpv de bajo consumo + pausa inteligente (lo lanza livewall.py)
settings.py / config.py     Primera configuración y rutas
install.sh / uninstall.sh   Lanzador, menú, atajo (Cinnamon) y autoarranque
instalar-dependencias.sh / instalar-animados.sh
```

## Cambios respecto a la versión de Fedora

- Hanabi (extensión de GNOME) → reproductor propio con **mpv** para Cinnamon/X11 (`player.py`).
- Se mantiene todo lo de Mint: pywal, corrección de saturación, Starship, Kitty y tema GTK
  (para videos se usa un fotograma).
- Atajo de teclado e instalación adaptados a Cinnamon (`org.cinnamon.desktop.keybindings`).
- El video se guarda en `~/.config/wallpaper-switcher/live.json` para restaurarlo al iniciar sesión.
- Rutas con espacios, tildes o `#` se codifican correctamente al fijar el fondo.

## Notas

La base original está pensada y probada para Linux Mint Cinnamon. No me hago responsable de problemas en otros
entornos. La parte de fondos animados depende de cómo Cinnamon (Muffin/Nemo) apile las ventanas de escritorio:
si en tu equipo se comporta distinto, mira la tabla de *Solución de problemas* (Capa del video y Modo compatibilidad).
