"""NIFTYBEES launcher.

Serves the built dashboard on http://localhost:4200, starts/stops the backend, and gives the page a few
local-only endpoints (they work while the backend is down):

    GET  /launcher/status         -> {"backend_running", "starting", "pid", "app_mode"}
    GET  /launcher/setup-status   -> {"python_ok", "env_exists", "api_key_set", "api_secret_set", "api_key_hint", ...}
    GET  /launcher/backend-log    -> {"lines": [...]}  last lines of logs/backend.log (app mode)
    POST /launcher/env            -> {"api_key", "api_secret"} saved into backend/.env (created from .env.example)
    POST /launcher/start-backend  -> start the backend (uvicorn on 127.0.0.1:8000)
    POST /launcher/quit           -> stop the backend and this launcher

Two ways to run:
  * App mode  (`pythonw launcher.py --app`, what the "NIFTYBEES Algo" shortcut does): no windows at all.
    Starts the backend hidden (output -> logs/backend.log), opens the app window, keeps running in the
    background until Quit is clicked in the app. Running it again just reopens the app window.
  * Dev mode  (`start.bat` in the repo): console window; the backend opens in its own console window.

Layouts: release folder (UI in app/, Python in runtime/python) or source repo (frontend/dist, backend/.venv).
Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
ENV_FILE = BACKEND_DIR / ".env"
ENV_EXAMPLE = BACKEND_DIR / ".env.example"
LOG_DIR = ROOT / "logs"
BACKEND_LOG = LOG_DIR / "backend.log"
UI_DIRS = [ROOT / "app", ROOT / "frontend" / "dist" / "frontend" / "browser"]  # release first, then dev build
PYTHON_DIRS = [ROOT / "runtime" / "python", BACKEND_DIR / ".venv" / "Scripts"]

APP_MODE = "--app" in sys.argv


def _arg_int(name: str, default: int) -> int:
    if name in sys.argv:
        try:
            return int(sys.argv[sys.argv.index(name) + 1])
        except (IndexError, ValueError):
            sys.exit(f"Usage: launcher.py [--app] [--open] [--port N] [--backend-port N]")
    return default


# --port / --backend-port are for testing next to a running copy. The UI only talks to a backend on 8000,
# and the backend only accepts the UI from port 4200 - keep the defaults for real use.
HOST, PORT = "127.0.0.1", _arg_int("--port", 4200)
BACKEND_HOST, BACKEND_PORT = "127.0.0.1", _arg_int("--backend-port", 8000)
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
APP_URL = f"http://localhost:{PORT}"
STARTUP_GRACE_SECONDS = 45  # report "starting" for this long after a start request
MAX_BODY = 4096
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Kite Connect API keys/secrets are alphanumeric; reject anything else (also prevents .env injection).
KEY_RE = re.compile(r"^[A-Za-z0-9]{6,64}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9]{12,128}$")

_proc: subprocess.Popen | None = None
_started_at = 0.0
_server: ThreadingHTTPServer | None = None


def log(msg: str) -> None:
    print(f"[launcher {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_python(windowless: bool = False) -> Path | None:
    name = "pythonw.exe" if windowless else "python.exe"
    return next((d / name for d in PYTHON_DIRS if (d / name).exists()), None)


def find_ui() -> Path | None:
    return next((d for d in UI_DIRS if (d / "index.html").exists()), None)


def backend_running() -> bool:
    try:
        with socket.create_connection((BACKEND_HOST, BACKEND_PORT), timeout=0.5):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- .env handling
def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def _is_set(value: str | None) -> bool:
    return bool(value) and not value.lower().startswith("your_")


def keys_configured() -> bool:
    env = read_env()
    return _is_set(env.get("KITE_API_KEY")) and _is_set(env.get("KITE_API_SECRET"))


def setup_status() -> dict:
    env = read_env()
    key = env.get("KITE_API_KEY", "")
    return {
        "python_ok": find_python() is not None,
        "env_exists": ENV_FILE.exists(),
        "api_key_set": _is_set(key),
        "api_secret_set": _is_set(env.get("KITE_API_SECRET")),
        "api_key_hint": f"{key[:3]}…{key[-2:]}" if _is_set(key) and len(key) > 6 else None,  # never the full key
        "mode": "PAPER" if env.get("DRY_RUN", "true").lower() in {"1", "true", "yes", "on"} else "LIVE",
        "backend_running": backend_running(),
        "app_mode": APP_MODE,
        "redirect_url": f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/auth/callback",
    }


def save_keys(api_key: str, api_secret: str) -> dict:
    api_key, api_secret = (api_key or "").strip(), (api_secret or "").strip()
    if not KEY_RE.match(api_key):
        return {"ok": False, "message": "API key looks wrong - it should be letters and digits only (from your Kite app page)."}
    if not SECRET_RE.match(api_secret):
        return {"ok": False, "message": "API secret looks wrong - it should be letters and digits only (from your Kite app page)."}
    if not ENV_FILE.exists():
        if not ENV_EXAMPLE.exists():
            return {"ok": False, "message": f"{ENV_EXAMPLE} is missing - the app folder looks incomplete."}
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    wanted = {"KITE_API_KEY": api_key, "KITE_API_SECRET": api_secret}
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if k in wanted and not line.lstrip().startswith("#"):
            lines[i] = f"{k}={wanted.pop(k)}"
    lines += [f"{k}={v}" for k, v in wanted.items()]  # keys that weren't in the file yet
    tmp = ENV_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(ENV_FILE)
    log("Kite API key/secret saved to backend/.env")
    restart = backend_running()
    return {"ok": True, "restart_needed": restart,
            "message": ("Saved. Restart the backend to use the new keys (Quit the app and open it again)."
                        if restart else "Saved.")}


# ---------------------------------------------------------------- backend process
def start_backend() -> dict:
    global _proc, _started_at
    if backend_running():
        return {"ok": True, "message": "Backend is already running."}
    if _proc is not None and _proc.poll() is None and time.monotonic() - _started_at < STARTUP_GRACE_SECONDS:
        return {"ok": True, "message": "Backend is already starting..."}
    python = find_python()
    if not python:
        return {"ok": False, "message": "Python is not set up - run setup.bat in the app folder first."}
    args = [str(python), "-m", "uvicorn", "app.main:app", "--app-dir", "src",
            "--host", BACKEND_HOST, "--port", str(BACKEND_PORT)]
    if APP_MODE:
        # Hidden: no console window; output goes to logs/backend.log (shown in the app if the start fails).
        LOG_DIR.mkdir(exist_ok=True)
        out = open(BACKEND_LOG, "a", encoding="utf-8", buffering=1)
        out.write(f"\n===== backend start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        _proc = subprocess.Popen(args, cwd=BACKEND_DIR, stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        out.close()  # the child keeps its own handle
    else:
        # Dev: own console window; cmd /k keeps it open after a crash so the error stays readable.
        cmd = subprocess.list2cmdline(args)
        _proc = subprocess.Popen(f'cmd.exe /k "title NIFTYBEES Backend (close to stop) && {cmd}"',
                                 cwd=BACKEND_DIR, creationflags=subprocess.CREATE_NEW_CONSOLE)
    _started_at = time.monotonic()
    log(f"backend start requested (pid {_proc.pid})")
    return {"ok": True, "message": "Starting the backend..."}


def _backend_pid_on_port() -> int | None:
    """PID of the python process listening on the backend port (also finds a backend we didn't start)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                             creationflags=NO_WINDOW, timeout=10).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        # A listening socket has remote address 0.0.0.0:0 / [::]:0 (the word LISTENING is translated on
        # non-English Windows, so don't rely on it).
        if len(parts) >= 5 and parts[1].endswith(f":{BACKEND_PORT}") and parts[2] in ("0.0.0.0:0", "[::]:0"):
            pid = int(parts[4])
            try:
                info = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                      text=True, creationflags=NO_WINDOW, timeout=10).stdout.lower()
            except Exception:
                return None
            return pid if "python" in info else None  # never kill something that isn't our Python backend
    return None


