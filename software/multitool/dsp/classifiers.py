"""Neuronale Klassifikatoren (optional). Fehlt ein Paket oder Modell, laufen die Modi trotzdem,
nur ohne diese Funktion. Installation: install_extras.sh
"""

from __future__ import annotations

import csv
import datetime
import logging
import os

import numpy as np

from .detectors import DroneHeuristic, log_mel

log = logging.getLogger("classifiers")

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")


def _interpreter(path):
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        try:
            from ai_edge_litert.interpreter import Interpreter
        except ImportError:
            from tensorflow.lite.python.interpreter import Interpreter  # type: ignore
    it = Interpreter(model_path=path, num_threads=2)
    it.allocate_tensors()
    return it


# Gruppen für das Überwachungsmikrofon: Anzeige -> YAMNet-Klassen
YAMNET_GROUPS = {
    "glass": ("Glasbruch", ["Glass", "Shatter", "Breaking", "Chink, clink"]),
    "alarm": ("Alarm", ["Alarm", "Smoke detector, smoke alarm", "Fire alarm", "Car alarm", "Alarm clock", "Buzzer"]),
    "dog": ("Hund", ["Dog", "Bark", "Howl", "Growling", "Yip", "Bow-wow"]),
    "scream": ("Schreien", ["Screaming", "Shout", "Yell", "Children shouting"]),
    "knock": ("Klopfen", ["Knock", "Door", "Doorbell", "Ding-dong"]),
    "bang": ("Knall", ["Gunshot, gunfire", "Explosion", "Bang", "Slam", "Burst, pop"]),
    "speech": ("Sprache", ["Speech", "Conversation", "Narration, monologue"]),
    "engine": ("Motor", ["Engine", "Vehicle", "Motorcycle", "Car", "Lawn mower", "Chainsaw"]),
    "aircraft": ("Fluggerät", ["Aircraft", "Helicopter", "Propeller, airscrew", "Aircraft engine", "Fixed-wing aircraft, airplane"]),
}


class Yamnet:
    """YAMNet (521 Alltagsgeräusche). Eingabe: 0,975 s Mono bei 16 kHz."""

    N = 15600

    def __init__(self):
        self.ok = False
        model = os.path.join(MODEL_DIR, "yamnet.tflite")
        labels = os.path.join(MODEL_DIR, "yamnet_class_map.csv")
        try:
            self.it = _interpreter(model)
            self.inp = self.it.get_input_details()[0]
            self.out = self.it.get_output_details()[0]
            with open(labels, newline="", encoding="utf-8") as f:
                self.labels = [row["display_name"] for row in csv.DictReader(f)]
            self.group_idx = {g: [i for i, n in enumerate(self.labels) if n in names]
                              for g, (_, names) in YAMNET_GROUPS.items()}
            self.ok = True
            log.info("YAMNet geladen")
        except Exception as e:
            log.warning("YAMNet nicht verfügbar (%s). install_extras.sh ausführen.", e)

    def scores(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float32)
        if len(x) < self.N:
            x = np.pad(x, (0, self.N - len(x)))
        x = x[-self.N:]
        shape = self.inp["shape"]
        if len(shape) == 2:
            x = x[None, :]
        self.it.set_tensor(self.inp["index"], x.reshape(shape).astype(np.float32))
        self.it.invoke()
        s = self.it.get_tensor(self.out["index"])
        return s.reshape(-1, s.shape[-1]).mean(axis=0)

    def groups(self, x: np.ndarray) -> dict:
        """-> {gruppe: wert 0..1} und Top-Klasse."""
        s = self.scores(x)
        res = {g: float(s[idx].max()) if idx else 0.0 for g, idx in self.group_idx.items()}
        top = int(np.argmax(s))
        res["_top"] = (self.labels[top], float(s[top]))
        return res


class DroneClassifier:
    """Eigenes Modell (models/drone.tflite) falls vorhanden, sonst Heuristik."""

    def __init__(self):
        self.heur = DroneHeuristic()
        self.model = None
        path = os.path.join(MODEL_DIR, "drone.tflite")
        if os.path.exists(path):
            try:
                self.model = _interpreter(path)
                self.inp = self.model.get_input_details()[0]
                self.out = self.model.get_output_details()[0]
                log.info("Drohnen-Modell geladen")
            except Exception as e:
                log.warning("drone.tflite nicht ladbar: %s", e)
                self.model = None

    @property
    def kind(self):
        return "Modell" if self.model else "Heuristik"

    def score(self, x: np.ndarray) -> tuple[float, float]:
        p_h, f0 = self.heur.score(x)
        if self.model is None:
            return p_h, f0
        # Erwartet: log-Mel (1, 64, frames, 1), 1 s bei 16 kHz
        m = log_mel(x[-16000:])
        shape = self.inp["shape"]
        m = m[:, :shape[2]] if m.shape[1] >= shape[2] else np.pad(m, ((0, 0), (0, shape[2] - m.shape[1])))
        self.model.set_tensor(self.inp["index"], m.reshape(shape).astype(np.float32))
        self.model.invoke()
        out = self.model.get_tensor(self.out["index"]).ravel()
        p = float(out[-1]) if out.size > 1 else float(out[0])
        return p, f0


class BirdNet:
    """BirdNET über das Paket birdnetlib. 3-s-Abschnitte."""

    def __init__(self, lat, lon):
        self.ok = False
        self.lat, self.lon = lat, lon
        try:
            import contextlib
            import io
            from birdnetlib.analyzer import Analyzer
            with contextlib.redirect_stdout(io.StringIO()):
                self.analyzer = Analyzer()
            self.ok = True
            log.info("BirdNET geladen")
        except Exception as e:
            log.warning("BirdNET nicht verfügbar (%s). install_extras.sh ausführen.", e)

    def detect(self, x: np.ndarray, fs: int, min_conf: float) -> list[dict]:
        if not self.ok:
            return []
        try:
            from birdnetlib import RecordingBuffer
            from scipy.signal import resample_poly
            x = np.asarray(x, np.float32)
            if fs != 48000:                       # BirdNET erwartet 48 kHz
                x = resample_poly(x, 48000 // np.gcd(48000, fs), fs // np.gcd(48000, fs)).astype(np.float32)
                fs = 48000
            rec = RecordingBuffer(self.analyzer, x, fs, lat=self.lat, lon=self.lon,
                                  date=datetime.date.today(), min_conf=min_conf)
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):      # birdnetlib ist sehr gesprächig
                rec.analyze()
            return [{"name": d.get("common_name", "?"), "sci": d.get("scientific_name", ""),
                     "conf": float(d.get("confidence", 0))} for d in rec.detections]
        except Exception as e:
            log.warning("BirdNET-Fehler: %s", e)
            return []
