"""LED-Ring testen: jede LED einzeln (mit Nummer), dann Rot, Grün, Blau, Weiss."""

from _common import leds, time


def main():
    ring, c = leds()
    ring.start()
    print("Jede LED leuchtet 1 s. Notiere, ob die Nummer zur Position passt.")
    for i in range(12):
        fr = [(0, 0, 0)] * 12
        fr[i] = (255, 255, 255)
        ring.set_frame(fr)
        print(f"  LED {i}")
        time.sleep(1)
    for name, col in (("Rot", (255, 0, 0)), ("Grün", (0, 255, 0)), ("Blau", (0, 0, 255)), ("Weiss", (255, 255, 255))):
        print(f"  alle {name}")
        ring.fill(col)
        time.sleep(1.5)
    print("Wenn Rot als Blau erscheint: in der Webseite 'Farbreihenfolge' auf RGB stellen.")
    ring.stop()


if __name__ == "__main__":
    main()
