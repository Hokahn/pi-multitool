"""Bedienung am Gerät mit Tastenkreuz, Drehencoder und Aufnahme-Taster.

Im Livebild
  hoch / runter       Modusliste öffnen und blättern, OK wechselt, links bricht ab
  OK oder rechts      Einstellungen des Modus öffnen
  Encoder drehen      Schnellregler des Modus (z.B. Licht im Camcorder)
  Encoder drücken     anderen Schnellregler wählen (falls der Modus mehrere hat)
  links 3 s halten    Hotspot an/aus
  OK 3 s halten       Herunterfahren
  Aufnahme kurz/lang  je nach Modus

Im Menü
  hoch / runter       Eintrag wählen
  Encoder drehen      Wert ändern (schnell drehen = grosse Schritte)
  rechts oder OK      Wert eine Stufe weiter, bzw. Aktion ausführen
  Encoder drücken     Wert auf Standard zurücksetzen
  links / Aufnahme    Menü schliessen
"""

from __future__ import annotations

import time

import numpy as np

from .draw import Canvas


def step_value(opt, cur, d, wrap):
    """Nächster Wert einer Einstellung in Richtung d (Schritte, auch mehrere)."""
    vals = opt.values_for_menu()
    if not vals:
        return cur
    try:
        i = vals.index(cur)
    except ValueError:
        # nächstgelegenen Wert suchen (z.B. nach Handeingabe auf der Webseite)
        try:
            i = int(np.argmin([abs(float(v) - float(cur)) for v in vals]))
        except (TypeError, ValueError):
            i = 0
    if wrap and opt.kind in ("bool", "choice"):
        i = (i + d) % len(vals)
    else:
        i = max(0, min(len(vals) - 1, i + d))
    return vals[i]


