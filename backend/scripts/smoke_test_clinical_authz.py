#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from smoke_test_inference_api import BACKEND_DIR, get_free_port, wait_until_ready

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    token: str | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url=url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return int(resp.getcode()), json.loads(raw)
            except json.JSONDecodeError:
                return int(resp.getcode()), {"raw": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return int(exc.code), parsed


def _expect_status(label: str, status: int, expected: int, payload: dict) -> None:
    if int(status) != int(expected):
        raise AssertionError(
            f"{label}: expected HTTP {expected}, got {status}, payload={payload}"
        )
    print(f"[ok] {label} -> HTTP {expected}")


def _login(base_url: str, username: str, password: str) -> str:
    status, payload = _http_json(
        f"{base_url}/v1/auth/login",
        method="POST",
        payload={"username": username, "password": password},
        timeout=12.0,
    )
    _expect_status(f"/v1/auth/login ({username})", status, 200, payload)
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise AssertionError(f"login succeeded but access_token missing: payload={payload}")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clinical authz smoke test (401/403/cross-patient-400)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 means auto")
    parser.add_argument("--startup-timeout", type=float, default=45.0)
    args = parser.parse_args()

    host = args.host
    port = args.port if args.port > 0 else get_free_port(host)
    base_url = f"http://{host}:{port}"

    db_fd, db_path_raw = tempfile.mkstemp(prefix="myopia_clinical_authz_smoke_", suffix=".db")
    os.close(db_fd)
    db_path = Path(db_path_raw)
    storage_dir = Path(tempfile.mkdtemp(prefix="myopia_clinical_authz_storage_"))
    database_url = f"sqlite+pysqlite:///{db_path}"

    from myopia_backend.db import models as _models  # noqa: F401
    from myopia_backend.db.base import Base
    from myopia_backend.db.models import User
    from myopia_backend.db.session import create_engine_from_url, session_scope
    from myopia_backend.security.auth import hash_password

    engine = create_engine_from_url(database_url)
    Base.metadata.create_all(engine)

    doctor_username = "doctor_authz"
    ops_username = "ops_authz"
    doctor_password = "DoctorAuthz123!"
    ops_password = "OpsAuthz123!"

    with session_scope(database_url=database_url) as session:
        session.add(
            User(
                username=doctor_username,
                display_name="Doctor Authz",
                role="doctor",
                is_active=True,
                password_hash=hash_password(doctor_password),
            )
        )
        session.add(
            User(
                username=ops_username,
                display_name="Ops Authz",
                role="ops",
                is_active=True,
                password_hash=hash_password(ops_password),
            )
        )

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["MYOPIA_DATABASE_URL"] = database_url
    env["MYOPIA_STORAGE_BACKEND"] = "local"
    env["MYOPIA_LOCAL_STORAGE_DIR"] = str(storage_dir)
    env["MYOPIA_SKIP_STARTUP_CHECK"] = "1"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "myopia_backend.api:app",
        "--host",
        host,
        "--port",
        str(port),
        "--app-dir",
        str(BACKEND_DIR),
        "--log-level",
        "warning",
    ]

    print(f"[info] python={sys.executable}")
    print(f"[info] base_url={base_url}")
    print(f"[info] database_url={database_url}")
    print(f"[info] local_storage_dir={storage_dir}")

    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        wait_until_ready(f"{base_url}/healthz", timeout_s=args.startup_timeout)
        print("[ok] /healthz")

        doctor_token = _login(base_url, doctor_username, doctor_password)
        ops_token = _login(base_url, ops_username, ops_password)

        status_401, body_401 = _http_json(
            f"{base_url}/v1/clinical/predictions/by-encounters",
            method="POST",
            payload={"patient_id": 1, "encounter_ids": [1], "horizons": [1]},
            token=None,
        )
        _expect_status("unauthorized clinical prediction request", status_401, 401, body_401)

        status_403, body_403 = _http_json(
            f"{base_url}/v1/clinical/patients?limit=1&offset=0",
            method="GET",
            token=ops_token,
        )
        _expect_status("ops role access clinical endpoint", status_403, 403, body_403)

        suffix = os.urandom(4).hex()
        status_p1, body_p1 = _http_json(
            f"{base_url}/v1/clinical/patients",
            method="POST",
            token=doctor_token,
            payload={
                "patient_code": f"AUTHZ-A-{suffix}",
                "full_name": "Authz A",
                "sex": "U",
                "birth_date": "2015-01-01",
            },
        )
        _expect_status("create patient A", status_p1, 200, body_p1)
        patient_a_id = int(body_p1["id"])

        status_p2, body_p2 = _http_json(
            f"{base_url}/v1/clinical/patients",
            method="POST",
            token=doctor_token,
            payload={
                "patient_code": f"AUTHZ-B-{suffix}",
                "full_name": "Authz B",
                "sex": "U",
                "birth_date": "2015-01-01",
            },
        )
        _expect_status("create patient B", status_p2, 200, body_p2)
        patient_b_id = int(body_p2["id"])

        status_enc, body_enc = _http_json(
            f"{base_url}/v1/clinical/encounters",
            method="POST",
            token=doctor_token,
            payload={
                "patient_id": patient_b_id,
                "encounter_date": "2026-03-20",
                "se": -1.25,
            },
        )
        _expect_status("create encounter for patient B", status_enc, 200, body_enc)
        encounter_b_id = int(body_enc["id"])

        status_400, body_400 = _http_json(
            f"{base_url}/v1/clinical/predictions/by-encounters",
            method="POST",
            token=doctor_token,
            payload={
                "patient_id": patient_a_id,
                "encounter_ids": [encounter_b_id],
                "horizons": [1],
                "actor": doctor_username,
            },
        )
        _expect_status(
            "cross-patient encounter prediction request",
            status_400,
            400,
            body_400,
        )
        detail = str(body_400.get("detail") or "")
        if "does not belong to patient" not in detail:
            raise AssertionError(
                "cross-patient 400 detail mismatch: "
                f"expected contains 'does not belong to patient', got={body_400}"
            )
        print("[ok] cross-patient detail check")

        status_cp_401, body_cp_401 = _http_json(
            f"{base_url}/v1/auth/change-password",
            method="POST",
            payload={"old_password": doctor_password, "new_password": "DoctorAuthz456!"},
            token=None,
        )
        _expect_status("change password without token", status_cp_401, 401, body_cp_401)

        status_cp_bad_old, body_cp_bad_old = _http_json(
            f"{base_url}/v1/auth/change-password",
            method="POST",
            token=doctor_token,
            payload={"old_password": "wrong-password", "new_password": "DoctorAuthz456!"},
        )
        _expect_status(
            "change password with invalid current password",
            status_cp_bad_old,
            401,
            body_cp_bad_old,
        )

        new_doctor_password = "DoctorAuthz456!"
        status_cp_ok, body_cp_ok = _http_json(
            f"{base_url}/v1/auth/change-password",
            method="POST",
            token=doctor_token,
            payload={"old_password": doctor_password, "new_password": new_doctor_password},
        )
        _expect_status("change password success", status_cp_ok, 200, body_cp_ok)

        status_old_login, body_old_login = _http_json(
            f"{base_url}/v1/auth/login",
            method="POST",
            payload={"username": doctor_username, "password": doctor_password},
        )
        _expect_status(
            "old password login after change",
            status_old_login,
            401,
            body_old_login,
        )

        _login(base_url, doctor_username, new_doctor_password)
        print("[ok] new password login after change")

        print("[done] clinical authz smoke test passed")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

        if db_path.exists():
            db_path.unlink()
        if storage_dir.exists():
            shutil.rmtree(storage_dir)


if __name__ == "__main__":
    main()
