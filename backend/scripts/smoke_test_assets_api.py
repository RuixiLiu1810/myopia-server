#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from PIL import Image

from smoke_test_inference_api import BACKEND_DIR, resolve_path, wait_until_ready

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def get_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def http_get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict, timeout: float = 30.0) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def make_sample_image_b64() -> str:
    image = Image.new("RGB", (64, 64), color=(24, 118, 210))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def assert_predict_payload(result: dict, expected_horizons: list[int]) -> None:
    for key in ("used_seq_len", "used_horizons", "models", "predictions", "file_asset_ids"):
        if key not in result:
            raise AssertionError(f"Missing key in /v1/predict-assets response: {key}")

    used_horizons = [int(x) for x in result["used_horizons"]]
    if used_horizons != expected_horizons:
        raise AssertionError(
            f"used_horizons mismatch: expected={expected_horizons}, got={used_horizons}"
        )
    if len(result["file_asset_ids"]) != 1:
        raise AssertionError(f"Unexpected file_asset_ids: {result['file_asset_ids']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Assets API interface-level smoke test.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 means auto")
    parser.add_argument("--model-dir", default="../models")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--startup-timeout", type=float, default=45.0)
    args = parser.parse_args()

    model_dir = resolve_path(args.model_dir)
    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")

    host = args.host
    port = args.port if args.port > 0 else get_free_port(host)
    base_url = f"http://{host}:{port}"

    db_fd, db_path_raw = tempfile.mkstemp(prefix="myopia_assets_smoke_", suffix=".db")
    os.close(db_fd)
    db_path = Path(db_path_raw)
    storage_dir = Path(tempfile.mkdtemp(prefix="myopia_assets_storage_"))
    database_url = f"sqlite+pysqlite:///{db_path}"

    from myopia_backend.db import models as _models  # noqa: F401
    from myopia_backend.db.base import Base
    from myopia_backend.db.session import create_engine_from_url

    engine = create_engine_from_url(database_url)
    Base.metadata.create_all(engine)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["MYOPIA_MODEL_DIR"] = str(model_dir)
    env["MYOPIA_DEFAULT_DEVICE"] = args.device
    env["MYOPIA_SETUP_ENFORCE_LOCK"] = "0"
    env["MYOPIA_DATABASE_URL"] = database_url
    env["MYOPIA_STORAGE_BACKEND"] = "local"
    env["MYOPIA_LOCAL_STORAGE_DIR"] = str(storage_dir)
    env["MYOPIA_SKIP_STARTUP_CHECK"] = "0"

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

        upload_resp = http_post_json(
            f"{base_url}/v1/files/upload-inline",
            {
                "image_b64": make_sample_image_b64(),
                "image_ext": ".png",
                "original_filename": "assets_smoke.png",
                "content_type": "image/png",
                "metadata": {"source": "assets-smoke"},
            },
        )
        file_asset_id = int(upload_resp["file_asset_id"])
        if int(upload_resp.get("size_bytes", 0)) <= 0:
            raise AssertionError(f"Invalid size_bytes from upload: {upload_resp}")
        print(f"[ok] /v1/files/upload-inline file_asset_id={file_asset_id}")

        asset_resp = http_get_json(f"{base_url}/v1/files/{file_asset_id}")
        if int(asset_resp.get("id", -1)) != file_asset_id:
            raise AssertionError(f"Asset detail mismatch: {asset_resp}")
        if asset_resp.get("storage_backend") != "local":
            raise AssertionError(f"Unexpected storage backend: {asset_resp}")
        print("[ok] /v1/files/{file_asset_id}")

        predict_resp = http_post_json(
            f"{base_url}/v1/predict-assets",
            {
                "visits": [{"file_asset_id": file_asset_id, "se": 0.25}],
                "horizons": [1],
                "device": args.device,
            },
            timeout=40.0,
        )
        assert_predict_payload(predict_resp, expected_horizons=[1])
        print("[ok] /v1/predict-assets")

        print("[done] assets API smoke test passed")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"HTTPError during assets API smoke test: status={exc.code}, url={exc.url}, body={detail}"
        ) from exc
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
