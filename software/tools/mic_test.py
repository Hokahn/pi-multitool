"""Etappe 1: Kommen alle 7 Mikrofone an?

Zeigt 30 Sekunden lang den Pegel jedes USB-Kanals. Tippe nacheinander leicht mit dem Finger
neben jedes Mikrofon: der passende Balken muss ausschlagen. Kanal 7 bleibt immer leer.
"""

from _common import Mic, bar, db, time


def main():
    m = Mic()
    print(f"Gerät: {m.name}\n")
    print("Kanal:     0      1      2      3      4      5      6      7")
    t_end = time.time() + 30
    peak = [-120.0] * 8
    try:
        while time.time() < t_end:
            x = m.read(0.2)
            lv = [db(x[:, c]) for c in range(8)]
            peak = [max(p, v) for p, v in zip(peak, lv)]
            print("dBFS:  " + " ".join(f"{v:6.1f}" for v in lv), end="\r", flush=True)
    except KeyboardInterrupt:
        pass
    m.close()
    print("\n\nHöchster Pegel je Kanal:")
    for c in range(8):
        print(f"  Kanal {c}: {peak[c]:6.1f} dBFS  {bar(peak[c])}")
    dead = [c for c in range(7) if peak[c] < -95]
    print()
    if dead:
        print(f"ACHTUNG: Kanal {dead} ist stumm. Lötstellen/Kabel D0..D3 und Speisung prüfen.")
    else:
        print("OK: Alle 7 Mikrofonkanäle liefern Signal. Weiter mit:  multitool-test calibrate")


if __name__ == "__main__":
    main()
