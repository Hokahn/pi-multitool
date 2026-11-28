"""Hauptprogramm: verbindet Hardware, Modi, Display, Webseite und Netzwerk."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time

import numpy as np

from . import __version__
from .config import Config
from .core.audio import AudioBus
from .core.battery import Battery
from .core.camera import CameraBus
from .core.events import EventLog
from .core.io import Inputs, Siren
from .core.leds import LedRing
from .core.storage import Storage
from .modes import MODE_CLASSES
from .net.network import Network
from .ui import draw
from .ui.display import Display
from .ui.menu import Menu

log = logging.getLogger("app")
W, H = 800, 480


class App:
    def __init__(self, data_dir: str, sim: bool = False, port: int | None = None):
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.sim = sim
        self.cfg = Config(data_dir)
        self.port = port or (8080 if sim else int(self.cfg.get("network", "web_port") or 80))
        self.simulator = None
        if sim:
            from .sim import Simulator
            self.simulator = Simulator(self.cfg)
        self.events = EventLog(os.path.join(data_dir, "events.db"))
        self.storage = Storage(self.cfg, data_dir)
        self.audio = AudioBus(self.cfg, self.simulator)
        self.camera = CameraBus(self.simulator)
        self.leds = LedRing(self.cfg, sim)
        self.siren = Siren(self.cfg, sim)
        self.inputs = Inputs(sim)
        self.battery = Battery(self.cfg, data_dir, self.simulator)
        self.network = Network(self.cfg, sim)
        self.modes = [cls(self) for cls in MODE_CLASSES]
        self.mode = None
        self.menu = Menu(self)
        self.display = Display(self)
        self.frame = None
        self.frame_id = 0
        self.jpeg_cache = (-1, b"")
        self.toast = ("", 0.0)
        self.started = time.time()
        self._stop = threading.Event()
        self._switch_lock = threading.Lock()
        self.cpu_temp = 0.0
        self.disk = {}

    # ---------------------------------------------------------------- Start
    def run(self) -> int:
        log.info("Pi Multitool %s startet (%s)", __version__, "Simulation" if self.sim else "Hardware")
        self.leds.start()
        self.audio.start()
        self.battery.on_level = self._battery_level
        self.battery.start()
        self.network.start()
        self._wire_inputs()
        start = self.cfg.get("system", "start_mode")
        if start == "last":
            start = self.cfg.get("state", "last_mode") or "acoustic_cam"
        self.switch_mode(start)
        threading.Thread(target=self._compose_loop, name="compositor", daemon=True).start()
        threading.Thread(target=self._housekeeping, name="housekeeping", daemon=True).start()
        self.display.start()
        from .web.server import start_web
        start_web(self, self.port)
        log.info("Webseite: http://%s:%d", "localhost" if self.sim else "multitool.local", self.port)
        signal.signal(signal.SIGTERM, lambda *a: self._stop.set())
        try:
            while not self._stop.is_set():
                self._stop.wait(1)
        except KeyboardInterrupt:
            pass
        self._shutdown_app()
        return 0

    def _shutdown_app(self):
        log.info("Beende ...")
        try:
            if self.mode:
                self.mode.stop()
            self.camera.stop()
            self.siren.stop()
            self.leds.stop()
            self.audio.stop()
        except Exception:
            log.exception("Beim Beenden")

    # ---------------------------------------------------------------- Modi
    def mode_index(self) -> int:
        return self.modes.index(self.mode) if self.mode in self.modes else 0

    def get_mode(self, key):
        for m in self.modes:
            if m.key == key:
                return m
        return None

    def switch_mode(self, key: str):
        new = self.get_mode(key) or self.modes[0]
        with self._switch_lock:
            if new is self.mode:
                return
            if self.mode is not None:
                log.info("Modus aus: %s", self.mode.name)
                self.mode.stop()
            self.leds.override_brightness = None
            self.leds.stop_alarm()
            self.siren.stop()
            if new.needs_camera:
                size, fps = getattr(new, "camera_size", ((1920, 1080), 30))
                self.camera.start(size, fps)
            else:
                self.camera.stop()
            if self.simulator:
                self.simulator.set_orientation(new.orientation)
            self.mode = new
            self.cfg.set("state", "last_mode", new.key)
            log.info("Modus an: %s", new.name)
            new.start()
            self.show_toast(new.name)
            self.leds.show_mode_select(self.mode_index() + 1, new.color)

    def show_toast(self, text, seconds=2.5):
        self.toast = (text, time.time() + seconds)

    def touch(self, fx, fy):
        """Antippen (0..1 im 800x480-Bild) -> auf Kamerabild umrechnen."""
        self.display.wake()
        if self.menu.kind:
            return
        x0, y0, nw, nh = draw.fit_rect(640, 360, W, H)
        ix = (fx * W - x0) / nw
        iy = (fy * H - y0) / nh
        if 0 <= ix <= 1 and 0 <= iy <= 1:
            self.mode.on_touch(ix, iy)

    # ---------------------------------------------------------------- Eingaben
    def _wire_inputs(self):
        def act(fn):
            def wrapped(*a):
                was_on = self.display.on
                self.display.wake()
                if not was_on and not self.sim:
                    return          # erster Tastendruck weckt nur das Display
                fn(*a)
            return wrapped
        i = self.inputs
        mn = self.menu
        i.on("enc", act(mn.enc))
        i.on("enc_press", act(mn.enc_press))
        i.on("up", act(mn.up))
        i.on("down", act(mn.down))
        i.on("left", act(mn.left))
        i.on("right", act(mn.right))
        i.on("ok", act(mn.ok))
        i.on("ok_long", act(self.shutdown))
        i.on("left_long", act(self._toggle_network))
        i.on("rec", act(self._rec_short))
        i.on("rec_long", act(lambda: self.mode.on_record_long()))

    def _rec_short(self):
        if self.menu.kind:          # im Menü schliesst die Aufnahme-Taste nur das Menü
            self.menu.close()
            return
        self.mode.on_record()

    def _toggle_network(self):
        self.show_toast("Hotspot aus" if self.network.mode == "hotspot" else "Hotspot an")
        self.network.toggle()

    def network_label(self):
        n = self.network
        return {"wifi": f"WLAN {n.ssid}", "hotspot": "Hotspot (tippen = WLAN)", "aus": "aus"}.get(n.mode, "offline")

    def display_off(self):
        self.display.sleep()

    def shutdown(self, reboot=False):
        self.show_toast("Neustart ..." if reboot else "Fahre herunter ...", 30)
        log.warning("Herunterfahren angefordert (reboot=%s)", reboot)

        def run():
            time.sleep(1.0)
            self._shutdown_app()
            if not self.sim:
                subprocess.run(["systemctl", "reboot" if reboot else "poweroff"])
            self._stop.set()
        threading.Thread(target=run, daemon=True).start()

    def _battery_level(self, lvl):
        self.leds.battery_level = min(lvl, 2)
        if lvl == 1:
            self.show_toast(f"Akku {self.battery.percent} %")
        elif lvl == 2:
            self.show_toast(f"Akku schwach: {self.battery.percent} %", 5)
            if self.cfg.get("battery", "warn_beep"):
                self.siren.beep(0.08)
            self.events.add("system", "akku", f"{self.battery.percent} %")
        elif lvl == 3:
            self.events.add("system", "akku", "leer, fahre herunter")
            self.shutdown()

    # ---------------------------------------------------------------- Bild
    def _compose_loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.menu.tick()
                img = self.mode.render(W, H) if self.mode else np.zeros((H, W, 3), np.uint8)
                if img.shape[:2] != (H, W):
                    img = draw.fit(img, W, H)
                img = self._overlay(img.copy())
                img = self.menu.draw(img)
                self.frame = img
                self.frame_id += 1
            except Exception:
                log.exception("Bildaufbau")
                time.sleep(0.5)
            fps = float(self.cfg.get("display", "fps") or 8)
            time.sleep(max(0.01, 1 / fps - (time.time() - t0)))

    def _overlay(self, img):
        c = draw.Canvas(img)
        c.rect((0, 0, W, 32), fill=(0, 0, 0, 150))
        m = self.mode
        c.rect((0, 0, 6, 32), fill=m.color if m else (255, 255, 255))
        c.text((14, 16), m.name if m else "", 18, (255, 255, 255), bold=True, anchor="lm")
        right = []
        if m and m.is_recording():
            right.append("● REC")
        n = self.network
        right.append({"wifi": "WLAN", "hotspot": "Hotspot", "aus": "WLAN aus"}.get(n.mode, "offline"))
        b = self.battery
        if b.ok and b.percent is not None and self.cfg.get("battery", "show_percent"):
            right.append(f"{'↑ ' if b.charging else ''}{b.percent} %")
        right.append(time.strftime("%H:%M"))
        x = W - 10
        for s in reversed(right):
            col = (255, 255, 255)
            if s == "● REC":
                col = (255, 70, 70)
            elif s.endswith("%"):
                col = (255, 80, 80) if b.level >= 2 else ((255, 200, 0) if b.level == 1 else (160, 255, 160))
            c.text((x, 16), s, 17, col, bold=True, anchor="rm")
            x -= int(draw.font(17, True).getlength(s)) + 18
        if m:
            lines = m.hud()
            for k, line in enumerate(lines[:2]):
                y = H - 14 - 22 * (len(lines[:2]) - 1 - k)
                c.text((10, y), line, 15, (230, 230, 230), anchor="lm", bg=(0, 0, 0, 120))
        msg = ""
        if self.menu.kind:
            pass                    # bei offenem Menü keine Einblendungen dahinter
        elif m and time.time() < m.message_until:
            msg = m.message
        elif time.time() < self.toast[1]:
            msg = self.toast[0]
        if msg:
            c.text((W // 2, 70), msg, 24, (255, 255, 255), bold=True, anchor="mm", bg=(0, 0, 0, 170))
        if not self.audio.ok and not self.sim:
            c.text((W // 2, H // 2 + 60), "Kein Mikrofon: RP2040 per USB anschliessen", 18, (255, 120, 120),
                   anchor="mm", bg=(0, 0, 0, 170))
        ql = self.menu.quick_label() if (m and self.menu.kind is None) else ""
        if ql:
            c.text((W - 10, H - 14), ql, 14, (255, 220, 120), anchor="rm", bg=(0, 0, 0, 120))
        if self.network.mode == "hotspot" and self.menu.kind is None and m and m.key in ("drone", "birdnet", "surveillance_mic"):
            c.text((W - 10, H - 36), f"Hotspot {self.network.ssid}  http://10.42.0.1", 14, (200, 220, 255),
                   anchor="rm", bg=(0, 0, 0, 120))
        return c.result()

    def jpeg(self, quality=70) -> bytes:
        """Aktuelles Bild als JPEG für die Webseite (zwischengespeichert)."""
        import cv2
        fid, data = self.jpeg_cache
        if fid == self.frame_id and data:
            return data
        if self.frame is None:
            return b""
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(self.frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
        data = buf.tobytes() if ok else b""
        self.jpeg_cache = (self.frame_id, data)
        return data

    # ---------------------------------------------------------------- Hintergrund
    def _housekeeping(self):
        while not self._stop.is_set():
            try:
                with open("/sys/class/thermal/thermal_zone0/temp") as f:
                    self.cpu_temp = int(f.read()) / 1000
            except Exception:
                pass
            if self.network.mode in ("wifi", "hotspot", "offline"):
                self.network.refresh()
            try:
                self.disk = self.storage.disk()
            except Exception:
                pass
            # Echtzeituhr stündlich nachstellen, wenn die Zeit aus dem Internet stimmt
            if not self.sim and time.time() - getattr(self, "_rtc_synced", 0) > 3600:
                try:
                    r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                                       capture_output=True, text=True, timeout=5)
                    if r.stdout.strip() == "yes" and os.path.exists("/dev/rtc0"):
                        subprocess.run(["hwclock", "--systohc", "--utc"], timeout=10)
                        self._rtc_synced = time.time()
                except Exception:
                    pass
            self._stop.wait(10)

    def status(self) -> dict:
        m = self.mode
        return {
            "version": __version__, "sim": self.sim,
            "mode": m.key if m else None, "mode_name": m.name if m else "",
            "modes": [{"key": x.key, "name": x.name, "color": "#%02x%02x%02x" % x.color,
                       "orientation": x.orientation} for x in self.modes],
            "recording": bool(m and m.is_recording()),
            "mode_status": m.status() if m else {},
            "hud": m.hud() if m else [],
            "message": m.message if m and time.time() < m.message_until else "",
            "battery": self.battery.status(),
            "leds": ["#%02x%02x%02x" % tuple(c) for c in self.leds.frame],
            "network": self.network.status(),
            "audio_ok": self.audio.ok, "audio_error": self.audio.error,
            "camera_error": self.camera.error,
            "cpu_temp": round(self.cpu_temp, 1),
            "disk": self.disk,
            "uptime_s": int(time.time() - self.started),
            "siren_on": self.siren.on,
        }
