"""Geometrie: Mikrofonpositionen, Richtungen und Laufzeiten.

Array-Koordinaten:  x/y liegen in der Platinenebene, z zeigt aus der Bestückungsseite heraus
(Richtung Schallquelle bzw. Himmel). Mic 0 liegt bei Azimut 0°.
Azimut wird in der Ebene gemessen, Elevation von der Ebene weg.
"""

from __future__ import annotations

import numpy as np

C_SOUND = 343.0   # m/s bei 20 °C


def mic_positions(radius_mm: float, direction: int = 1) -> np.ndarray:
    """(7, 3) Positionen in Metern: Mic 0..5 auf dem Kreis, Index 6 = Mitte."""
    r = radius_mm / 1000.0
    pos = np.zeros((7, 3))
    for k in range(6):
        a = np.deg2rad(direction * 60.0 * k)
        pos[k] = (r * np.cos(a), r * np.sin(a), 0.0)
    return pos


def positions_from_cfg(cfg) -> np.ndarray:
    return mic_positions(float(cfg.get("array", "radius_mm")), int(cfg.get("array", "mic_direction")))


def dir_vectors_azel(az_deg: np.ndarray, el_deg: np.ndarray) -> np.ndarray:
    """Einheitsvektoren (D, 3) für Kombinationen aus Azimut und Elevation (Gitter)."""
    az, el = np.meshgrid(np.deg2rad(az_deg), np.deg2rad(el_deg), indexing="xy")
    az, el = az.ravel(), el.ravel()
    return np.stack([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], axis=1)


def camera_grid(nx: int, ny: int, hfov: float, vfov: float, rotation_deg: float, mirror: bool) -> np.ndarray:
    """Richtungen (ny*nx, 3) für ein Raster über das Kamerabild, in Array-Koordinaten.

    Reihenfolge: Zeile für Zeile von oben links. Kamera und Array schauen in die gleiche Richtung.
    rotation_deg: Winkel, unter dem Mic 0 im Kamerabild liegt (0 = rechts, 90 = oben).
    """
    tx = np.tan(np.deg2rad(hfov) / 2)
    ty = np.tan(np.deg2rad(vfov) / 2)
    u = (np.arange(nx) + 0.5) / nx * 2 - 1          # -1 links .. +1 rechts
    v = (np.arange(ny) + 0.5) / ny * 2 - 1          # -1 oben .. +1 unten
    uu, vv = np.meshgrid(u, v, indexing="xy")
    x_img = uu.ravel() * tx                          # nach rechts
    y_img = -vv.ravel() * ty                         # nach oben
    return image_to_array(x_img, y_img, rotation_deg, mirror)


def image_to_array(x_img, y_img, rotation_deg, mirror):
    """Bildkoordinaten (rechts, oben, Abstand 1) -> Einheitsvektoren in Array-Koordinaten."""
    x_img = np.asarray(x_img, float)
    y_img = np.asarray(y_img, float)
    # Wir schauen von hinten durch die Kamera, die Bestückungsseite des Arrays zeigt nach vorne.
    # Von vorne gesehen ist links und rechts vertauscht.
    if not mirror:
        x_img = -x_img
    a = np.deg2rad(rotation_deg)
    xa = np.cos(-a) * x_img - np.sin(-a) * y_img
    ya = np.sin(-a) * x_img + np.cos(-a) * y_img
    d = np.stack([xa, ya, np.ones_like(xa)], axis=-1)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def array_to_image(d, rotation_deg, mirror):
    """Umkehrung: Richtung in Array-Koordinaten -> (x_img, y_img) auf der Ebene Abstand 1."""
    d = np.asarray(d, float)
    z = np.maximum(d[..., 2], 1e-3)
    xa, ya = d[..., 0] / z, d[..., 1] / z
    a = np.deg2rad(rotation_deg)
    x_img = np.cos(a) * xa - np.sin(a) * ya
    y_img = np.sin(a) * xa + np.cos(a) * ya
    if not mirror:
        x_img = -x_img
    return x_img, y_img


def delays(positions: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    """Laufzeiten tau (M, D) in Sekunden. Eine Welle aus Richtung d erreicht Mikrofone,
    die näher an der Quelle liegen, früher (negatives tau)."""
    return -(positions @ dirs.T) / C_SOUND


def bearing(az_deg: float, cfg) -> float:
    """Array-Azimut -> Peilung für Anzeige und Protokoll: von oben gesehen im Uhrzeigersinn,
    0° = Richtung von LED 0 (oder Norden, wenn array.north_offset_deg gesetzt ist)."""
    led0 = float(cfg.get("leds", "led0_deg") or 0)
    north = float(cfg.get("array", "north_offset_deg") or 0)
    return (led0 - az_deg + north) % 360


def az_to_led(az_deg: float, led0_deg: float, led_direction: int, n_leds: int = 12) -> int:
    step = 360.0 / n_leds
    return int(round(((az_deg - led0_deg) * led_direction) / step)) % n_leds