def stop_backend() -> str:
    pid = _backend_pid_on_port()
    if pid is None and _proc is not None and _proc.poll() is None:
        pid = _proc.pid
    if pid is None:
        return "Backend was not running."
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(20):
        if not backend_running():
            break
        time.sleep(0.25)
    log(f"backend stopped (pid {pid})")
    return "Backend stopped."


def quit_all() -> None:
    """Stop the backend, then this launcher (after the HTTP reply has been sent)."""
    def _later() -> None:
        time.sleep(0.5)
        stop_backend()
        log("launcher quitting")
        if _server:
            _server.shutdown()
    threading.Thread(target=_later, daemon=True).start()


def status() -> dict:
    running = backend_running()
    starting = (not running and _proc is not None and _proc.poll() is None
                and time.monotonic() - _started_at < STARTUP_GRACE_SECONDS)
    return {"backend_running": running, "starting": starting, "pid": _proc.pid if _proc else None,
            "app_mode": APP_MODE}


def backend_log_tail(n: int = 25) -> list[str]:
    if not BACKEND_LOG.exists():
        return []
    lines = BACKEND_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


# ---------------------------------------------------------------- app window
def open_app_window() -> None:
    """Open the app in its own window (Edge/Chrome app mode); fall back to the default browser."""
    if "--no-window" in sys.argv:  # automated tests
        return
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    browser = next((p for p in candidates if p.exists()), None)
    if browser:
        subprocess.Popen([str(browser), f"--app={APP_URL}"], creationflags=NO_WINDOW)
    else:
        webbrowser.open(APP_URL)


