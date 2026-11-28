"""Weboberfläche: läuft auf dem Pi, erreichbar unter http://multitool.local (WLAN) bzw.
http://10.42.0.1 (Hotspot). Nur im lokalen Netz, mit Passwort."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import secrets
import threading
import time
import zipfile

from flask import (Flask, Response, abort, jsonify, redirect, request, send_file, send_from_directory, session)

from ..config import find_global_option

log = logging.getLogger("web")
STATIC = os.path.join(os.path.dirname(__file__), "static")


def _hash(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()


def create_app(app) -> Flask:
    web = Flask(__name__, static_folder=None)
    sec = app.cfg.group("secret")
    if not sec.get("session_key"):
        app.cfg.set("secret", "session_key", secrets.token_hex(24))
    web.secret_key = app.cfg.get("secret", "session_key")
    web.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    web.config["PERMANENT_SESSION_LIFETIME"] = 30 * 24 * 3600

    def has_password():
        return bool(app.cfg.get("secret", "pw_hash"))

    @web.before_request
    def guard():
        open_paths = ("/login", "/setup", "/api/login", "/api/setup", "/favicon.ico")
        if request.path in open_paths:
            return None
        if not has_password():
            return redirect("/setup") if not request.path.startswith("/api") else (jsonify(error="setup"), 401)
        if not session.get("ok"):
            return redirect("/login") if not request.path.startswith("/api") else (jsonify(error="login"), 401)
        return None

    # ---- Seiten ----
    @web.route("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @web.route("/login")
    def login_page():
        return send_from_directory(STATIC, "login.html")

    @web.route("/setup")
    def setup_page():
        if has_password():
            return redirect("/login")
        return send_from_directory(STATIC, "setup.html")

    @web.route("/favicon.ico")
    def favicon():
        return ("", 204)

    @web.post("/api/setup")
    def api_setup():
        if has_password():
            abort(403)
        pw = (request.json or {}).get("password", "")
        if len(pw) < 6:
            return jsonify(error="Mindestens 6 Zeichen"), 400
        salt = secrets.token_hex(8)
        app.cfg.set("secret", "pw_salt", salt)
        app.cfg.set("secret", "pw_hash", _hash(pw, salt))
        session.permanent = True
        session["ok"] = True
        return jsonify(ok=True)

    @web.post("/api/login")
    def api_login():
        pw = (request.json or {}).get("password", "")
        if has_password() and _hash(pw, app.cfg.get("secret", "pw_salt")) == app.cfg.get("secret", "pw_hash"):
            session.permanent = True
            session["ok"] = True
            return jsonify(ok=True)
        time.sleep(1)
        return jsonify(error="Falsches Passwort"), 403

    @web.post("/api/password")
    def api_password():
        pw = (request.json or {}).get("password", "")
        if len(pw) < 6:
            return jsonify(error="Mindestens 6 Zeichen"), 400
        salt = secrets.token_hex(8)
        app.cfg.set("secret", "pw_salt", salt)
        app.cfg.set("secret", "pw_hash", _hash(pw, salt))
        return jsonify(ok=True)

    # ---- Live ----
    @web.route("/stream.mjpg")
    def stream():
        def gen():
            last = -1
            while True:
                if app.frame_id != last:
                    last = app.frame_id
                    data = app.jpeg()
                    if data:
                        yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode() \
                            + b"\r\n\r\n" + data + b"\r\n"
                time.sleep(0.05)
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @web.route("/snapshot.jpg")
    def snapshot():
        return Response(app.jpeg(85), mimetype="image/jpeg")

    @web.route("/api/status")
    def api_status():
        return jsonify(app.status())

    @web.post("/api/mode")
    def api_mode():
        key = (request.json or {}).get("key")
        threading.Thread(target=app.switch_mode, args=(key,), daemon=True).start()
        return jsonify(ok=True)

    @web.route("/api/options")
    def api_options():
        m = app.mode
        return jsonify(mode=m.key, name=m.name, options=m.options_json(), globals=app.cfg.global_json())

    @web.post("/api/option")
    def api_option():
        j = request.json or {}
        try:
            if j.get("scope") == "mode":
                m = app.get_mode(j.get("mode")) or app.mode
                v = m.set_opt(j["key"], j["value"])
            else:
                group, key = j["group"], j["key"]
                opt = find_global_option(group, key)
                if opt is None:
                    abort(400)
                v = opt.coerce(j["value"])
                app.cfg.set(group, key, v)
            return jsonify(ok=True, value=v)
        except Exception as e:
            return jsonify(error=str(e)), 400

    @web.post("/api/action")
    def api_action():
        name = (request.json or {}).get("name")
        m = app.mode
        actions = {
            "record": m.on_record,
            "record_long": m.on_record_long,
            "siren_test": lambda: app.siren.sound(1.0),
            "siren_stop": app.siren.stop,
            "leds_test": lambda: app.leds.flash(1.0),
            "hotspot": lambda: threading.Thread(target=app.network.start_hotspot, daemon=True).start(),
            "wifi": lambda: threading.Thread(target=app.network.stop_hotspot, daemon=True).start(),
            "reboot": lambda: app.shutdown(reboot=True),
            "shutdown": app.shutdown,
            "sim_clap": lambda: app.simulator and app.simulator.clap(),
            "sim_drone": lambda: app.simulator and setattr(app.simulator, "drone_forced",
                                                            not app.simulator.drone_forced),
        }
        fn = actions.get(name)
        if fn is None:
            return jsonify(error="unbekannte Aktion"), 400
        threading.Thread(target=fn, daemon=True).start()
        return jsonify(ok=True)

    @web.post("/api/key")
    def api_key():
        """Tasten des Geräts über die Webseite drücken (rotate, ok, back, rec ...)."""
        j = request.json or {}
        ev = j.get("event")
        if ev == "enc":
            app.inputs.inject("enc", int(j.get("dir", 1)))
        elif ev in ("up", "down", "left", "right", "ok", "enc_press", "rec", "rec_long", "ok_long", "left_long"):
            app.inputs.inject(ev)
        return jsonify(ok=True)

    @web.post("/api/touch")
    def api_touch():
        j = request.json or {}
        app.mode.on_touch(float(j["x"]), float(j["y"]))
        return jsonify(ok=True)

    @web.post("/api/masks")
    def api_masks():
        m = app.get_mode("surveillance_cam")
        masks = (request.json or {}).get("masks", [])
        m.set_masks([[float(v) for v in r][:4] for r in masks])
        return jsonify(ok=True)

    @web.post("/api/wifi")
    def api_wifi():
        j = request.json or {}
        ok, out = app.network.add_wifi(j.get("ssid", ""), j.get("password", ""))
        return jsonify(ok=ok, message=out)

    # ---- Aufnahmen ----
    @web.route("/api/recordings")
    def api_recordings():
        return jsonify(app.storage.list(request.args.get("mode") or None))

    @web.route("/files/<path:rel>")
    def files(rel):
        try:
            p = app.storage.abs(rel)
        except ValueError:
            abort(404)
        if not os.path.isfile(p):
            abort(404)
        return send_file(p, as_attachment=request.args.get("dl") == "1", conditional=True)

    @web.delete("/api/recordings/<path:rel>")
    def api_delete(rel):
        try:
            app.storage.delete(rel)
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(error=str(e)), 400

    @web.post("/api/recordings.zip")
    def api_zip():
        paths = (request.json or {}).get("paths", [])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for rel in paths:
                try:
                    z.write(app.storage.abs(rel), rel)
                except Exception:
                    pass
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=time.strftime("multitool_%Y%m%d_%H%M.zip"))

    # ---- Ereignisse ----
    @web.route("/api/events")
    def api_events():
        return jsonify(app.events.query(limit=int(request.args.get("limit", 200)),
                                        mode=request.args.get("mode") or None))

    @web.route("/api/events.csv")
    def api_events_csv():
        data = app.events.csv(request.args.get("mode") or None)
        return Response("﻿" + data, mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=ereignisse.csv"})

    @web.route("/api/qr")
    def api_qr():
        """QR-Code fürs Hotspot-Login (PNG)."""
        try:
            import qrcode
            img = qrcode.make(app.network.hotspot_qr_text())
            b = io.BytesIO()
            img.save(b, "PNG")
            return Response(b.getvalue(), mimetype="image/png")
        except Exception:
            abort(404)

    return web


def start_web(app, port: int):
    web = create_app(app)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    def run():
        from werkzeug.serving import make_server
        try:
            srv = make_server("0.0.0.0", port, web, threaded=True)
        except OSError as e:
            log.error("Webseite: Port %d nicht verfügbar (%s)", port, e)
            return
        srv.serve_forever()
    threading.Thread(target=run, name="web", daemon=True).start()