class Menu:
    TIMEOUT = 8.0

    def __init__(self, app):
        self.app = app
        self.kind = None          # None, "modes", "options"
        self.idx = 0
        self.items = []
        self.last_input = 0.0
        self.quick_idx = {}       # Modus -> Index des aktiven Schnellreglers
        self._last_enc = 0.0

    # ---------------------------------------------------------------- Hilfen
    def _touch(self):
        self.last_input = time.time()

    def _enc_steps(self, d, n_vals):
        """Schnell drehen = grössere Schritte (bei langen Wertelisten)."""
        now = time.time()
        fast = now - self._last_enc < 0.07
        self._last_enc = now
        if fast and n_vals > 20:
            return d * 5
        return d

    def close(self):
        self.kind = None

    def tick(self):
        if self.kind and time.time() - self.last_input > self.TIMEOUT:
            self.close()

    # ---------------------------------------------------------------- Tastenkreuz
    def up(self):
        self._move(-1)

    def down(self):
        self._move(1)

    def _move(self, d):
        self._touch()
        if self.kind is None:
            self.kind = "modes"
            self.idx = self.app.mode_index()
        if self.kind == "modes":
            self.idx = (self.idx + d) % len(self.app.modes)
            m = self.app.modes[self.idx]
            self.app.leds.show_mode_select(self.idx + 1, m.color)
        elif self.items:
            self.idx = (self.idx + d) % len(self.items)

    def left(self):
        self._touch()
        self.close()

    def right(self):
        self.ok()

    def ok(self):
        self._touch()
        if self.kind is None:
            self._open_options()
        elif self.kind == "modes":
            self.app.switch_mode(self.app.modes[self.idx].key)
            self.close()
        elif self.kind == "options":
            it = self.items[self.idx]
            if it["type"] == "action":
                it["fn"]()
                if it.get("close", True):
                    self.close()
            else:
                o = it["opt"]
                self.app.mode.set_opt(o.key, step_value(o, self.app.mode.opt(o.key), 1, wrap=True))

    # ---------------------------------------------------------------- Encoder
    def enc(self, d):
        if self.kind == "modes":
            self._move(d)
            return
        if self.kind == "options":
            self._touch()
            it = self.items[self.idx]
            if it["type"] == "option":
                o = it["opt"]
                n = len(o.values_for_menu())
                self.app.mode.set_opt(o.key, step_value(o, self.app.mode.opt(o.key), self._enc_steps(d, n), wrap=False))
            return
        # Schnellregler im Livebild
        o = self._quick_option()
        if o is None:
            self.app.show_toast("Kein Schnellregler in diesem Modus", 1.5)
            return
        m = self.app.mode
        n = len(o.values_for_menu())
        v = m.set_opt(o.key, step_value(o, m.opt(o.key), self._enc_steps(d, n), wrap=False))
        self.app.show_toast(f"{o.label}: {o.display(v)}", 1.5)

    def enc_press(self):
        if self.kind == "options":
            self._touch()
            it = self.items[self.idx]
            if it["type"] == "option":
                o = it["opt"]
                self.app.mode.set_opt(o.key, o.default)
                self.app.show_toast(f"{o.label}: Standard", 1.2)
            return
        if self.kind == "modes":
            self.ok()
            return
        m = self.app.mode
        keys = getattr(m, "QUICK", [])
        if len(keys) > 1:
            self.quick_idx[m.key] = (self.quick_idx.get(m.key, 0) + 1) % len(keys)
        o = self._quick_option()
        if o is not None:
            self.app.show_toast(f"Rad: {o.label} ({o.display(m.opt(o.key))})", 1.8)

    def _quick_option(self):
        m = self.app.mode
        keys = getattr(m, "QUICK", [])
        if not keys:
            return None
        k = keys[self.quick_idx.get(m.key, 0) % len(keys)]
        return m.option(k)

    def quick_label(self):
        o = self._quick_option()
        if o is None:
            return ""
        return f"Rad: {o.label} {o.display(self.app.mode.opt(o.key))}"

    # ---------------------------------------------------------------- Einträge
    def _open_options(self):
        mode = self.app.mode
        self.items = []
        for o in mode.OPTIONS:
            if o.values_for_menu():
                self.items.append({"type": "option", "opt": o})
        a = self.app
        self.items.append({"type": "action", "label": "Netzwerk: " + a.network_label(), "fn": a.network.toggle,
                           "close": False})
        self.items.append({"type": "action", "label": "Display aus", "fn": a.display_off})
        self.items.append({"type": "action", "label": "Herunterfahren", "fn": a.shutdown})
        self.kind = "options"
        self.idx = 0

    # ---------------------------------------------------------------- Zeichnen
    def draw(self, img: np.ndarray) -> np.ndarray:
        if self.kind is None:
            return img
        H, W = img.shape[:2]
        c = Canvas(img)
        if self.kind == "modes":
            c.rounded((W // 2 - 220, 50, W // 2 + 220, 50 + 44 * len(self.app.modes) + 40), 14, fill=(0, 0, 0, 200))
            c.text((W // 2, 64), "Modus wählen   OK = wechseln   ◀ = abbrechen", 15, (200, 200, 200), anchor="mt")
            for i, m in enumerate(self.app.modes):
                y = 94 + 44 * i
                sel = i == self.idx
                if sel:
                    c.rounded((W // 2 - 200, y - 4, W // 2 + 200, y + 36), 8, fill=(*m.color, 90), outline=m.color, width=2)
                act = " (aktiv)" if m is self.app.mode else ""
                c.text((W // 2 - 180, y + 16), f"{i + 1}  {m.name}{act}", 22, (255, 255, 255), bold=sel, anchor="lm")
        else:
            n = len(self.items)
            top = max(0, min(self.idx - 4, n - 8))
            vis = self.items[top:top + 8]
            c.rounded((40, 44, W - 40, 44 + 40 * len(vis) + 70), 14, fill=(0, 0, 0, 215))
            c.text((60, 56), f"{self.app.mode.name}: Einstellungen", 18, (220, 220, 220), bold=True)
            for j, it in enumerate(vis):
                i = top + j
                y = 86 + 40 * j
                sel = i == self.idx
                if sel:
                    col = (80, 140, 255)
                    c.rounded((52, y - 2, W - 52, y + 34), 8, fill=(*col, 70), outline=col, width=2)
                if it["type"] == "option":
                    o = it["opt"]
                    c.text((66, y + 16), o.label, 19, (255, 255, 255), anchor="lm")
                    val = o.display(self.app.mode.opt(o.key))
                    c.text((W - 66, y + 16), ("◀ " + val + " ▶") if sel else val, 19,
                           (255, 220, 120), bold=sel, anchor="rm")
                else:
                    c.text((66, y + 16), it["label"], 19, (200, 230, 255), anchor="lm")
            yb = 86 + 40 * len(vis) + 8
            c.text((W // 2, yb + 10), "▲▼ wählen    Rad = Wert    OK/▶ weiter    Rad drücken = Standard    ◀ zurück",
                   13, (170, 180, 190), anchor="mm")
        return c.result()
