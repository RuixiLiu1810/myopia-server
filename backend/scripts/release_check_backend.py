#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent


def _run(step: str, cmd: list[str]) -> None:
    print(f"[check] {step}: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(BACKEND_DIR))
    print(f"[ok] {step}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Release check: run unit tests + smoke tests + contract check."
    )
    parser.add_argument("--model-dir", default="../models")
    parser.add_argument("--data-dir", default="../../all")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    python = sys.executable
    _run("unit_tests", [python, "scripts/run_unit_tests.py"])
    _run(
        "clinical_authz_smoke_test",
        [
            python,
            "scripts/smoke_test_clinical_authz.py",
            "--model-dir",
            args.model_dir,
        ],
    )
    _run(
        "smoke_test",
        [
            python,
            "scripts/smoke_test_inference_api.py",
            "--model-dir",
            args.model_dir,
            "--data-dir",
            args.data_dir,
            "--device",
            args.device,
        ],
    )
    _run(
        "contract_check",
        [
            python,
            "scripts/contract_check_backend.py",
            "--model-dir",
            args.model_dir,
            "--data-dir",
            args.data_dir,
            "--device",
            args.device,
        ],
    )
    print("[done] release check passed")


if __name__ == "__main__":
    main()
