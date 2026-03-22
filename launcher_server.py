#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR / "backend"
DOCTOR_APP_DIR = APP_DIR / "doctor_app"
OPS_CONSOLE_DIR = APP_DIR / "ops_console"


def _http_healthz(url: str, timeout: float = 1.5) -> bool:
    try:
        req = urllib.request.Request(url=url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return bool(body.get("status") == "ok")
    except Exception:
        return False


class BackendController:
    def __init__(self, host: str, port: int, *, manage_process: bool = True) -> None:
        self._lock = threading.Lock()
        self.host = host
        self.port = int(port)
        self.manage_process = bool(manage_process)
        self.model_dir: str | None = None
        self.device: str | None = None
        self._proc: subprocess.Popen[str] | None = None
        self.last_error: str | None = None

    @property
    def backend_url(self) -> str:
        probe_host = self.host
        if probe_host in {"0.0.0.0", "::", "[::]"}:
            probe_host = "127.0.0.1"
        return f"http://{probe_host}:{self.port}"

    def _is_running_unlocked(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _command(self) -> list[str]:
        cmd = [
            sys.executable,
            "scripts/run_backend.py",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--allowed-origins",
            "*",
        ]
        if self.model_dir:
            cmd.extend(["--model-dir", self.model_dir])
        if self.device:
            cmd.extend(["--default-device", self.device])
        return cmd

    def start(self, payload: dict) -> dict:
        if not self.manage_process:
            ready = _http_healthz(f"{self.backend_url}/healthz")
            return {
                "ok": False,
                "code": 403,
                "running": ready,
                "ready": ready,
                "backend_url": self.backend_url,
                "message": "process control disabled; manage backend with run_server.py/systemd/docker",
            }

        with self._lock:
            if payload.get("host"):
                self.host = str(payload["host"]).strip()
            if payload.get("port"):
                self.port = int(payload["port"])
            self.model_dir = str(payload.get("model_dir", "")).strip() or None
            self.device = str(payload.get("device", "")).strip() or None

            if self._is_running_unlocked():
                ready = _http_healthz(f"{self.backend_url}/healthz")
                return {
                    "ok": True,
                    "running": True,
                    "ready": ready,
                    "backend_url": self.backend_url,
                    "message": "backend already running",
                }

            self.last_error = None
            self._proc = subprocess.Popen(self._command(), cwd=str(BACKEND_DIR))

        ready = self.wait_ready(timeout_s=30.0)
        if ready:
            return {
                "ok": True,
                "running": True,
                "ready": True,
                "backend_url": self.backend_url,
                "message": "backend started",
            }

        with self._lock:
            code = self._proc.poll() if self._proc is not None else None
            self.last_error = f"backend not ready in time; exit_code={code}"
        return {
            "ok": False,
            "running": self.is_running(),
            "ready": False,
            "backend_url": self.backend_url,
            "message": self.last_error,
        }

    def wait_ready(self, timeout_s: float = 20.0) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.is_ready():
                return True
            if not self.is_running():
                return False
            time.sleep(0.25)
        return False

    def stop(self) -> dict:
        if not self.manage_process:
            ready = _http_healthz(f"{self.backend_url}/healthz")
            return {
                "ok": False,
                "code": 403,
                "running": ready,
                "ready": ready,
                "backend_url": self.backend_url,
                "message": "process control disabled; stop backend in service manager",
            }

        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._proc = None
                return {"ok": True, "running": False, "message": "backend already stopped"}
            proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        with self._lock:
            self._proc = None
        return {"ok": True, "running": False, "message": "backend stopped"}

    def is_running(self) -> bool:
        if not self.manage_process:
            return self.is_ready()
        with self._lock:
            return self._is_running_unlocked()

    def is_ready(self) -> bool:
        if not self.manage_process:
            return _http_healthz(f"{self.backend_url}/healthz")
        if not self.is_running():
            return False
        return _http_healthz(f"{self.backend_url}/healthz")

    def status(self) -> dict:
        if not self.manage_process:
            ready = self.is_ready()
            return {
                "running": ready,
                "ready": ready,
                "pid": None,
                "backend_url": self.backend_url,
                "host": self.host,
                "port": self.port,
                "model_dir": self.model_dir,
                "device": self.device,
                "last_error": self.last_error,
                "process_control": False,
                "managed_by_launcher": False,
            }

        with self._lock:
            pid = self._proc.pid if self._proc is not None and self._proc.poll() is None else None
        return {
            "running": self.is_running(),
            "ready": self.is_ready(),
            "pid": pid,
            "backend_url": self.backend_url,
            "host": self.host,
            "port": self.port,
            "model_dir": self.model_dir,
            "device": self.device,
            "last_error": self.last_error,
            "process_control": True,
            "managed_by_launcher": True,
        }


class LauncherHandler(BaseHTTPRequestHandler):
    controller: BackendController | None = None

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(self, location: str, status: int = HTTPStatus.FOUND) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        mime, _ = mimetypes.guess_type(str(path))
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (mime or "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json_body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0:
            return {}
        raw = self.rfile.read(size)
        return json.loads(raw.decode("utf-8"))

    def _is_backend_proxy_path(self, path: str) -> bool:
        return path == "/api" or path.startswith("/api/")

    def _build_backend_target_url(self) -> str:
        parsed = urllib.parse.urlsplit(self.path)
        backend_path = parsed.path[4:] if len(parsed.path) >= 4 else parsed.path
        if not backend_path:
            backend_path = "/"
        if not backend_path.startswith("/"):
            backend_path = f"/{backend_path}"
        target = f"{self.controller.backend_url}{backend_path}"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        return target

    def _proxy_to_backend(self, method: str) -> None:
        target_url = self._build_backend_target_url()
        body: bytes | None = None
        if method in {"POST", "PUT", "PATCH"}:
            size = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(size) if size > 0 else b""

        headers: dict[str, str] = {}
        # Pass through essential client headers so backend auth/session checks work
        # when doctor/ops UIs access backend via same-origin /api proxy.
        for header_name in ("Content-Type", "Accept", "Authorization"):
            value = self.headers.get(header_name)
            if value:
                headers[header_name] = value

        try:
            req = urllib.request.Request(url=target_url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
                self.send_response(resp.status)
                for header_name in ("Content-Type", "Cache-Control"):
                    value = resp.headers.get(header_name)
                    if value:
                        self.send_header(header_name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)
            return
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            content_type = exc.headers.get("Content-Type") if exc.headers else None
            self.send_header("Content-Type", content_type or "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)
            return
        except Exception as exc:
            self._send_json({"ok": False, "message": f"backend proxy error: {exc}"}, status=502)
            return

    def _safe_static_path(self, requested: str, root_dir: Path) -> Path | None:
        candidate = (root_dir / requested).resolve()
        root = root_dir.resolve()
        if not str(candidate).startswith(str(root)):
            return None
        return candidate

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/"}:
            self._send_redirect("/clinical")
            return
        if path in {"/clinical", "/clinical/", "/app", "/app/"}:
            self._send_file(DOCTOR_APP_DIR / "index.html")
            return
        if path in {"/launcher", "/launcher/"}:
            self._send_redirect("/ops/dashboard")
            return
        if path in {"/ops/dashboard", "/ops/dashboard/"}:
            self._send_file(OPS_CONSOLE_DIR / "dashboard.html")
            return
        if path in {"/ops/launcher", "/ops/launcher/"}:
            self._send_redirect("/ops/dashboard")
            return
        if path == "/_launcher/status":
            self._send_json(self.controller.status())
            return
        if self._is_backend_proxy_path(path):
            self._proxy_to_backend("GET")
            return

        if path.startswith("/doctor-static/"):
            rel = path[len("/doctor-static/") :]
            safe = self._safe_static_path(rel, DOCTOR_APP_DIR)
            if safe is None:
                self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                return
            self._send_file(safe)
            return

        if path.startswith("/ops-static/"):
            rel = path[len("/ops-static/") :]
            safe = self._safe_static_path(rel, OPS_CONSOLE_DIR)
            if safe is None:
                self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                return
            self._send_file(safe)
            return

        rel = path.lstrip("/")
        safe = self._safe_static_path(rel, DOCTOR_APP_DIR)
        if safe is not None and safe.exists() and safe.is_file():
            self._send_file(safe)
            return

        safe_ops = self._safe_static_path(rel, OPS_CONSOLE_DIR)
        if safe_ops is not None and safe_ops.exists() and safe_ops.is_file():
            self._send_file(safe_ops)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if self._is_backend_proxy_path(path):
            self._proxy_to_backend("POST")
            return

        try:
            payload = self._read_json_body()
        except Exception as exc:
            self._send_json({"ok": False, "message": f"invalid json: {exc}"}, status=400)
            return

        if path == "/_launcher/start-backend":
            result = self.controller.start(payload)
            code = int(result.get("code", 500 if not result.get("ok") else 200))
            self._send_json(result, status=code)
            return
        if path == "/_launcher/stop-backend":
            result = self.controller.stop()
            code = int(result.get("code", 500 if not result.get("ok") else 200))
            self._send_json(result, status=code)
            return
        self._send_json({"ok": False, "message": "unknown endpoint"}, status=404)

    def log_message(self, fmt: str, *args) -> None:
        return


def run_backend_only(host: str, port: int) -> int:
    cmd = [
        sys.executable,
        "scripts/run_backend.py",
        "--host",
        host,
        "--port",
        str(port),
    ]
    print(f"[run] backend: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(BACKEND_DIR), check=False)
    return int(proc.returncode)


def run_launcher(host: str, port: int, backend_host: str, backend_port: int) -> int:
    controller = BackendController(host=backend_host, port=backend_port)

    class _Handler(LauncherHandler):
        pass

    _Handler.controller = controller
    server = ThreadingHTTPServer((host, int(port)), _Handler)

    print(f"[launcher] url=http://{host}:{port}")
    print(f"[launcher] clinical=http://{host}:{port}/clinical")
    print(f"[launcher] ops_dashboard=http://{host}:{port}/ops/dashboard")
    print(f"[launcher] ops_launcher_legacy=http://{host}:{port}/ops/launcher -> /ops/dashboard")
    print("[launcher] press Ctrl+C to exit")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.stop()
    return 0


def run_ops_dashboard(
    host: str,
    port: int,
    backend_host: str,
    backend_port: int,
    *,
    allow_process_control: bool = False,
) -> int:
    """Run ops-only dashboard UI (no clinical UI/static routes)."""
    controller = BackendController(
        host=backend_host,
        port=backend_port,
        manage_process=allow_process_control,
    )

    class _OpsHandler(LauncherHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/", "/ops", "/ops/"}:
                self._send_redirect("/ops/dashboard")
                return
            if path in {"/ops/launcher", "/ops/launcher/"}:
                self._send_redirect("/ops/dashboard")
                return
            if path in {"/ops/dashboard", "/ops/dashboard/"}:
                self._send_file(OPS_CONSOLE_DIR / "dashboard.html")
                return
            if path == "/_launcher/status":
                self._send_json(self.controller.status())
                return
            if self._is_backend_proxy_path(path):
                self._proxy_to_backend("GET")
                return
            if path.startswith("/ops-static/"):
                rel = path[len("/ops-static/") :]
                safe = self._safe_static_path(rel, OPS_CONSOLE_DIR)
                if safe is None:
                    self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                    return
                self._send_file(safe)
                return

            # Explicitly hide clinical routes in ops-only mode.
            if path.startswith("/clinical") or path.startswith("/doctor-static/") or path in {"/app", "/app/"}:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return

            rel = path.lstrip("/")
            safe_ops = self._safe_static_path(rel, OPS_CONSOLE_DIR)
            if safe_ops is not None and safe_ops.exists() and safe_ops.is_file():
                self._send_file(safe_ops)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    _OpsHandler.controller = controller
    server = ThreadingHTTPServer((host, int(port)), _OpsHandler)

    print(f"[ops] url=http://{host}:{port}")
    print(f"[ops] dashboard=http://{host}:{port}/ops/dashboard")
    print(f"[ops] launcher_legacy=http://{host}:{port}/ops/launcher -> /ops/dashboard")
    print(f"[ops] process_control={'enabled' if allow_process_control else 'disabled'}")
    print("[ops] press Ctrl+C to exit")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.stop()
    return 0


def run_ops_launcher(
    host: str,
    port: int,
    backend_host: str,
    backend_port: int,
    *,
    allow_process_control: bool = False,
) -> int:
    """Backward-compatible alias. Prefer `run_ops_dashboard`."""
    return run_ops_dashboard(
        host=host,
        port=port,
        backend_host=backend_host,
        backend_port=backend_port,
        allow_process_control=allow_process_control,
    )


def run_doctor_app(host: str, port: int, backend_host: str, backend_port: int) -> int:
    """Run doctor-only UI (no ops launcher or backend start/stop controls)."""
    controller = BackendController(host=backend_host, port=backend_port)

    class _DoctorHandler(LauncherHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/"}:
                self._send_redirect("/clinical")
                return
            if path in {"/doctor", "/doctor/", "/clinical", "/clinical/", "/app", "/app/"}:
                self._send_file(DOCTOR_APP_DIR / "index.html")
                return
            if self._is_backend_proxy_path(path):
                self._proxy_to_backend("GET")
                return
            if path.startswith("/doctor-static/"):
                rel = path[len("/doctor-static/") :]
                safe = self._safe_static_path(rel, DOCTOR_APP_DIR)
                if safe is None:
                    self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                    return
                self._send_file(safe)
                return

            # Explicitly hide ops and launcher routes in doctor-only mode.
            if (
                path.startswith("/ops")
                or path.startswith("/ops-static/")
                or path.startswith("/launcher")
                or path.startswith("/_launcher/")
                or path == "/_launcher"
            ):
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return

            rel = path.lstrip("/")
            safe = self._safe_static_path(rel, DOCTOR_APP_DIR)
            if safe is not None and safe.exists() and safe.is_file():
                self._send_file(safe)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if self._is_backend_proxy_path(path):
                self._proxy_to_backend("POST")
                return
            self._send_json({"ok": False, "message": "unknown endpoint"}, status=404)

    _DoctorHandler.controller = controller
    server = ThreadingHTTPServer((host, int(port)), _DoctorHandler)

    print(f"[doctor] url=http://{host}:{port}")
    print(f"[doctor] clinical=http://{host}:{port}/clinical")
    print("[doctor] press Ctrl+C to exit")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.stop()
    return 0
