#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

from smoke_test_inference_api import (
    BACKEND_DIR,
    get_free_port,
    pick_image,
    resolve_path,
    wait_until_ready,
)


def http_post_json_allow_error(url: str, payload: dict, timeout: float = 20.0) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"detail": raw}
        return int(exc.code), parsed


def http_get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_request_status(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 8.0,
) -> int:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def assert_status(step: str, status: int, expected: int) -> None:
    if status != expected:
        raise AssertionError(f"{step}: expected HTTP {expected}, got {status}")


def assert_detail_contains(step: str, body: dict, keyword: str) -> None:
    detail = str(body.get("detail", ""))
    if keyword not in detail:
        raise AssertionError(f"{step}: detail does not contain {keyword!r}, body={body}")


def assert_predict_shape(step: str, body: dict) -> None:
    required = ("used_seq_len", "used_horizons", "models", "predictions", "latency_ms")
    for key in required:
        if key not in body:
            raise AssertionError(f"{step}: missing key {key} in response {body}")


def assert_endpoint_registered(
    base_url: str,
    endpoint: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> None:
    status = http_request_status(
        f"{base_url}{endpoint}",
        method=method,
        payload=payload,
        timeout=10.0,
    )
    if status == 404:
        raise AssertionError(f"ops_contract: endpoint not found {method} {endpoint}")
    if status not in {200, 400, 401, 403, 405, 422, 500, 503}:
        raise AssertionError(f"ops_contract: unexpected status={status} endpoint={method} {endpoint}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Contract check for frontend/backend API compatibility."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 means auto")
    parser.add_argument("--model-dir", default="../models")
    parser.add_argument("--data-dir", default="../../all")
    parser.add_argument("--image-path", default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--startup-timeout", type=float, default=45.0)
    args = parser.parse_args()

    model_dir = resolve_path(args.model_dir)
    data_dir = resolve_path(args.data_dir)
    image_path = pick_image(args.image_path, data_dir)

    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")

    host = args.host
    port = args.port if args.port > 0 else get_free_port(host)
    base_url = f"http://{host}:{port}"

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["MYOPIA_MODEL_DIR"] = str(model_dir)
    env["MYOPIA_DEFAULT_DEVICE"] = args.device

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
    print(f"[info] model_dir={model_dir}")

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
        print("[ok] startup")

        limits = http_get_json(f"{base_url}/limits")
        for key in ("max_visits", "max_inline_image_bytes", "max_inline_total_bytes", "allowed_image_exts"):
            if key not in limits:
                raise AssertionError(f"Missing key in /limits response: {key}")
        print("[ok] /limits contract")

        status_ok, body_ok = http_post_json_allow_error(
            f"{base_url}/predict",
            {
                "visits": [{"image_path": str(image_path), "se": 0.125}],
                "horizons": [1],
                "device": args.device,
            },
            timeout=30.0,
        )
        assert_status("predict_success", status_ok, 200)
        assert_predict_shape("predict_success", body_ok)
        print("[ok] /predict success contract")

        too_many = int(limits["max_visits"]) + 1
        status_many, body_many = http_post_json_allow_error(
            f"{base_url}/predict",
            {
                "visits": [{"image_path": str(image_path), "se": 0.125}] * too_many,
                "horizons": [1],
                "device": args.device,
            },
            timeout=30.0,
        )
        assert_status("predict_too_many_visits", status_many, 400)
        assert_detail_contains("predict_too_many_visits", body_many, "Too many visits")
        print("[ok] /predict too-many-visits error contract")

        inline_ok = base64.b64encode(image_path.read_bytes()).decode("ascii")

        status_ext, body_ext = http_post_json_allow_error(
            f"{base_url}/predict-inline",
            {
                "visits": [{"image_b64": inline_ok, "image_ext": ".exe", "se": 0.125}],
                "horizons": [1],
                "device": args.device,
            },
            timeout=30.0,
        )
        assert_status("predict_inline_bad_ext", status_ext, 400)
        assert_detail_contains("predict_inline_bad_ext", body_ext, "Unsupported image_ext")
        print("[ok] /predict-inline bad-ext error contract")

        status_b64, body_b64 = http_post_json_allow_error(
            f"{base_url}/predict-inline",
            {
                "visits": [{"image_b64": "not_base64***", "image_ext": ".jpg", "se": 0.125}],
                "horizons": [1],
                "device": args.device,
            },
            timeout=30.0,
        )
        assert_status("predict_inline_bad_base64", status_b64, 400)
        assert_detail_contains("predict_inline_bad_base64", body_b64, "Invalid base64")
        print("[ok] /predict-inline bad-base64 error contract")

        status_h, body_h = http_post_json_allow_error(
            f"{base_url}/predict",
            {
                "visits": [{"image_path": str(image_path), "se": 0.125}],
                "horizons": [99],
                "device": args.device,
            },
            timeout=30.0,
        )
        assert_status("predict_bad_horizon", status_h, 400)
        assert_detail_contains("predict_bad_horizon", body_h, "Horizon")
        print("[ok] /predict bad-horizon error contract")

        ops_endpoints = [
            ("GET", "/v1/ops/health", None),
            ("GET", "/v1/ops/model-info", None),
            ("GET", "/v1/ops/db-status", None),
            ("GET", "/v1/ops/metrics/summary?window_hours=24", None),
            ("GET", "/v1/ops/alerts?window_hours=24", None),
            ("GET", "/v1/ops/users", None),
            ("GET", "/v1/ops/jobs?limit=1", None),
            ("POST", "/v1/ops/actions/backup", {"precheck": True}),
            ("POST", "/v1/ops/actions/migration-check", {"precheck": True}),
            ("POST", "/v1/ops/actions/reindex", {"precheck": True, "table_name": "audit_logs"}),
            ("GET", "/v1/ops/audit-logs?limit=1&offset=0", None),
            ("GET", "/v1/ops/audit-logs/export?limit=1&offset=0", None),
            ("GET", "/v1/ops/db/tables", None),
            ("GET", "/v1/ops/db/tables/audit_logs/schema", None),
            ("GET", "/v1/ops/db/tables/audit_logs/rows?limit=1&offset=0", None),
            ("GET", "/v1/ops/db/tables/audit_logs/rows/export?limit=1&offset=0", None),
        ]
        for method, endpoint, payload in ops_endpoints:
            assert_endpoint_registered(base_url, endpoint, method=method, payload=payload)
        print("[ok] /v1/ops endpoint registration contract")

        print("[done] backend contract check passed")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    main()
