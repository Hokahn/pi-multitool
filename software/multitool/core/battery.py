"""Akku-Anzeige über den INA219 auf dem UPS HAT (B), I2C-Adresse 0x42.

Prozent = Coulomb-Zähler (Strom aufsummieren), in Ruhe mit der Leerlaufspannung nachgeführt.
Nur aus der Spannung zu rechnen springt stark, weil die Spannung unter Last absackt.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("battery")

# Leerlaufspannung je Zelle -> Ladezustand (typisch Li-Ion NMC, z.B. Samsung 35E)
OCV_V = np.array([3.00, 3.30, 3.45, 3.55, 3.62, 3.68, 3.74, 3.80, 3.87, 3.95, 4.05, 4.20])
OCV_P = np.array([0, 3, 8, 15, 25, 35, 45, 55, 65, 75, 87, 100], dtype=float)
R_INTERNAL = 0.15     # Ohm für das ganze 2S-Paket inkl. Kontakte (Schätzung)


class Ina219:
    REG_CONFIG, REG_SHUNT, REG_BUS, REG_POWER, REG_CURRENT, REG_CAL = 0, 1, 2, 3, 4, 5

    def __init__(self, bus=1, addr=0x42, ups_type="B"):
        from smbus2 import SMBus
        self.bus = SMBus(bus)
        self.addr = addr
        if ups_type == "A":
            # Waveshare UPS HAT (A): 32 V Bereich, Shunt 0,1 Ohm, bis ca. 2 A
            self.cal = 4096
            self.current_lsb_ma = 0.1
            cfg = (1 << 13) | (3 << 11) | (0x0D << 7) | (0x0D << 3) | 0x07
        else:
            # Waveshare UPS HAT (B): 16 V Bereich, Shunt 0,01 Ohm, bis ca. 5 A
            self.cal = 26868
            self.current_lsb_ma = 0.1524
            cfg = (0 << 13) | (1 << 11) | (0x0D << 7) | (0x0D << 3) | 0x07
        self._w(self.REG_CAL, self.cal)
        self._w(self.REG_CONFIG, cfg)

    def _w(self, reg, val):
        self.bus.write_i2c_block_data(self.addr, reg, [(val >> 8) & 0xFF, val & 0xFF])

    def _r(self, reg):
        d = self.bus.read_i2c_block_data(self.addr, reg, 2)
        return (d[0] << 8) | d[1]

    def voltage(self) -> float:
        return ((self._r(self.REG_BUS) >> 3) * 4) / 1000.0

    def current_a(self) -> float:
        self._w(self.REG_CAL, self.cal)
        v = self._r(self.REG_CURRENT)
        if v > 32767:
            v -= 65536
        return v * self.current_lsb_ma / 1000.0


class Battery:
    """Liest alle 2 s. Ruft on_level(stufe) auf: 0 ok, 1 Warnung 1, 2 Warnung 2, 3 Herunterfahren."""

    def __init__(self, cfg, data_dir, sim=None):
        self.cfg = cfg
        self.sim = sim
        self.state_path = os.path.join(data_dir, "battery.json")
        self.ok = False
        self.voltage = 0.0
        self.current = 0.0        # A, positiv = laden, negativ = entladen
        self.percent = None
        self.charging = False
        self.level = 0
        self.on_level = None
        self.minutes_left = None
        self._soc = None
        self._rest_since = time.time()
        self._last_save = 0.0
        self._dev = None
        if sim is None:
            try:
                self._dev = Ina219(addr=int(str(cfg.get("battery", "i2c_address") or "0x42"), 16),
                                   ups_type=cfg.get("battery", "ups_type") or "B")
                self.ok = True
            except Exception as e:
                log.error("Akku-Messung (INA219) nicht gefunden: %s", e)
        else:
            self.ok = True
        try:
            with open(self.state_path) as f:
                st = json.load(f)
            if time.time() - st.get("t", 0) < 12 * 3600:
                self._soc = float(st["soc"])
        except Exception:
            pass

    def start(self):
        threading.Thread(target=self._loop, name="battery", daemon=True).start()

    def _read(self):
        if self.sim is not None:
            return self.sim.battery()
        i = self._dev.current_a()
        if self.cfg.get("battery", "current_invert"):
            i = -i
        return self._dev.voltage(), i

    @staticmethod
    def ocv_percent(v_pack: float, i: float) -> float:
        v_cell = (v_pack + (-i) * R_INTERNAL) / 2.0      # beim Entladen (i<0) Spannung hochrechnen
        return float(np.interp(v_cell, OCV_V, OCV_P))

    def _loop(self):
        last = time.time()
        while True:
            try:
                v, i = self._read()
                self.ok = True
            except Exception as e:
                if self.ok:
                    log.error("Akku lesen: %s", e)
                self.ok = False
                time.sleep(5)
                continue
            now = time.time()
            dt = now - last
            last = now
            self.voltage, self.current = v, i
            self.charging = i > 0.05
            # 2 Zellen in Serie: Kapazität in Ah = Kapazität einer Zelle
            cap_ah = float(self.cfg.get("battery", "capacity_mah") or 3400) / 1000.0
            ocv = self.ocv_percent(v, i)
            if self._soc is None:
                self._soc = ocv
            else:
                self._soc += (i * dt / 3600.0) / cap_ah * 100.0
                # in Ruhe (wenig Strom) langsam zur Leerlaufspannung ziehen
                if abs(i) < 0.15:
                    if now - self._rest_since > 60:
                        self._soc += (ocv - self._soc) * 0.02
                else:
                    self._rest_since = now
                # Laden fertig
                if self.charging and v >= 8.35 and i < 0.15:
                    self._soc = 100.0
                # grobe Fehler korrigieren
                if abs(ocv - self._soc) > 25:
                    self._soc += (ocv - self._soc) * 0.1
            self._soc = float(min(100.0, max(0.0, self._soc)))
            self.percent = int(round(self._soc))
            if i < -0.05:
                self.minutes_left = int(self._soc / 100 * cap_ah / (-i) * 60)
            else:
                self.minutes_left = None
            if now - self._last_save > 60:
                self._save()
            self._update_level()
            time.sleep(2)

    def _update_level(self):
        p = self.percent
        b = self.cfg.group("battery")
        if p is None:
            return
        lvl = 0
        if not self.charging:
            if p <= b.get("shutdown", 5) or (0 < self.voltage < 6.4):
                lvl = 3
            elif p <= b.get("warn2", 10):
                lvl = 2
            elif p <= b.get("warn1", 20):
                lvl = 1
        if lvl != self.level:
            self.level = lvl
            if self.on_level:
                try:
                    self.on_level(lvl)
                except Exception:
                    log.exception("on_level")

    def _save(self):
        self._last_save = time.time()
        try:
            with open(self.state_path, "w") as f:
                json.dump({"soc": self._soc, "t": time.time()}, f)
        except Exception:
            pass

    def status(self) -> dict:
        return {"ok": self.ok, "percent": self.percent, "voltage": round(self.voltage, 2),
                "current": round(self.current, 3), "charging": self.charging,
                "minutes_left": self.minutes_left, "level": self.level}
