#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alembic upgrade for backend database.")
    parser.add_argument("--revision", default="head", help='Target revision, default "head".')
    parser.add_argument("--database-url", default=None, help="Optional override for MYOPIA_DATABASE_URL.")
    args = parser.parse_args()

    env = os.environ.copy()
    if args.database_url:
        env["MYOPIA_DATABASE_URL"] = args.database_url

    cmd = [
        "alembic",
        "-c",
        str(ALEMBIC_INI),
        "upgrade",
        args.revision,
    ]
    print(f"[db] {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(BACKEND_DIR), env=env)
    print("[done] alembic upgrade finished")


if __name__ == "__main__":
    main()
