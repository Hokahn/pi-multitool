# Pi Multitool – Software

Alles Wichtige steht in der Word-Dokumentation (Ordner `Dokumentation`). Hier nur das Nötigste.

## Ordner

| Ordner | Inhalt |
| --- | --- |
| `multitool/` | das Programm (Python) |
| `tools/` | Testwerkzeuge für die Inbetriebnahme |
| `firmware/` | Firmware für den RP2040-Zero (.uf2) |
| `models/` | YAMNet-Modell für Geräuschklassen |
| `systemd/` | Autostart |
| `install.sh` | Installation auf dem Pi |
| `install_extras.sh` | optional: Laufzeit für YAMNet und BirdNET |

## Installation (Kurzform)

```
scp -r software hokahn@multitool.local:~/      (am Windows-PC, PowerShell)
ssh hokahn@multitool.local
sudo bash ~/software/install.sh
```

Danach im Browser `http://multitool.local` öffnen.

## Am PC ausprobieren (Simulation)

Mit Python 3.10 oder neuer:

```
pip install numpy scipy opencv-python-headless pillow pyyaml flask qrcode
python -m multitool --sim
```

Dann `http://localhost:8080` öffnen. Mikrofone, Kamera und Akku werden simuliert.

## Nützliche Befehle auf dem Pi

```
sudo systemctl status multitool       läuft es?
sudo journalctl -u multitool -f       Meldungen live ansehen (Ctrl+C beendet)
sudo systemctl restart multitool      neu starten
multitool-test                        Liste der Tests
```

Einstellungen: `/opt/multitool/data/config.yaml`, Aufnahmen: `/opt/multitool/data/recordings/`.
