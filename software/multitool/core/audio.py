"""Audio vom RP2040 (8 Kanäle, 16 kHz) lesen und in einem 30-s-Ringpuffer bereitstellen.

Reihenfolge im Puffer ist immer:  Mic 0, Mic 1, ... Mic 5, Mittel-Mic  (7 Zeilen, float32, -1..1).
Welcher USB-Kanal welches Mikrofon ist, steht in  array.channel_map  (tools/calibrate.py setzt das).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave

import numpy as np

log = logging.getLogger("audio")

FS = 16000          # Abtastrate der Firmware
BLOCK = 256         # 16 ms pro Block
N_MICS = 7
CENTER = 6          # Index des Mittel-Mikrofons im Puffer


def parse_channel_map(text: str) -> list[int]:
    vals = [int(x.strip()) for x in str(text).split(",") if x.strip() != ""]
    if len(vals) != N_MICS or any(v < 0 or v > 7 for v in vals):
        log.warning("channel_map %r ungültig, benutze Standard", text)
        return [0, 4, 1, 5, 2, 6, 3]
    return vals


class RingBuffer:
    """Ringpuffer für (Kanäle x Samples). Schreiben aus einem Thread, Lesen aus vielen."""

    def __init__(self, channels: int, seconds: float, fs: int = FS):
        self.n = int(seconds * fs)
        self.buf = np.zeros((channels, self.n), dtype=np.float32)
        self.pos = 0                    # Gesamtzahl geschriebener Samples (läuft immer weiter)
        self.lock = threading.Lock()

    def write(self, block: np.array):
        k = block.shape[1]
        with self.lock:
            i = self.pos % self.n
            first = min(k, self.n - i)
            self.buf[:, i:i + first] = block[:, :first]
            if first < k:
                self.buf[:, :k - first] = block[:, first:]
            self.pos += k

    def latest(self, k: int) -> np.ndarray:
        """Die letzten k Samples aller Kanäle (Kopie)."""
        with self.lock:
            k = min(k, self.n, self.pos)
            end = self.pos % self.n
            start = (end - k) % self.n
            if k == 0:
                return np.zeros((self.buf.shape[0], 0), np.float32)
            if start < end:
                return self.buf[:, start:end].copy()
            return np.concatenate([self.buf[:, start:], self.buf[:, :end]], axis=1)

    def since(self, pos: int) -> tuple[np.ndarray, int]:
        """Alles seit Position pos. Gibt (Daten, neue Position) zurück."""
        with self.lock:
            now = self.pos
        k = now - pos
        if k <= 0:
            return np.zeros((self.buf.shape[0], 0), np.float32), now
        data = self.latest(min(k, self.n))
        return data, now


class _DcBlocker:
    """Entfernt den Gleichanteil der MEMS-Mikrofone (Hochpass ca. 30 Hz)."""

    def __init__(self, channels: int, fs: int = FS, fc: float = 30.0):
        a = float(np.exp(-2 * np.pi * fc / fs))
        self.b = np.array([1.0, -1.0])          # y[n] = x[n] - x[n-1] + a*y[n-1]
        self.a = np.array([1.0, -a])
        self.zi = np.zeros((channels, 1))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        from scipy.signal import lfilter
        y, self.zi = lfilter(self.b, self.a, x.astype(np.float64), axis=1, zi=self.zi)
        return y


class WavWriter:
    """Schreibt Audio laufend in eine WAV-Datei (int16). Kanäle: 1 (Strahl) oder 7 (roh)."""

    def __init__(self, path: str, channels: int, fs: int = FS, gain: float = 8.0):
        self.path = path
        self.gain = gain
        self.w = wave.open(path, "wb")
        self.w.setnchannels(channels)
        self.w.setsampwidth(2)
        self.w.setframerate(fs)
        self.lock = threading.Lock()
        self.frames = 0

    def write(self, data: np.ndarray):
        """data: (Kanäle, n) oder (n,)"""
        if data.ndim == 1:
            data = data[None, :]
        pcm = np.clip(data.T * self.gain * 32767.0, -32768, 32767).astype("<i2")
        with self.lock:
            if self.w is not None:
                self.w.writeframes(pcm.tobytes())
                self.frames += pcm.shape[0]

    def close(self):
        with self.lock:
            if self.w is not None:
                self.w.close()
                self.w = None


class AudioBus:
    """Liest das USB-Mikrofon und verteilt die Daten.

    Wer laufend Audio braucht, meldet einen Empfänger an (add_sink). Der Empfänger bekommt
    jeden Block (7 x 256) im Audio-Thread. Er muss schnell sein. Schwere Rechnungen macht
    jeder Modus in seinem eigenen Thread mit  ring.latest(...).
    """

    def __init__(self, cfg, sim=None):
        self.cfg = cfg
        self.sim = sim
        self.ring = RingBuffer(N_MICS, 30.0)
        self.channel_map = parse_channel_map(cfg.get("array", "channel_map"))
        self._sinks: list = []
        self._sinks_lock = threading.Lock()
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._stream = None
        self._running = False
        self._dc = _DcBlocker(N_MICS)
        self.ok = False
        self.error = ""
        self.overruns = 0
        self.last_block_time = 0.0
        cfg.on_change(self._cfg_changed)

    def _cfg_changed(self, group, key, value):
        if group == "array" and key == "channel_map":
            self.channel_map = parse_channel_map(value)

    # ---- Empfänger ----
    def add_sink(self, fn):
        with self._sinks_lock:
            self._sinks.append(fn)

    def remove_sink(self, fn):
        with self._sinks_lock:
            if fn in self._sinks:
                self._sinks.remove(fn)

    # ---- Start/Stopp ----
    def start(self):
        self._running = True
        threading.Thread(target=self._worker, name="audio-worker", daemon=True).start()
        if self.sim is not None:
            threading.Thread(target=self._sim_loop, name="audio-sim", daemon=True).start()
            self.ok = True
            return
        threading.Thread(target=self._open_loop, name="audio-open", daemon=True).start()

    def stop(self):
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass

    def _find_device(self):
        import sounddevice as sd
        want = str(self.cfg.get("audio", "device_name") or "").lower()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] >= 8 and (not want or want in d["name"].lower()):
                return i
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] >= 8:
                return i
        return None

    def _open_loop(self):
        """Gerät öffnen. Wenn das USB-Mikrofon fehlt, alle 3 s erneut versuchen."""
        import sounddevice as sd
        while self._running:
            if self._stream is None or not self._stream.active:
                try:
                    try:   # Geräteliste neu einlesen (falls der RP2040 später eingesteckt wurde)
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        pass
                    dev = self._find_device()
                    if dev is None:
                        raise RuntimeError("USB-Mikrofon (RP2040) nicht gefunden")
                    self._stream = sd.InputStream(device=dev, channels=8, samplerate=FS, dtype="int32",
                                                  blocksize=BLOCK, callback=self._callback, latency="high")
                    self._stream.start()
                    self.ok = True
                    self.error = ""
                    log.info("Audio läuft: %s", sd.query_devices(dev)["name"])
                except Exception as e:
                    self.ok = False
                    if self.error != str(e):
                        log.error("Audio: %s", e)
                    self.error = str(e)
            # Watchdog: kommen keine Daten mehr (Kabel gezogen), neu öffnen
            if self.ok and time.time() - self.last_block_time > 3 and self.last_block_time > 0:
                log.warning("Audio: keine Daten mehr, öffne neu")
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
                self.ok = False
            time.sleep(3)

    def _callback(self, indata, frames, time_info, status):
        if status:
            self.overruns += 1
        try:
            self._q.put_nowait(indata.copy())
        except queue.Full:
            self.overruns += 1

    def _worker(self):
        scale = 1.0 / 2147483648.0
        while self._running:
            try:
                raw = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            self.last_block_time = time.time()
            if raw.dtype != np.float32:
                x = raw.astype(np.float32) * scale          # (n, 8)
            else:
                x = raw
            block = x[:, self.channel_map].T.copy()         # (7, n)
            block = self._dc(block).astype(np.float32)
            self.ring.write(block)
            with self._sinks_lock:
                sinks = list(self._sinks)
            for fn in sinks:
                try:
                    fn(block)
                except Exception:
                    log.exception("Audio-Empfänger")

    def _sim_loop(self):
        """Simulation: liefert Blöcke im Echtzeittakt aus dem Simulator."""
        t_next = time.time()
        while self._running:
            raw = self.sim.audio_block(BLOCK)               # (n, 8) float32
            try:
                self._q.put_nowait(raw)
            except queue.Full:
                pass
            t_next += BLOCK / FS
            dt = t_next - time.time()
            if dt > 0:
                time.sleep(dt)
            else:
                t_next = time.time()

    # ---- Hilfen ----
    def level_dbfs(self, seconds: float = 0.25, ch: int = CENTER) -> float:
        x = self.ring.latest(int(seconds * FS))[ch]
        if x.size == 0:
            return -120.0
        return float(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-9))

    def dbfs_to_spl(self, dbfs: float) -> float:
        """MSM261: -26 dBFS bei 94 dB SPL  ->  dB SPL = dBFS + 120 (+ Offset). Nur Richtwert."""
        return dbfs + 120.0 + float(self.cfg.get("audio", "level_offset_db") or 0)
