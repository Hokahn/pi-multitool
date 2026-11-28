"""Modus 1: Akustische Kamera. Farbige Karte der Schallquellen über dem Livebild."""

from __future__ import annotations

import os
import threading
import time

import cv2
import numpy as np

from ..config import Option as O
from ..core.audio import CENTER, FS
from ..core.storage import ffmpeg
from ..dsp import doa
from ..dsp import geometry as geo
from ..ui import draw
from .base import Mode

BANDS = {"auto": (1500, 7000), "1-2k": (1000, 2000), "2-4k": (2000, 4000), "4-7k": (4000, 7500)}


class AcousticCamMode(Mode):
    key = "acoustic_cam"
    name = "Akustische Kamera"
    color = (0, 80, 255)
    QUICK = ["band", "dyn"]          # Encoder im Livebild
    orientation = "front"
    needs_camera = True
    OPTIONS = [
        O("band", "Frequenzband", "choice", "auto", choices=[
            ("auto", "Auto 1,5-7 kHz"), ("1-2k", "1-2 kHz"), ("2-4k", "2-4 kHz"), ("4-7k", "4-7,5 kHz")]),
        O("method", "Verfahren", "choice", "das", choices=[
            ("das", "Delay-and-Sum (schnell)"), ("functional", "Functional (schärfer)")]),
        O("dyn", "Dynamik", "choice", 10, choices=[(6, "6 dB"), (10, "10 dB"), (15, "15 dB")]),
        O("alpha", "Deckkraft der Farbe", "int", 50, min=20, max=80, step=10, unit="%"),
        O("marker", "Marker mit Pegel", "bool", True),
        O("min_level", "Karte erst ab Pegel", "int", 40, min=20, max=90, step=5, unit="dB",
          help="Unter diesem Pegel (im Band) wird keine Farbe gezeigt, damit Rauschen nicht flackert."),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.mapper = None
        self.power = None
        self.level_db = -120.0
        self.peak = None
        self.last_view = None
        self._rebuild = True
        self._video = None
        self._lock = threading.Lock()
        app.cfg.on_change(self._cfg_changed)

    def _cfg_changed(self, group, key, value):
        if group in ("array",) or group == f"modes.{self.key}":
            self._rebuild = True

    def on_option_changed(self, key, value):
        self._rebuild = True

    def _build(self):
        a = self.cfg.group("array")
        fmin, fmax = BANDS.get(self.opt("band"), BANDS["auto"])
        self.mapper = doa.CsmMapper(self.positions, 32, 18, a["hfov"], a["vfov"], a["rotation_deg"], a["mirror"],
                                    fs=FS, nfft=512, fmin=fmin, fmax=fmax, bin_step=2 if fmax - fmin > 3000 else 1)
        self.band = (fmin, fmax)
        self._rebuild = False

    def start(self):
        super().start()
        self.app.leds.off()
        self.spawn(self._loop, "akustikkamera")

    def stop(self):
        self._stop_video()
        super().stop()

    def _loop(self):
        while self.running:
            t0 = time.time()
            if self._rebuild or self.mapper is None:
                self._build()
            x = self.audio.ring.latest(int(0.2 * FS))
            if x.shape[1] >= 1024:
                self.mapper.update(x, smooth=0.5)
                p = self.mapper.map(self.opt("method"))
                self.level_db = self.audio.dbfs_to_spl(doa.band_level_dbfs(x[CENTER], FS, *self.band))
                with self._lock:
                    self.power = p
            self.sleep(max(0.02, 0.12 - (time.time() - t0)))

    # ---- Anzeige ----
    def compose(self, frame):
        """Kamerabild + Karte (in Kameraauflösung)."""
        if frame is None:
            frame = np.zeros((360, 640, 3), np.uint8)
        with self._lock:
            p = None if self.power is None else self.power.copy()
        img = frame
        self.peak = None
        if p is not None and self.level_db >= float(self.opt("min_level")):
            img, self.peak = draw.heat_overlay(frame, p, float(self.opt("dyn")), float(self.opt("alpha")) / 100)
        return img

    def render(self, W, H):
        img = self.compose(self.app.camera.frame())
        h, w = img.shape[:2]
        view = draw.fit(img, W, H)
        x0, y0, nw, nh = draw.fit_rect(w, h, W, H)
        c = draw.Canvas(view)
        if self.peak is not None and self.opt("marker"):
            px, py = int(x0 + self.peak[0] * nw), int(y0 + self.peak[1] * nh)
            c.rect((px - 14, py - 14, px + 14, py + 14), outline=(255, 255, 255), width=2)
            right = px < W - 130
            c.text((px + 18 if right else px - 18, py - 10), f"ca. {self.level_db:.0f} dB", 16, (255, 255, 255),
                   bold=True, anchor="la" if right else "ra", bg=(0, 0, 0, 140))
        if self._video:
            c.text((W - 12, 44), f"● REC {int(time.time() - self._video['t0'])} s", 18, (255, 60, 60),
                   bold=True, anchor="ra")
        self.last_view = c.result()
        return self.last_view

    def hud(self):
        fmin, fmax = getattr(self, "band", BANDS["auto"])
        return [f"Band {fmin / 1000:.1f}-{fmax / 1000:.1f} kHz   Pegel ca. {self.level_db:.0f} dB",
                "Aufnahme kurz: Foto   lang: Video"]

    def is_recording(self):
        return self._video is not None

    # ---- Aufnahme ----
    def on_record(self):
        st = self.app.storage
        if not st.can_record():
            self.say("Speicher voll")
            return
        p_ov = st.new_path(self.key, "karte", ".jpg")
        img = self.compose(self.app.camera.frame())
        big = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_CUBIC)
        cv2.imwrite(p_ov, cv2.cvtColor(big, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
        self.app.camera.still(p_ov.replace("_karte.jpg", "_foto.jpg"))
        self.save_ring_wav(p_ov.replace("_karte.jpg", "_7kanal.wav"), 5.0, mode="raw")
        self.app.events.add(self.key, "foto", f"{self.level_db:.0f} dB", level=self.level_db, file=st.rel(p_ov))
        self.app.leds.flash(0.15)
        self.say("Foto gespeichert")

    def on_record_long(self):
        if self._video:
            self._stop_video()
        else:
            self._start_video()

    def _start_video(self):
        st = self.app.storage
        if not st.can_record():
            self.say("Speicher voll")
            return
        path = st.new_path(self.key, "video", ".mp4")
        tmp_v = path + ".part.avi"
        tmp_a = path + ".part.wav"
        vw = cv2.VideoWriter(tmp_v, cv2.VideoWriter_fourcc(*"MJPG"), 8, (800, 480))
        rec = self.start_audio_rec(tmp_a, mode="raw")
        self._video = {"path": path, "tmp_v": tmp_v, "tmp_a": tmp_a, "vw": vw, "rec": rec, "t0": time.time()}

        def writer():
            v = self._video
            nxt = time.time()
            while self.running and self._video is v:
                if self.last_view is not None:
                    fr = cv2.resize(self.last_view, (800, 480))
                    v["vw"].write(cv2.cvtColor(fr, cv2.COLOR_RGB2BGR))
                nxt += 1 / 8
                time.sleep(max(0, nxt - time.time()))
        self.spawn(writer, "akustik-video")
        self.say("Video läuft")

    def _stop_video(self):
        v, self._video = self._video, None
        if not v:
            return
        time.sleep(0.2)
        v["vw"].release()
        self.stop_audio_rec(v["rec"])

        def finish():
            ok = ffmpeg(["-i", v["tmp_v"], "-i", v["tmp_a"], "-map", "0:v", "-map", "1:a",
                         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-ac", "1", "-shortest", v["path"]])
            # 7-Kanal-Rohdaten zusätzlich behalten
            os.replace(v["tmp_a"], v["path"].replace(".mp4", "_7kanal.wav"))
            if ok:
                os.remove(v["tmp_v"])
            self.app.events.add(self.key, "video", f"{int(time.time() - v['t0'])} s", file=self.app.storage.rel(v["path"]))
        threading.Thread(target=finish, daemon=True).start()
        self.say("Video gespeichert")

    def status(self):
        return {"level_db": round(self.level_db, 1), "peak": self.peak, "recording": self.is_recording()}


# für Diagnose wiederverwendbar
def bearing_from_peak(mode, peak):
    """Bildposition (0..1) -> Richtungsvektor im Array."""
    a = mode.cfg.group("array")
    tx = np.tan(np.deg2rad(a["hfov"]) / 2)
    ty = np.tan(np.deg2rad(a["vfov"]) / 2)
    x = (peak[0] * 2 - 1) * tx
    y = -(peak[1] * 2 - 1) * ty
    return geo.image_to_array(np.array([x]), np.array([y]), a["rotation_deg"], a["mirror"])[0]
