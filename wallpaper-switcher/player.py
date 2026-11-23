#!/usr/bin/env python3
"""Reproductor de fondo animado para Cinnamon (X11).

Crea una ventana sin bordes por monitor, la marca como ventana de ESCRITORIO
(_NET_WM_WINDOW_TYPE_DESKTOP, así Muffin la deja detrás de todo, incluidos los
iconos de Nemo) y le incrusta mpv con --wid.

Ahorro de recursos:
  * mpv usa opciones de bajo consumo (escalado bilineal, sin cache de disco, hilos acotados,
    prioridad baja) y, si el video original es pesado, decodificación rápida.
  * Se PAUSA solo el monitor cuyo escritorio queda tapado por una ventana maximizada o a
    pantalla completa (preferencia "video_pause_covered"), y todos los monitores si el equipo
    va con batería (preferencia "video_pause_battery").
  * Si mpv se cae con aceleración por hardware, se relanza por software.

No se usa directamente: lo lanza livewall.py y lee su configuración de
~/.config/wallpaper-switcher/live.json.

    python3 player.py --restore            # reproduce lo guardado en live.json
    python3 player.py --restore --delay 4  # igual, esperando 4 s (inicio de sesión)
"""
import os
import sys

# Qt debe usar X11 aunque la sesión anunciara otra cosa (antes de importar Qt).
os.environ["QT_QPA_PLATFORM"] = "xcb"

import argparse
import ctypes
import functools
import itertools
import json
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time

from PyQt6.QtCore import Qt, QTimer, QSocketNotifier
from PyQt6.QtWidgets import QApplication, QWidget

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livewall  # noqa: E402

# Margen extra (ms) tras la duración de la transición antes de mostrar el video. Súbelo si ves un corte
# al final de la animación; bájalo (o pon 0) si quieres que el video entre antes.
REVEAL_MARGIN_MS = 150

_surface_ids = itertools.count()


def _child_setup():
    """Se ejecuta en el hijo (mpv) antes de lanzarlo: muere con su padre (incluso
    con SIGKILL) y corre con prioridad algo menor para no frenar el escritorio."""
    try:
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except Exception:
        pass
    # SCHED_IDLE: mpv solo usa la CPU que sobra. Con un navegador o cualquier otra app pidiendo CPU,
    # el fondo cede (puede perder fotogramas, que es lo deseable en un equipo modesto).
    try:
        os.nice(5)          # SCHED_IDLE dejaba a mpv sin CPU ante cualquier otra carga (tirones)
    except OSError:
        pass


