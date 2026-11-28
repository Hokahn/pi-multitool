"""Ablage der Aufnahmen und Aufräumen bei vollem Speicher.

Ordner:  <Datenordner>/recordings/<modus>/<JJJJ-MM-TT>/<HHMMSS>_<art>.<endung>
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
import subprocess
import threading

log = logging.getLogger("storage")

SURVEILLANCE_MODES = ("surveillance_cam", "surveillance_mic", "drone")
MEDIA_EXT = {".mp4": "video", ".wav": "audio", ".jpg": "bild", ".png": "bild", ".csv": "daten", ".h264": "video"}


class Storage:
    def __init__(self, cfg, data_dir):
        self.cfg = cfg
        self.root = os.path.join(data_dir, "recordings")
        os.makedirs(self.root, exist_ok=True)
        self.lock = threading.Lock()

    def new_path(self, mode: str, kind: str, ext: str) -> str:
        now = datetime.datetime.now()
        d = os.path.join(self.root, mode, now.strftime("%Y-%m-%d"))
        os.makedirs(d, exist_ok=True)
        base = now.strftime("%H%M%S") + f"_{kind}"
        p = os.path.join(d, base + ext)
        n = 1
        while os.path.exists(p):
            p = os.path.join(d, f"{base}_{n}{ext}")
            n += 1
        return p

    def rel(self, path: str) -> str:
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def abs(self, rel: str) -> str:
        p = os.path.normpath(os.path.join(self.root, rel))
        if not p.startswith(os.path.normpath(self.root) + os.sep):
            raise ValueError("Pfad ausserhalb des Aufnahmeordners")
        return p

    def free_gb(self) -> float:
        return shutil.disk_usage(self.root).free / 1e9

    def disk(self) -> dict:
        u = shutil.disk_usage(self.root)
        return {"total_gb": round(u.total / 1e9, 1), "free_gb": round(u.free / 1e9, 1),
                "used_recordings_gb": round(self._size(self.root) / 1e9, 2)}

    @staticmethod
    def _size(path) -> int:
        tot = 0
        for dp, _, fs in os.walk(path):
            for f in fs:
                try:
                    tot += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        return tot

    def can_record(self) -> bool:
        """Platz prüfen und wenn eingestellt alte Überwachungsaufnahmen löschen."""
        with self.lock:
            min_free = float(self.cfg.get("storage", "min_free_gb") or 2)
            max_surv = float(self.cfg.get("storage", "max_surveillance_gb") or 50) * 1e9
            files = []
            for m in SURVEILLANCE_MODES:
                base = os.path.join(self.root, m)
                for dp, _, fs in os.walk(base):
                    for f in fs:
                        p = os.path.join(dp, f)
                        try:
                            files.append((os.path.getmtime(p), os.path.getsize(p), p))
                        except OSError:
                            pass
            files.sort()
            surv_total = sum(s for _, s, _ in files)
            delete_ok = self.cfg.get("storage", "when_full") == "delete_oldest"
            while files and (surv_total > max_surv or (delete_ok and self.free_gb() < min_free)):
                _, s, p = files.pop(0)
                try:
                    os.remove(p)
                    surv_total -= s
                    log.info("Speicher: gelöscht %s", p)
                except OSError:
                    break
            return self.free_gb() >= min_free * 0.5

    def list(self, mode: str | None = None, limit: int = 300) -> list[dict]:
        out = []
        for dp, _, fs in os.walk(self.root):
            for f in fs:
                ext = os.path.splitext(f)[1].lower()
                if ext not in MEDIA_EXT or ".part." in f:
                    continue
                p = os.path.join(dp, f)
                r = self.rel(p)
                m = r.split("/")[0]
                if mode and m != mode:
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append({"path": r, "mode": m, "name": f, "type": MEDIA_EXT[ext],
                            "size": st.st_size, "time": st.st_mtime})
        out.sort(key=lambda x: x["time"], reverse=True)
        return out[:limit]

    def delete(self, rel: str):
        os.remove(self.abs(rel))


def ffmpeg(args: list, timeout=600) -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args,
                           capture_output=True, timeout=timeout)
        if r.returncode != 0:
            log.error("ffmpeg: %s", r.stderr.decode(errors="ignore")[-400:])
        return r.returncode == 0
    except Exception as e:
        log.error("ffmpeg nicht ausführbar: %s", e)
        return False


def mux_audio_video(video: str, audio: str, out: str, delete_inputs=True) -> bool:
    """Video (ohne Ton) und WAV zu einer MP4 zusammenfügen."""
    ok = ffmpeg(["-i", video, "-i", audio, "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", out])
    if ok and delete_inputs:
        for p in (video, audio):
            try:
                os.remove(p)
            except OSError:
                pass
    return ok


def h264_to_mp4(src: str, dst: str, fps: int = 30, delete_src=True) -> bool:
    ok = ffmpeg(["-framerate", str(fps), "-i", src, "-c", "copy", dst])
    if ok and delete_src:
        try:
            os.remove(src)
        except OSError:
            pass
    return ok


def images_to_mp4(folder: str, dst: str, fps: int = 30) -> bool:
    """Zeitraffer: JPGs eines Ordners zu einem Film. Erst Hardware-Encoder, sonst Software."""
    pattern = os.path.join(folder, "*.jpg")
    if ffmpeg(["-framerate", str(fps), "-pattern_type", "glob", "-i", pattern,
               "-c:v", "h264_v4l2m2m", "-b:v", "8M", "-pix_fmt", "yuv420p", dst], timeout=3600):
        return True
    return ffmpeg(["-framerate", str(fps), "-pattern_type", "glob", "-i", pattern,
                   "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", dst],
                  timeout=7200)
