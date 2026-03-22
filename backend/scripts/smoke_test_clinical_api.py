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
    image = Image.new("RGB", (64, 64), color=(36, 142, 72))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clinical API interface-level smoke test.")
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

    db_fd, db_path_raw = tempfile.mkstemp(prefix="myopia_clinical_smoke_", suffix=".db")
    os.close(db_fd)
    db_path = Path(db_path_raw)
    storage_dir = Path(tempfile.mkdtemp(prefix="myopia_clinical_storage_"))
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
                "original_filename": "clinical_smoke.png",
                "content_type": "image/png",
                "metadata": {"source": "clinical-smoke"},
            },
        )
        file_asset_id = int(upload_resp["file_asset_id"])
        print(f"[ok] /v1/files/upload-inline file_asset_id={file_asset_id}")

        patient_resp = http_post_json(
            f"{base_url}/v1/patients",
            {
                "patient_code": "CLINICAL-001",
                "full_name": "Smoke Test Patient",
                "sex": "U",
                "birth_date": "2015-01-01",
            },
        )
        patient_id = int(patient_resp["id"])
        print(f"[ok] /v1/patients patient_id={patient_id}")

        patients = http_get_json(f"{base_url}/v1/patients")
        if not any(int(p["id"]) == patient_id for p in patients):
            raise AssertionError(f"Created patient missing from list: {patients}")
        print("[ok] /v1/patients (list)")

        encounter_resp = http_post_json(
            f"{base_url}/v1/encounters",
            {
                "patient_id": patient_id,
                "encounter_date": "2026-03-19",
                "se": 0.25,
                "image_asset_id": file_asset_id,
                "notes": {"source": "clinical-smoke"},
            },
        )
        encounter_id = int(encounter_resp["id"])
        print(f"[ok] /v1/encounters encounter_id={encounter_id}")

        encounters = http_get_json(f"{base_url}/v1/patients/{patient_id}/encounters")
        if not any(int(e["id"]) == encounter_id for e in encounters):
            raise AssertionError(f"Created encounter missing from list: {encounters}")
        print("[ok] /v1/patients/{patient_id}/encounters")

        updated_note = {"source": "clinical-smoke-update"}
        updated_se = 0.35
        updated_date = "2026-03-21"

        patch_body = json.dumps(
            {"encounter_date": updated_date, "se": updated_se, "notes": updated_note},
            ensure_ascii=False,
        ).encode("utf-8")
        patch_req = urllib.request.Request(
            url=f"{base_url}/v1/encounters/{encounter_id}",
            data=patch_body,
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(patch_req, timeout=10.0) as patch_resp:
            updated_encounter = json.loads(patch_resp.read().decode("utf-8"))
        if int(updated_encounter["id"]) != encounter_id:
            raise AssertionError(f"Updated encounter mismatch: {updated_encounter}")
        if str(updated_encounter.get("encounter_date")) != updated_date:
            raise AssertionError(f"Encounter date not updated: {updated_encounter}")
        if float(updated_encounter.get("se")) != updated_se:
            raise AssertionError(f"Encounter se not updated: {updated_encounter}")
        print("[ok] PATCH /v1/encounters/{encounter_id}")

        prediction_resp = http_post_json(
            f"{base_url}/v1/predictions",
            {
                "patient_id": patient_id,
                "encounter_id": encounter_id,
                "visits": [{"file_asset_id": file_asset_id, "se": 0.25}],
                "horizons": [1],
                "device": args.device,
                "actor": "clinical-smoke",
            },
            timeout=40.0,
        )
        prediction_id = int(prediction_resp["id"])
        if "predictions" not in prediction_resp:
            raise AssertionError(f"Invalid prediction response: {prediction_resp}")
        print(f"[ok] /v1/predictions prediction_id={prediction_id}")

        fetched_prediction = http_get_json(f"{base_url}/v1/predictions/{prediction_id}")
        if int(fetched_prediction["id"]) != prediction_id:
            raise AssertionError(f"Prediction fetch mismatch: {fetched_prediction}")
        print("[ok] /v1/predictions/{prediction_id}")

        upload_resp_2 = http_post_json(
            f"{base_url}/v1/files/upload-inline",
            {
                "image_b64": make_sample_image_b64(),
                "image_ext": ".png",
                "original_filename": "clinical_smoke_2.png",
                "content_type": "image/png",
                "metadata": {"source": "clinical-smoke-2"},
            },
        )
        file_asset_id_2 = int(upload_resp_2["file_asset_id"])
        print(f"[ok] /v1/files/upload-inline file_asset_id={file_asset_id_2}")

        encounter_resp_2 = http_post_json(
            f"{base_url}/v1/encounters",
            {
                "patient_id": patient_id,
                "encounter_date": "2026-03-20",
                "se": 0.5,
                "image_asset_id": file_asset_id_2,
                "notes": {"source": "clinical-smoke-2"},
            },
        )
        encounter_id_2 = int(encounter_resp_2["id"])
        print(f"[ok] /v1/encounters encounter_id={encounter_id_2}")

        prediction_by_encounters_resp = http_post_json(
            f"{base_url}/v1/predictions/by-encounters",
            {
                "patient_id": patient_id,
                "encounter_ids": [encounter_id_2, encounter_id],
                "horizons": [1, 3],
                "device": args.device,
                "actor": "clinical-smoke",
            },
            timeout=40.0,
        )
        prediction_by_encounters_id = int(prediction_by_encounters_resp["id"])
        if "encounter_ids" not in prediction_by_encounters_resp:
            raise AssertionError(
                f"Invalid by-encounters response (missing encounter_ids): "
                f"{prediction_by_encounters_resp}"
            )
        if "visit_asset_ids" not in prediction_by_encounters_resp:
            raise AssertionError(
                f"Invalid by-encounters response (missing visit_asset_ids): "
                f"{prediction_by_encounters_resp}"
            )
        expected_encounter_ids = {encounter_id, encounter_id_2}
        if set(prediction_by_encounters_resp["encounter_ids"]) != expected_encounter_ids:
            raise AssertionError(
                "By-encounters response mismatch for encounter_ids: "
                f"{prediction_by_encounters_resp['encounter_ids']}"
            )
        print(f"[ok] /v1/predictions/by-encounters prediction_id={prediction_by_encounters_id}")

        patient_predictions = http_get_json(f"{base_url}/v1/patients/{patient_id}/predictions")
        by_id = {int(p["id"]): p for p in patient_predictions}
        if prediction_id not in by_id:
            raise AssertionError(
                f"Missing standard prediction in patient history: id={prediction_id}, rows={patient_predictions}"
            )
        if prediction_by_encounters_id not in by_id:
            raise AssertionError(
                "Missing by-encounters prediction in patient history: "
                f"id={prediction_by_encounters_id}, rows={patient_predictions}"
            )
        row2 = by_id[prediction_by_encounters_id]
        if set(row2.get("encounter_ids", [])) != expected_encounter_ids:
            raise AssertionError(
                f"Patient prediction history encounter_ids mismatch: row={row2}"
            )
        print("[ok] /v1/patients/{patient_id}/predictions")

        print("[done] clinical API smoke test passed")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"HTTPError during clinical API smoke test: status={exc.code}, url={exc.url}, body={detail}"
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
