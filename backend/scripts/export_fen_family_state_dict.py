#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import torch


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from myopia_backend.model_defs import register_notebook_classes_for_unpickle


@dataclass(frozen=True)
class FamilySpec:
    key: str
    prefix: str
    default_source_dir: Path


FAMILY_SPECS: dict[str, FamilySpec] = {
    "fen": FamilySpec(key="fen", prefix="Fen", default_source_dir=PROJECT_ROOT / "fen"),
    "feng": FamilySpec(key="feng", prefix="FenG", default_source_dir=PROJECT_ROOT / "fenG"),
}


def _sha256_hex(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve(path_raw: str, base: Path) -> Path:
    path = Path(path_raw)
    if path.is_absolute():
        return path

    cwd_resolved = (Path.cwd() / path).resolve()
    if cwd_resolved.exists():
        return cwd_resolved

    return (base / path).resolve()


def _parse_name(prefix: str, filename: str) -> tuple[int, int]:
    m = re.match(rf"^{re.escape(prefix)}([1-5])([1-5])b\.pth$", filename)
    if not m:
        raise ValueError(f"Invalid checkpoint filename: {filename}")
    return int(m.group(1)), int(m.group(2))


def _as_int_or_none(obj: Any, attr: str) -> int | None:
    value = getattr(obj, attr, None)
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _export_family(
    *,
    spec: FamilySpec,
    source_dir: Path,
    output_dir: Path,
    overwrite: bool,
    limit: int | None,
) -> list[dict[str, Any]]:
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found for family={spec.key}: {source_dir}")

    files = sorted(source_dir.glob(f"{spec.prefix}*b.pth"))
    if limit is not None:
        files = files[:limit]

    if not files:
        raise FileNotFoundError(f"No .pth files found for family={spec.key} in {source_dir}")

    print(f"[info] family={spec.key} source={source_dir} files={len(files)}")

    rows: list[dict[str, Any]] = []
    for idx, ckpt_path in enumerate(files, start=1):
        seq_len, horizon = _parse_name(spec.prefix, ckpt_path.name)
        out_name = ckpt_path.stem + "_state_dict.pt"
        out_path = output_dir / out_name

        if out_path.exists() and not overwrite:
            sha = _sha256_hex(out_path)
            print(f"[skip] {idx:02d}/{len(files)} exists: {out_name}")
            rows.append(
                {
                    "family": spec.key,
                    "name": ckpt_path.stem,
                    "seq_len": seq_len,
                    "horizon": horizon,
                    "hidden_size": None,
                    "output_size": None,
                    "source_checkpoint": str(ckpt_path.resolve()),
                    "state_dict": str(out_path.resolve()),
                    "sha256": sha,
                    "status": "skipped_existing",
                }
            )
            continue

        print(f"[run ] {idx:02d}/{len(files)} load {ckpt_path.name}")
        model = torch.load(ckpt_path, map_location="cpu", weights_only=False)

        seq_len_model = _as_int_or_none(model, "seq_len")
        if seq_len_model is not None and seq_len_model != seq_len:
            raise RuntimeError(
                f"seq_len mismatch for {ckpt_path.name}: filename={seq_len}, model={seq_len_model}"
            )

        hidden_size = _as_int_or_none(getattr(model, "rnn", object()), "hidden_size")
        output_size = _as_int_or_none(getattr(model, "linear", object()), "out_features")

        state = model.state_dict()
        torch.save(state, out_path)
        sha = _sha256_hex(out_path)
        print(f"[ok  ] {idx:02d}/{len(files)} saved {out_name}")

        rows.append(
            {
                "family": spec.key,
                "name": ckpt_path.stem,
                "seq_len": seq_len,
                "horizon": horizon,
                "hidden_size": hidden_size,
                "output_size": output_size,
                "source_checkpoint": str(ckpt_path.resolve()),
                "state_dict": str(out_path.resolve()),
                "sha256": sha,
                "status": "exported",
            }
        )

        del model
        del state
        gc.collect()

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Fen/FenG full checkpoints (.pth) to state_dict (.pt)."
    )
    parser.add_argument(
        "--families",
        default="fen,feng",
        help='Comma-separated families from {"fen","feng"}, default "fen,feng".',
    )
    parser.add_argument("--fen-dir", default=str(FAMILY_SPECS["fen"].default_source_dir))
    parser.add_argument("--feng-dir", default=str(FAMILY_SPECS["feng"].default_source_dir))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "models"))
    parser.add_argument(
        "--manifest",
        default=str(PROJECT_ROOT / "models" / "manifest_fen_family.json"),
        help="Manifest output path.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional per-family file limit for validation runs.",
    )
    args = parser.parse_args()

    requested = [x.strip().lower() for x in args.families.split(",") if x.strip()]
    invalid = [x for x in requested if x not in FAMILY_SPECS]
    if invalid:
        raise ValueError(f"Invalid families={invalid}; allowed={sorted(FAMILY_SPECS)}")

    source_dirs = {
        "fen": _resolve(args.fen_dir, PROJECT_ROOT),
        "feng": _resolve(args.feng_dir, PROJECT_ROOT),
    }
    output_dir = _resolve(args.output_dir, PROJECT_ROOT)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = _resolve(args.manifest, PROJECT_ROOT)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    register_notebook_classes_for_unpickle()

    all_rows: list[dict[str, Any]] = []
    for key in requested:
        rows = _export_family(
            spec=FAMILY_SPECS[key],
            source_dir=source_dirs[key],
            output_dir=output_dir,
            overwrite=args.overwrite,
            limit=args.limit,
        )
        all_rows.extend(rows)

    payload = {
        "version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir.resolve()),
        "source_dirs": {k: str(v.resolve()) for k, v in source_dirs.items() if k in requested},
        "families": requested,
        "models": all_rows,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    exported = sum(1 for row in all_rows if row.get("status") == "exported")
    skipped = sum(1 for row in all_rows if row.get("status") == "skipped_existing")
    print(
        "[done]"
        f" families={requested}"
        f" total={len(all_rows)}"
        f" exported={exported}"
        f" skipped_existing={skipped}"
        f" manifest={manifest_path}"
    )


if __name__ == "__main__":
    main()
