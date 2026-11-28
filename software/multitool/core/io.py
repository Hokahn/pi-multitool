"""Bedienteile und Sirene an den GPIOs.

Belegung (BCM-Nummern, Pin = Nummer auf der Stiftleiste):
  Drehencoder   A GPIO17 (Pin 11), B GPIO27 (Pin 13), Taster GPIO22 (Pin 15)
  Tastenkreuz   hoch GPIO5 (29), runter GPIO6 (31), links GPIO13 (33), rechts GPIO19 (35),
                OK GPIO26 (37), COM an GND (Pin 39)
  Aufnahme      GPIO23 (Pin 16)
  Sirene        GPIO18 (Pin 12) zum MOSFET-Modul
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("io")

PIN_ENC_A, PIN_ENC_B, PIN_ENC_SW = 17, 27, 22
PIN_UP, PIN_DOWN, PIN_LEFT, PIN_RIGHT, PIN_OK = 5, 6, 13, 19, 26
PIN_REC, PIN_SIREN = 23, 18

# Ereignisse, die Inputs auslöst:
#   enc(+1/-1)      Encoder gedreht           enc_press      Encoder gedrückt
#   up, down        Kreuz (wiederholt beim Halten)
#   left            Kreuz links kurz          left_long      links 3 s
#   right           Kreuz rechts
#   ok              Kreuz Mitte kurz          ok_long        Mitte 3 s
#   rec             Aufnahme kurz             rec_long       Aufnahme 3 s


class Inputs:
    def __init__(self, sim=False):
        self.handlers = {}
        self.ok = False
        if sim:
            return
        try:
            from gpiozero import Button, RotaryEncoder
            self.enc = RotaryEncoder(PIN_ENC_A, PIN_ENC_B, max_steps=0, bounce_time=None)
            self.enc.when_rotated_clockwise = lambda: self._fire("enc", 1)
            self.enc.when_rotated_counter_clockwise = lambda: self._fire("enc", -1)
            self.buttons = {}
            # kurz / lang (3 s)
            for name, pin in (("ok", PIN_OK), ("left", PIN_LEFT), ("rec", PIN_REC), ("enc_press", PIN_ENC_SW)):
                b = Button(pin, pull_up=True, bounce_time=0.03, hold_time=3.0)
                self._wire_short_long(b, name)
                self.buttons[name] = b
            # sofort beim Drücken, beim Halten wiederholen
            for name, pin in (("up", PIN_UP), ("down", PIN_DOWN), ("right", PIN_RIGHT)):
                b = Button(pin, pull_up=True, bounce_time=0.03, hold_time=0.45, hold_repeat=True)
                b.when_pressed = (lambda n=name: self._fire(n))
                if name != "right":
                    b.when_held = (lambda n=name: self._fire(n))
                self.buttons[name] = b
            self.ok = True
        except Exception as e:
            log.error("GPIO-Eingänge nicht verfügbar: %s", e)

    def _wire_short_long(self, b, name):
        state = {"held": False}

        def pressed():
            state["held"] = False

        def held():
            state["held"] = True
            long_name = "enc_long" if name == "enc_press" else name + "_long"
            self._fire(long_name)

        def released():
            if not state["held"]:
                self._fire(name)
        b.when_pressed = pressed
        b.when_held = held
        b.when_released = released

    def on(self, event: str, fn):
        self.handlers[event] = fn

    def _fire(self, event, *args):
        fn = self.handlers.get(event)
        if fn:
            try:
                fn(*args)
            except Exception:
                log.exception("Fehler bei Taste %s", event)

    # Für Simulation und Webseite
    def inject(self, event: str, *args):
        self._fire(event, *args)


class Siren:
    """Sirene über MOSFET. Nie länger als siren.max_seconds, global sperrbar."""

    def __init__(self, cfg, sim=False):
        self.cfg = cfg
        self.dev = None
        self.on = False
        self._off_at = 0.0
        self._lock = threading.Lock()
        self.last_on = 0.0
        if not sim:
            try:
                from gpiozero import OutputDevice
                self.dev = OutputDevice(PIN_SIREN, active_high=True, initial_value=False)
            except Exception as e:
                log.error("Sirene nicht verfügbar: %s", e)
        threading.Thread(target=self._watch, name="siren", daemon=True).start()

    def sound(self, seconds: float):
        if self.cfg.get("siren", "locked"):
            log.info("Sirene gesperrt (Einstellungen)")
            return
        seconds = min(float(seconds), float(self.cfg.get("siren", "max_seconds") or 60))
        with self._lock:
            self._off_at = max(self._off_at, time.time() + seconds)
            self._set(True)

    def beep(self, seconds=0.08):
        self.sound(seconds)

    def stop(self):
        with self._lock:
            self._off_at = 0
            self._set(False)

    def busy(self, extra: float = 0.5) -> bool:
        """True während die Sirene läuft und kurz danach (Detektoren sollen dann pausieren)."""
        return self.on or (time.time() - self.last_on) < extra

    def _set(self, v: bool):
        self.on = v
        if v:
            self.last_on = time.time()
        if self.dev is not None:
            self.dev.value = 1 if v else 0

    def _watch(self):
        while True:
            with self._lock:
                if self.on:
                    self.last_on = time.time()
                    if time.time() >= self._off_at:
                        self._set(False)
            time.sleep(0.02)
