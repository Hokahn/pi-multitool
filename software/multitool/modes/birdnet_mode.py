"""Modus 6: BirdNET. Vogelstimmen erkennen, mit Richtung. Array liegt flach.

Die Firmware liefert 16 kHz, also Töne bis 8 kHz. Das reicht für die meisten Arten,
sehr hohe Rufe (z.B. Goldhähnchen) werden schlechter erkannt.
"""

from __future__ import annotations

import collections
import datetime
import threading
import time

import numpy as np

from ..config import Option as O
from ..core import leds as L
from ..core.audio import FS
from ..dsp import doa
from ..dsp.classifiers import BirdNet
from ..ui import draw
from .base import Mode


class BirdNetMode(Mode):
    key = "birdnet"
    name = "BirdNET"
    color = (0, 255, 0)
    QUICK = ["min_conf"]          # Encoder im Livebild
    orientation = "up"
    OPTIONS = [
        O("min_conf", "Mindestkonfidenz", "choice", 0.7, choices=[
            (0.5, "0,5"), (0.6, "0,6"), (0.7, "0,7"), (0.8, "0,8"), (0.9, "0,9")]),
        O("clips", "Clip speichern", "choice", "best", choices=[
            ("off", "aus"), ("best", "bester pro Art und Tag"), ("all", "alle")]),
        O("led", "Richtung auf LED-Ring", "bool", True),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.today = {}               # name -> {"n", "best", "last_b", "last_t"}
        self.recent = collections.deque(maxlen=20)
        self.busy = False
        self.bn = None
        self.day = datetime.date.today()

    def start(self):
        super().start()
        self.af = doa.AzimuthFinder(self.positions, fmin=1000, fmax=7500)
        self.app.leds.off()

        def load():
            self.bn = BirdNet(float(self.cfg.get("location", "lat")), float(self.cfg.get("location", "lon")))
        threading.Thread(target=load, daemon=True).start()
        self.spawn(self._loop, "birdnet")

    def stop(self):
        super().stop()
        self.app.leds.off()

    def _loop(self):
        while self.running:
            if not self.sleep(3.0):
                break
            if self.bn is None or not self.bn.ok or self.busy:
                continue
            x = self.audio.ring.latest(3 * FS)
            self.busy = True
            threading.Thread(target=self._analyze, args=(x,), daemon=True).start()

    def _analyze(self, x):
        try:
            if datetime.date.today() != self.day:
                self.day = datetime.date.today()
                self.today.clear()
            dets = self.bn.detect(x[:6].mean(axis=0), FS, float(self.opt("min_conf")))
            if not dets:
                return
            az, el, q = self.af.find(x)
            b = self.bearing(az)
            for d in dets:
                info = self.today.setdefault(d["name"], {"n": 0, "best": 0.0, "last_b": b, "last_t": 0})
                info["n"] += 1
                info["last_b"], info["last_t"] = b, time.time()
                f = ""
                clips = self.opt("clips")
                if clips == "all" or (clips == "best" and d["conf"] > info["best"]):
                    p = self.app.storage.new_path(self.key, d["name"].replace(" ", "_").replace("/", "-"), ".wav")
                    a = np.deg2rad(az)
                    self.save_ring_wav(p, 3.0, mode="beam", direction=(np.cos(a) * 0.8, np.sin(a) * 0.8, 0.6))
                    f = self.app.storage.rel(p)
                info["best"] = max(info["best"], d["conf"])
                self.app.events.add(self.key, "vogel", d["name"], direction=b, confidence=d["conf"], file=f,
                                    extra={"sci": d["sci"]})
                self.recent.append((time.time(), b, d["name"]))
            if self.opt("led"):
                self.app.leds.sector(self.led_for_az(az), L.GREEN, width=1)

                def off():
                    if self.sleep(3):
                        self.app.leds.off()
                self.spawn(off, "led-aus")
        finally:
            self.busy = False

    def render(self, W, H):
        now = time.time()
        evs = [(b, (now - t) / 60, (80, 255, 80)) for t, b, _ in self.recent if now - t < 60]
        img = draw.radar(W, H, None, None, (0, 255, 0), bool(self.cfg.get("array", "north_offset_deg")), evs)
        c = draw.Canvas(img)
        x = W - 300
        c.text((x, 56), "Heute gehört", 22, (120, 255, 120), True)
        if self.bn is None:
            c.text((x, 90), "BirdNET lädt ...", 16, (180, 180, 180))
        elif not self.bn.ok:
            c.text((x, 90), "BirdNET nicht installiert", 16, (255, 160, 120))
            c.text((x, 112), "install_extras.sh ausführen", 14, (180, 180, 180))
        y = 92
        for name, info in sorted(self.today.items(), key=lambda kv: -kv[1]["last_t"])[:12]:
            c.text((x, y), f"{name}", 15, (230, 230, 230))
            c.text((x + 290, y), f"{info['n']}x  {info['last_b']:.0f}°", 14, (160, 200, 160), anchor="ra")
            y += 24
        return c.result()

    def hud(self):
        return ["Standort für die Artenliste in den Einstellungen setzen"]

    def status(self):
        return {"species": {k: v["n"] for k, v in self.today.items()}}
