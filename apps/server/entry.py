from __future__ import annotations

import argparse
import os

from apps.shared.cli import env_int
from launcher_server import run_backend_only


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run backend API service only.")
    parser.add_argument("--host", default=os.getenv("MYOPIA_API_HOST") or "0.0.0.0")
    parser.add_argument("--port", type=int, default=env_int("MYOPIA_API_PORT", 8000))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_backend_only(host=args.host, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())