def already_running() -> bool:
    """Another launcher already serves the app on this port (e.g. shortcut clicked twice)."""
    try:
        with urllib.request.urlopen(f"{APP_URL}/launcher/status", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------- HTTP
class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
        ".json": "application/json", ".webmanifest": "application/manifest+json",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    }

    def _host_ok(self) -> bool:
        # Blocks DNS-rebinding: only answer requests addressed to localhost:<port> itself.
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def _origin_ok(self) -> bool:
        # Only the dashboard itself may call POST endpoints - not some other website open in the browser.
        return self._host_ok() and self.headers.get("Origin", "").removeprefix("http://") in ALLOWED_HOSTS

    def _send_json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                return None
            body = json.loads(self.rfile.read(length))
            return body if isinstance(body, dict) else None
        except (ValueError, json.JSONDecodeError):
            return None

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path.startswith("/launcher/"):
            if not self._host_ok():
                return self._send_json(403, {"ok": False, "message": "Forbidden host"})
            if path == "/launcher/status":
                return self._send_json(200, status())
            if path == "/launcher/setup-status":
                return self._send_json(200, setup_status())
            if path == "/launcher/backend-log":
                return self._send_json(200, {"lines": backend_log_tail()})
            return self._send_json(404, {"ok": False, "message": "Not found"})
        return super().do_GET()

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path not in ("/launcher/start-backend", "/launcher/env", "/launcher/quit"):
            return self._send_json(404, {"ok": False, "message": "Not found"})
        if not self._origin_ok():
            return self._send_json(403, {"ok": False, "message": "Forbidden origin"})
        if path == "/launcher/start-backend":
            result = start_backend()
            return self._send_json(200 if result["ok"] else 500, result)
        if path == "/launcher/quit":
            quit_all()
            return self._send_json(200, {"ok": True, "message": "Stopping NIFTYBEES Algo..."})
        body = self._read_json()
        if body is None:
            return self._send_json(400, {"ok": False, "message": "Bad request"})
        result = save_keys(str(body.get("api_key", "")), str(body.get("api_secret", "")))
        return self._send_json(200 if result["ok"] else 400, result)

    def log_message(self, fmt: str, *args) -> None:
        if APP_MODE or "/launcher/" in (args[0] if args else ""):
            return  # app mode: no request log; dev: hide the 1-second status polling
        super().log_message(fmt, *args)


def main() -> None:
    global _server
    if APP_MODE and (sys.stdout is None or sys.stderr is None):
        # pythonw.exe has no console: send our messages to logs/launcher.log instead.
        LOG_DIR.mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(LOG_DIR / "launcher.log", "a", encoding="utf-8", buffering=1)

    if already_running():  # e.g. the shortcut was clicked again - just show the app
        open_app_window()
        return
    ui = find_ui()
    if not ui:
        sys.exit("Dashboard files not found (app\\ or frontend\\dist). In the repo, run `npm run build` in frontend\\.")
    _server = ThreadingHTTPServer((HOST, PORT), partial(Handler, directory=str(ui)))
    log(f"NIFTYBEES Algo on {APP_URL} | ui {ui} | python {find_python() or 'NOT SET UP'} | "
        f"backend running: {backend_running()} | mode: {'app (hidden)' if APP_MODE else 'dev'}")

    if APP_MODE:
        if keys_configured() and not backend_running():
            start_backend()  # first run without keys: the app's setup dialog asks for them, then starts it
        open_app_window()
    else:
        log("Keep this window open while you use the app. Ctrl+C stops the launcher "
            "(the backend keeps running in its own window).")
        if "--open" in sys.argv:
            webbrowser.open(APP_URL)  # server socket is already bound, so the page loads
    try:
        _server.serve_forever()
    except KeyboardInterrupt:
        pass
    log("launcher stopped")


if __name__ == "__main__":
    main()
