from .acoustic_cam import AcousticCamMode
from .birdnet_mode import BirdNetMode
from .camcorder import CamcorderMode
from .diagnosis import DiagnosisMode
from .drone import DroneMode
from .surveillance_cam import SurveillanceCamMode
from .surveillance_mic import SurveillanceMicMode

# Reihenfolge = Reihenfolge am Drehencoder
MODE_CLASSES = [AcousticCamMode, DiagnosisMode, CamcorderMode, DroneMode,
                SurveillanceCamMode, SurveillanceMicMode, BirdNetMode]
