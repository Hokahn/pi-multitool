"""Ausgabe auf das 3,5-Zoll-DSI-Display (480x800 hochkant) über pygame/KMS, ohne Desktop.

Die Software zeichnet quer (800x480) und dreht das Bild selbst, Touch wird zurückgerechnet.
"""

from __future__ import annotations

import glob
import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("display")


class Backlight:
    def __init__(self):
        self.path = None
        for p in glob.glob("/sys/class/backlight/*"):
            self.path = p
            break
        self.max = 255
        if self.path:
            try:
                self.max = int(open(os.path.join(self.path, "max_brightness")).read())
            except Exception:
                pass

    def set(self, percent: float):
        if not self.path:
            return
        try:
            with open(os.path.join(self.path, "brightness"), "w") as f:
                f.write(str(int(self.max * max(0, min(100, percent)) / 100)))
        except Exception as e:
            log.debug("Hintergrundlicht: %s", e)


class Display:
    def __init__(self, app):
        self.app = app
        self.ok = False
        self.on = True
        self.backlight = Backlight()
        self.last_activity = time.time()
        self._running = False

    def start(self):
        if self.app.sim or not self.app.cfg.get("display", "enabled"):
            return
        self._running = True
        threading.Thread(target=self._loop, name="display", daemon=True).start()

    def wake(self):
        self.last_activity = time.time()
        if not self.on:
            self.on = True
            self.backlight.set(float(self.app.cfg.get("display", "brightness") or 70))

    def sleep(self):
        self.on = False
        self.backlight.set(0)

    def _loop(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        os.environ.setdefault("SDL_NOMOUSE", "1")
        try:
            import pygame
            pygame.display.init()
            info = pygame.display.Info()
            size = (info.current_w, info.current_h)
            screen = pygame.display.set_mode(size, pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
            self.ok = True
            log.info("Display %dx%d", *size)
        except Exception as e:
            log.error("Display nicht verfügbar: %s", e)
            return
        self.backlight.set(float(self.app.cfg.get("display", "brightness") or 70))
        last_id = -1
        while self._running:
            for ev in pygame.event.get():
                if ev.type in (pygame.MOUSEBUTTONDOWN, getattr(pygame, "FINGERDOWN", -1)):
                    if ev.type == pygame.MOUSEBUTTONDOWN:
                        sx, sy = ev.pos[0] / size[0], ev.pos[1] / size[1]
                    else:
                        sx, sy = ev.x, ev.y
                    was_on = self.on
                    self.wake()
                    if was_on:
                        fx, fy = self._screen_to_frame(sx, sy)
                        self.app.touch(fx, fy)
            off_after = int(self.app.cfg.get("display", "off_after") or 0)
            if self.on and off_after and time.time() - self.last_activity > off_after:
                self.sleep()
            if self.on and self.app.frame_id != last_id and self.app.frame is not None:
                last_id = self.app.frame_id
                img = self._rotate(self.app.frame)
                if img.shape[1] != size[0] or img.shape[0] != size[1]:
                    import cv2
                    img = cv2.resize(img, size)
                surf = pygame.surfarray.make_surface(np.ascontiguousarray(img.swapaxes(0, 1)))
                screen.blit(surf, (0, 0))
                pygame.display.flip()
            time.sleep(0.02)

    def _rotation(self):
        return int(self.app.cfg.get("display", "rotation") or 0)

    def _rotate(self, img):
        r = self._rotation()
        if r == 90:
            return np.rot90(img, 3)      # im Uhrzeigersinn
        if r == 180:
            return img[::-1, ::-1]
        if r == 270:
            return np.rot90(img, 1)
        return img

    def _screen_to_frame(self, sx, sy):
        r = self._rotation()
        if r == 90:
            return sy, 1 - sx
        if r == 180:
            return 1 - sx, 1 - sy
        if r == 270:
            return 1 - sy, sx
        return sx, sy
