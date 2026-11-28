"""Richtung laufend anzeigen (auf dem LED-Ring und im Terminal). Ctrl+C beendet."""

from _common import Mic, cfg, db, leds, mapped
from multitool.dsp import doa, geometry as geo


def main():
    c = cfg()
    m = Mic()
    ring, _ = leds()
    ring.start()
    af = doa.AzimuthFinder(geo.positions_from_cfg(c), fmin=300, fmax=6000)
    print("Geräusch machen (sprechen, klatschen, Handy mit Ton). Ctrl+C beendet.")
    try:
        while True:
            x = mapped(m.read(0.25), c)
            lv = db(x[6])
            if lv > -55:
                az, el, q = af.find(x)
                led = geo.az_to_led(az, float(c.get("leds", "led0_deg")), int(c.get("leds", "led_direction")))
                ring.sector(led, (0, 255, 0), width=1)
                print(f"Richtung {geo.bearing(az, c):5.0f}°  LED {led:2d}  Pegel {lv:5.1f} dBFS  Güte {q:.2f}   ", end="\r")
            else:
                ring.off()
    except KeyboardInterrupt:
        print()
    ring.stop()
    m.close()


if __name__ == "__main__":
    main()
