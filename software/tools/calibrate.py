"""Etappe 2: Kalibrierung. Dauert etwa 5 Minuten. Drei Schritte:

  1. Welcher USB-Kanal gehört zu welchem Mikrofon?   (Finger neben das Mikrofon tippen)
  2. Wo sitzt LED 0 und in welche Richtung zählen die LEDs?   (Enter drücken)
  3. Kontrolle: Klatschen, die LED Richtung Schall muss leuchten.

Das Ergebnis wird in die Einstellungen geschrieben.
"""

import threading

from _common import Mic, cfg, db, leds, mapped, np, time, wait_enter

NAMES = ["Mikrofon 0", "Mikrofon 1", "Mikrofon 2", "Mikrofon 3", "Mikrofon 4", "Mikrofon 5", "Mittel-Mikrofon"]


def find_channel(m, used, label):
    """Wartet auf einen deutlichen, kurzen Pegelanstieg und gibt den lautesten Kanal zurück."""
    print(f"\n>>> Tippe 3 x leicht mit dem Fingernagel direkt neben {label}.")
    base = np.array([db(m.read(0.3)[:, c]) for c in range(8)])
    votes = []
    t_end = time.time() + 20
    while time.time() < t_end and len(votes) < 3:
        x = m.read(0.15)
        lv = np.array([db(x[:, c]) for c in range(8)])
        rise = lv - base
        cand = [c for c in range(7) if c not in used]
        c = max(cand, key=lambda k: rise[k])
        if rise[c] > 15:
            # zweitstärkster Kanal muss deutlich schwächer sein
            others = sorted((rise[k] for k in cand if k != c), reverse=True)
            if not others or rise[c] - others[0] > 3:
                votes.append(c)
                print(f"    erkannt: Kanal {c} (+{rise[c]:.0f} dB)")
                time.sleep(0.4)
        else:
            base = 0.9 * base + 0.1 * lv
    if not votes:
        return None
    return max(set(votes), key=votes.count)


def step_channels(m, c):
    print("\nSCHRITT 1: Kanalzuordnung")
    print("Die Zahlen 0 bis 5 stehen auf der Platine neben den Ecken. Das Mittel-Mikrofon sitzt in der Mitte.")
    used, mapping = [], []
    for name in NAMES:
        ch = None
        while ch is None:
            ch = find_channel(m, used, name)
            if ch is None:
                print("    nichts erkannt, nochmal (etwas kräftiger tippen)")
        used.append(ch)
        mapping.append(ch)
    text = ",".join(str(v) for v in mapping)
    c.set("array", "channel_map", text)
    print(f"\nGespeichert: Kanalzuordnung = {text}")


def step_direction(c):
    print("\nSCHRITT 2a: Zählrichtung der Mikrofone")
    ans = input("Laufen die Zahlen 0, 1, 2 ... auf der LED-Seite im UHRZEIGERSINN? [J/n] ").strip().lower()
    c.set("array", "mic_direction", 1 if ans.startswith("n") else -1)


def step_leds(c):
    print("\nSCHRITT 2b: LED-Ring")
    ring, _ = leds()
    ring.start()
    idx = {"i": 0}
    stop = threading.Event()

    def cycle():
        while not stop.is_set():
            fr = [(0, 0, 0)] * 12
            fr[idx["i"]] = (255, 255, 255)
            ring.set_frame(fr)
            time.sleep(0.8)
            idx["i"] = (idx["i"] + 1) % 12
    threading.Thread(target=cycle, daemon=True).start()
    res = []
    for name in ("Mikrofon 0", "Mikrofon 1"):
        wait_enter(f"Drücke Enter, wenn die LED leuchtet, die {name} am nächsten ist ... ")
        res.append(idx["i"])
        print(f"    LED {idx['i']}")
    stop.set()
    i0, i1 = res
    d = (i1 - i0) % 12
    mic_dir = int(c.get("array", "mic_direction"))
    led_dir = mic_dir if d <= 6 else -mic_dir
    led0 = (-i0 * 30 * led_dir) % 360
    c.set("leds", "led_direction", led_dir)
    c.set("leds", "led0_deg", int(led0))
    print(f"Gespeichert: LED 0 bei {led0:.0f}°, Zählrichtung {'gleich' if led_dir == mic_dir else 'umgekehrt'} wie Mikrofone")
    ring.stop()


def step_check(m, c):
    print("\nSCHRITT 3: Kontrolle. 30 s lang klatschen, von verschiedenen Seiten, ca. 1-2 m entfernt.")
    print("Es muss die LED aufleuchten, die zu dir zeigt.")
    from multitool.dsp import doa, geometry as geo
    ring, _ = leds()
    ring.start()
    af = doa.AzimuthFinder(geo.positions_from_cfg(c), fmin=300, fmax=6000)
    t_end = time.time() + 30
    while time.time() < t_end:
        x = mapped(m.read(0.25), c)
        if db(x[6]) > -50:
            az, el, q = af.find(x)
            led = geo.az_to_led(az, float(c.get("leds", "led0_deg")), int(c.get("leds", "led_direction")))
            ring.sector(led, (0, 255, 0), width=1)
            print(f"    Richtung {geo.bearing(az, c):5.0f}°   LED {led:2d}   Güte {q:.2f}")
    ring.stop()
    ok = input("Hat die LED richtig gezeigt? [J/n] ").strip().lower()
    if ok.startswith("n"):
        print("Tipp: Zeigt sie spiegelverkehrt, Schritt 2a mit der anderen Antwort wiederholen.")
        print("Zeigt sie immer um den gleichen Winkel daneben, in der Webseite 'Winkel von LED 0' anpassen.")


def main():
    c = cfg()
    m = Mic()
    print(f"Gerät: {m.name}")
    try:
        step_channels(m, c)
        step_direction(c)
        step_leds(c)
        step_check(m, c)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
    m.close()
    print("\nFertig. Das Hauptprogramm wird wieder gestartet.")


if __name__ == "__main__":
    main()
