"""Modus 2: Camcorder mit Licht, Sprecher-Tracking, Zeitraffer und akustischem Auslöser."""

from __future__ import annotations

import collections
import os
import shutil
import threading
import time

import numpy as np

from ..config import Option as O
from ..core import leds as L
from ..core.audio import CENTER, FS
from ..core.storage import images_to_mp4, mux_audio_video
from ..dsp import doa
from ..dsp import geometry as geo
from ..dsp.detectors import ClapDetector, VoiceActivity, WhistleDetector
from ..ui import draw
from .base import Mode

RES = {"1080p30": ((1920, 1080), 30), "720p60": ((1280, 720), 60)}


class CamcorderMode(Mode):
    key = "camcorder"
    name = "Camcorder"
    color = (255, 255, 255)
    QUICK = ["light"]          # Encoder im Livebild
    orientation = "front"
    needs_camera = True
    OPTIONS = [
        O("light", "Licht", "choice", 0, choices=[(0, "aus"), (25, "25 %"), (50, "50 %"), (100, "100 %")]),
        O("sound", "Ton", "choice", "front", choices=[
            ("center", "Mittel-Mic"), ("front", "Richtmikro vorne"), ("sum", "alle Mikrofone")]),
        O("tracking", "Sprecher-Tracking", "bool", False,
          help="Der Ton folgt der Person, die gerade spricht. Die Kamera schwenkt nicht."),
        O("timelapse", "Zeitraffer", "choice", 0, choices=[
            (0, "aus"), (1, "1 s"), (5, "5 s"), (10, "10 s"), (30, "30 s"), (60, "1 min"), (300, "5 min")]),
        O("tl_light", "Licht nur beim Zeitraffer-Bild", "bool", True),
        O("trigger", "Akustischer Auslöser", "choice", "off", choices=[
            ("off", "aus"), ("clap", "doppelt klatschen"), ("whistle", "Pfiff")]),
        O("autostop", "Auto-Stopp nach", "choice", 0, choices=[
            (0, "aus"), (1, "1 min"), (5, "5 min"), (15, "15 min"), (60, "60 min")]),
        O("res", "Auflösung", "choice", "1080p30", choices=[("1080p30", "1080p 30 fps"), ("720p60", "720p 60 fps")]),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.rec = None
        self.track_dir = np.array([0, 0, 1.0])
        self.track_img = None
        self.speaking = False
        self.tl = None

    @property
    def camera_size(self):
        return RES.get(self.opt("res"), RES["1080p30"])

    def start(self):
        super().start()
        size, fps = self.camera_size
        self.app.camera.start(size, fps)
        self._apply_light()
        self.clap = ClapDetector()
        self.whistle = WhistleDetector()
        self.vad = VoiceActivity()
        self.audio.add_sink(self._trigger_sink)
        self.spawn(self._track_loop, "tracking")

    def stop(self):
        self.audio.remove_sink(self._trigger_sink)
        self._stop()
        super().stop()
        self.app.leds.off()

    def on_option_changed(self, key, value):
        if key in ("light", "tl_light", "timelapse"):
            self._apply_light()
        if key == "res" and not self.rec:
            size, fps = self.camera_size
            self.app.camera.start(size, fps)

    def _apply_light(self):
        lvl = int(self.opt("light") or 0)
        if lvl and not (self.opt("timelapse") and self.opt("tl_light")):
            # Licht darf heller sein als die Grundhelligkeit (Stromlimit gilt trotzdem)
            self.app.leds.override_brightness = lvl / 100
            self.app.leds.fill(L.WHITE, 1.0)
        else:
            self.app.leds.override_brightness = None
            self.app.leds.off()

    # ---- akustischer Auslöser ----
    def _trigger_sink(self, block):
        trig = self.opt("trigger")
        if trig == "off":
            return
        x = block[CENTER]
        hit = self.clap.feed(x) if trig == "clap" else self.whistle.feed(x)
        if hit:
            threading.Thread(target=self.on_record, daemon=True).start()

    # ---- Sprecher-Tracking ----
    def _track_loop(self):
        a = self.cfg.group("array")
        # grobes Raster über das Bild: 15 x 7 Richtungen, Sprachband
        dirs = geo.camera_grid(15, 7, a["hfov"] * 1.6, a["vfov"] * 1.6, a["rotation_deg"], a["mirror"])
        srp = doa.SRP(self.positions, dirs, nfft=512, fmin=300, fmax=3400)
        cand = collections.deque(maxlen=5)
        while self.running:
            if not self.opt("tracking"):
                self.track_img = None
                self.sleep(0.3)
                continue
            x = self.audio.ring.latest(int(0.1 * FS))
            if x.shape[1] < 1024:
                self.sleep(0.1)
                continue
            self.speaking = self.vad(x[CENTER])
            if self.speaking:
                i, d, q, _ = srp.best(x)
                cand.append(i)
                if len(cand) == cand.maxlen and len(set(cand)) <= 2 and cand.count(i) >= 3:
                    self.track_dir = d
                    gx, gy = i % 15, i // 15
                    self.track_img = ((gx + 0.5) / 15 * 1.6 - 0.3, (gy + 0.5) / 7 * 1.6 - 0.3)
                    if self.rec and self.rec.get("audio") and self.rec["audio"]["bf"] is not None:
                        self.rec["audio"]["bf"].set_direction(d)
                    if not int(self.opt("light") or 0):
                        az = float(np.rad2deg(np.arctan2(d[1], d[0])))
                        self.app.leds.sector(self.led_for_az(az), (0, 120, 0), width=0)
            self.sleep(0.1)

    # ---- Aufnahme ----
    def is_recording(self):
        return self.rec is not None or self.tl is not None

    def on_record(self):
        if self.is_recording():
            self._stop()
        else:
            self._start()

    def on_record_long(self):
        # Foto in voller Auflösung
        p = self.app.storage.new_path(self.key, "foto", ".jpg")
        if self.app.camera.still(p):
            self.app.events.add(self.key, "foto", file=self.app.storage.rel(p))
            self.say("Foto gespeichert")

    def _start(self):
        st = self.app.storage
        if not st.can_record():
            self.say("Speicher voll")
            return
        interval = int(self.opt("timelapse") or 0)
        if interval:
            folder = st.new_path(self.key, "zeitraffer", "")
            os.makedirs(folder, exist_ok=True)
            self.tl = {"folder": folder, "t0": time.time(), "n": 0, "interval": interval}
            self.spawn(self._timelapse_loop, "zeitraffer")
            self.say(f"Zeitraffer alle {interval} s")
            return
        path = st.new_path(self.key, "video", ".mp4")
        tmp_v = path.replace(".mp4", ".part.mp4")
        tmp_a = path.replace(".mp4", ".part.wav")
        if not self.app.camera.start_recording(tmp_v):
            self.say("Kamera-Fehler")
            return
        mode = "beam" if (self.opt("tracking") or self.opt("sound") == "front") else self.opt("sound")
        direction = self.track_dir if self.opt("tracking") else (0, 0, 1.0)
        audio = self.start_audio_rec(tmp_a, mode=mode, direction=direction)
        self.rec = {"path": path, "tmp_v": tmp_v, "tmp_a": tmp_a, "audio": audio, "t0": time.time()}
        self.spawn(self._autostop_loop, "autostop")
        self.say("Aufnahme läuft")

    def _autostop_loop(self):
        r = self.rec
        while self.running and self.rec is r:
            lim = int(self.opt("autostop") or 0)
            if lim and time.time() - r["t0"] > lim * 60:
                self._stop()
                break
            if not self.app.storage.can_record():
                self.say("Speicher voll, Stopp")
                self._stop()
                break
            self.sleep(1)

    def _stop(self):
        if self.tl is not None:
            tl, self.tl = self.tl, None
            self._apply_light()

            def finish_tl():
                out = tl["folder"] + ".mp4"
                if tl["n"] > 1 and images_to_mp4(tl["folder"], out):
                    shutil.rmtree(tl["folder"], ignore_errors=True)
                    self.app.events.add(self.key, "zeitraffer", f"{tl['n']} Bilder", file=self.app.storage.rel(out))
            threading.Thread(target=finish_tl, daemon=True).start()
            self.say("Zeitraffer wird erstellt")
            return
        r, self.rec = self.rec, None
        if r is None:
            return
        self.app.camera.stop_recording()
        self.stop_audio_rec(r["audio"])
        dur = int(time.time() - r["t0"])

        def finish():
            if mux_audio_video(r["tmp_v"], r["tmp_a"], r["path"]):
                self.app.events.add(self.key, "video", f"{dur} s", file=self.app.storage.rel(r["path"]))
            else:   # ohne Ton behalten
                os.replace(r["tmp_v"], r["path"])
        threading.Thread(target=finish, daemon=True).start()
        self.say("Video gespeichert")

    def _timelapse_loop(self):
        tl = self.tl
        nxt = time.time()
        while self.running and self.tl is tl:
            if time.time() >= nxt:
                light = int(self.opt("light") or 0)
                if light and self.opt("tl_light"):
                    self.app.leds.override_brightness = light / 100
                    self.app.leds.fill(L.WHITE, 1.0)
                    time.sleep(0.6)            # Belichtung nachregeln lassen
                p = os.path.join(tl["folder"], f"{tl['n']:06d}.jpg")
                if self.app.camera.still(p):
                    tl["n"] += 1
                if light and self.opt("tl_light"):
                    self.app.leds.off()
                    self.app.leds.override_brightness = None
                nxt += tl["interval"]
                if not self.app.storage.can_record():
                    self.say("Speicher voll")
                    break
            self.sleep(0.2)

    # ---- Anzeige ----
    def render(self, W, H):
        view = draw.fit(self.app.camera.frame(), W, H)
        c = draw.Canvas(view)
        if self.track_img is not None and self.opt("tracking"):
            x0, y0, nw, nh = draw.fit_rect(640, 360, W, H)
            px = int(x0 + min(max(self.track_img[0], 0), 1) * nw)
            py = int(y0 + min(max(self.track_img[1], 0), 1) * nh)
            col = (80, 255, 80) if self.speaking else (120, 160, 120)
            c.rounded((px - 30, py - 30, px + 30, py + 30), 8, outline=col, width=3)
        if self.rec:
            c.text((W - 12, 44), f"● REC {_mmss(time.time() - self.rec['t0'])}", 20, (255, 60, 60), True, anchor="ra")
        if self.tl:
            c.text((W - 12, 44), f"Zeitraffer {self.tl['n']} Bilder", 18, (255, 200, 60), True, anchor="ra")
        return c.result()

    def hud(self):
        s = {"center": "Mittel-Mic", "front": "Richtmikro", "sum": "alle Mics"}[self.opt("sound")]
        parts = [f"Ton: {'Tracking' if self.opt('tracking') else s}"]
        if int(self.opt("light") or 0):
            parts.append(f"Licht {self.opt('light')} %")
        if self.opt("timelapse"):
            parts.append(f"Zeitraffer {self.opt('timelapse')} s")
        if self.opt("trigger") != "off":
            parts.append("Auslöser: " + ("Klatschen" if self.opt("trigger") == "clap" else "Pfiff"))
        return ["   ".join(parts), "Aufnahme kurz: Start/Stopp   lang: Foto"]


def _mmss(s):
    s = int(s)
    return f"{s // 60:02d}:{s % 60:02d}"
