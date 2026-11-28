"""WLAN oder eigener Hotspot über NetworkManager (nmcli).

Auto-Modus: beim Start 30 s auf ein bekanntes WLAN warten, sonst Hotspot "Multitool" starten.
Der WLAN-Chip des Pi 3 kann nicht zuverlässig beides gleichzeitig, deshalb wird umgeschaltet.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time

log = logging.getLogger("network")
HOTSPOT = "multitool-hotspot"


def _nmcli(*args, timeout=20) -> tuple[int, str]:
    try:
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


class Network:
    def __init__(self, cfg, sim=False):
        self.cfg = cfg
        self.sim = sim
        self.mode = "unbekannt"     # "wifi", "hotspot", "offline", "aus"
        self.ssid = ""
        self.ip = ""
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._boot, name="network", daemon=True).start()

    def _boot(self):
        if self.sim:
            self.mode, self.ssid, self.ip = "wifi", "Simulation", "127.0.0.1"
            return
        m = self.cfg.get("network", "mode")
        if m == "off":
            _nmcli("radio", "wifi", "off")
            self.mode = "aus"
            return
        _nmcli("radio", "wifi", "on")
        if m == "hotspot":
            self.start_hotspot()
            return
        for _ in range(30):
            if self.refresh() == "wifi":
                return
            time.sleep(1)
        if m == "auto":
            log.info("Kein bekanntes WLAN, starte Hotspot")
            self.start_hotspot()
        else:
            self.refresh()

    def refresh(self) -> str:
        if self.sim:
            return self.mode
        code, out = _nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")
        mode, ssid = "offline", ""
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[2] == "wlan0":
                if parts[0] == HOTSPOT:
                    mode, ssid = "hotspot", self.cfg.get("network", "hotspot_ssid")
                else:
                    mode, ssid = "wifi", parts[0]
        self.mode, self.ssid = mode, ssid
        self.ip = self._ip()
        return mode

    @staticmethod
    def _ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return ""

    def start_hotspot(self) -> bool:
        if self.sim:
            self.mode = "hotspot"
            return True
        with self._lock:
            ssid = self.cfg.get("network", "hotspot_ssid") or "Multitool"
            pw = str(self.cfg.get("network", "hotspot_password") or "multitool123")
            if len(pw) < 8:
                pw = (pw + "12345678")[:8]
            _nmcli("connection", "delete", HOTSPOT)
            code, out = _nmcli("connection", "add", "type", "wifi", "ifname", "wlan0", "con-name", HOTSPOT,
                               "autoconnect", "no", "ssid", ssid, "mode", "ap", "ipv4.method", "shared",
                               "ipv4.addresses", "10.42.0.1/24",
                               "wifi.band", "bg", "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", pw)
            if code != 0:
                log.error("Hotspot anlegen: %s", out)
            code, out = _nmcli("connection", "up", HOTSPOT, timeout=40)
            if code != 0:
                log.error("Hotspot starten: %s", out)
            self.refresh()
            return self.mode == "hotspot"

    def stop_hotspot(self) -> bool:
        if self.sim:
            self.mode = "wifi"
            return True
        with self._lock:
            _nmcli("connection", "down", HOTSPOT)
            for _ in range(20):
                if self.refresh() == "wifi":
                    return True
                time.sleep(1)
            return False

    def toggle(self):
        if self.mode == "hotspot":
            threading.Thread(target=self.stop_hotspot, daemon=True).start()
        else:
            threading.Thread(target=self.start_hotspot, daemon=True).start()

    def add_wifi(self, ssid: str, password: str) -> tuple[bool, str]:
        """Neues Heim-WLAN eintragen (über die Webseite im Hotspot-Modus)."""
        if self.sim:
            return True, "Simulation"
        _nmcli("connection", "down", HOTSPOT)
        code, out = _nmcli("device", "wifi", "connect", ssid, "password", password, timeout=60)
        self.refresh()
        if code != 0:
            self.start_hotspot()
        return code == 0, out

    def status(self) -> dict:
        return {"mode": self.mode, "ssid": self.ssid, "ip": self.ip,
                "hotspot_ssid": self.cfg.get("network", "hotspot_ssid"),
                "hotspot_password": self.cfg.get("network", "hotspot_password")}

    def hotspot_qr_text(self) -> str:
        ssid = self.cfg.get("network", "hotspot_ssid")
        pw = self.cfg.get("network", "hotspot_password")
        return f"WIFI:T:WPA;S:{ssid};P:{pw};;"
