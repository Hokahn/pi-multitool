"""Modus 1b: Maschinendiagnose. Akustische Kamera + Spektrum der Stelle, auf die der Hörstrahl zeigt.
Mit Kopfhörer an der 3,5-mm-Buchse hört man genau diese Richtung."""

from __future__ import annotations

import logging
import queue

import cv2
import numpy as np

from ..config import Option as O
from ..core.audio import FS
from ..dsp.beamform import StreamBeamformer, beamform_offline
from ..ui import draw
from .acoustic_cam import AcousticCamMode, bearing_from_peak

log = logging.getLogger("diagnosis")


class DiagnosisMode(AcousticCamMode):
    key = "diagnosis"
    name = "Maschinendiagnose"
    color = (0, 220, 220)
    QUICK = ["rpm", "band"]          # Encoder im Livebild
    OPTIONS = AcousticCamMode.OPTIONS + [
        O("beam", "Hörstrahl", "choice", "peak", choices=[("peak", "folgt Maximum"), ("touch", "Touch-Punkt")]),
        O("phones", "Kopfhörer", "choice", "beam", choices=[("off", "aus"), ("beam", "Strahl"), ("center", "Mittel-Mic")]),
        O("rpm", "Drehzahl für Ordnungslinien", "int", 0, min=0, max=12000, step=100, unit="1/min",
          help="0 = keine Linien. Markiert 1., 2. und 3. Ordnung im Spektrum."),
        O("scale", "Spektrum", "choice", "log", choices=[("log", "logarithmisch"), ("lin", "linear")]),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.target = (0.5, 0.5)
        self.spec = (None, None)
        self.out_q = queue.Queue(maxsize=40)
        self._stream = None
        self._bf = None

    def start(self):
        super().start()
        self._bf = StreamBeamformer(self.positions)
        self.audio.add_sink(self._sink)
        self.spawn(self._spec_loop, "spektrum")
        self._open_phones()

    def stop(self):
        self.audio.remove_sink(self._sink)
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        super().stop()

    def on_touch(self, x, y):
        self.target = (float(x), float(y))
        if self.opt("beam") != "touch":
            self.set_opt("beam", "touch")
        self.say("Hörstrahl gesetzt")

    def _direction(self):
        tgt = self.target if self.opt("beam") == "touch" or self.peak is None else self.peak
        return bearing_from_peak(self, tgt), tgt

    def _sink(self, block):
        d, _ = self._direction()
        self._bf.set_direction(d)
        self._bf.mode = "center" if self.opt("phones") == "center" else "beam"
        y = self._bf.process(block)
        if self._stream is not None:
            try:
                self.out_q.put_nowait(y)
            except queue.Full:
                pass

    def _open_phones(self):
        if self.app.sim or self.opt("phones") == "off":
            return
        try:
            import sounddevice as sd
            vol = float(self.cfg.get("audio", "headphone_volume") or 60) / 100 * 6

            def cb(outdata, frames, t, status):
                buf = np.zeros(frames, np.float32)
                i = 0
                while i < frames:
                    try:
                        blk = self.out_q.get_nowait()
                    except queue.Empty:
                        break
                    k = min(frames - i, len(blk))
                    buf[i:i + k] = blk[:k]
                    i += k
                outdata[:, 0] = np.clip(buf * vol, -1, 1)
            dev = None
            for i, d in enumerate(sd.query_devices()):     # 3,5-mm-Buchse des Pi bevorzugen
                if d["max_output_channels"] > 0 and "headphone" in d["name"].lower():
                    dev = i
                    break
            self._stream = sd.OutputStream(device=dev, samplerate=FS, channels=1, dtype="float32",
                                           blocksize=256, callback=cb)
            self._stream.start()
        except Exception as e:
            log.warning("Kopfhörer-Ausgabe nicht möglich: %s", e)
            self._stream = None

    def on_option_changed(self, key, value):
        super().on_option_changed(key, value)
        if key == "phones":
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            self._open_phones()

    def _spec_loop(self):
        while self.running:
            x = self.audio.ring.latest(int(0.5 * FS))
            if x.shape[1] >= 4096:
                d, _ = self._direction()
                y = beamform_offline(x, self.positions, d)
                n = 4096
                Y = np.abs(np.fft.rfft(y[-n:] * np.hanning(n))) / (n / 4)
                L = 20 * np.log10(Y + 1e-9)
                f = np.fft.rfftfreq(n, 1 / FS)
                old = self.spec[1]
                if old is not None and len(old) == len(L):
                    L = 0.6 * old + 0.4 * L
                self.spec = (f, L)
            self.sleep(0.2)

    def render(self, W, H):
        view = super().render(W, H)
        f, L = self.spec
        h = 130
        markers = []
        rpm = int(self.opt("rpm") or 0)
        if rpm > 0:
            for k, col in ((1, (255, 220, 0)), (2, (255, 150, 0)), (3, (255, 80, 0))):
                markers.append((rpm / 60 * k, col))
        peak = ""
        if f is not None:
            m = f > 40
            i = int(np.argmax(L[m]))
            peak = f"Spitze {f[m][i]:.0f} Hz"
        if self.opt("scale") == "log" and f is not None:
            # logarithmische Frequenzachse 50 Hz .. 8 kHz
            fl = np.geomspace(50, 8000, 400)
            Ll = np.interp(fl, f, L)
            xs = (np.log(fl / 50) / np.log(8000 / 50)) * 8000
            draw.spectrum_panel(view, xs, Ll, 0, H - h - 50, W, h, 8000,
                                [(np.log(fm / 50) / np.log(160) * 8000, c) for fm, c in markers if fm > 50], peak)
        else:
            draw.spectrum_panel(view, f, L, 0, H - h - 50, W, h, 8000, markers, peak)
        _, tgt = self._direction()
        if tgt is not None:
            img_h, img_w = 360, 640
            x0, y0, nw, nh = draw.fit_rect(img_w, img_h, W, H)
            px, py = int(x0 + tgt[0] * nw), int(y0 + tgt[1] * nh)
            cv2.circle(view, (px, py), 18, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.line(view, (px - 26, py), (px - 8, py), (0, 255, 255), 2)
            cv2.line(view, (px + 8, py), (px + 26, py), (0, 255, 255), 2)
        self.last_view = view
        return view

    def hud(self):
        return [f"Pegel ca. {self.level_db:.0f} dB   Strahl: {'Touch' if self.opt('beam') == 'touch' else 'Maximum'}",
                "Bild antippen = Hörstrahl setzen"]

    def on_record(self):
        st = self.app.storage
        p = st.new_path(self.key, "spektrum", ".csv")
        f, L = self.spec
        if f is not None:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("frequenz_hz;pegel_dbfs\n")
                for a, b in zip(f, L):
                    fh.write(f"{a:.1f};{b:.2f}\n")
        if self.last_view is not None:
            cv2.imwrite(p.replace(".csv", ".jpg"), cv2.cvtColor(self.last_view, cv2.COLOR_RGB2BGR))
        d, _ = self._direction()
        self.save_ring_wav(p.replace(".csv", "_strahl.wav"), 10.0, mode="beam", direction=d)
        self.app.events.add(self.key, "messung", f"{self.level_db:.0f} dB", level=self.level_db, file=st.rel(p))
        self.app.leds.flash(0.15)
        self.say("Messung gespeichert")
