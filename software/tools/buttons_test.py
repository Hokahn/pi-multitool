"""Tastenkreuz, Drehencoder und Aufnahme-Taster testen. Ctrl+C beendet."""

from _common import time
from multitool.core.io import Inputs

NAMES = {
    "up": "Kreuz hoch", "down": "Kreuz runter", "left": "Kreuz links", "left_long": "Kreuz links 3 s",
    "right": "Kreuz rechts", "ok": "Kreuz OK", "ok_long": "Kreuz OK 3 s",
    "enc_press": "Rad gedrückt", "enc_long": "Rad 3 s gedrückt",
    "rec": "Aufnahme", "rec_long": "Aufnahme 3 s",
}


def main():
    i = Inputs(sim=False)
    if not i.ok:
        print("GPIO nicht verfügbar.")
        return
    for ev, name in NAMES.items():
        i.on(ev, lambda n=name: print(f"  {n}"))
    i.on("enc", lambda d: print(f"  Rad {'rechts' if d > 0 else 'links'}"))
    print("Alle Tasten drücken und das Rad drehen. Ctrl+C beendet.")
    print("Zeigt eine Taste den falschen Namen: Drähte am Tastenkreuz tauschen (siehe Doku).")
    print("Zählt das Rad verkehrt herum: Drähte CLK und DT tauschen.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
