"""Local-only browser UI. No dependencies, remote UI assets or stored credentials."""
from __future__ import annotations

import argparse
import csv
import hmac
import io
import json
import os
import queue
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from core import (CONFIRM_URLS, POTENTIAL, PLATFORM_NAMES, SPEEDS, DISCORD_SPEEDS, UBISOFT_SPEEDS,
                  candidates, make_alphabet, webhook_url)
from ubisoft import session_headers

BASE = Path(__file__).resolve().parent
THEMES = {"iris", "lagoon", "ember", "rose", "glacier"}


def candidate_options(payload):
    platform = payload.get("platform", "minecraft")
    if platform not in PLATFORM_NAMES:
        raise ValueError("Choose a platform.")
    def enabled(key, default=False):
        value = payload.get(key, default)
        if type(value) is not bool:
            raise ValueError("Invalid character settings.")
        return value
    try:
        count, length = int(payload.get("count", 100)), int(payload.get("length", 4))
    except (ValueError, TypeError):
        raise ValueError("Use a whole number for batch size and length.") from None
    return dict(count=count, length=length, platform=platform,
                alphabet=make_alphabet(enabled("letters", True), enabled("numbers", True),
                    enabled("underscores", True), enabled("periods") and platform != "minecraft",
                    enabled("dashes") and platform == "ubisoft"),
                pattern=str(payload.get("pattern", "")), supplied=str(payload.get("supplied", "")),
                source=payload.get("source", "random"))


