"""Model discovery and loading layer.

Responsibilities:
- discover available Xu/Fen/FenG model assets in a directory
- parse model names into (seq_len, horizon) keys
- load full checkpoints or state_dict assets with caching
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

import torch
from torch import nn

from .model_defs import build_xu_model, register_notebook_classes_for_unpickle


ModelAssetKey = tuple[str, int, int]  # (family, seq_len, horizon)

MODEL_ASSET_RE = re.compile(r"^(Xu|FenG|Fen)([1-5])([1-5])b\.pth$")
STATE_DICT_ASSET_RE = re.compile(r"^(Xu|FenG|Fen)([1-5])([1-5])b_state_dict\.pt$")

FAMILY_PREFIX_TO_KEY = {
    "Xu": "xu",
    "Fen": "fen",
    "FenG": "feng",
}
FAMILY_KEY_TO_PREFIX = {
    "xu": "Xu",
    "fen": "Fen",
    "feng": "FenG",
}
FAMILY_OUTPUT_SIZE = {
    "xu": 1,
    "fen": 2,
    "feng": 2,
}
SUPPORTED_FAMILIES = tuple(FAMILY_KEY_TO_PREFIX.keys())


def _parse_model_asset_key_from_name(name: str) -> ModelAssetKey | None:
    """Parse one model filename to (family, seq_len, horizon)."""
    m_sd = STATE_DICT_ASSET_RE.match(name)
    if m_sd:
        family = FAMILY_PREFIX_TO_KEY[m_sd.group(1)]
        return family, int(m_sd.group(2)), int(m_sd.group(3))
    m_pth = MODEL_ASSET_RE.match(name)
    if m_pth:
        family = FAMILY_PREFIX_TO_KEY[m_pth.group(1)]
        return family, int(m_pth.group(2)), int(m_pth.group(3))
    return None


def _parse_model_key_from_name(name: str) -> tuple[int, int] | None:
    """Compatibility parser for legacy Xu-only callers."""
    key = _parse_model_asset_key_from_name(name)
    if key is None:
        return None
    family, seq_len, horizon = key
    if family != "xu":
        return None
    return seq_len, horizon


def _extract_asset_key_from_state_dict_name(path: Path) -> ModelAssetKey:
    m = STATE_DICT_ASSET_RE.match(path.name)
    if not m:
        raise ValueError(f"Invalid state_dict filename: {path.name}")
    family = FAMILY_PREFIX_TO_KEY[m.group(1)]
    return family, int(m.group(2)), int(m.group(3))


def list_available_model_assets(model_dir: str | Path) -> dict[ModelAssetKey, Path]:
    """Return available model paths keyed by (family, seq_len, horizon).

    Priority:
    1) *b_state_dict.pt
    2) *b.pth fallback (when state_dict asset is absent)
    """
    root = Path(model_dir)
    if not root.exists():
        raise FileNotFoundError(f"Model directory not found: {root}")

    found: dict[ModelAssetKey, Path] = {}

    for p in sorted(root.glob("*b_state_dict.pt")):
        key = _parse_model_asset_key_from_name(p.name)
        if key is not None:
            found[key] = p

    for p in sorted(root.glob("*b.pth")):
        key = _parse_model_asset_key_from_name(p.name)
        if key is not None and key not in found:
            found[key] = p

    return found


def list_available_models(model_dir: str | Path) -> dict[tuple[int, int], Path]:
    """Legacy Xu-only model mapping: (seq_len, horizon) -> path."""
    assets = list_available_model_assets(model_dir)
    out: dict[tuple[int, int], Path] = {}
    for (family, seq_len, horizon), path in sorted(assets.items()):
        if family == "xu":
            out[(seq_len, horizon)] = path
    return out


def _build_model_for_family(family: str, seq_len: int) -> nn.Module:
    if family not in FAMILY_OUTPUT_SIZE:
        raise ValueError(f"Unsupported model family: {family}")
    return build_xu_model(
        seq_len=seq_len,
        hidden_size=256,
        output_size=FAMILY_OUTPUT_SIZE[family],
        pooling="avg",
    )


@lru_cache(maxsize=16)
def _load_full_checkpoint_cached(path_str: str, device_str: str) -> nn.Module:
    path = Path(path_str)
    device = torch.device(device_str)

    register_notebook_classes_for_unpickle()

    try:
        try:
            model = torch.load(path, map_location=device, weights_only=False)
        except TypeError:
            model = torch.load(path, map_location=device)
    except Exception as exc:
        raise RuntimeError(f"Failed to load full checkpoint: {path}. Error: {exc}") from exc

    model = model.to(device)
    model.eval()
    return model


@lru_cache(maxsize=16)
def _load_state_dict_cached(path_str: str, device_str: str) -> nn.Module:
    path = Path(path_str)
    device = torch.device(device_str)
    family, seq_len, _ = _extract_asset_key_from_state_dict_name(path)

    model = _build_model_for_family(family=family, seq_len=seq_len)

    try:
        try:
            state = torch.load(path, map_location=device, weights_only=True)
        except TypeError:
            state = torch.load(path, map_location=device)
    except Exception as exc:
        raise RuntimeError(f"Failed to load state_dict: {path}. Error: {exc}") from exc

    if not isinstance(state, dict):
        raise RuntimeError(f"Invalid state_dict payload in {path}: expected dict, got {type(state)}")

    model.load_state_dict(state, strict=True)
    model = model.to(device)
    model.eval()
    return model


def load_model(path: str | Path, device: torch.device) -> nn.Module:
    """Load one model file (state_dict or full checkpoint)."""
    p = Path(path).resolve()
    if STATE_DICT_ASSET_RE.match(p.name):
        return _load_state_dict_cached(str(p), str(device))
    return _load_full_checkpoint_cached(str(p), str(device))


# --------- legacy helpers kept for backward compatibility ----------


def _extract_seq_horizon_from_state_dict_name(path: Path) -> tuple[int, int]:
    family, seq_len, horizon = _extract_asset_key_from_state_dict_name(path)
    if family != "xu":
        raise ValueError(f"Not a Xu state_dict filename: {path.name}")
    return seq_len, horizon


MODEL_NAME_RE = re.compile(r"^Xu([1-5])([1-5])b\.pth$")
STATE_DICT_NAME_RE = re.compile(r"^Xu([1-5])([1-5])b_state_dict\.pt$")
