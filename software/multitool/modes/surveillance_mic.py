"""Modus 5: Überwachungsmikrofon. Meldet Geräusche mit Richtung, speichert wahlweise den Ton.

Hinweis: Gespräche anderer ohne Einwilligung aufzunehmen ist in der Schweiz strafbar
(StGB Art. 179bis). Standard ist deshalb "nur Ereignis und Richtung".
"""

from __future__ import annotations

import collections
import time

import numpy as np

from ..config import Option as O
from ..core import leds as L
from ..core.audio import CENTER, FS
from ..dsp import doa
from ..dsp.classifiers import YAMNET_GROUPS, Yamnet
from ..dsp.detectors import NoiseFloor, rms_db
from ..ui import draw
from .base import Mode


class SurveillanceMicMode(Mode):
    key = "surveillance_mic"
    name = "Überwachungsmikrofon"
    color = (255, 110, 0)
    QUICK = ["threshold"]          # Encoder im Livebild
    orientation = "up"
    OPTIONS = [
        O("trigger", "Auslöser", "choice", "level", choices=[
            ("level", "Pegel"), ("classes", "Geräuschklassen (YAMNet)"), ("both", "beides")]),
        O("threshold", "Schwelle über Grundgeräusch", "int", 10, min=6, max=30, step=2, unit="dB"),
        O("classes", "Klassen (Komma-Liste)", "text", "glass,alarm,dog",
          help="Möglich: " + ", ".join(f"{k} = {v[0]}" for k, v in YAMNET_GROUPS.items())),
        O("alarm", "Alarm (Sirene + Blinken)", "bool", False),
        O("save", "Speichern", "choice", "event", choices=[
            ("event", "nur Ereignis und Richtung"), ("audio", "Audio mit Richtung")]),
        O("pre", "Vorlauf", "int", 5, min=0, max=10, step=1, unit="s"),
        O("push", "Push ans Handy", "bool", False),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.level = -120.0
        self.floor = NoiseFloor(60, 4)
        self.event = None
        self.timeline = collections.deque(maxlen=30)
        self.last_class = ("", 0.0)
        self.yam = None

    def start(self):
        super().start()
        self.af = doa.AzimuthFinder(self.positions, fmin=200, fmax=6000)
        self.app.leds.off()
        if self.opt("trigger") != "level":
            self.yam = Yamnet()
        self.spawn(self._loop, "geraeusch")

    def stop(self):
        self._end()
        super().stop()
        self.app.leds.off()

    def on_option_changed(self, key, value):
        if key == "trigger" and value != "level" and self.yam is None:
            self.yam = Yamnet()

    def _classes(self):
        return [c.strip() for c in str(self.opt("classes")).split(",") if c.strip() in YAMNET_GROUPS]

    def _loop(self):
        quiet_since = time.time()
        last_yam = 0.0
        while self.running:
            if self.app.siren.busy(0.5):
                self.sleep(0.1)
                continue
            x = self.audio.ring.latest(FS // 4)
            if x.shape[1] < 1000:
                self.sleep(0.1)
                continue
            # lautestes 50-ms-Stück zählt, damit auch kurze Knalle erkannt werden
            seg = x[CENTER][: (x.shape[1] // 800) * 800].reshape(-1, 800)
            self.level = self.audio.dbfs_to_spl(max(rms_db(r) for r in seg))
            fl = self.floor.update(self.level) if self.event is None else self.floor.value()
            trig = self.opt("trigger")
            loud = self.level - fl > float(self.opt("threshold"))
            label = "Geräusch"
            hit_cls = False
            if trig in ("classes", "both") and self.yam is not None and self.yam.ok and time.time() - last_yam > 0.5:
                last_yam = time.time()
                g = self.yam.groups(self.audio.ring.latest(15600)[CENTER])
                self.last_class = g.pop("_top")
                for c in self._classes():
                    if g.get(c, 0) > 0.35:
                        hit_cls = True
                        label = YAMNET_GROUPS[c][0]
            fire = (trig == "level" and loud) or (trig == "classes" and hit_cls) or (trig == "both" and loud and hit_cls)
            if trig == "both" and loud and not hit_cls and (self.yam is None or not self.yam.ok):
                fire = True
            if fire:
                quiet_since = time.time()
                if self.event is None:
                    self._begin(label)
            elif self.event is not None and time.time() - quiet_since > 2.0:
                self._end()
            if self.event is not None and time.time() - self.event["t0"] > 30:
                self._end()
            self.sleep(0.25)

    def _begin(self, label):
        xx = self.audio.ring.latest(FS // 2)
        az, el, q = self.af.find(xx)
        b = self.bearing(az)
        eid = self.app.events.add(self.key, "geraeusch", label, direction=b, level=self.level)
        self.timeline.append((time.time(), b, label, self.level))
        self.app.leds.sector(self.led_for_az(az), L.ORANGE, width=1)
        rec = None
        if self.opt("save") == "audio" and self.app.storage.can_record():
            p = self.app.storage.new_path(self.key, "geraeusch", ".wav")
            a = np.deg2rad(az)
            rec = self.start_audio_rec(p, mode="beam", direction=(np.cos(a) * 0.9, np.sin(a) * 0.9, 0.42),
                                       preroll_s=int(self.opt("pre")))
            self.app.events.update_file(eid, self.app.storage.rel(p))
        if self.opt("alarm"):
            self.app.siren.sound(3)
            self.app.leds.alarm(3, L.RED)
        if self.opt("push"):
            self.push(f"{label} erkannt", f"Richtung {b:.0f}°, ca. {self.level:.0f} dB")
        self.event = {"t0": time.time(), "rec": rec, "az": az}

    def _end(self):
        ev, self.event = self.event, None
        if ev and ev["rec"]:
            self.stop_audio_rec(ev["rec"])

        def off():
            if self.sleep(3) and self.event is None:
                self.app.leds.off()
        if ev:
            self.spawn(off, "led-aus")

    def render(self, W, H):
        now = time.time()
        evs = [(b, (now - t) / 120, (255, 140, 40)) for t, b, _, _ in self.timeline if now - t < 120]
        cur = None if self.event is None else self.bearing(self.event["az"])
        img = draw.radar(W, H, None, cur, (255, 120, 0), bool(self.cfg.get("array", "north_offset_deg")), evs)
        c = draw.Canvas(img)
        x = W - 300
        c.text((x, 60), "Geräusch!" if self.event else "Ruhig", 28, (255, 140, 40) if self.event else (120, 220, 120), True)
        fl = self.floor.value()
        c.text((x, 104), f"Pegel ca. {self.level:.0f} dB", 18, (230, 230, 230))
        c.text((x, 128), f"Grundgeräusch {fl:.0f} dB, Schwelle +{self.opt('threshold')} dB", 14, (170, 180, 190))
        # Pegelbalken 30..100 dB
        def px(v):
            return int(x + (min(max(v, 30), 100) - 30) / 70 * 260)
        c.rect((x, 152, x + 260, 170), outline=(120, 120, 120))
        c.rect((x + 1, 153, max(x + 2, px(self.level)), 169), fill=(255, 140, 40) if self.event else (90, 160, 255))
        c.rect((px(fl + float(self.opt("threshold"))), 148, px(fl + float(self.opt("threshold"))) + 2, 174), fill=(255, 255, 255))
        if self.opt("trigger") != "level":
            ok = self.yam is not None and self.yam.ok
            c.text((x, 182), f"YAMNet: {self.last_class[0]} {self.last_class[1]:.2f}" if ok else "YAMNet nicht installiert",
                   14, (170, 180, 190))
        y = 214
        c.text((x, y), "Zeitleiste", 15, (200, 200, 200), True)
        for t, b, lab, lv in list(self.timeline)[-9:][::-1]:
            y += 22
            c.text((x, y), f"{time.strftime('%H:%M:%S', time.localtime(t))}  {b:3.0f}°  {lab}", 14, (180, 190, 200))
        return c.result()

    def hud(self):
        return ["Speichern: " + ("Audio" if self.opt("save") == "audio" else "nur Ereignis")]

    def is_recording(self):
        return bool(self.event and self.event["rec"])

    def status(self):
        return {"level": round(self.level, 1), "floor": round(self.floor.value(), 1), "active": self.event is not None}
