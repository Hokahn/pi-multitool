"""Einfache Detektoren ohne neuronales Netz: Sprache, Klatschen, Pfiff, Pegel, Drohne (Heuristik)."""

from __future__ import annotations

import collections
import time

import numpy as np


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-9))


class NoiseFloor:
    """Gleitendes Grundgeräusch: Median der Pegel der letzten `seconds` Sekunden."""

    def __init__(self, seconds=60.0, rate_hz=4.0):
        self.hist = collections.deque(maxlen=max(4, int(seconds * rate_hz)))

    def update(self, level_db: float) -> float:
        self.hist.append(level_db)
        return self.value()

    def value(self) -> float:
        if not self.hist:
            return -90.0
        return float(np.median(self.hist))


class VoiceActivity:
    """Sprache ja/nein: Energie im Sprachband deutlich über Grundgeräusch und nicht zu "flach"."""

    def __init__(self, fs=16000):
        self.fs = fs
        self.floor = NoiseFloor(30, 10)

    def __call__(self, x: np.ndarray) -> bool:
        n = len(x)
        X = np.abs(np.fft.rfft(x * np.hanning(n))) + 1e-12
        f = np.fft.rfftfreq(n, 1 / self.fs)
        band = (f >= 300) & (f <= 3400)
        e = 10 * np.log10(np.mean(X[band] ** 2))
        floor = self.floor.update(e)
        flat = np.exp(np.mean(np.log(X[band]))) / np.mean(X[band])   # 1 = Rauschen, klein = tonal
        return (e - floor) > 8 and flat < 0.45


class ClapDetector:
    """Doppeltes Klatschen: zwei kurze, laute Impulse im Abstand 150..600 ms."""

    def __init__(self, fs=16000, cooldown=1.5):
        self.fs = fs
        self.frame = fs // 100                    # 10 ms
        self.floor = NoiseFloor(10, 100)
        self.onsets = collections.deque(maxlen=4)
        self.prev_e = -100.0
        self.cooldown = cooldown
        self.last_fire = 0.0
        self._buf = np.zeros(0, np.float32)
        self._t = 0.0

    def feed(self, x: np.ndarray) -> bool:
        """Laufend Mono-Audio füttern. True, wenn gerade doppelt geklatscht wurde."""
        self._buf = np.concatenate([self._buf, x])
        fired = False
        while len(self._buf) >= self.frame:
            fr, self._buf = self._buf[:self.frame], self._buf[self.frame:]
            self._t += 0.01
            e = rms_db(fr)
            floor = self.floor.update(e)
            hf = np.abs(np.diff(fr)).mean() / (np.abs(fr).mean() + 1e-9)   # Klatschen ist breitbandig
            if e - floor > 18 and e - self.prev_e > 12 and hf > 0.3:
                if not self.onsets or self._t - self.onsets[-1] > 0.08:
                    self.onsets.append(self._t)
            self.prev_e = e
            if len(self.onsets) >= 2:
                d = self.onsets[-1] - self.onsets[-2]
                if 0.15 <= d <= 0.6 and time.time() - self.last_fire > self.cooldown:
                    # nicht dreifach: kein Impuls kurz davor
                    if len(self.onsets) < 3 or self.onsets[-2] - self.onsets[-3] > 0.7:
                        fired = True
                        self.last_fire = time.time()
                        self.onsets.clear()
        return fired


class WhistleDetector:
    """Pfiff: ein schmaler Ton zwischen 1 und 4 kHz, mindestens 0,5 s stabil."""

    def __init__(self, fs=16000, min_s=0.5, cooldown=1.5):
        self.fs = fs
        self.n = 1024
        self.hits = 0
        self.need = int(min_s / (self.n / fs))
        self.last_f = 0.0
        self.cooldown = cooldown
        self.last_fire = 0.0
        self._buf = np.zeros(0, np.float32)

    def feed(self, x: np.ndarray) -> bool:
        self._buf = np.concatenate([self._buf, x])
        fired = False
        while len(self._buf) >= self.n:
            fr, self._buf = self._buf[:self.n], self._buf[self.n:]
            X = np.abs(np.fft.rfft(fr * np.hanning(self.n))) + 1e-12
            f = np.fft.rfftfreq(self.n, 1 / self.fs)
            band = (f >= 1000) & (f <= 4000)
            i = np.argmax(X[band])
            peak = X[band][i]
            ratio = 20 * np.log10(peak / np.median(X[band]))
            fpk = f[band][i]
            if ratio > 25 and rms_db(fr) > -60 and (self.hits == 0 or abs(fpk - self.last_f) < 0.1 * fpk):
                self.hits += 1
                self.last_f = fpk
            else:
                self.hits = 0
            if self.hits >= self.need and time.time() - self.last_fire > self.cooldown:
                fired = True
                self.hits = 0
                self.last_fire = time.time()
        return fired


