#!/bin/bash
# =====================================================================================
#  Zusatzmodelle: YAMNet (Geräuschklassen) und BirdNET (Vogelstimmen)
#  Aufruf:  sudo bash /opt/multitool/app/install_extras.sh
#  Dauer: 10 bis 40 Minuten (BirdNET braucht viele Pakete). Ohne diese Modelle
#  läuft alles andere normal, nur die Klassen-Erkennung und BirdNET fehlen.
# =====================================================================================
set -u
DEST=/opt/multitool
PIP="$DEST/venv/bin/pip"
PY="$DEST/venv/bin/python"
M="$DEST/app/models"
mkdir -p "$M"
[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo starten"; exit 1; }

echo "==> TensorFlow Lite Laufzeit"
$PIP install --no-cache-dir tflite-runtime || $PIP install --no-cache-dir ai-edge-litert \
  || echo "HINWEIS: keine TFLite-Laufzeit für diese Python-Version gefunden"

echo "==> YAMNet-Modell"
[ -s "$M/yamnet.tflite" ] || wget -q -O "$M/yamnet.tflite" https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite \
  && echo "    OK" || echo "HINWEIS: yamnet.tflite nicht ladbar"
[ -s "$M/yamnet_class_map.csv" ] || wget -q -O "$M/yamnet_class_map.csv" https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv \
  && echo "    OK" || echo "HINWEIS: Klassenliste nicht ladbar"

echo "==> BirdNET (birdnetlib, dauert)"
apt-get install -y --no-install-recommends libsndfile1 llvm >/dev/null 2>&1
$PIP install --no-cache-dir birdnetlib librosa resampy audioread "audioop-lts; python_version>='3.13'" \
  || echo "HINWEIS: BirdNET-Installation fehlgeschlagen"

# Gibt es tflite_runtime nicht (neue Python-Version), einen kleinen Ersatz über ai-edge-litert anlegen
if ! $PY -c "import tflite_runtime.interpreter" 2>/dev/null && $PY -c "import ai_edge_litert" 2>/dev/null; then
  SP=$($PY -c "import site; print(site.getsitepackages()[0])")
  mkdir -p "$SP/tflite_runtime"
  : > "$SP/tflite_runtime/__init__.py"
  cat > "$SP/tflite_runtime/interpreter.py" <<'PYEOF'
"""Ersatz für tflite_runtime auf neuen Python-Versionen (über ai-edge-litert)."""
from ai_edge_litert import interpreter as _lr
from ai_edge_litert.interpreter import *  # noqa


class Interpreter(_lr.Interpreter):
    def __init__(self, *a, **k):
        k.setdefault("experimental_op_resolver_type", _lr.OpResolverType.BUILTIN_WITHOUT_DEFAULT_DELEGATES)
        super().__init__(*a, **k)
PYEOF
  echo "    tflite_runtime-Ersatz angelegt"
fi

echo "==> Test"
$PY - <<'PYEOF'
import sys
sys.path.insert(0, "/opt/multitool/app")
from multitool.dsp.classifiers import Yamnet, BirdNet
import numpy as np
y = Yamnet(); print("YAMNet:", "ok" if y.ok else "fehlt")
if y.ok: print("   Test:", y.groups(np.zeros(15600, np.float32))["_top"])
b = BirdNet(47.0, 7.5); print("BirdNET:", "ok" if b.ok else "fehlt")
PYEOF
systemctl restart multitool
echo "Fertig."
