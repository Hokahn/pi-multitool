"""Sirene testen: 0,3 s, dann 1,5 s. Gehörschutz oder Abstand!"""

from _common import cfg, time
from multitool.core.io import Siren


def main():
    s = Siren(cfg(), sim=False)
    input("Achtung laut. Enter = 0,3 s Sirene ")
    s.sound(0.3)
    time.sleep(1)
    input("Enter = 1,5 s Sirene ")
    s.sound(1.5)
    time.sleep(2)
    s.stop()
    print("Bleibt sie stumm: MOSFET-Modul, GPIO18, Masse und 12-V-Step-up prüfen.")
    print("Heult sie dauernd: Gate-Pull-down (10 kOhm) fehlt oder MOSFET falsch herum.")


if __name__ == "__main__":
    main()
