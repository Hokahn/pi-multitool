"""Richtungsschätzung und akustische Karten.

SRP-PHAT  : robust für "aus welcher Richtung kommt das?" (Drohne, Geräusch, Vogel, Sprecher)
CsmMapper : Karte über das Kamerabild aus der Kreuzspektralmatrix, wahlweise
            Delay-and-Sum oder Functional Beamforming (schärfer bei einer dominanten Quelle).
"""

from __future__ import annotations

import numpy as np

from . import geometry as geo


def _frames(x: np.ndarray, nfft: int, hop: int) -> np.ndarray:
    """(M, n) -> (K, M, nfft) überlappende Fenster."""
    m, n = x.shape
    if n < nfft:
        x = np.pad(x, ((0, 0), (0, nfft - n)))
        n = nfft
    k = 1 + (n - nfft) // hop
    idx = np.arange(nfft)[None, :] + hop * np.arange(k)[:, None]
    return x[:, idx].transpose(1, 0, 2)


class SRP:
    """Steered Response Power mit PHAT-Gewichtung über ein festes Richtungsraster."""

    def __init__(self, positions, dirs, fs=16000, nfft=1024, fmin=300.0, fmax=6000.0, phat=True):
        self.dirs = np.asarray(dirs)
        self.nfft = nfft
        self.phat = phat
        self.win = np.hanning(nfft).astype(np.float32)
        freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
        self.bins = np.where((freqs >= fmin) & (freqs <= fmax))[0]
        f = freqs[self.bins]
        tau = geo.delays(positions, self.dirs)                         # (M, D)
        # Ausrichten: Phase um +2*pi*f*tau drehen
        self.E = np.exp(2j * np.pi * f[:, None, None] * tau[None, :, :]).astype(np.complex64)  # (F, M, D)

    def power(self, x: np.ndarray, hop: int | None = None) -> np.ndarray:
        """x: (M, n) Zeitsignal -> Leistung je Richtung (D,), über alle Fenster gemittelt."""
        hop = hop or self.nfft // 2
        fr = _frames(x, self.nfft, hop) * self.win                     # (K, M, N)
        X = np.fft.rfft(fr, axis=-1)[:, :, self.bins]                  # (K, M, F)
        if self.phat:
            X = X / (np.abs(X) + 1e-12)
        X = X.transpose(0, 2, 1).astype(np.complex64)                  # (K, F, M)
        p = np.zeros(self.dirs.shape[0], np.float64)
        for k in range(X.shape[0]):
            Y = np.einsum("fm,fmd->fd", X[k], self.E, optimize=True)
            p += np.sum(np.abs(Y) ** 2, axis=0)
        return p / max(1, X.shape[0])

    def best(self, x: np.ndarray):
        p = self.power(x)
        i = int(np.argmax(p))
        # Schärfe: wie weit ragt das Maximum über den Mittelwert hinaus (0..1)
        q = float((p[i] - np.mean(p)) / (p[i] + 1e-12))
        return i, self.dirs[i], q, p


class AzimuthFinder:
    """Azimut (0..360°) bei liegendem Array, über drei Elevationen gesucht."""

    def __init__(self, positions, fs=16000, fmin=300.0, fmax=6000.0, step_deg=5, nfft=1024):
        self.az = np.arange(0, 360, step_deg, dtype=float)
        self.el = np.array([0.0, 30.0, 60.0])
        dirs = geo.dir_vectors_azel(self.az, self.el)                  # (len(el)*len(az), 3)
        self.srp = SRP(positions, dirs, fs=fs, nfft=nfft, fmin=fmin, fmax=fmax, phat=True)

    def find(self, x: np.ndarray):
        """-> (azimut_grad, elevation_grad, güte 0..1)"""
        i, d, q, p = self.srp.best(x)
        az = float(np.rad2deg(np.arctan2(d[1], d[0])) % 360)
        el = float(np.rad2deg(np.arcsin(np.clip(d[2], -1, 1))))
        return az, el, q

    def profile(self, x: np.ndarray) -> np.ndarray:
        """Leistung je Azimut (Maximum über Elevation), normiert 0..1."""
        p = self.srp.power(x).reshape(len(self.el), len(self.az)).max(axis=0)
        p = p - p.min()
        return p / (p.max() + 1e-12)


class CsmMapper:
    """Akustische Karte über das Kamerabild."""

    def __init__(self, positions, nx, ny, hfov, vfov, rotation_deg, mirror,
                 fs=16000, nfft=512, fmin=1500.0, fmax=7000.0, bin_step=1):
        self.nx, self.ny = nx, ny
        self.nfft = nfft
        self.win = np.hanning(nfft).astype(np.float32)
        freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
        self.bins = np.where((freqs >= fmin) & (freqs <= fmax))[0][::bin_step]
        self.freqs = freqs[self.bins]
        self.dirs = geo.camera_grid(nx, ny, hfov, vfov, rotation_deg, mirror)
        tau = geo.delays(positions, self.dirs)                                     # (M, D)
        self.M = positions.shape[0]
        # Steuervektoren a = exp(-j 2 pi f tau) / sqrt(M)
        self.A = (np.exp(-2j * np.pi * self.freqs[:, None, None] * tau[None]) / np.sqrt(self.M)).astype(np.complex64)
        self.R = None

    def update(self, x: np.ndarray, smooth: float = 0.5):
        """Neue Daten (M, n) in die gemittelte Kreuzspektralmatrix einrechnen."""
        fr = _frames(x, self.nfft, self.nfft // 2) * self.win
        X = np.fft.rfft(fr, axis=-1)[:, :, self.bins].transpose(2, 1, 0)           # (F, M, K)
        R = np.einsum("fmk,fnk->fmn", X, np.conj(X)) / X.shape[2]                  # (F, M, M)
        R = R.astype(np.complex64)
        self.R = R if self.R is None else smooth * self.R + (1 - smooth) * R

    def map(self, method: str = "das", nu: float = 8.0, remove_diag: bool = True) -> np.ndarray:
        """-> Karte (ny, nx) als Leistung (linear)."""
        if self.R is None:
            return np.zeros((self.ny, self.nx))
        R = self.R
        if method == "functional":
            w, U = np.linalg.eigh(R)                                               # je Frequenz
            w = np.maximum(w, 0) ** (1.0 / nu)
            Rn = np.einsum("fmk,fk,fnk->fmn", U, w, np.conj(U))
            T = np.einsum("fmn,fnd->fmd", Rn, self.A)
            b = np.real(np.sum(np.conj(self.A) * T, axis=1))                       # (F, D)
            b = np.maximum(b, 0) ** nu
        else:
            if remove_diag:
                R = R.copy()
                idx = np.arange(self.M)
                R[:, idx, idx] = 0
            T = np.einsum("fmn,fnd->fmd", R, self.A)
            b = np.real(np.sum(np.conj(self.A) * T, axis=1))
            b = np.maximum(b, 0)
        return b.sum(axis=0).reshape(self.ny, self.nx)


def band_level_dbfs(x: np.ndarray, fs: int, fmin: float, fmax: float) -> float:
    """Pegel eines Kanals in einem Frequenzband (dBFS)."""
    n = len(x)
    if n < 64:
        return -120.0
    X = np.fft.rfft(x * np.hanning(n))
    f = np.fft.rfftfreq(n, 1 / fs)
    m = (f >= fmin) & (f <= fmax)
    p = np.sum(np.abs(X[m]) ** 2) / (np.sum(np.hanning(n) ** 2) * n / 2)
    return float(10 * np.log10(p + 1e-18))
