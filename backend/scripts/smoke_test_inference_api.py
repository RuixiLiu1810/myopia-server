#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent


def resolve_path(raw: str | None) -> Path:
    if raw is None:
        return Path()
    path = Path(raw)
    if path.is_absolute():
        return path
    cwd_resolved = (Path.cwd() / path).resolve()
    if cwd_resolved.exists():
        return cwd_resolved
    return (BACKEND_DIR / path).resolve()


def pick_image(image_path: str | None, data_dir: Path) -> Path:
    if image_path:
        path = resolve_path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Sample image not found: {path}")
        return path

    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    for pattern in ("*.jpg", "*.jpeg", "*.png"):
        candidates = sorted(data_dir.glob(pattern))
        if candidates:
            return candidates[0]

    raise FileNotFoundError(f"No image found in {data_dir} (*.jpg/*.jpeg/*.png)")


def get_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def http_get_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict, timeout: float = 20.0) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_until_ready(url: str, timeout_s: float = 30.0, interval_s: float = 0.3) -> None:
    deadline = time.time() + timeout_s
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            data = http_get_json(url, timeout=2.0)
            if data.get("status") == "ok":
                return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(interval_s)
    raise RuntimeError(f"Server not ready: {url}. last_error={last_exc}")


def assert_predict_payload(result: dict, expected_horizons: list[int]) -> None:
    for key in ("used_seq_len", "used_horizons", "models", "predictions"):
        if key not in result:
            raise AssertionError(f"Missing key in response: {key}")

    used_horizons = [int(x) for x in result["used_horizons"]]
    if used_horizons != expected_horizons:
        raise AssertionError(
            f"used_horizons mismatch: expected={expected_horizons}, got={used_horizons}"
        )

    for horizon in expected_horizons:
        pred_key = f"t+{horizon}"
        if pred_key not in result["predictions"]:
            raise AssertionError(f"Missing prediction key: {pred_key}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inference API interface-level smoke test.")
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
    env["MYOPIA_SETUP_ENFORCE_LOCK"] = "0"

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
    print(f"[info] model_dir={model_dir}")
    print(f"[info] sample_image={image_path}")
    print(f"[info] base_url={base_url}")

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

        model_info = http_get_json(f"{base_url}/model-info")
        groups = model_info.get("groups", {})
        if not isinstance(groups, dict) or not groups:
            raise AssertionError("Invalid /model-info response")
        print(f"[ok] /model-info groups={sorted(groups.keys())}")

        limits = http_get_json(f"{base_url}/limits")
        for key in ("max_visits", "max_inline_image_bytes", "max_inline_total_bytes"):
            if key not in limits:
                raise AssertionError(f"Missing key in /limits response: {key}")
        print("[ok] /limits")

        predict_payload = {
            "visits": [{"image_path": str(image_path), "se": 0.375}],
            "horizons": [1, 2],
            "device": args.device,
        }
        predict_response = http_post_json(f"{base_url}/predict", predict_payload, timeout=30.0)
        assert_predict_payload(predict_response, expected_horizons=[1, 2])
        print("[ok] /predict")

        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        predict_inline_payload = {
            "visits": [{"image_b64": image_b64, "image_ext": ".jpg", "se": 0.375}],
            "horizons": [1, 2],
            "device": args.device,
        }
        predict_inline_response = http_post_json(
            f"{base_url}/predict-inline",
            predict_inline_payload,
            timeout=30.0,
        )
        assert_predict_payload(predict_inline_response, expected_horizons=[1, 2])
        print("[ok] /predict-inline")

        print("[done] inference API smoke test passed")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"HTTPError during inference API smoke test: status={exc.code}, url={exc.url}, body={detail}"
        ) from exc
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
