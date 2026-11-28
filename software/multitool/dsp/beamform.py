"""Richtmikrofon per Software: Delay-and-Sum im Frequenzbereich, blockweise (Overlap-Add)."""

from __future__ import annotations

import threading

import numpy as np

from . import geometry as geo


class StreamBeamformer:
    """Bekommt Blöcke (M, hop) und liefert gleich lange Mono-Blöcke in die gewählte Richtung."""

    def __init__(self, positions, fs=16000, nfft=512, direction=(0.0, 0.0, 1.0)):
        self.pos = np.asarray(positions)
        self.fs = fs
        self.nfft = nfft
        self.hop = nfft // 2
        n = np.arange(nfft)
        self.win = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * n / nfft)).astype(np.float32)  # periodisch, sqrt-Hann
        self.freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
        self.M = self.pos.shape[0]
        self._prev = np.zeros((self.M, self.hop), np.float32)
        self._tail = np.zeros(self.hop, np.float32)
        self._pending = np.zeros((self.M, 0), np.float32)
        self._out = np.zeros(0, np.float32)
        self._lock = threading.Lock()
        self.mode = "beam"          # "beam", "center", "sum"
        self.set_direction(direction)

    def set_direction(self, d):
        d = np.asarray(d, float)
        d = d / (np.linalg.norm(d) + 1e-12)
        tau = geo.delays(self.pos, d[None, :])[:, 0]
        with self._lock:
            self.W = (np.exp(2j * np.pi * self.freqs[None, :] * tau[:, None]) / self.M).astype(np.complex64)
            self.direction = d

    def process(self, block: np.ndarray) -> np.ndarray:
        """block: (M, n) beliebige Länge. Gibt Mono gleicher Länge zurück (Verzögerung hop Samples)."""
        if self.mode == "center":
            return block[6].copy()
        if self.mode == "sum":
            return block.mean(axis=0)
        self._pending = np.concatenate([self._pending, block], axis=1)
        out_parts = []
        while self._pending.shape[1] >= self.hop:
            cur = self._pending[:, :self.hop]
            self._pending = self._pending[:, self.hop:]
            frame = np.concatenate([self._prev, cur], axis=1) * self.win
            self._prev = cur
            X = np.fft.rfft(frame, axis=1)
            with self._lock:
                Y = np.sum(X * self.W, axis=0)
            y = np.fft.irfft(Y, n=self.nfft).astype(np.float32) * self.win
            out_parts.append(y[:self.hop] + self._tail)
            self._tail = y[self.hop:]
        if out_parts:
            self._out = np.concatenate([self._out] + out_parts)
        n = block.shape[1]
        if self._out.shape[0] >= n:
            res, self._out = self._out[:n], self._out[n:]
            return res
        res = np.concatenate([np.zeros(n - self._out.shape[0], np.float32), self._out])
        self._out = np.zeros(0, np.float32)
        return res


def beamform_offline(x: np.ndarray, positions, direction, fs=16000) -> np.ndarray:
    """Ganzes Stück (M, n) in eine Richtung bündeln."""
    bf = StreamBeamformer(positions, fs=fs, direction=direction)
    y = bf.process(x)
    # Verzögerung ausgleichen
    pad = bf.process(np.zeros((x.shape[0], bf.hop), np.float32))
    return np.concatenate([y, pad])[bf.hop:]