@functools.lru_cache(maxsize=1)
def _mpv_options():
    """Opciones que entiende este mpv (una opción desconocida es fatal: mpv no arranca)."""
    try:
        out = subprocess.run(["mpv", "--no-config", "--list-options"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return frozenset()
    return frozenset(m.group(1) for m in re.finditer(r"^\s*--([a-z0-9-]+)", out, re.M))


def _mpv_has(name):
    opts = _mpv_options()
    name = name[3:] if name.startswith("no-") else name
    return not opts or name in opts          # si no se pudo listar, se asume que existe


def _xprop(wid, *args):
    try:
        subprocess.run(["xprop", "-id", str(wid), *args], capture_output=True, timeout=3)
    except Exception:
        pass


def _ipc_dir():
    d = os.environ.get("XDG_RUNTIME_DIR")
    return d if d and os.path.isdir(d) else tempfile.gettempdir()


class Surface(QWidget):
    """Una ventana de escritorio que cubre un monitor y contiene un mpv."""

    def __init__(self, screen, cfg, reveal_at=0):
        super().__init__()
        self.screen_obj = screen
        # reveal_at (ms epoch): hasta entonces la ventana es transparente, así se ve la transición del
        # fondo que hay debajo y no un recuadro negro mientras mpv carga. 0 = visible desde el principio.
        self.reveal_at = reveal_at
        self.revealed = reveal_at <= 0
        self._reveal_timer = None
        self._ready_at = None
        self.cfg = cfg
        self.proc = None
        self.restarts = 0
        self.paused = False
        self.sock_path = os.path.join(_ipc_dir(), f"wps-mpv-{os.getpid()}-{next(_surface_ids)}.sock")

        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        if cfg["layer"] == "below":
            flags |= Qt.WindowType.WindowStaysOnBottomHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: black;")
        self.setGeometry(screen.geometry())
        self.wid = int(self.winId())

        self._mark_desktop()
        if not self.revealed:
            self.setWindowOpacity(0.01)   # casi invisible, pero NO 0: con 0 Muffin deja de actualizar la ventana
        self.show()
        # Muffin lee el tipo de ventana al mapearla y también si cambia después:
        # se repite tras mostrarla para asegurar que queda en la capa del escritorio.
        QTimer.singleShot(150, self._after_show)

    def native_rect(self):
        """(x, y, ancho, alto) del monitor en píxeles nativos de X11."""
        g = self.screen_obj.geometry()
        r = self.screen_obj.devicePixelRatio()
        return g.x(), g.y(), g.width() * r, g.height() * r

    def _mark_desktop(self, state=True):
        if self.cfg["layer"] != "desktop":
            return
        _xprop(self.wid, "-f", "_NET_WM_WINDOW_TYPE", "32a",
               "-set", "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DESKTOP")
        if state:
            # _NET_WM_STATE solo se escribe como propiedad ANTES de mapear la ventana (estado inicial).
            # Con la ventana ya mapeada, el estándar exige un mensaje al gestor; reescribirla a mano
            # puede dejar a Muffin con un estado de ventanas incoherente.
            _xprop(self.wid, "-f", "_NET_WM_STATE", "32a", "-set", "_NET_WM_STATE",
                   "_NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_SKIP_PAGER, _NET_WM_STATE_STICKY")

    def _after_show(self):
        self._mark_desktop(state=False)
        self.setGeometry(self.screen_obj.geometry())
        self.lower()          # por debajo de la ventana de escritorio de Nemo (iconos)
        self.start_mpv()
        if not self.revealed:
            self._reveal_timer = QTimer(self)
            self._reveal_timer.timeout.connect(self._try_reveal)
            self._reveal_timer.start(80)

    def _try_reveal(self):
        """Se muestra cuando ya pasó la transición Y mpv dibujó su primer fotograma (o tras un tope de 4 s)."""
        now = time.time() * 1000
        if now < self.reveal_at:
            return
        if self._ready_at is None and self._first_frame():
            self._ready_at = now
        if now >= self.reveal_at + 6000 or (self._ready_at is not None and now >= self._ready_at + 250):
            self.revealed = True
            self._reveal_timer.stop()
            self.setWindowOpacity(1.0)
            # Empujón de redibujado: obliga a mpv a emitir un fotograma nuevo y a Muffin a repintar la ventana
            QTimer.singleShot(60, lambda: self._ipc(["seek", 0, "relative+exact"]))

    def _first_frame(self):
        n = self._ipc_get("estimated-frame-number")
        return bool(self._ipc_get("vo-configured")) and isinstance(n, (int, float)) and n >= 3

    # ---- mpv ---------------------------------------------------------------
    def _mpv_cmd(self):
        cfg = self.cfg
        # Tras dos caídas seguidas se descarta la aceleración por hardware (drivers problemáticos)
        use_hw = cfg["hwdec"] and not cfg["compat"] and self.restarts < 2
        threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        cmd = [
            "mpv", f"--wid={self.wid}", "--no-config", "--msg-level=all=error", "--no-terminal",
            "--load-scripts=no", "--loop-file=inf", "--idle=no", "--force-window=yes",
            "--no-osc", "--osd-level=0", "--no-input-default-bindings", "--input-cursor=no",
            "--input-vo-keyboard=no", "--cursor-autohide=always",
            f"--input-ipc-server={self.sock_path}",   # para pausar/reanudar sin matar el proceso
            "--panscan=1.0",                 # rellena la pantalla recortando (como 'zoom')
            "--sub-auto=no", "--sid=no", "--ytdl=no", "--stop-screensaver=no",
            # --- bajo consumo: un fondo no necesita calidad de cine ---
            "--scale=bilinear", "--cscale=bilinear", "--dscale=bilinear",
            "--dither=no", "--deband=no", "--interpolation=no",
            "--correct-downscaling=no", "--linear-downscaling=no", "--sigmoid-upscaling=no",
            "--framedrop=vo", "--video-sync=desync",
            "--cache=no", "--demuxer-max-bytes=24MiB", "--demuxer-max-back-bytes=0",
            f"--vd-lavc-threads={threads}",
            "--hwdec=auto-safe" if use_hw else "--hwdec=no",
        ]
        if use_hw:
            cmd.append("--hwdec-codecs=all")
        if cfg.get("heavy") or not use_hw:
            # Decodificación por software (o original pesado): se sacrifica calidad por CPU
            cmd += ["--vd-lavc-fast", "--vd-lavc-skiploopfilter=nonref"]
        for extra in ("--audio-file-auto=no", "--gpu-dumb-mode=yes", "--demuxer-thread=yes", "--swapchain-depth=1", "--load-stats-overlay=no", "--load-osd-console=no",
                      "--load-auto-profiles=no", "--cache-pause=no", "--demuxer-readahead-secs=2"):
            if _mpv_has(extra.split("=")[0].lstrip("-")):
                cmd.append(extra)
        if cfg["compat"]:
            cmd.append("--vo=x11")           # modo compatibilidad: sin GPU/OpenGL, funciona en todas partes
        if cfg["mute"]:
            cmd += ["--mute=yes", "--no-audio"]
        else:
            cmd += ["--volume=40", "--audio-display=no"]
        # una opción que este mpv no conozca es fatal: se descartan las desconocidas
        cmd = cmd[:1] + [a for a in cmd[1:] if not a.startswith("--") or _mpv_has(a[2:].split("=")[0])]
        if self.restarts >= 3:      # último recurso: configuración mínima
            cmd = ["mpv", f"--wid={self.wid}", "--no-config", "--loop-file=inf", "--panscan=1.0", "--no-audio", "--hwdec=no"]
        cmd.append(cfg["play"])
        return cmd

    def start_mpv(self):
        self.paused = False
        try:
            try:
                err = open(livewall.LOG_FILE, "a")
            except OSError:
                err = subprocess.DEVNULL
            self.proc = subprocess.Popen(self._mpv_cmd(), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=err,
                                         preexec_fn=_child_setup)
        except OSError as e:
            print(f"No se pudo lanzar mpv: {e}", flush=True)

    def _ipc(self, command):
        """Envía una orden JSON al mpv por su socket. True si llegó."""
        if self.proc is None or self.proc.poll() is not None:
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(0.3)
            s.connect(self.sock_path)
            s.sendall(json.dumps({"command": command}).encode() + b"\n")
            return True
        except OSError:
            return False        # el socket aún no existe (mpv arrancando): se reintenta en el siguiente ciclo
        finally:
            s.close()

    def _ipc_get(self, prop):
        """Lee una propiedad de mpv por su socket. None si no está disponible (aún)."""
        if self.proc is None or self.proc.poll() is not None:
            return None
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(0.3)
            s.connect(self.sock_path)
            s.sendall(json.dumps({"command": ["get_property", prop], "request_id": 77}).encode() + b"\n")
            buf = b""
            for _ in range(20):
                buf += s.recv(4096)
                for line in buf.split(b"\n"):
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("request_id") == 77:
                        return msg.get("data") if msg.get("error") == "success" else None
            return None
        except (OSError, ValueError):
            return None
        finally:
            s.close()

    def set_paused(self, paused):
        if paused != self.paused and self._ipc(["set_property", "pause", bool(paused)]):
            self.paused = paused

    def check(self):
        """Si mpv se cae, se relanza (con un límite para no entrar en bucle)."""
        if self.proc is not None and self.proc.poll() is not None and self.restarts < 5:
            self.restarts += 1
            print(f"mpv terminó con código {self.proc.returncode}; reinicio {self.restarts}/5"
                  f"{' (sin hwdec)' if self.restarts >= 2 else ''}", flush=True)
            self.start_mpv()

    def shutdown(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        try:
            os.remove(self.sock_path)
        except OSError:
            pass
        self.close()


class PauseMonitor(threading.Thread):
    """Consulta X11 (xprop/xwininfo) y la batería FUERA del hilo de la interfaz.

    Antes se hacía con subprocess en el hilo de Qt: cada consulta podía bloquear la ventana del
    fondo hasta 4,5 s si el servidor X iba cargado (justo cuando se minimiza/restaura una ventana
    pesada), dejando de atender a Muffin. Ahora el hilo de Qt solo lee el último resultado."""

    INTERVAL = 2.0

    def __init__(self):
        super().__init__(name="pause-monitor", daemon=True)
        self.state = (False, None)        # (pausar todo, ventana que tapa)
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        while not self._halt.is_set():
            try:
                prefs = livewall.read_prefs()
                pause_all = bool(prefs.get("video_pause_battery", False)) and livewall.on_battery()
                covered = None
                if not pause_all and prefs.get("video_pause_covered", True):
                    covered = livewall.covering_window()
                self.state = (pause_all, covered)
            except Exception:
                self.state = (False, None)    # ante cualquier fallo, el video sigue (nunca se queda congelado)
            self._halt.wait(self.INTERVAL)


class Player:
    def __init__(self, app, cfg, stamp, reveal_at=0):
        self.app = app
        self.cfg = cfg
        self.stamp = stamp
        self.reveal_at = reveal_at
        self.surfaces = []
        self._rebuild_timer = QTimer()
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self._rebuild_if_changed)

        self.build(reveal_at)
        # Con transición: los reproductores anteriores se retiran ya (no deben tapar la animación).
        # Sin transición: cuando el nuevo ya dibuja.
        self._retire_tries = 0
        self._retire_timer = QTimer(app)
        self._retire_timer.timeout.connect(self._retire_when_visible)
        self._retire_timer.start(200)

        watchdog = QTimer(app)
        watchdog.timeout.connect(self._tick)
        watchdog.start(2000)
        self._watchdog = watchdog

        # Pausa por ventanas que tapan el escritorio / batería: el hilo consulta, Qt solo aplica
        self._monitor = PauseMonitor()
        self._monitor.start()
        pause = QTimer(app)
        pause.timeout.connect(self._update_pause)
        pause.start(1000)
        self._pause = pause

        self._signature = self._layout_signature()
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screens_changed)
        for s in app.screens():
            s.geometryChanged.connect(self._on_screens_changed)

    def _layout_signature(self):
        out = []
        for s in self._screens():
            g = s.geometry()
            out.append((s.name(), g.x(), g.y(), g.width(), g.height()))
        return tuple(out)

    def _on_screen_added(self, screen):
        screen.geometryChanged.connect(self._on_screens_changed)   # antes solo se vigilaban las del arranque
        self._rebuild_timer.start(800)

    def _on_screens_changed(self, *_):
        self._rebuild_timer.start(800)

    def _screens(self):
        if self.cfg["screens"] == "primary":
            return [self.app.primaryScreen()]
        return self.app.screens()

    def _rebuild_if_changed(self):
        """Reconstruir cierra y vuelve a abrir las ventanas de escritorio y los mpv: solo se hace
        si de verdad cambió la disposición de monitores (no por avisos espurios del servidor)."""
        sig = self._layout_signature()
        if sig != self._signature:
            self._signature = sig
            self.build()

    def build(self, reveal_at=0):
        for s in self.surfaces:
            s.shutdown()
        self.surfaces = [Surface(s, self.cfg, reveal_at) for s in self._screens()]

    def _retire_when_visible(self):
        self._retire_tries += 1
        if all(x.revealed for x in self.surfaces) or self._retire_tries > 40:
            self._retire_timer.stop()
            QTimer.singleShot(300, self._retire_old)

    def _retire_old(self):
        for pid in livewall.player_pids(older_than=self.stamp):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def _tick(self):
        for s in self.surfaces:
            s.check()
        # El archivo desapareció (disco desmontado, borrado…): no tiene sentido seguir
        if not os.path.exists(self.cfg["play"]):
            self.app.quit()

    def _update_pause(self):
        pause_all, covered = self._monitor.state       # covered: None, "all" o rect (x, y, w, h)
        for s in self.surfaces:
            s.set_paused(pause_all or self._is_covered(s, covered))

    @staticmethod
    def _is_covered(surface, cover):
        if cover is None:
            return False
        if cover == "all":
            return True
        cx, cy = cover[0] + cover[2] / 2, cover[1] + cover[3] / 2     # centro de la ventana
        x, y, w, h = surface.native_rect()
        return x <= cx < x + w and y <= cy < y + h

    def shutdown(self):
        self._monitor.stop()
        for s in self.surfaces:
            s.shutdown()
        self.surfaces = []


def _install_signal_handling(app):
    """SIGTERM/SIGINT sin temporizador: el descriptor de despertar avisa a Qt solo cuando llega la señal
    (antes un QTimer despertaba el proceso 3 veces por segundo para siempre)."""
    import socket as _socket
    rsock, wsock = _socket.socketpair()
    rsock.setblocking(False)
    wsock.setblocking(False)
    signal.set_wakeup_fd(wsock.fileno())
    notifier = QSocketNotifier(rsock.fileno(), QSocketNotifier.Type.Read, app)

    def on_wake():
        try:
            rsock.recv(64)
        except OSError:
            pass
        app.quit()

    notifier.activated.connect(on_wake)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: None)      # el aviso lo da el wakeup fd
    app._sig_keepalive = (rsock, wsock, notifier)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", action="store_true", help="usa ~/.config/wallpaper-switcher/live.json")
    ap.add_argument("--stamp", type=int, default=0, help="uso interno (orden de arranque)")
    ap.add_argument("--delay", type=float, default=0.0, help="segundos de espera antes de empezar")
    args = ap.parse_args()

    if args.delay > 0:
        time.sleep(args.delay)

    st = livewall.read_state()
    play = st.get("play") or st.get("video")
    heavy = bool(st.get("heavy", False))
    if (not play or not os.path.exists(play)) and st.get("video") and os.path.exists(st["video"]):
        play, heavy = st["video"], True      # la copia optimizada se borró: vuelve al original (modo ligero)
    if not play or not os.path.exists(play):
        return 0
    if not shutil.which("mpv") or not shutil.which("xprop"):
        print("Faltan mpv o xprop (ejecuta ./instalar-animados.sh)", flush=True)
        return 1

    cfg = {
        "play": play,
        "layer": st.get("layer", "desktop"),
        "screens": st.get("screens", "all"),
        "mute": bool(st.get("mute", True)),
        "hwdec": bool(st.get("hwdec", True)),
        "compat": bool(st.get("compat", False)),
        "heavy": heavy,
    }

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    stamp = args.stamp or int(time.time() * 1000)

    # wallpaper.py ya retrasó el arranque hasta que acabó la transición: solo se espera al primer fotograma
    reveal_at = stamp + REVEAL_MARGIN_MS

    player = Player(app, cfg, stamp, reveal_at)
    app.aboutToQuit.connect(player.shutdown)
    _install_signal_handling(app)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
