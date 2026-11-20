"""NIFTYBEES dashboard launcher.

Serves the built dashboard on http://localhost:4200 (replaces `python -m http.server`) and adds two
local-only endpoints so the page can start the backend with a button:

    GET  /launcher/status         -> {"backend_running": bool, "starting": bool, "pid": int|null}
    POST /launcher/start-backend  -> opens the backend (uvicorn on 127.0.0.1:8000) in its own console window

The backend window stays open after start (and after a crash, so the error is readable). Close that
window or press Ctrl+C in it to stop the backend. Closing this launcher does NOT stop the backend.

Run:  backend\\.venv\\Scripts\\python.exe launcher.py      (or double-click start.bat)
Standard library only.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
VENV_PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
DIST_DIR = ROOT / "frontend" / "dist" / "frontend" / "browser"

HOST, PORT = "127.0.0.1", 4200
BACKEND_HOST, BACKEND_PORT = "127.0.0.1", 8000
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
STARTUP_GRACE_SECONDS = 45  # report "starting" for this long after a start request

_proc: subprocess.Popen | None = None
_started_at = 0.0


def backend_running() -> bool:
    try:
        with socket.create_connection((BACKEND_HOST, BACKEND_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def start_backend() -> dict:
    global _proc, _started_at
    if backend_running():
        return {"ok": True, "message": "Backend is already running."}
    if _proc is not None and time.monotonic() - _started_at < STARTUP_GRACE_SECONDS:
        return {"ok": True, "message": "Backend is already starting..."}
    if not VENV_PYTHON.exists():
        return {"ok": False, "message": f"Python venv not found at {VENV_PYTHON}. Run the setup steps in README.md."}

    py = f'"{VENV_PYTHON}"' if " " in str(VENV_PYTHON) else str(VENV_PYTHON)
    # cmd /k keeps the window open if uvicorn exits, so a crash message stays readable.
    command = (f'cmd.exe /k "title NIFTYBEES Backend (close to stop) && '
               f'{py} -m uvicorn app.main:app --app-dir src --host {BACKEND_HOST} --port {BACKEND_PORT}"')
    _proc = subprocess.Popen(command, cwd=BACKEND_DIR, creationflags=subprocess.CREATE_NEW_CONSOLE)
    _started_at = time.monotonic()
    print(f"[launcher] backend start requested (window pid {_proc.pid})")
    return {"ok": True, "message": "Backend window opened - starting..."}


def status() -> dict:
    running = backend_running()
    starting = (not running and _proc is not None
                and time.monotonic() - _started_at < STARTUP_GRACE_SECONDS)
    return {"backend_running": running, "starting": starting, "pid": _proc.pid if _proc else None}


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
        ".json": "application/json", ".webmanifest": "application/manifest+json",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    }

    def _host_ok(self) -> bool:
        # Blocks DNS-rebinding: only answer requests addressed to localhost:4200 itself.
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def _send_json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/launcher/status":
            if not self._host_ok():
                return self._send_json(403, {"ok": False, "message": "Forbidden host"})
            return self._send_json(200, status())
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path.split("?")[0] != "/launcher/start-backend":
            return self._send_json(404, {"ok": False, "message": "Not found"})
        # Only the dashboard itself may start the backend - not some other website open in the browser.
        origin = self.headers.get("Origin", "")
        if not self._host_ok() or origin.removeprefix("http://") not in ALLOWED_HOSTS:
            return self._send_json(403, {"ok": False, "message": "Forbidden origin"})
        result = start_backend()
        return self._send_json(200 if result["ok"] else 500, result)

    def log_message(self, fmt: str, *args) -> None:
        if "/launcher/" in (args[0] if args else ""):
            return  # keep the console quiet: status is polled every second while starting
        super().log_message(fmt, *args)


def main() -> None:
    if not (DIST_DIR / "index.html").exists():
        sys.exit(f"Dashboard build not found at {DIST_DIR}. Run `npx ng build` in frontend/ first.")
    server = ThreadingHTTPServer((HOST, PORT), partial(Handler, directory=str(DIST_DIR)))
    print(f"[launcher] dashboard on http://localhost:{PORT}  (backend running: {backend_running()})")
    print("[launcher] Ctrl+C to stop the launcher (the backend keeps running in its own window).")
    if "--open" in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}")  # server socket is already bound, so the page loads
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
