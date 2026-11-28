"""Akku-Messung (INA219 auf dem UPS HAT) anzeigen. Ctrl+C beendet."""

from _common import cfg, time
from multitool.core.battery import Battery, Ina219


def main():
    c = cfg()
    try:
        from smbus2 import SMBus
        bus = SMBus(1)
        found = []
        for a in (0x40, 0x41, 0x42, 0x43):
            try:
                bus.read_byte(a)
                found.append(a)
            except OSError:
                pass
        print("INA219 gefunden an:", ", ".join(hex(a) for a in found) or "keiner")
        if found and hex(found[0]) != c.get("battery", "i2c_address"):
            c.set("battery", "i2c_address", hex(found[0]))
            print(f"  -> Einstellung auf {hex(found[0])} gesetzt")
        ina = Ina219(addr=int(c.get("battery", "i2c_address"), 16), ups_type=c.get("battery", "ups_type"))
    except Exception as e:
        print(f"FEHLER: INA219 nicht gefunden ({e}).")
        print("  - I2C aktiviert?   sudo i2cdetect -y 1   muss 42 zeigen.")
        print("  - Sitzt das UPS HAT richtig unter dem Pi (Pogo-Pins)?")
        return
    print("Spannung   Strom    Schätzung   (negativer Strom = Akku entlädt)")
    try:
        while True:
            v, i = ina.voltage(), ina.current_a()
            p = Battery.ocv_percent(v, i)
            print(f"{v:6.2f} V  {i:+6.3f} A   ca. {p:3.0f} %", end="\r", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