class Controller:
    def __init__(self, scanner, preferences_path=None):
        self.scanner = scanner
        self.preferences_path = preferences_path or BASE / "data" / "preferences.json"
        self.lock = threading.RLock()
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.rows = []
        self.run = 0
        self.platform = "minecraft"
        self.checked = self.found = self.total = 0
        self.message = "Ready"
        self.error = ""
        self.last_access = time.monotonic()
        self.preferences = {"theme": "iris", "motion": True}
        self.downloads = {}
        try:
            saved = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                if isinstance(saved.get("theme"), str) and saved["theme"] in THEMES:
                    self.preferences["theme"] = saved["theme"]
                if type(saved.get("motion")) is bool:
                    self.preferences["motion"] = saved["motion"]
        except (OSError, ValueError):
            pass

    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def drain(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "result":
                self.rows.append(value)
            elif kind == "counts":
                self.checked, self.found, self.total = value
            elif kind == "error":
                self.error = self.message = value
            elif kind in {"message", "done"}:
                self.message = value

    def snapshot(self, after=0, run=-1):
        with self.lock:
            self.last_access = time.monotonic()
            self.drain()
            after = max(0, min(after, len(self.rows))) if run == self.run else 0
            return dict(run=self.run, platform=self.platform, running=self.running(),
                        checked=self.checked, found=self.found, total=self.total,
                        message=self.message, error=self.error, rows=self.rows[after:],
                        offset=after, preferences=self.preferences.copy(),
                        has_env_webhook=bool(os.environ.get("DISCORD_WEBHOOK_URL")))

    def start(self, payload):
        with self.lock:
            if self.running():
                raise ValueError("A search is already running.")
            options = candidate_options(payload)
            names = candidates(**options)
            platform = options["platform"]
            speeds = {"minecraft": SPEEDS, "discord": DISCORD_SPEEDS, "ubisoft": UBISOFT_SPEEDS}[platform]
            if payload.get("speed", "Fast") not in speeds:
                raise ValueError("Choose a speed.")
            workers, delay = speeds[payload.get("speed", "Fast")]
            raw_webhook = str(payload.get("webhook", ""))
            if not raw_webhook.strip() and payload.get("use_env_webhook") is True:
                raw_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
            webhook = webhook_url(raw_webhook)
            ticket = str(payload.get("ticket", "")) if platform == "ubisoft" else ""
            session_id = str(payload.get("session_id", "")) if platform == "ubisoft" else ""
            if platform == "ubisoft":
                session_headers(ticket, session_id)
            self.drain()
            self.rows.clear()
            self.run += 1
            self.checked = self.found = 0
            self.total = len(names)
            self.platform = platform
            self.error = ""
            self.message = "Searching…"
            self.stop.clear()
            self.worker = threading.Thread(target=self.scanner,
                args=(names, delay, webhook, self.stop, self.events, workers, platform, ticket, session_id),
                daemon=True, name="name-finder-scan")
            self.worker.start()
            return self.snapshot()

    def preview(self, payload):
        options = candidate_options(payload)
        names = candidates(**options)
        return {"names": names[:6], "count": len(names)}

    def save_preferences(self, payload):
        if set(payload) != {"theme", "motion"} or payload["theme"] not in THEMES or type(payload["motion"]) is not bool:
            raise ValueError("Invalid appearance settings.")
        with self.lock:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.preferences_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(self.preferences_path)
            self.preferences = payload.copy()
        return self.preferences

    def prepare_export(self, potential_only=False):
        with self.lock:
            self.drain()
            rows = [row for row in self.rows if not potential_only or row[1] == POTENTIAL]
            if not rows:
                raise ValueError("No matching results to export.")
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(["platform", "username", "lookup_status", "webhook_or_details", "confirm_url"])
            writer.writerows([self.platform, *row, CONFIRM_URLS[self.platform]] for row in rows)
            key = secrets.token_urlsafe(32)
            now = time.monotonic()
            self.downloads = {k: v for k, v in self.downloads.items() if v[0] > now}
            self.downloads[key] = (now + 60, f"{self.platform}_names.csv", buffer.getvalue().encode("utf-8-sig"))
            return {"url": "/download/" + key, "count": len(rows)}

    def take_download(self, key):
        with self.lock:
            item = self.downloads.pop(key, None)
            return item if item and item[0] > time.monotonic() else None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    connection_timeout = 10
    max_active_requests = 16
    def __init__(self, controller, port=0):
        self.request_slots = threading.BoundedSemaphore(self.max_active_requests)
        super().__init__(("127.0.0.1", port), Handler)
        self.controller = controller
        self.token = secrets.token_urlsafe(32)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.url = self.origin + "/#" + self.token

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.connection_timeout)
        return connection, address

    def process_request(self, request, client_address):
        # Even unauthenticated local clients must not create unlimited threads.
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # No request URLs, bodies, tokens or credentials in logs.

    def respond(self, status, body, content_type="application/json", filename=None):
        data = json.dumps(body).encode() if content_type == "application/json" else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, authenticated=False):
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self.respond(403, {"error": "Invalid host."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self.respond(403, {"error": "Invalid origin."})
            return False
        supplied_token = self.headers.get("X-Name-Finder", "")
        if authenticated and (not supplied_token.isascii() or not hmac.compare_digest(supplied_token, self.server.token)):
            self.respond(403, {"error": "Reopen the app with start.bat to reconnect."})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path)
        if not self.allowed(path.path.startswith("/api/")):
            return
        if path.path.startswith("/download/"):
            item = self.server.controller.take_download(path.path.removeprefix("/download/"))
            if item:
                return self.respond(200, item[2], "text/csv; charset=utf-8", filename=item[1])
            return self.respond(404, {"error": "Download expired. Export again from Results."})
        if path.path == "/api/state":
            try:
                query = parse_qs(path.query)
                after = int(query.get("after", [0])[0])
                run = int(query.get("run", [-1])[0])
            except ValueError:
                return self.respond(400, {"error": "Invalid result cursor."})
            return self.respond(200, self.server.controller.snapshot(after, run))
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8"),
                 "/ui.js": ("ui.js", "text/javascript; charset=utf-8"),
                 "/icon.svg": ("icon.svg", "image/svg+xml")}
        if path.path not in files:
            return self.respond(404, {"error": "Not found."})
        name, mime = files[path.path]
        try:
            self.respond(200, (BASE / "ui" / name).read_bytes(), mime)
        except OSError:
            self.respond(500, {"error": "UI files missing. Extract the whole ZIP."})

    def do_POST(self):
        if not self.allowed(authenticated=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                return self.respond(413, {"error": "Request is too large."})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond(415, {"error": "JSON is required."})
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Invalid request.")
            control = self.server.controller
            control.last_access = time.monotonic()
            path = urlsplit(self.path).path
            if path == "/api/start":
                result = control.start(payload)
            elif path == "/api/preview":
                result = control.preview(payload)
            elif path == "/api/stop":
                control.stop.set()
                result = {"ok": True}
            elif path == "/api/preferences":
                result = control.save_preferences(payload)
            elif path == "/api/export":
                result = control.prepare_export(payload.get("potential_only") is True)
            elif path == "/api/quit":
                control.stop.set()
                result = {"ok": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                return self.respond(404, {"error": "Not found."})
            self.respond(200, result)
        except (ValueError, TypeError, KeyError) as error:
            # Only our ValueErrors contain user-facing text, never HTTP/auth exceptions.
            message = str(error) if type(error) is ValueError else "Invalid settings."
            self.respond(400, {"error": message})
        except Exception:
            self.respond(500, {"error": "Could not complete the request. Check the app folder and try again."})


def open_window(url):
    if os.name == "nt":
        roots = [os.environ.get(key, "") for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            for root in roots:
                path = Path(root) / relative
                if root and path.is_file():
                    subprocess.Popen([str(path), "--app=" + url, "--window-size=1180,860"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
    webbrowser.open(url)


def launch(scanner):
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    controller = Controller(scanner)
    with Server(controller, args.port) as server:
        if args.no_browser:
            print(server.url, flush=True)
        else:
            open_window(server.url)
        def idle_shutdown():
            while True:
                time.sleep(30)
                if time.monotonic() - controller.last_access > 300:
                    controller.stop.set()
                    server.shutdown()
                    return
        threading.Thread(target=idle_shutdown, daemon=True).start()
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            controller.stop.set()
