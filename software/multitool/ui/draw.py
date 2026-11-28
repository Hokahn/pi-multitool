"""Zeichenhilfen für Display und Webseite (gleiches Bild für beide)."""

from __future__ import annotations

import math
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONTS = {}
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def font(size: int, bold: bool = False):
    k = (size, bold)
    if k not in _FONTS:
        f = None
        for p in (FONT_BOLD if bold else FONT_PATHS):
            if os.path.exists(p):
                f = ImageFont.truetype(p, size)
                break
        _FONTS[k] = f or ImageFont.load_default()
    return _FONTS[k]


class Canvas:
    """Bild (H, W, 3) RGB mit Text in Umlauten über PIL."""

    def __init__(self, img: np.ndarray):
        self.img = img
        self._pil = None
        self._draw = None

    def _ensure_pil(self):
        if self._pil is None:
            self._pil = Image.fromarray(self.img)
            self._draw = ImageDraw.Draw(self._pil, "RGBA")
        return self._draw

    def text(self, xy, s, size=18, color=(255, 255, 255), bold=False, anchor="la", bg=None):
        d = self._ensure_pil()
        f = font(size, bold)
        if bg is not None:
            box = d.textbbox(xy, s, font=f, anchor=anchor)
            d.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill=bg)
        d.text(xy, s, font=f, fill=color, anchor=anchor)

    def rect(self, box, fill=None, outline=None, width=1):
        self._ensure_pil().rectangle(box, fill=fill, outline=outline, width=width)

    def rounded(self, box, r, fill=None, outline=None, width=1):
        self._ensure_pil().rounded_rectangle(box, r, fill=fill, outline=outline, width=width)

    def result(self) -> np.ndarray:
        if self._pil is not None:
            self.img = np.array(self._pil.convert("RGB"))
            self._pil = None
            self._draw = None
        return self.img


def fit(img: np.ndarray, W: int, H: int) -> np.ndarray:
    """Bild auf W x H skalieren (Seitenverhältnis halten, schwarze Ränder)."""
    if img is None:
        return np.zeros((H, W, 3), np.uint8)
    h, w = img.shape[:2]
    s = min(W / w, H / h)
    nw, nh = int(w * s), int(h * s)
    out = np.zeros((H, W, 3), np.uint8)
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    x0, y0 = (W - nw) // 2, (H - nh) // 2
    out[y0:y0 + nh, x0:x0 + nw] = r
    return out


def fit_rect(w, h, W, H):
    s = min(W / w, H / h)
    nw, nh = int(w * s), int(h * s)
    return (W - nw) // 2, (H - nh) // 2, nw, nh


def heat_overlay(img: np.ndarray, power: np.ndarray, dyn_db: float, alpha: float) -> tuple[np.ndarray, tuple]:
    """Akustische Karte als Farbe über das Bild legen. -> (Bild, (x, y) des Maximums 0..1)."""
    h, w = img.shape[:2]
    p = np.maximum(power, 1e-20)
    db = 10 * np.log10(p / p.max())
    norm = np.clip((db + dyn_db) / dyn_db, 0, 1)
    big = cv2.resize(norm.astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    big = np.clip(big, 0, 1)
    col = cv2.applyColorMap((big * 255).astype(np.uint8), cv2.COLORMAP_JET)
    col = cv2.cvtColor(col, cv2.COLOR_BGR2RGB)
    a = (big > 0.02).astype(np.float32)[..., None] * alpha * (0.35 + 0.65 * big[..., None])
    out = (img.astype(np.float32) * (1 - a) + col.astype(np.float32) * a).astype(np.uint8)
    j = np.unravel_index(np.argmax(power), power.shape)
    peak = ((j[1] + 0.5) / power.shape[1], (j[0] + 0.5) / power.shape[0])
    return out, peak


def radar(W, H, profile=None, bearing=None, color=(255, 60, 60), north=False, events=None, label="") -> np.ndarray:
    """Rundumanzeige für liegendes Array, von oben gesehen: 0° oben, im Uhrzeigersinn.

    profile: Liste (peilung_grad, wert 0..1)    bearing: aktuelle Peilung oder None
    events:  Liste (peilung_grad, alter 0..1, farbe)
    """
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (14, 18, 24)
    cx, cy = W // 2 - 140, H // 2 + 4
    R = min(H // 2 - 70, 170)

    def pt(b, r):
        a = math.radians(b - 90)
        return int(cx + r * math.cos(a)), int(cy + r * math.sin(a))
    for k in range(12):
        cv2.line(img, (cx, cy), pt(k * 30, R), (45, 55, 65), 1)
    for r in (R // 3, 2 * R // 3, R):
        cv2.circle(img, (cx, cy), r, (55, 65, 80), 1, cv2.LINE_AA)
    if profile:
        pts = [pt(b, 20 + (R - 20) * float(v)) for b, v in sorted(profile)]
        cv2.polylines(img, [np.array(pts, np.int32)], True, (70, 140, 200), 1, cv2.LINE_AA)
    for b, age, col in events or []:
        cv2.circle(img, pt(b, R * (0.95 - 0.5 * min(1.0, age))), 6, col, -1, cv2.LINE_AA)
    if bearing is not None:
        poly = [(cx, cy)] + [pt(bearing + da, R) for da in np.linspace(-12, 12, 7)]
        ov = img.copy()
        cv2.fillPoly(ov, [np.array(poly, np.int32)], color)
        img = cv2.addWeighted(ov, 0.55, img, 0.45, 0)
    c = Canvas(img)
    c.text(pt(0, R + 16), "N" if north else "LED 0", 15, (180, 190, 200), anchor="mm")
    if bearing is not None:
        c.text((cx, cy + R + 22), f"{bearing:.0f}°", 22, color, bold=True, anchor="mm")
    if label:
        c.text((cx, cy), label, 14, (150, 160, 170), anchor="mm")
    return c.result()


def spectrum_panel(img, freqs, level_db, x0, y0, w, h, fmax=8000, markers=None, peak_info=""):
    """Spektrum unten ins Bild zeichnen."""
    ov = img.copy()
    cv2.rectangle(ov, (x0, y0), (x0 + w, y0 + h), (0, 0, 0), -1)
    img[:] = cv2.addWeighted(ov, 0.6, img, 0.4, 0)
    if freqs is None or len(freqs) == 0:
        return img
    lo, hi = -100.0, -20.0
    m = freqs <= fmax
    f, L = freqs[m], level_db[m]
    xs = (x0 + f / fmax * w).astype(np.int32)
    ys = (y0 + h - (np.clip(L, lo, hi) - lo) / (hi - lo) * (h - 18)).astype(np.int32)
    cv2.polylines(img, [np.stack([xs, ys], 1)], False, (120, 230, 255), 1, cv2.LINE_AA)
    for fm, col in markers or []:
        if fm < fmax:
            x = int(x0 + fm / fmax * w)
            cv2.line(img, (x, y0 + 14), (x, y0 + h), col, 1)
    c = Canvas(img)
    for fk in range(0, int(fmax) + 1, 1000):
        x = int(x0 + fk / fmax * w)
        c.text((x + 2, y0 + 2), f"{fk // 1000}k", 11, (160, 160, 160))
    if peak_info:
        c.text((x0 + w - 6, y0 + 2), peak_info, 13, (255, 255, 255), anchor="ra")
    img[:] = c.result()
    return img
