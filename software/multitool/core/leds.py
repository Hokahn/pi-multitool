"""LED-Ring (12 x SK9822) über SPI0 des Pi.

Ebenen, von wichtig nach unwichtig:
  1. Modusanzeige beim Drehen des Encoders (2 s)
  2. Alarm (rotes Blinken)
  3. Muster des aktiven Modus (Richtung, Licht, Countdown ...)
  4. Akku-Warnung (wird über das Modusmuster gelegt)
"""

from __future__ import annotations

import logging
import math
import threading
import time

log = logging.getLogger("leds")

N = 12
BLACK = (0, 0, 0)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 80, 255)
WHITE = (255, 255, 255)
ORANGE = (255, 110, 0)
YELLOW = (255, 200, 0)
CYAN = (0, 220, 220)


def scale(c, f):
    return tuple(int(max(0, min(255, v * f))) for v in c)


class _SpiBackend:
    def __init__(self):
        import spidev
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 2_000_000
        self.spi.mode = 0

    def write(self, data: bytes):
        self.spi.xfer2(list(data))


class _NullBackend:
    def write(self, data: bytes):
        pass


class LedRing:
    def __init__(self, cfg, sim=False):
        self.cfg = cfg
        self.sim = sim
        self.lock = threading.Lock()
        self.frame = [BLACK] * N          # letzter ausgegebener Zustand (für Webseite)
        self.mode_frame = [BLACK] * N     # Muster des Modus
        self.pattern = None               # Funktion t -> Liste von 12 Farben
        self.alarm_until = 0.0
        self.alarm_color = RED
        self.select_until = 0.0
        self.select_frame = [BLACK] * N
        self.battery_level = 0            # 0 ok, 1 Warnung 1, 2 Warnung 2
        self.flash_until = 0.0
        self.flash_color = WHITE
        self.override_brightness = None   # z.B. Camcorder-Licht
        self._running = False
        try:
            self.backend = _NullBackend() if sim else _SpiBackend()
        except Exception as e:
            log.error("SPI für LEDs nicht verfügbar (%s). Ist SPI aktiviert?", e)
            self.backend = _NullBackend()

    # ---- öffentliche Funktionen ----
    def set_frame(self, colors):
        with self.lock:
            self.pattern = None
            self.mode_frame = list(colors) + [BLACK] * (N - len(colors))

    def set_pattern(self, fn):
        """fn(t) -> 12 Farben, wird ca. 30 x pro Sekunde aufgerufen."""
        with self.lock:
            self.pattern = fn

    def off(self):
        self.set_frame([BLACK] * N)

    def fill(self, color, level=1.0):
        self.set_frame([scale(color, level)] * N)

    def sector(self, led: int, color, width: int = 1):
        """Eine LED hell, die Nachbarn schwach (Richtungsanzeige)."""
        fr = [BLACK] * N
        fr[led % N] = color
        for k in range(1, width + 1):
            fr[(led + k) % N] = scale(color, 0.25 / k)
            fr[(led - k) % N] = scale(color, 0.25 / k)
        self.set_frame(fr)

    def alarm(self, seconds: float, color=RED):
        self.alarm_color = color
        self.alarm_until = time.time() + seconds

    def stop_alarm(self):
        self.alarm_until = 0

    def flash(self, seconds: float, color=WHITE):
        self.flash_color = color
        self.flash_until = time.time() + seconds

    def show_mode_select(self, index: int, color):
        """Modus Nummer index (1..7) als Anzahl leuchtender LEDs."""
        fr = [BLACK] * N
        for i in range(min(N, index)):
            fr[i] = color
        self.select_frame = fr
        self.select_until = time.time() + 2.0

    # ---- Schleife ----
    def start(self):
        self._running = True
        threading.Thread(target=self._loop, name="leds", daemon=True).start()

    def stop(self):
        self._running = False
        time.sleep(0.1)
        self._write([BLACK] * N, 0)

    def _brightness(self) -> float:
        b = float(self.cfg.get("leds", "brightness") or 30) / 100.0
        if self.override_brightness is not None:
            b = float(self.override_brightness)
        return min(b, float(self.cfg.get("leds", "max_brightness") or 100) / 100.0)

    def _loop(self):
        while self._running:
            t = time.time()
            with self.lock:
                pat = self.pattern
                base = list(self.mode_frame)
            if pat is not None:
                try:
                    base = list(pat(t))
                except Exception:
                    log.exception("LED-Muster")
            night = bool(self.cfg.get("leds", "night"))
            out = base
            if night:
                out = [BLACK] * N
            # Akku-Warnung
            if self.battery_level == 1 and (t % 10) < 0.4:
                out = list(out)
                out[0] = ORANGE
            elif self.battery_level >= 2 and (t % 30) < 1.5 and (t % 0.5) < 0.25:
                out = [RED] * N
            if t < self.flash_until:
                out = [self.flash_color] * N
            if t < self.alarm_until:
                out = [self.alarm_color if (t % 0.4) < 0.2 else BLACK] * N
            if t < self.select_until:
                out = self.select_frame
            bri = self._brightness()
            # Alarm und Licht dürfen heller sein als die Grundhelligkeit
            if t < self.alarm_until or t < self.flash_until:
                bri = float(self.cfg.get("leds", "max_brightness") or 100) / 100.0
            self._write(out, bri)
            time.sleep(1 / 30)

    def _write(self, colors, bri: float):
        order = self.cfg.get("leds", "color_order") or "BGR"
        # 5-Bit-Globalhelligkeit und 8-Bit-Farbe kombiniert
        g5 = max(1, min(31, int(round(bri * 31)))) if bri > 0 else 0
        data = bytearray(4)
        shown = []
        for c in colors[:N]:
            r, g, b = (int(v) for v in c)
            shown.append(scale((r, g, b), g5 / 31))
            px = {"R": r, "G": g, "B": b}
            data += bytes([0xE0 | g5, px[order[0]], px[order[1]], px[order[2]]])
        data += bytes(4) + bytes((N + 15) // 16)
        with self.lock:
            self.frame = shown
        try:
            self.backend.write(bytes(data))
        except Exception as e:
            log.error("LED-Ausgabe: %s", e)
            self.backend = _NullBackend()


def spinner(color, speed=1.0):
    def fn(t):
        i = int(t * 12 * speed) % N
        fr = [BLACK] * N
        fr[i] = color
        fr[(i - 1) % N] = scale(color, 0.3)
        return fr
    return fn


def pulse(color, period=2.0, low=0.1):
    def fn(t):
        f = low + (1 - low) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))
        return [scale(color, f)] * N
    return fn


def countdown(color, t_end, total):
    """Ring leert sich bis t_end (Scharfschaltverzögerung)."""
    def fn(t):
        left = max(0.0, t_end - t)
        k = int(math.ceil(N * left / max(total, 0.1)))
        return [color if i < k else BLACK for i in range(N)]
    return fn
