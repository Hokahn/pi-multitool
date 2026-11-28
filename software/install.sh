#!/bin/bash
# =====================================================================================
#  Pi Multitool – Installation auf dem Raspberry Pi
#
#  Aufruf (im Ordner, in den du die Software kopiert hast):
#       sudo bash install.sh
#
#  Dauer: 10 bis 25 Minuten. Danach startet der Pi neu und das Multitool läuft von selbst.
#  Ein Protokoll liegt in /var/log/multitool-install.log
# =====================================================================================
set -u
LOG=/var/log/multitool-install.log
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/multitool
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot

say()  { echo -e "\n\033[1;36m==> $*\033[0m"; echo "==> $*" >> "$LOG"; }
ok()   { echo -e "    \033[32mOK\033[0m $*"; }
warn() { echo -e "    \033[33mHINWEIS\033[0m $*"; echo "HINWEIS $*" >> "$LOG"; }
fail() { echo -e "\n\033[1;31mFEHLER: $*\033[0m\nDetails in $LOG\n"; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo starten:   sudo bash install.sh"; exit 1
fi
echo "Installation gestartet $(date)" > "$LOG"

say "1/9  Systempakete installieren (dauert am längsten)"
export DEBIAN_FRONTEND=noninteractive
apt-get update >> "$LOG" 2>&1 || fail "apt-get update. Ist der Pi mit dem Internet verbunden?"
PKGS="python3-venv python3-pip python3-picamera2 python3-opencv python3-numpy python3-scipy \
python3-pygame python3-flask python3-yaml python3-spidev python3-gpiozero python3-lgpio python3-pil \
python3-qrcode libportaudio2 ffmpeg i2c-tools alsa-utils fonts-dejavu-core avahi-daemon \
network-manager util-linux-extra rsync"
apt-get install -y --no-install-recommends $PKGS >> "$LOG" 2>&1 || {
  warn "Nicht alle Pakete auf einmal installierbar, versuche einzeln"
  for p in $PKGS; do apt-get install -y --no-install-recommends "$p" >> "$LOG" 2>&1 || warn "Paket $p fehlt"; done
}
ok "Pakete"

say "2/9  Programm nach $DEST kopieren"
mkdir -p "$DEST/data"
rsync -a --delete --exclude data --exclude venv --exclude '__pycache__' "$SRC/" "$DEST/app/" >> "$LOG" 2>&1 \
  || fail "Kopieren nach $DEST/app"
ok "$DEST/app"

say "3/9  Python-Umgebung"
python3 -m venv --system-site-packages "$DEST/venv" >> "$LOG" 2>&1 || fail "venv anlegen"
"$DEST/venv/bin/pip" install --no-cache-dir sounddevice smbus2 >> "$LOG" 2>&1 || fail "pip install sounddevice smbus2"
"$DEST/venv/bin/python" -c "import numpy, scipy, cv2, flask, yaml, sounddevice, smbus2, PIL" >> "$LOG" 2>&1 \
  || fail "Python-Module fehlen (siehe Protokoll)"
ok "venv"

say "4/9  I2C und SPI einschalten"
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_i2c 0 >> "$LOG" 2>&1
  raspi-config nonint do_spi 0 >> "$LOG" 2>&1
fi
ok "I2C (Akku, Uhr) und SPI (LEDs)"

say "5/9  config.txt: Echtzeituhr und Display"
CFG="$BOOT/config.txt"
cp "$CFG" "$CFG.vor-multitool" 2>/dev/null
add_line() { grep -qxF "$1" "$CFG" || echo "$1" >> "$CFG"; }
grep -q "# --- Pi Multitool ---" "$CFG" || echo -e "\n# --- Pi Multitool ---" >> "$CFG"
add_line "dtparam=i2c_arm=on"
add_line "dtparam=spi=on"
add_line "dtparam=audio=on"
add_line "dtoverlay=i2c-rtc,ds3231"
add_line "dtoverlay=vc4-kms-v3d"
if [ ! -f "$BOOT/overlays/waveshare_35DSI.dtbo" ] && [ ! -f "$BOOT/overlays/Waveshare_35DSI.dtbo" ]; then
  if wget -q -O "$BOOT/overlays/waveshare_35DSI.dtbo" https://files.waveshare.com/wiki/common/Waveshare_35DSI.dtbo; then
    ok "Display-Treiber geladen"
  else
    rm -f "$BOOT/overlays/waveshare_35DSI.dtbo"
    warn "Display-Treiber nicht ladbar. Siehe Doku Kapitel Display, Schritt von Hand."
  fi
fi
add_line "dtoverlay=waveshare_35DSI,35H,dsi1"
# Konsole: Cursor aus, kein Bildschirmschoner
CMD="$BOOT/cmdline.txt"
grep -q "vt.global_cursor_default=0" "$CMD" || sed -i '1 s/$/ vt.global_cursor_default=0 consoleblank=0/' "$CMD"
ok "config.txt (Sicherung: config.txt.vor-multitool)"

say "6/9  Uhr: Zeitzone, Echtzeituhr"
timedatectl set-timezone Europe/Zurich >> "$LOG" 2>&1
apt-get purge -y fake-hwclock >> "$LOG" 2>&1
ok "Europe/Zurich"

say "7/9  Name im Netzwerk: multitool"
OLD=$(hostname)
if [ "$OLD" != "multitool" ]; then
  hostnamectl set-hostname multitool >> "$LOG" 2>&1
  sed -i "s/127.0.1.1.*/127.0.1.1\tmultitool/" /etc/hosts
fi
systemctl enable --now avahi-daemon >> "$LOG" 2>&1
ok "http://multitool.local"

say "8/9  Dienste einrichten"
install -m 644 "$DEST/app/systemd/multitool.service" /etc/systemd/system/multitool.service
install -m 644 "$DEST/app/systemd/multitool-rtc.service" /etc/systemd/system/multitool-rtc.service
install -m 755 "$DEST/app/systemd/multitool-test" /usr/local/bin/multitool-test
systemctl daemon-reload
systemctl enable multitool-rtc.service multitool.service >> "$LOG" 2>&1
ok "Startet automatisch beim Einschalten"

say "9/9  Kontrolle"
lsusb | grep -qi "2e8a\|raspberry" && ok "RP2040 per USB gefunden" || warn "RP2040 nicht gefunden (USB-Kabel, Firmware?)"
i2cdetect -y 1 2>/dev/null | grep -q " 42" && ok "Akku-Messung (0x42)" || warn "INA219 (0x42) erst nach Neustart sichtbar oder UPS HAT nicht richtig aufgesteckt"
rpicam-hello --list-cameras 2>/dev/null | grep -q imx708 && ok "Kamera Module 3" || warn "Kamera nicht gefunden (Flachkabel, Kontakte Richtung?)"

echo
echo "------------------------------------------------------------------"
echo " Fertig. Der Pi startet in 10 s neu."
echo " Danach im Browser:   http://multitool.local   (oder IP-Adresse)"
echo " Beim ersten Aufruf legst du ein Passwort fest."
echo " Tests:  multitool-test mic_test   (siehe Doku, Kapitel Inbetriebnahme)"
echo "------------------------------------------------------------------"
sleep 10
reboot