class DroneHeuristic:
    """Erkennt das typische Summen von Drohnen ohne trainiertes Modell (experimentell).

    Idee: Propeller erzeugen eine Grundfrequenz (Blattfrequenz) zwischen ca. 80 und 400 Hz
    mit vielen sauberen Obertönen. Wir messen, wie stark die Obertonreihe aus dem Spektrum
    herausragt. Das reagiert leider auch auf Rasenmäher, Laubbläser, Motorräder.
    Mit einem trainierten Modell (models/drone.tflite) wird das deutlich besser.
    """

    def __init__(self, fs=16000):
        self.fs = fs
        self.nfft = 2048
        self.f = np.fft.rfftfreq(self.nfft, 1 / fs)
        self.df = self.f[1]
        self.f0s = np.arange(80.0, 400.0, 2.0)
        self.hist_f0 = collections.deque(maxlen=6)

    def score(self, x: np.ndarray) -> tuple[float, float]:
        """x: Mono, ca. 1 s. -> (Wahrscheinlichkeit 0..1, geschätzte Grundfrequenz Hz)"""
        from scipy.signal import welch
        if len(x) < self.nfft:
            return 0.0, 0.0
        f, p = welch(x, fs=self.fs, nperseg=self.nfft, noverlap=self.nfft // 2)
        L = 10 * np.log10(p + 1e-18)
        # lokale Grundlinie: gleitender Median über ~60 Hz
        from scipy.ndimage import median_filter
        base = median_filter(L, size=int(120 / self.df) | 1, mode="nearest")
        prom = L - base
        best, best_f0, best_n = 0.0, 0.0, 0
        ks = np.arange(1, 13)
        for f0 in self.f0s:
            fk = f0 * ks
            fk = fk[fk < 4000]
            idx = np.round(fk / self.df).astype(int)
            tol = np.maximum(1, np.round(0.015 * fk / self.df)).astype(int)   # Drehzahl schwankt etwas
            vals = np.array([prom[max(0, i - t):i + t + 1].max() for i, t in zip(idx, tol)])
            n_good = int(np.sum(vals > 6))
            s = float(np.mean(np.clip(vals, 0, 20)))
            if n_good >= 4 and s > best:
                best, best_f0, best_n = s, f0, n_good
        # Hochfrequentes Motorsirren 4..8 kHz als Zusatzindiz
        hf = float(np.mean(prom[(f > 4000) & (f < 7900)].clip(0, None)))
        self.hist_f0.append(best_f0)
        # Drohnen-Grundfrequenz schwankt etwas (Regelung), bleibt aber im Bereich
        f0s = np.array([v for v in self.hist_f0 if v > 0])
        stable = 1.0 if len(f0s) >= 3 and np.std(f0s) < 40 else 0.5
        z = 0.5 * (best - 6) + 0.25 * (best_n - 4) + 0.3 * hf
        prob = float(1 / (1 + np.exp(-z))) * stable
        return prob, best_f0


def log_mel(x: np.ndarray, fs=16000, n_mels=64, nfft=1024, hop=320, fmin=50, fmax=8000) -> np.ndarray:
    """Log-Mel-Spektrogramm (n_mels, frames) für eigene Modelle."""
    def hz2mel(h):
        return 2595 * np.log10(1 + h / 700)

    def mel2hz(m):
        return 700 * (10 ** (m / 2595) - 1)
    mels = np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2)
    hz = mel2hz(mels)
    bins = np.floor((nfft + 1) * hz / fs).astype(int)
    fb = np.zeros((n_mels, nfft // 2 + 1))
    for m in range(1, n_mels + 1):
        a, b, c = bins[m - 1], bins[m], bins[m + 1]
        for k in range(a, b):
            fb[m - 1, k] = (k - a) / max(1, b - a)
        for k in range(b, c):
            fb[m - 1, k] = (c - k) / max(1, c - b)
    n = 1 + max(0, (len(x) - nfft) // hop)
    idx = np.arange(nfft)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx] * np.hanning(nfft)
    S = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    return np.log(fb @ S.T + 1e-6).astype(np.float32)
