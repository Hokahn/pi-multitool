"""Modus 3: Drohnenerkennung. Array liegt flach und zeigt zum Himmel.

Ablauf bei Erkennung:  rotes Blinken + Sirene 1-2 s (beides abschaltbar),
danach leuchtet der Sektor rot, aus dem das Geräusch kommt, solange die Drohne hörbar ist.
"""

from __future__ import annotations

import collections
import time

import numpy as np

from ..config import Option as O
from ..core import leds as L
from ..core.audio import FS
from ..dsp import doa
from ..dsp.classifiers import DroneClassifier
from ..ui import draw
from .base import Mode


class DroneMode(Mode):
    key = "drone"
    name = "Drohnenerkennung"
    color = (255, 0, 0)
    QUICK = ["threshold"]          # Encoder im Livebild
    orientation = "up"
    OPTIONS = [
        O("siren", "Sirene", "bool", True),
        O("siren_s", "Dauer Sirene", "choice", 1.5, choices=[(1.0, "1 s"), (1.5, "1,5 s"), (2.0, "2 s")]),
        O("blink", "Rotes Blinklicht", "bool", True),
        O("confirm", "Bestätigung", "choice", 3, choices=[(2, "2 von 5"), (3, "3 von 5"), (4, "4 von 5")],
          help="Wie viele der letzten 5 Sekunden positiv sein müssen."),
        O("threshold", "Empfindlichkeit", "choice", 0.6, choices=[(0.75, "niedrig"), (0.6, "mittel"), (0.45, "hoch")]),
        O("save", "Ereignis speichern", "choice", "clip", choices=[
            ("off", "aus"), ("log", "nur Protokoll"), ("clip", "Protokoll und 10-s-Clip")]),
        O("push", "Push ans Handy", "bool", True),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.prob = 0.0
        self.f0 = 0.0
        self.state = "idle"        # idle, alarm, track
        self.az = None
        self.q = 0.0
        self.profile = None
        self.recent = collections.deque(maxlen=20)   # (zeit, peilung)
        self.event_id = None

    def start(self):
        super().start()
        self.clf = DroneClassifier()
        self.af = doa.AzimuthFinder(self.positions, fmin=150, fmax=4000)
        self.app.leds.off()
        self.spawn(self._detect_loop, "drohne")
        self.spawn(self._track_loop, "drohne-richtung")

    def stop(self):
        super().stop()
        self.app.leds.off()

    def _detect_loop(self):
        hist = collections.deque(maxlen=5)
        negatives = 0
        while self.running:
            if self.app.siren.busy(0.5):
                self.sleep(0.1)          # eigene Sirene nicht als Drohne erkennen
                continue
            x = self.audio.ring.latest(FS)
            if x.shape[1] < FS:
                self.sleep(0.2)
                continue
            mono = x[:6].mean(axis=0)
            self.prob, self.f0 = self.clf.score(mono)
            hit = self.prob >= float(self.opt("threshold"))
            hist.append(hit)
            if self.state == "idle" and sum(hist) >= int(self.opt("confirm")):
                self._alarm()
                negatives = 0
            elif self.state != "idle":
                negatives = 0 if hit else negatives + 1
                if negatives >= 10:          # 5 s nichts mehr gehört
                    self.state = "idle"
                    self.app.leds.off()
                    hist.clear()
            self.sleep(0.5)

    def _alarm(self):
        self.state = "alarm"
        az, el, q = self.af.find(self.audio.ring.latest(FS // 2))
        self.az = az
        b = self.bearing(az)
        if self.opt("save") != "off":
            self.event_id = self.app.events.add(self.key, "drohne", f"{self.clf.kind}, f0 {self.f0:.0f} Hz",
                                                direction=b, confidence=self.prob)
        if self.opt("push"):
            self.push("Drohne erkannt", f"Richtung {b:.0f}°, {time.strftime('%H:%M:%S')}")
        dur = float(self.opt("siren_s"))
        if self.opt("blink"):
            self.app.leds.alarm(dur + 0.5, L.RED)
        if self.opt("siren"):
            self.app.siren.sound(dur)
        if self.opt("save") == "clip":
            eid = self.event_id

            def clip():
                if self.sleep(5):
                    p = self.app.storage.new_path(self.key, "drohne", ".wav")
                    self.save_ring_wav(p, 10.0, mode="beam",
                                       direction=self.af.srp.dirs[0] if self.az is None else _azvec(self.az))
                    if eid:
                        self.app.events.update_file(eid, self.app.storage.rel(p))
            self.spawn(clip, "drohne-clip")
        self.alarm_end = time.time() + (dur + 0.5 if (self.opt("blink") or self.opt("siren")) else 0)
        self.state = "track"

    def _track_loop(self):
        while self.running:
            x = self.audio.ring.latest(FS // 2)
            if x.shape[1] >= 4096 and not self.app.siren.busy(0.2):
                self.profile = self.af.profile(x)
                if self.state == "track":
                    az, el, q = self.af.find(x)
                    self.az, self.q = az, q
                    self.recent.append((time.time(), self.bearing(az)))
                    if time.time() >= getattr(self, "alarm_end", 0):
                        self.app.leds.sector(self.led_for_az(az), L.RED, width=1)
            self.sleep(0.25)

    def render(self, W, H):
        prof = None
        if self.profile is not None:
            prof = [(self.bearing(a), v) for a, v in zip(self.af.az, self.profile)]
        now = time.time()
        evs = [(b, (now - t) / 20, (255, 120, 120)) for t, b in self.recent if now - t < 20]
        bearing = self.bearing(self.az) if (self.state != "idle" and self.az is not None) else None
        img = draw.radar(W, H, prof, bearing, (255, 50, 50), bool(self.cfg.get("array", "north_offset_deg")), evs)
        c = draw.Canvas(img)
        x = W - 300
        if self.state == "idle":
            c.text((x, 70), "Keine Drohne", 26, (120, 220, 120), True)
        else:
            c.text((x, 70), "DROHNE", 30, (255, 60, 60), True)
            if bearing is not None:
                c.text((x, 110), f"Richtung {bearing:.0f}°", 22, (255, 255, 255))
        c.text((x, 160), f"Wahrscheinlichkeit {self.prob * 100:.0f} %", 18, (220, 220, 220))
        c.rect((x, 188, x + 260, 204), outline=(120, 120, 120))
        c.rect((x + 1, 189, x + 1 + int(258 * self.prob), 203),
               fill=(255, 60, 60) if self.prob >= float(self.opt("threshold")) else (90, 160, 255))
        thr = float(self.opt("threshold"))
        c.rect((x + int(258 * thr), 184, x + int(258 * thr) + 2, 208), fill=(255, 255, 255))
        c.text((x, 220), f"Erkennung: {self.clf.kind if hasattr(self, 'clf') else '-'}", 15, (160, 170, 180))
        if self.f0:
            c.text((x, 242), f"Grundton {self.f0:.0f} Hz", 15, (160, 170, 180))
        flags = []
        flags.append("Sirene an" if self.opt("siren") else "Sirene aus")
        flags.append("Blinken an" if self.opt("blink") else "Blinken aus")
        c.text((x, 270), ", ".join(flags), 15, (160, 170, 180))
        evs = self.app.events.query(limit=5, mode=self.key)
        y = 310
        c.text((x, y), "Letzte Ereignisse", 15, (200, 200, 200), True)
        for e in evs:
            y += 22
            d = "" if e["direction"] is None else f"{e['direction']:.0f}°"
            c.text((x, y), f"{time.strftime('%d.%m. %H:%M', time.localtime(e['ts']))}  {d}", 15, (170, 180, 190))
        return c.result()

    def hud(self):
        return ["Array flach hinlegen, LED 0 nach Norden oder Bezugsrichtung"]

    def status(self):
        return {"state": self.state, "prob": round(self.prob, 2), "bearing": None if self.az is None else round(self.bearing(self.az))}


def _azvec(az):
    a = np.deg2rad(az)
    return np.array([np.cos(a) * 0.94, np.sin(a) * 0.94, 0.34])
