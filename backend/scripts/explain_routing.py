#!/usr/bin/env python3
"""Print human-readable model routing explanation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from myopia_backend.inference_service import routing_rules
from myopia_backend.model_store import list_available_models


def main() -> None:
    parser = argparse.ArgumentParser(description="Explain routing rules and available models.")
    parser.add_argument(
        "--model-dir",
        default="../models",
        help="Model directory used to inspect available files.",
    )
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    if not model_dir.is_absolute():
        cwd_resolved = (Path.cwd() / model_dir).resolve()
        if cwd_resolved.exists():
            model_dir = cwd_resolved
        else:
            model_dir = (BACKEND_DIR / model_dir).resolve()

    print("=== Routing Rules ===")
    rules = routing_rules(max_seq_len=5)
    for seq_len, horizons in rules.items():
        print(f"seq_len={seq_len} -> horizons={horizons}")

    print("\n=== Available Model Files ===")
    available = list_available_models(model_dir)
    for seq_len in sorted(rules.keys()):
        horizons = rules[seq_len]
        for h in horizons:
            key = (seq_len, h)
            name = available[key].name if key in available else "<missing>"
            print(f"Xu{seq_len}{h}b -> {name}")

    missing = [k for k in sorted((n, h) for n, hs in rules.items() for h in hs) if k not in available]
    if missing:
        print(f"\n[warn] Missing model keys: {missing}")
    else:
        print("\n[ok] All routing keys have model files.")


if __name__ == "__main__":
    main()
