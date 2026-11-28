"""Gemeinsame Hilfen für die Testwerkzeuge. Werden mit  multitool-test <name>  gestartet."""

import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from multitool.config import Config  # noqa: E402
from multitool.core.audio import FS, parse_channel_map  # noqa: E402

DATA = os.environ.get("MULTITOOL_DATA", "/opt/multitool/data")


def cfg():
    os.makedirs(DATA, exist_ok=True)
    return Config(DATA)


def find_device(name_hint="pico"):
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] >= 8 and name_hint.lower() in d["name"].lower():
            return i
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] >= 8:
            return i
    return None


class Mic:
    """Liest das 8-Kanal-USB-Mikrofon blockweise. Rohkanäle (USB-Reihenfolge) als float."""

    def __init__(self):
        import sounddevice as sd
        dev = find_device()
        if dev is None:
            print("\nFEHLER: Kein 8-Kanal-Mikrofon gefunden.")
            print("  - Ist der RP2040-Zero per USB am Pi?   lsusb   sollte 'Raspberry Pi' zeigen.")
            print("  - Ist die Firmware drauf? (Doku, Kapitel Firmware)")
            print("  - Läuft noch das Hauptprogramm? Dann zuerst:  sudo systemctl stop multitool")
            sys.exit(1)
        self.name = sd.query_devices(dev)["name"]
        self.stream = sd.InputStream(device=dev, channels=8, samplerate=FS, dtype="int32", blocksize=1024)
        self.stream.start()

    def read(self, seconds):
        n = int(seconds * FS)
        data, _ = self.stream.read(n)
        x = data.astype(np.float32) / 2147483648.0
        return x - x.mean(axis=0)            # Gleichanteil weg

    def close(self):
        self.stream.stop()
        self.stream.close()


def db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-9)


def bar(v, lo=-90, hi=-20, width=30):
    k = int(max(0, min(1, (v - lo) / (hi - lo))) * width)
    return "#" * k + "." * (width - k)


def leds():
    from multitool.core.leds import LedRing
    c = cfg()
    r = LedRing(c, sim=False)
    return r, c


def wait_enter(prompt):
    try:
        input(prompt)
        return True
    except (EOFError, KeyboardInterrupt):
        return False


def mapped(x, c):
    """Rohkanäle -> Mic 0..5, Mitte (nach channel_map)."""
    return x[:, parse_channel_map(c.get("array", "channel_map"))].T


__all__ = ["cfg", "Mic", "db", "bar", "leds", "wait_enter", "mapped", "FS", "time", "np"]
