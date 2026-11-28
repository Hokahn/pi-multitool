"""Camera Module 3 über picamera2.

Hauptbild (main) 1920x1080 für Aufnahmen über den Hardware-Encoder,
kleines Bild (lores) 640x360 für Vorschau, Bewegungserkennung und Webseite.
"""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("camera")

LORES = (640, 360)


class CameraBus:
    def __init__(self, sim=None):
        self.sim = sim
        self.picam = None
        self.running = False
        self.size = (1920, 1080)
        self.fps = 30
        self.lock = threading.RLock()
        self.latest = None               # RGB (360, 640, 3)
        self.latest_t = 0.0
        self.recording = None            # "normal" oder "circular"
        self._encoder = None
        self._output = None
        self.error = ""
        self._grab_thread = None

    # ---- Start/Stopp ----
    def start(self, size=(1920, 1080), fps=30):
        with self.lock:
            if self.running and size == self.size and fps == self.fps:
                return True
            self.stop()
            self.size, self.fps = tuple(size), int(fps)
            if self.sim is None:
                try:
                    from picamera2 import Picamera2
                    if self.picam is None:
                        self.picam = Picamera2()
                    cfg = self.picam.create_video_configuration(
                        main={"size": self.size, "format": "YUV420"},
                        lores={"size": LORES, "format": "YUV420"},
                        controls={"FrameRate": self.fps}, buffer_count=6)
                    self.picam.configure(cfg)
                    self.picam.start()
                    try:
                        from libcamera import controls
                        self.picam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
                    except Exception:
                        pass
                    self.error = ""
                except Exception as e:
                    self.error = str(e)
                    log.error("Kamera: %s", e)
                    return False
            self.running = True
            self._grab_thread = threading.Thread(target=self._grab, name="camera", daemon=True)
            self._grab_thread.start()
            log.info("Kamera an %dx%d @ %d fps", *self.size, self.fps)
            return True

    def stop(self):
        with self.lock:
            self.stop_recording()
            self.running = False
            if self.picam is not None:
                try:
                    self.picam.stop()
                except Exception:
                    pass
        if self._grab_thread is not None:
            self._grab_thread.join(timeout=1)

    # ---- Bilder ----
    def _grab(self):
        while self.running:
            try:
                if self.sim is not None:
                    rgb = self.sim.camera_frame()
                    time.sleep(1 / 15)
                else:
                    yuv = self.picam.capture_array("lores")
                    rgb = cv2.cvtColor(yuv, cv2.COLOR_YUV420p2RGB)
                self.latest = rgb
                self.latest_t = time.time()
            except Exception as e:
                if self.running:
                    log.error("Kamerabild: %s", e)
                    time.sleep(0.5)

    def frame(self):
        """Letztes kleines Bild (RGB) oder None."""
        return self.latest

    def still(self, path: str) -> bool:
        """Foto in voller Auflösung als JPG."""
        try:
            if self.sim is not None:
                img = cv2.resize(self.sim.camera_frame(), self.size)
                return cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
            with self.lock:
                yuv = self.picam.capture_array("main")
            bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV420p2BGR)
            return cv2.imwrite(path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
        except Exception as e:
            log.error("Foto: %s", e)
            return False

    # ---- Aufnahme ----
    def start_recording(self, path_mp4: str, bitrate=10_000_000) -> bool:
        """Video ohne Ton direkt als MP4 (Ton kommt separat vom AudioBus)."""
        with self.lock:
            if self.recording:
                return False
            if self.sim is not None:
                self._sim_writer = _SimRecorder(self, path_mp4)
                self.recording = "normal"
                return True
            try:
                from picamera2.encoders import H264Encoder
                from picamera2.outputs import FfmpegOutput
                self._encoder = H264Encoder(bitrate=bitrate)
                self._output = FfmpegOutput(path_mp4)
                self.picam.start_encoder(self._encoder, self._output)
                self.recording = "normal"
                return True
            except Exception as e:
                log.error("Aufnahme-Start: %s", e)
                return False

    def start_circular(self, seconds: float, bitrate=6_000_000) -> bool:
        """Ringpuffer für Vorlauf: läuft dauernd, schreibt erst bei Auslösung in eine Datei."""
        with self.lock:
            if self.recording:
                return False
            if self.sim is not None:
                self.recording = "circular"
                self._circ_path = None
                return True
            try:
                from picamera2.encoders import H264Encoder
                from picamera2.outputs import CircularOutput
                self._encoder = H264Encoder(bitrate=bitrate, repeat=True, iperiod=self.fps // 2)
                self._output = CircularOutput(buffersize=int(seconds * self.fps))
                self.picam.start_encoder(self._encoder, self._output)
                self.recording = "circular"
                return True
            except Exception as e:
                log.error("Ringpuffer-Start: %s", e)
                return False

    def circular_save_start(self, path_h264: str):
        with self.lock:
            if self.recording != "circular":
                return
            if self.sim is not None:
                self._sim_writer = _SimRecorder(self, path_h264.replace(".h264", ".mp4"))
                return
            self._output.fileoutput = path_h264
            self._output.start()

    def circular_save_stop(self):
        with self.lock:
            if self.recording != "circular":
                return
            if self.sim is not None:
                if getattr(self, "_sim_writer", None):
                    self._sim_writer.close()
                    self._sim_writer = None
                return
            self._output.stop()

    def stop_recording(self):
        with self.lock:
            if not self.recording:
                return
            try:
                if self.sim is not None:
                    if getattr(self, "_sim_writer", None):
                        self._sim_writer.close()
                        self._sim_writer = None
                else:
                    self.picam.stop_encoder()
            except Exception as e:
                log.error("Aufnahme-Stopp: %s", e)
            self.recording = None
            self._encoder = self._output = None


class _SimRecorder:
    """Simulation: schreibt das kleine Bild als MP4 (nur zum Testen am PC)."""

    def __init__(self, cam, path):
        self.cam = cam
        self.w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 15, LORES)
        self.on = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.on:
            f = self.cam.latest
            if f is not None:
                self.w.write(cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
            time.sleep(1 / 15)

    def close(self):
        self.on = False
        time.sleep(0.1)
        self.w.release()


class MotionDetector:
    """Bewegung aus dem Vergleich mit einem gleitenden Hintergrundbild."""

    def __init__(self):
        self.bg = None
        self.hits = 0
        self.boxes = []
        self.fraction = 0.0

    def reset(self):
        self.bg = None
        self.hits = 0

    def __call__(self, rgb: np.ndarray, sensitivity: int, min_area_pct: float, masks: list) -> bool:
        small = cv2.resize(rgb, (160, 90), interpolation=cv2.INTER_AREA)
        g = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0).astype(np.float32)
        if self.bg is None:
            self.bg = g.copy()
            return False
        diff = cv2.absdiff(g, self.bg)
        cv2.accumulateWeighted(g, self.bg, 0.05)
        thr = 55 - 4.5 * max(1, min(10, sensitivity))          # 1 -> 50, 10 -> 10
        m = (diff > thr).astype(np.uint8)
        for (x0, y0, x1, y1) in masks or []:
            m[int(y0 * 90):int(y1 * 90), int(x0 * 160):int(x1 * 160)] = 0
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        self.fraction = float(m.mean() * 100)
        self.boxes = []
        n, _, stats, _ = cv2.connectedComponentsWithStats(m)
        for i in range(1, n):
            x, y, w, h, a = stats[i]
            if a >= 6:
                self.boxes.append((x / 160, y / 90, (x + w) / 160, (y + h) / 90))
        if self.fraction >= min_area_pct:
            self.hits += 1
        else:
            self.hits = 0
        return self.hits >= 2
