"""Core inference service.

This module is intentionally pure business-logic:
- no FastAPI dependency
- no filesystem-wide config loading
- only typed input normalization, routing, model execution
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import torch

from .model_store import list_available_model_assets, load_model
from .preprocessing import prepare_inputs


@dataclass(frozen=True)
class Visit:
    """A single chronological visit input."""

    image_path: str
    se: float


def _get_device(device: str | None = None) -> torch.device:
    """Resolve runtime torch device.

    Priority:
    1) explicit request value
    2) first available CUDA
    3) CPU fallback
    """
    if device:
        return torch.device(device)
    return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


def normalize_visits(visits: Iterable[Visit | dict]) -> list[Visit]:
    """Normalize mixed visit payloads into typed Visit list."""
    normalized: list[Visit] = []

    for visit in visits:
        if isinstance(visit, Visit):
            normalized.append(visit)
            continue

        if not isinstance(visit, dict):
            raise TypeError("Each visit must be Visit or dict(image_path, se).")
        if "image_path" not in visit or "se" not in visit:
            raise ValueError("Each visit dict must include keys: image_path, se")

        normalized.append(Visit(image_path=str(visit["image_path"]), se=float(visit["se"])))

    if not normalized:
        raise ValueError("At least 1 visit is required.")

    return normalized


def normalize_model_families(model_families: Sequence[str] | None = None) -> list[str]:
    """Normalize requested model families.

    Defaults to legacy Xu-only behavior when request is omitted.
    """
    if model_families is None:
        return ["xu"]

    aliases = {
        "xu": "xu",
        "quantitative": "xu",
        "fen": "fen",
        "myopia_risk": "fen",
        "feng": "feng",
        "fen_g": "feng",
        "high_myopia_risk": "feng",
    }

    out: list[str] = []
    for raw in model_families:
        key = str(raw or "").strip().lower()
        if not key:
            continue
        if key not in aliases:
            allowed = sorted(aliases.keys())
            raise ValueError(f"Unsupported model family={raw}. Allowed: {allowed}")
        normalized = aliases[key]
        if normalized not in out:
            out.append(normalized)

    if not out:
        raise ValueError("At least one model family is required.")
    return out


def resolve_horizons(seq_len: int, requested_horizons: Sequence[int] | None = None) -> list[int]:
    """Resolve valid forecast horizons for a given sequence length.

    By dataset design:
    - seq_len=1 supports horizons 1..5
    - seq_len=2 supports horizons 1..4
    - ...
    - seq_len=5 supports horizon 1
    """
    max_horizon = 6 - seq_len
    if max_horizon < 1:
        raise ValueError(f"Invalid seq_len={seq_len}. Must be in [1,5].")

    if requested_horizons is None:
        return list(range(1, max_horizon + 1))

    out: list[int] = []
    for horizon in requested_horizons:
        h = int(horizon)
        if h < 1 or h > max_horizon:
            raise ValueError(
                f"Horizon {h} not available for seq_len={seq_len}. Allowed: 1..{max_horizon}"
            )
        out.append(h)

    return sorted(set(out))


def routing_rules(max_seq_len: int = 5) -> dict[int, list[int]]:
    """Return static route rules: seq_len -> supported horizons."""
    if max_seq_len < 1 or max_seq_len > 5:
        raise ValueError("max_seq_len must be in [1,5]")
    return {n: list(range(1, 6 - n + 1)) for n in range(1, max_seq_len + 1)}


def predict_future(
    visits: Sequence[Visit | dict],
    model_dir: str | Path,
    horizons: Sequence[int] | None = None,
    device: str | None = None,
    model_families: Sequence[str] | None = None,
    risk_threshold: float = 0.5,
    max_seq_len: int = 5,
) -> dict:
    """Predict future SE values using automatic model routing.

    Route rule:
    - effective seq_len = min(len(visits), max_seq_len)
    - model key = (family, seq_len, horizon)
    """
    if max_seq_len < 1 or max_seq_len > 5:
        raise ValueError("max_seq_len must be in [1,5]")
    if risk_threshold < 0.0 or risk_threshold > 1.0:
        raise ValueError("risk_threshold must be in [0,1]")

    visits_normalized = normalize_visits(visits)
    seq_len = min(len(visits_normalized), max_seq_len)
    selected_families = normalize_model_families(model_families)

    runtime_device = _get_device(device)
    available_assets = list_available_model_assets(model_dir)
    selected_horizons = resolve_horizons(seq_len, horizons)

    image_tensor, feature_tensor = prepare_inputs(
        visits=visits_normalized,
        seq_len=seq_len,
        device=runtime_device,
    )

    family_results: dict[str, dict] = {}

    with torch.inference_mode():
        for family in selected_families:
            used_models: dict[str, str] = {}
            if family == "xu":
                predictions: dict[str, float] = {}
            else:
                probabilities: dict[str, float] = {}
                labels: dict[str, int] = {}

            for horizon in selected_horizons:
                key = (family, seq_len, horizon)
                if key not in available_assets:
                    family_prefix = {"xu": "Xu", "fen": "Fen", "feng": "FenG"}[family]
                    raise FileNotFoundError(
                        f"Model {family_prefix}{seq_len}{horizon}b not found in {model_dir}."
                    )

                model_path = available_assets[key]
                model = load_model(model_path, runtime_device)
                output = model(image_tensor, feature_tensor)
                used_models[str(horizon)] = model_path.name

                if family == "xu":
                    predictions[f"t+{horizon}"] = float(output.squeeze().detach().cpu().item())
                else:
                    if output.ndim == 1:
                        output = output.unsqueeze(0)
                    if output.shape[-1] < 2:
                        raise RuntimeError(
                            f"Family={family} expects 2-class logits, got output shape={tuple(output.shape)}"
                        )
                    probs = torch.softmax(output, dim=-1)
                    risk_prob = float(probs[..., 1].squeeze().detach().cpu().item())
                    key_name = f"t+{horizon}"
                    probabilities[key_name] = risk_prob
                    labels[key_name] = int(risk_prob >= risk_threshold)

            if family == "xu":
                family_results[family] = {
                    "kind": "regression",
                    "models": used_models,
                    "predictions": predictions,
                }
            else:
                family_results[family] = {
                    "kind": "classification",
                    "models": used_models,
                    "risk_probabilities": probabilities,
                    "risk_labels": labels,
                    "risk_threshold": float(risk_threshold),
                }

    response = {
        "used_seq_len": seq_len,
        "used_horizons": selected_horizons,
        "requested_model_families": selected_families,
        "family_results": family_results,
    }

    # Backward compatibility:
    # when Xu is requested (default behavior), keep top-level legacy keys.
    if "xu" in family_results:
        response["models"] = family_results["xu"]["models"]
        response["predictions"] = family_results["xu"]["predictions"]
    else:
        response["models"] = {}
        response["predictions"] = {}

    return response
