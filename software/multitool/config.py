"""Einstellungen: Beschreibung (Schema), Standardwerte, Laden und Speichern.

Alle Einstellungen stehen in  <Datenordner>/config.yaml.  Die Datei wird automatisch
angelegt. Man kann sie von Hand ändern, einfacher geht es über die Webseite.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

import yaml

log = logging.getLogger("config")


@dataclass
class Option:
    """Eine einstellbare Grösse. Display-Menü und Webseite bauen sich daraus selbst auf."""
    key: str
    label: str
    kind: str                      # "choice", "bool", "int", "float", "text", "password"
    default: Any
    choices: list = field(default_factory=list)   # [(wert, "Anzeige"), ...] für kind="choice"
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    help: str = ""

    def values_for_menu(self) -> list:
        """Werte, durch die der Drehencoder blättert."""
        if self.kind == "bool":
            return [False, True]
        if self.kind == "choice":
            return [c[0] for c in self.choices]
        if self.kind in ("int", "float") and self.min is not None and self.max is not None:
            step = self.step or 1
            vals, v = [], self.min
            while v <= self.max + 1e-9:
                vals.append(int(round(v)) if self.kind == "int" else round(v, 3))
                v += step
            return vals
        return []

    def display(self, value) -> str:
        if self.kind == "bool":
            return "an" if value else "aus"
        if self.kind == "choice":
            for v, lab in self.choices:
                if v == value:
                    return lab
            return str(value)
        if self.kind == "password":
            return "****" if value else "(leer)"
        return f"{value}{(' ' + self.unit) if self.unit else ''}"

    def coerce(self, value):
        """Wert aus Webformular oder YAML in den richtigen Typ bringen."""
        if self.kind == "bool":
            if isinstance(value, str):
                return value.lower() in ("1", "true", "an", "on", "ja", "yes")
            return bool(value)
        if self.kind == "int":
            v = int(float(value))
        elif self.kind == "float":
            v = float(value)
        elif self.kind == "choice":
            for c, _ in self.choices:
                if str(c) == str(value):
                    return c
            raise ValueError(f"{self.key}: ungültige Auswahl {value!r}")
        else:
            return str(value)
        if self.min is not None and v < self.min:
            v = type(v)(self.min)
        if self.max is not None and v > self.max:
            v = type(v)(self.max)
        return v

    def to_json(self, value) -> dict:
        return {
            "key": self.key, "label": self.label, "kind": self.kind,
            "value": "" if self.kind == "password" else value,
            "display": self.display(value),
            "choices": [{"value": c[0], "label": c[1]} for c in self.choices],
            "min": self.min, "max": self.max, "step": self.step, "unit": self.unit, "help": self.help,
        }


def O(*a, **k) -> Option:
    return Option(*a, **k)


# ----------------------------------------------------------------------------------------
# Globale Einstellungen, gruppiert. Schlüssel in config.yaml: <gruppe>.<schlüssel>
# ----------------------------------------------------------------------------------------
GLOBAL_SCHEMA: dict[str, tuple[str, list[Option]]] = {
    "battery": ("Akku", [
        O("show_percent", "Prozent in der Statusleiste", "bool", True),
        O("warn1", "Warnung 1 (gelb)", "int", 20, min=10, max=40, step=5, unit="%"),
        O("warn2", "Warnung 2 mit Warnblinken", "int", 10, min=5, max=20, step=1, unit="%"),
        O("warn_beep", "Kurzer Piepton bei Warnung 2", "bool", False),
        O("shutdown", "Herunterfahren bei", "int", 5, min=3, max=10, step=1, unit="%"),
        O("capacity_mah", "Kapazität einer Zelle", "int", 3400, min=1000, max=4000, step=100, unit="mAh"),
        O("ups_type", "Art des UPS HAT", "choice", "A", choices=[
            ("B", "UPS HAT (B), unter dem Pi, 5 A"), ("A", "UPS HAT (A), auf der Stiftleiste, 2,5 A")],
          help="Nach Änderung neu starten."),
        O("i2c_address", "I2C-Adresse der Akku-Messung", "choice", "0x42", choices=[
            ("0x40", "0x40"), ("0x41", "0x41"), ("0x42", "0x42"), ("0x43", "0x43")],
          help="multitool-test battery_test zeigt die gefundene Adresse."),
        O("current_invert", "Stromrichtung umkehren", "bool", False,
          help="Einschalten, wenn der Strom ohne Netzteil positiv angezeigt wird."),
    ]),
    "leds": ("LED-Ring", [
        O("brightness", "Grundhelligkeit", "int", 30, min=5, max=100, step=5, unit="%"),
        O("max_brightness", "Maximale Helligkeit (Stromlimit)", "int", 100, min=10, max=100, step=10, unit="%",
          help="Wenn das Mic Array am 3,3-V-Pin des Pi hängt: auf 25 % stellen."),
        O("night", "Nachtmodus (dunkel ausser Alarm)", "bool", False),
        O("led0_deg", "Winkel von LED 0", "int", 0, min=0, max=345, step=15, unit="°",
          help="Wird von tools/calibrate.py gesetzt."),
        O("led_direction", "LED-Zählrichtung", "choice", 1, choices=[(1, "gegen Uhrzeiger"), (-1, "mit Uhrzeiger")]),
        O("color_order", "Farbreihenfolge", "choice", "BGR", choices=[("BGR", "BGR (SK9822)"), ("RGB", "RGB"), ("GRB", "GRB")]),
    ]),
    "siren": ("Sirene", [
        O("locked", "Sirene global gesperrt", "bool", False, help="Zum Testen im Büro."),
        O("max_seconds", "Längste Einschaltdauer", "int", 60, min=5, max=300, step=5, unit="s"),
    ]),
    "audio": ("Audio", [
        O("level_offset_db", "Pegel-Offset für dB-Anzeige", "int", 0, min=-20, max=20, step=1, unit="dB"),
        O("headphone_volume", "Kopfhörer-Lautstärke", "int", 60, min=0, max=100, step=10, unit="%"),
        O("device_name", "Name des USB-Mikrofons", "text", "Pico Microphone"),
    ]),
    "array": ("Mikrofon-Array", [
        O("radius_mm", "Abstand Mitte bis Mikrofon", "float", 40.0, min=20, max=60, step=0.5, unit="mm",
          help="Mit dem Messschieber messen: Mitte Mittel-Mikrofon bis Mitte eines Aussen-Mikrofons."),
        O("mic_direction", "Nummerierung der Mikrofone", "choice", -1,
          choices=[(1, "gegen Uhrzeiger"), (-1, "mit Uhrzeiger")],
          help="Blick auf die LED-Seite. Beim Sipeed-Board laufen die Zahlen 0-5 im Uhrzeigersinn."),
        O("channel_map", "Kanalzuordnung Mic 0-5, Mitte", "text", "0,4,1,5,2,6,3",
          help="Wird von tools/calibrate.py gesetzt."),
        O("rotation_deg", "Drehung Array zu Kamera", "int", 0, min=-180, max=180, step=5, unit="°",
          help="Verschieben, bis der Farbfleck der akustischen Kamera auf der Schallquelle liegt."),
        O("mirror", "Array gespiegelt zur Kamera", "bool", False),
        O("hfov", "Bildwinkel horizontal", "float", 66.0, min=30, max=120, step=1, unit="°"),
        O("vfov", "Bildwinkel vertikal", "float", 41.0, min=20, max=90, step=1, unit="°"),
        O("north_offset_deg", "Nordrichtung (LED 0 zeigt nach)", "int", 0, min=0, max=355, step=5, unit="°"),
    ]),
    "storage": ("Speicher", [
        O("when_full", "Wenn voll", "choice", "delete_oldest",
          choices=[("delete_oldest", "älteste Überwachung löschen"), ("stop", "Aufnahme stoppen")]),
        O("max_surveillance_gb", "Maximum für Überwachung", "int", 50, min=5, max=100, step=5, unit="GB"),
        O("min_free_gb", "Mindestens frei lassen", "int", 2, min=1, max=10, step=1, unit="GB"),
    ]),
    "location": ("Standort", [
        O("lat", "Breitengrad", "float", 47.0, min=-90, max=90, step=0.01),
        O("lon", "Längengrad", "float", 7.5, min=-180, max=180, step=0.01),
    ]),
    "display": ("Display", [
        O("enabled", "Display benutzen", "bool", True),
        O("brightness", "Helligkeit", "int", 70, min=10, max=100, step=10, unit="%"),
        O("off_after", "Ausschalten nach", "choice", 60,
          choices=[(0, "nie"), (30, "30 s"), (60, "1 min"), (300, "5 min")]),
        O("rotation", "Drehung", "choice", 270, choices=[(0, "0°"), (90, "90°"), (180, "180°"), (270, "270°")],
          help="Das 3,5-Zoll-Display ist hochkant gebaut, 90° ergibt quer."),
        O("fps", "Bildrate", "int", 8, min=2, max=15, step=1, unit="fps"),
    ]),
    "network": ("Netzwerk", [
        O("mode", "Netzmodus", "choice", "auto", choices=[
            ("auto", "Auto: WLAN, sonst Hotspot"), ("wifi", "Nur WLAN"),
            ("hotspot", "Nur Hotspot"), ("off", "WLAN aus")]),
        O("hotspot_ssid", "Hotspot-Name", "text", "Multitool"),
        O("hotspot_password", "Hotspot-Passwort (min. 8 Zeichen)", "text", "multitool123"),
        O("ntfy_topic", "Push-Thema für ntfy.sh", "text", "",
          help="Frei wählbar, z.B. multitool-hokahn-7x3k. In der ntfy-App abonnieren."),
        O("web_port", "Port der Webseite", "int", 80, min=80, max=65535, step=1),
    ]),
    "system": ("System", [
        O("start_mode", "Startmodus", "choice", "last", choices=[
            ("last", "zuletzt benutzt"), ("acoustic_cam", "Akustische Kamera"), ("diagnosis", "Maschinendiagnose"),
            ("camcorder", "Camcorder"), ("drone", "Drohnenerkennung"), ("surveillance_cam", "Überwachungskamera"),
            ("surveillance_mic", "Überwachungsmikrofon"), ("birdnet", "BirdNET")]),
    ]),
}


def default_config() -> dict:
    cfg: dict[str, Any] = {}
    for group, (_, opts) in GLOBAL_SCHEMA.items():
        cfg[group] = {o.key: o.default for o in opts}
    cfg["modes"] = {}
    cfg["state"] = {"last_mode": "acoustic_cam"}
    cfg["secret"] = {}   # Web-Passwort (gehasht), Session-Schlüssel
    return cfg


def _deep_merge(base: dict, extra: dict) -> dict:
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    """Thread-sichere Einstellungen mit automatischem Speichern."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "config.yaml")
        self._lock = threading.RLock()
        self._data = default_config()
        self._listeners = []
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    _deep_merge(self._data, yaml.safe_load(f) or {})
            except Exception as e:  # kaputte Datei: Sicherung anlegen, mit Standard weiter
                log.error("config.yaml unlesbar (%s), benutze Standardwerte", e)
                os.replace(self.path, self.path + ".kaputt")
        self.save()

    # ---- Zugriff ----
    def get(self, group: str, key: str, default=None):
        with self._lock:
            return self._data.get(group, {}).get(key, default)

    def group(self, group: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._data.get(group, {}))

    def set(self, group: str, key: str, value, save: bool = True):
        with self._lock:
            self._data.setdefault(group, {})[key] = value
            if save:
                self.save()
        for cb in list(self._listeners):
            try:
                cb(group, key, value)
            except Exception:
                log.exception("Listener-Fehler")

    def mode_get(self, mode: str, key: str, default=None):
        with self._lock:
            return self._data["modes"].get(mode, {}).get(key, default)

    def mode_set(self, mode: str, key: str, value):
        with self._lock:
            self._data["modes"].setdefault(mode, {})[key] = value
            self.save()
        for cb in list(self._listeners):
            try:
                cb(f"modes.{mode}", key, value)
            except Exception:
                log.exception("Listener-Fehler")

    def on_change(self, cb):
        self._listeners.append(cb)

    def set_global_from_user(self, group: str, key: str, raw):
        opt = find_global_option(group, key)
        if opt is None:
            raise KeyError(f"unbekannte Einstellung {group}.{key}")
        self.set(group, key, opt.coerce(raw))

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(self._data, f, allow_unicode=True, sort_keys=False)
            os.replace(tmp, self.path)

    def global_json(self) -> list:
        out = []
        with self._lock:
            for group, (title, opts) in GLOBAL_SCHEMA.items():
                out.append({"group": group, "title": title,
                            "options": [o.to_json(self._data[group].get(o.key, o.default)) for o in opts]})
        return out


def find_global_option(group: str, key: str) -> Option | None:
    if group not in GLOBAL_SCHEMA:
        return None
    for o in GLOBAL_SCHEMA[group][1]:
        if o.key == key:
            return o
    return None
