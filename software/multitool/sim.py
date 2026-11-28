"""Simulator: ersetzt Mikrofone, Kamera und Akku, damit man die Software am PC testen kann.

Eine Schallquelle wandert langsam hin und her (bzw. im Kreis, wenn das Array liegt).
Im Kamerabild ist sie als heller Punkt zu sehen. Ab und zu fliegt eine "Drohne" vorbei
und ein Rechteck bewegt sich durchs Bild.
"""

from __future__ import annotations

import math
import threading
import time

import cv2
import numpy as np
from scipy.signal import lfilter

from .dsp import geometry as geo

FS = 16000


def _frac_delay_taps(delay, n=31):
    k = np.arange(n) - (n - 1) / 2
    h = np.sinc(k - delay) * np.hamming(n)
    return h / h.sum()


class Simulator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.orientation = "front"
        self.t = 0.0
        self.lock = threading.Lock()
        self.rng = np.random.default_rng(1)
        self.zi = None
        self.taps = None
        self.dir = np.array([0, 0, 1.0])
        self.clap_at = None
        self.drone_forced = False
        self.voltage = 8.2
        self.inv_map = None
        self.phase = np.zeros(16)

    # ---- Szenario ----
    def set_orientation(self, o):
        self.orientation = o

    def clap(self):
        self.clap_at = self.t + 0.05

    def source_direction(self, t):
        if self.orientation == "front":
            # wandert im Bild von links nach rechts und zurück
            x = 0.5 * math.sin(2 * math.pi * t / 40)
            y = 0.2 * math.sin(2 * math.pi * t / 23)
            rot = float(self.cfg.get("array", "rotation_deg") or 0)
            mir = bool(self.cfg.get("array", "mirror"))
            return geo.image_to_array(np.array([x]), np.array([y]), rot, mir)[0], (x, y)
        az = (t * 6) % 360
        d = geo.dir_vectors_azel(np.array([az]), np.array([20.0]))[0]
        return d, None

    def drone_active(self, t):
        return self.drone_forced or (t % 90) > 60

    # ---- Audio ----
    def audio_block(self, n):
        pos = geo.positions_from_cfg(self.cfg)
        d, _ = self.source_direction(self.t)
        tau = geo.delays(pos, d[None, :])[:, 0] * FS     # in Samples
        taps = [_frac_delay_taps(tt) for tt in tau]
        if self.zi is None:
            self.zi = [np.zeros(30) for _ in range(7)]
        tt = self.t + np.arange(n) / FS
        # Quelle: Pfeifton 3,2 kHz + Rauschband
        src = 0.02 * np.sin(2 * math.pi * 3200 * tt) + 0.01 * self.rng.standard_normal(n)
        if self.drone_active(self.t):
            f0 = 185 + 10 * math.sin(0.2 * self.t)
            for k in range(1, 13):
                src += 0.012 / k ** 0.5 * np.sin(2 * math.pi * f0 * k * tt + self.phase[k])
            src += 0.004 * self.rng.standard_normal(n)
        if self.clap_at is not None:
            for ca in (self.clap_at, self.clap_at + 0.3):
                i = int((ca - self.t) * FS)
                if 0 <= i < n:
                    m = min(n - i, 400)
                    src[i:i + m] += 0.5 * self.rng.standard_normal(m) * np.exp(-np.arange(m) / 60)
            if self.t > self.clap_at + 0.4:
                self.clap_at = None
        out = np.zeros((n, 8), np.float32)
        mics = np.zeros((7, n))
        for m in range(7):
            y, self.zi[m] = lfilter(taps[m], [1.0], src, zi=self.zi[m])
            mics[m] = y + 0.0005 * self.rng.standard_normal(n)
        # in USB-Kanalreihenfolge bringen
        from .core.audio import parse_channel_map
        cmap = parse_channel_map(self.cfg.get("array", "channel_map"))
        for m, ch in enumerate(cmap):
            out[:, ch] = mics[m]
        self.t += n / FS
        return out

    # ---- Kamera ----
    def camera_frame(self):
        h, w = 360, 640
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = (40, 50, 60)
        for x in range(0, w, 40):
            cv2.line(img, (x, 0), (x, h), (60, 70, 80), 1)
        for y in range(0, h, 40):
            cv2.line(img, (0, y), (w, y), (60, 70, 80), 1)
        cv2.putText(img, "SIMULATION", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
        if self.orientation == "front":
            _, (x, y) = self.source_direction(self.t)
            tx = math.tan(math.radians(float(self.cfg.get("array", "hfov") or 66)) / 2)
            ty = math.tan(math.radians(float(self.cfg.get("array", "vfov") or 41)) / 2)
            px = int((x / tx + 1) / 2 * w)
            py = int((1 - y / ty) / 2 * h)
            cv2.circle(img, (px, py), 10, (255, 255, 120), -1)
            cv2.putText(img, "Quelle", (px + 14, py + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 120), 1)
        # bewegtes Objekt für die Bewegungserkennung
        tm = time.time() % 45
        if tm < 8:
            x0 = int(tm / 8 * (w - 80))
            cv2.rectangle(img, (x0, 200), (x0 + 80, 320), (200, 120, 60), -1)
        return img

    # ---- Akku ----
    def battery(self):
        self.voltage = max(6.3, self.voltage - 0.00005)
        return self.voltage, -0.65
