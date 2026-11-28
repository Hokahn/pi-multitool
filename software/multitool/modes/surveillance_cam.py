"""Modus 4: Überwachungskamera mit Bewegungserkennung. Still oder mit Alarm (Sirene + rotes Blinken)."""

from __future__ import annotations

import os
import threading
import time

import cv2

from ..config import Option as O
from ..core import leds as L
from ..core.audio import CENTER, FS
from ..core.camera import MotionDetector
from ..core.storage import h264_to_mp4, mux_audio_video
from ..dsp.detectors import NoiseFloor, rms_db
from ..ui import draw
from .base import Mode


class SurveillanceCamMode(Mode):
    key = "surveillance_cam"
    name = "Überwachungskamera"
    color = (255, 200, 0)
    QUICK = ["sensitivity"]          # Encoder im Livebild
    orientation = "front"
    needs_camera = True
    OPTIONS = [
        O("alarm", "Untermodus", "choice", False, choices=[(False, "still"), (True, "Alarm (Sirene + Blinken)")]),
        O("arm_delay", "Scharfschaltverzögerung", "choice", 30, choices=[(0, "0 s"), (15, "15 s"), (30, "30 s"), (60, "60 s")]),
        O("sensitivity", "Empfindlichkeit", "int", 5, min=1, max=10, step=1),
        O("min_area", "Mindestfläche", "int", 3, min=1, max=20, step=1, unit="%"),
        O("pre", "Vorlauf", "int", 5, min=0, max=10, step=1, unit="s"),
        O("post", "Nachlauf", "int", 10, min=5, max=60, step=5, unit="s"),
        O("trigger", "Auslöser", "choice", "motion", choices=[("motion", "Bewegung"), ("motion_sound", "Bewegung und Geräusch")]),
        O("alarm_s", "Alarmdauer", "choice", 30, choices=[(10, "10 s"), (30, "30 s"), (60, "60 s")]),
        O("push", "Push ans Handy", "bool", True),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.motion = MotionDetector()
        self.armed_at = 0.0
        self.state = "arming"
        self.event = None
        self.last_motion = 0.0
        self.noise = NoiseFloor(60, 4)
        self.last_loud = 0.0

    def masks(self):
        return self.cfg.mode_get(self.key, "masks", []) or []

    def set_masks(self, masks):
        self.cfg.mode_set(self.key, "masks", masks)
        self.motion.reset()

    def start(self):
        super().start()
        self.app.camera.start((1920, 1080), 30)
        self.app.camera.start_circular(max(1, int(self.opt("pre"))) + 1)
        self._arm()
        self.spawn(self._loop, "bewegung")
        self.spawn(self._sound_loop, "geraeusch")

    def stop(self):
        self._end_event()
        self.app.camera.stop_recording()
        self.app.siren.stop()
        self.app.leds.stop_alarm()
        super().stop()
        self.app.leds.off()

    def _arm(self):
        d = int(self.opt("arm_delay"))
        self.armed_at = time.time() + d
        self.state = "arming"
        self.motion.reset()
        if d:
            self.app.leds.set_pattern(L.countdown(L.YELLOW, self.armed_at, d))
            self.say(f"Scharf in {d} s")

    def on_option_changed(self, key, value):
        if key == "pre" and not self.event:
            self.app.camera.stop_recording()
            self.app.camera.start_circular(max(1, int(value)) + 1)

    def on_record(self):
        """Taste: neu scharfschalten (Verzögerung läuft neu) bzw. Alarm quittieren."""
        self.app.siren.stop()
        self.app.leds.stop_alarm()
        self._end_event()
        self._arm()

    def _sound_loop(self):
        while self.running:
            lv = rms_db(self.audio.ring.latest(FS // 4)[CENTER])
            floor = self.noise.update(lv)
            if lv - floor > 10:
                self.last_loud = time.time()
            self.sleep(0.25)

    def _loop(self):
        while self.running:
            fr = self.app.camera.frame()
            if fr is None:
                self.sleep(0.2)
                continue
            moving = self.motion(fr, int(self.opt("sensitivity")), float(self.opt("min_area")), self.masks())
            now = time.time()
            if self.state == "arming":
                if now >= self.armed_at:
                    self.state = "armed"
                    self.app.leds.off()
                    self.say("Scharf")
            elif moving:
                need_sound = self.opt("trigger") == "motion_sound"
                if not need_sound or now - self.last_loud < 2.0:
                    self.last_motion = now
                    if self.event is None:
                        self._start_event()
            if self.event is not None and now - self.last_motion > int(self.opt("post")):
                self._end_event()
            self.sleep(0.1)

    def _start_event(self):
        st = self.app.storage
        if not st.can_record():
            self.say("Speicher voll")
            return
        path = st.new_path(self.key, "bewegung", ".mp4")
        h264 = path.replace(".mp4", ".part.h264")
        wav = path.replace(".mp4", ".part.wav")
        pre = int(self.opt("pre"))
        self.app.camera.circular_save_start(h264)
        rec = self.start_audio_rec(wav, mode="center", preroll_s=pre)
        eid = self.app.events.add(self.key, "bewegung", f"{self.motion.fraction:.1f} % Fläche",
                                  confidence=self.motion.fraction)
        self.event = {"path": path, "h264": h264, "wav": wav, "rec": rec, "t0": time.time(), "id": eid}
        # Standbild für die Webseite
        fr = self.app.camera.frame()
        if fr is not None:
            cv2.imwrite(path.replace(".mp4", ".jpg"), cv2.cvtColor(fr, cv2.COLOR_RGB2BGR))
        if self.opt("alarm"):
            s = int(self.opt("alarm_s"))
            self.app.siren.sound(s)
            self.app.leds.alarm(s, L.RED)
        if self.opt("push"):
            self.push("Bewegung erkannt", time.strftime("%H:%M:%S"))
        self.state = "recording"

    def _end_event(self):
        ev, self.event = self.event, None
        if ev is None:
            return
        self.app.camera.circular_save_stop()
        self.stop_audio_rec(ev["rec"])
        if self.state == "recording":
            self.state = "armed"

        def finish():
            tmp_mp4 = ev["path"].replace(".mp4", ".part.mp4")
            if self.app.sim:
                src = ev["h264"].replace(".h264", ".mp4")
                ok = os.path.exists(src)
                if ok:
                    os.replace(src, tmp_mp4)
            else:
                ok = h264_to_mp4(ev["h264"], tmp_mp4, fps=self.app.camera.fps)
            if ok and mux_audio_video(tmp_mp4, ev["wav"], ev["path"]):
                pass
            elif ok:
                os.replace(tmp_mp4, ev["path"])
            self.app.events.update_file(ev["id"], self.app.storage.rel(ev["path"]))
        threading.Thread(target=finish, daemon=True).start()

    def render(self, W, H):
        fr = self.app.camera.frame()
        view = draw.fit(fr, W, H)
        x0, y0, nw, nh = draw.fit_rect(640, 360, W, H)
        c = draw.Canvas(view)
        for (a, b, cc, d) in self.masks():
            c.rect((x0 + a * nw, y0 + b * nh, x0 + cc * nw, y0 + d * nh), fill=(0, 0, 0, 120), outline=(120, 120, 120))
        for (a, b, cc, d) in self.motion.boxes:
            c.rect((x0 + a * nw, y0 + b * nh, x0 + cc * nw, y0 + d * nh), outline=(255, 200, 0), width=2)
        now = time.time()
        if self.state == "arming":
            c.text((W // 2, H // 2), f"Scharf in {max(0, int(self.armed_at - now)) + 1} s", 34, (255, 220, 0), True,
                   anchor="mm", bg=(0, 0, 0, 150))
        elif self.event:
            c.text((W - 12, 44), f"● AUFNAHME {int(now - self.event['t0'])} s", 20, (255, 60, 60), True, anchor="ra")
        else:
            c.text((W - 12, 44), "SCHARF", 20, (255, 220, 0), True, anchor="ra")
        return c.result()

    def hud(self):
        sub = "Alarm" if self.opt("alarm") else "still"
        return [f"{sub}, Bewegung {self.motion.fraction:.1f} % (ab {self.opt('min_area')} %)",
                "Aufnahme-Taste: neu scharfschalten / Alarm quittieren"]

    def is_recording(self):
        return self.event is not None

    def status(self):
        return {"state": self.state, "motion": round(self.motion.fraction, 1), "masks": self.masks()}
