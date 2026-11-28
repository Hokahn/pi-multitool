"""Trainingsaufnahmen für ein eigenes Drohnenmodell: 7-Kanal-WAV mit Bezeichnung.

Beispiel:  multitool-test record_clip drohne 10      (10 s, Ordner training/drohne)
           multitool-test record_clip hintergrund 60
"""

import os
import sys
import wave

from _common import DATA, Mic, cfg, mapped, np, time


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "unbenannt"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 10
    c = cfg()
    d = os.path.join(DATA, "training", label)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, time.strftime("%Y%m%d_%H%M%S.wav"))
    m = Mic()
    print(f"Nehme {secs:.0f} s auf ...")
    x = mapped(m.read(secs), c)
    m.close()
    with wave.open(p, "wb") as w:
        w.setnchannels(7)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.clip(x.T * 8 * 32767, -32768, 32767).astype("<i2").tobytes())
    print(f"Gespeichert: {p}")


if __name__ == "__main__":
    main()
