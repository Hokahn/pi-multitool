"""Grundklasse für alle Modi.

Ein Modus beschreibt seine Einstellungen (OPTIONS) und implementiert:
  start(), stop()              beim Ein- und Ausschalten
  on_record(), on_record_long() Aufnahme-Taster kurz / lang (3 s)
  on_touch(x, y)               Antippen des Bildes (0..1)
  render(W, H) -> RGB-Bild     was auf Display und Webseite zu sehen ist
  hud() -> Textzeilen          Infozeilen unten links
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from ..config import Option
from ..core.audio import FS, WavWriter
from ..dsp import geometry as geo
from ..dsp.beamform import StreamBeamformer, beamform_offline

log = logging.getLogger("mode")


class Mode:
    key = "base"
    name = "Basis"
    color = (255, 255, 255)
    orientation = "front"          # "front": Array zeigt nach vorne, "up": Array liegt, zeigt zum Himmel
    needs_camera = False
    OPTIONS: list[Option] = []
    QUICK: list[str] = []          # Einstellungen, die der Encoder im Livebild direkt ändert

    def __init__(self, app):
        self.app = app
        self.cfg = app.cfg
        self.running = False
        self._threads: list[threading.Thread] = []
        self.message = ""
        self.message_until = 0.0
        self._audio_recs = []

    # ---- Einstellungen ----
    def opt(self, key):
        v = self.cfg.mode_get(self.key, key, None)
        if v is None:
            for o in self.OPTIONS:
                if o.key == key:
                    return o.default
        return v

    def option(self, key) -> Option | None:
        for o in self.OPTIONS:
            if o.key == key:
                return o
        return None

    def set_opt(self, key, raw):
        o = self.option(key)
        if o is None:
            raise KeyError(key)
        v = o.coerce(raw)
        self.cfg.mode_set(self.key, key, v)
        try:
            self.on_option_changed(key, v)
        except Exception:
            log.exception("on_option_changed")
        return v

    def options_json(self):
        return [o.to_json(self.opt(o.key)) for o in self.OPTIONS]

    def on_option_changed(self, key, value):
        pass

    # ---- Lebenszyklus ----
    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        for r in list(self._audio_recs):
            self.stop_audio_rec(r)
        for t in self._threads:
            t.join(timeout=2)
        self._threads = []

    def spawn(self, fn, name=None):
        def wrap():
            try:
                fn()
            except Exception:
                log.exception("Fehler im Modus %s", self.key)
        t = threading.Thread(target=wrap, name=name or self.key, daemon=True)
        t.start()
        self._threads.append(t)
        return t

    def sleep(self, s):
        """Schlafen, aber sofort zurück, wenn der Modus gestoppt wird."""
        end = time.time() + s
        while self.running and time.time() < end:
            time.sleep(min(0.05, end - time.time()))
        return self.running

    # ---- Eingaben ----
    def on_record(self):
        pass

    def on_record_long(self):
        pass

    def on_touch(self, x, y):
        pass

    # ---- Anzeige ----
    def render(self, W, H) -> np.ndarray:
        return np.zeros((H, W, 3), np.uint8)

    def hud(self) -> list[str]:
        return []

    def is_recording(self) -> bool:
        return False

    def status(self) -> dict:
        return {}

    def say(self, text, seconds=3.0):
        self.message = text
        self.message_until = time.time() + seconds

    # ---- Hilfen ----
    @property
    def audio(self):
        return self.app.audio

    @property
    def positions(self):
        return geo.positions_from_cfg(self.cfg)

    def bearing(self, az):
        return geo.bearing(az, self.cfg)

    def led_for_az(self, az):
        return geo.az_to_led(az, float(self.cfg.get("leds", "led0_deg") or 0),
                             int(self.cfg.get("leds", "led_direction") or 1))

    def start_audio_rec(self, path, mode="beam", direction=(0, 0, 1.0), preroll_s=0.0, channels=1):
        """WAV-Aufnahme starten. mode: "beam", "center", "sum" oder "raw" (7 Kanäle)."""
        w = WavWriter(path, 7 if mode == "raw" else 1)
        bf = None
        if mode != "raw":
            bf = StreamBeamformer(self.positions, direction=direction)
            bf.mode = mode
        if preroll_s > 0:
            pre = self.audio.ring.latest(int(preroll_s * FS))
            if mode == "raw":
                w.write(pre)
            elif mode == "beam":
                w.write(beamform_offline(pre, self.positions, direction))
            elif mode == "center":
                w.write(pre[6])
            else:
                w.write(pre.mean(axis=0))

        def sink(block):
            if mode == "raw":
                w.write(block)
            else:
                w.write(bf.process(block))
        rec = {"writer": w, "sink": sink, "bf": bf, "path": path, "t0": time.time()}
        self.audio.add_sink(sink)
        self._audio_recs.append(rec)
        return rec

    def stop_audio_rec(self, rec):
        if rec is None:
            return
        self.audio.remove_sink(rec["sink"])
        rec["writer"].close()
        if rec in self._audio_recs:
            self._audio_recs.remove(rec)

    def save_ring_wav(self, path, seconds, mode="raw", direction=(0, 0, 1.0)):
        x = self.audio.ring.latest(int(seconds * FS))
        w = WavWriter(path, 7 if mode == "raw" else 1)
        if mode == "raw":
            w.write(x)
        elif mode == "beam":
            w.write(beamform_offline(x, self.positions, direction))
        else:
            w.write(x[6])
        w.close()
        return path

    def push(self, title, msg):
        from ..core.events import push
        push(self.cfg.get("network", "ntfy_topic") or "", title, msg)
