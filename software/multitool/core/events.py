"""Ereignisprotokoll (SQLite): Drohnen, Bewegungen, Geräusche, Vögel, Systemmeldungen."""

from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
import threading
import time
import urllib.request

log = logging.getLogger("events")


class EventLog:
    def __init__(self, path: str):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, mode TEXT, kind TEXT, label TEXT,
            direction REAL, level REAL, confidence REAL, file TEXT, extra TEXT)""")
        self.db.commit()
        self.listeners = []

    def add(self, mode, kind, label="", direction=None, level=None, confidence=None, file="", extra=None) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO events (ts, mode, kind, label, direction, level, confidence, file, extra) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), mode, kind, label, direction, level, confidence, file,
                 json.dumps(extra or {}, ensure_ascii=False)))
            self.db.commit()
            eid = cur.lastrowid
        log.info("Ereignis %s/%s %s %s", mode, kind, label, "" if direction is None else f"{direction:.0f}°")
        for cb in list(self.listeners):
            try:
                cb(eid)
            except Exception:
                pass
        return eid

    def update_file(self, eid: int, file: str):
        with self.lock:
            self.db.execute("UPDATE events SET file=? WHERE id=?", (file, eid))
            self.db.commit()

    def query(self, limit=200, mode=None, since=None) -> list[dict]:
        sql = "SELECT id, ts, mode, kind, label, direction, level, confidence, file FROM events"
        cond, args = [], []
        if mode:
            cond.append("mode=?")
            args.append(mode)
        if since:
            cond.append("ts>=?")
            args.append(since)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        keys = ["id", "ts", "mode", "kind", "label", "direction", "level", "confidence", "file"]
        return [dict(zip(keys, r)) for r in rows]

    def csv(self, mode=None) -> str:
        rows = self.query(limit=100000, mode=mode)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "zeit", "modus", "art", "bezeichnung", "richtung_grad", "pegel_db", "konfidenz", "datei"])
        for r in reversed(rows):
            w.writerow([r["id"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["mode"], r["kind"],
                        r["label"], "" if r["direction"] is None else round(r["direction"], 1),
                        "" if r["level"] is None else round(r["level"], 1),
                        "" if r["confidence"] is None else round(r["confidence"], 3), r["file"]])
        return buf.getvalue()


def push(topic: str, title: str, message: str, priority: int = 4):
    """Push-Nachricht über ntfy.sh (nur mit Internet). Läuft im Hintergrund."""
    if not topic:
        return

    def run():
        try:
            from urllib.parse import quote
            url = f"https://ntfy.sh/{quote(topic)}?title={quote(title)}&priority={priority}"
            req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
            urllib.request.urlopen(req, timeout=8).read()
        except Exception as e:
            log.warning("Push fehlgeschlagen: %s", e)
    threading.Thread(target=run, daemon=True).start()
