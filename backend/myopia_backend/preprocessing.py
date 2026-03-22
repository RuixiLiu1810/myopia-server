from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
from PIL import Image
import torch


class VisitLike(Protocol):
    image_path: str
    se: float


def image_to_tensor(image: Image.Image) -> torch.Tensor:
    arr = np.array(image.convert("RGB"), dtype=np.float32)
    arr = arr / 255.0
    arr = np.transpose(arr, (2, 0, 1))
    return torch.from_numpy(arr)


def prepare_inputs(
    visits: Sequence[VisitLike],
    seq_len: int,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor]:
    if len(visits) < seq_len:
        raise ValueError(f"Need at least {seq_len} visits, got {len(visits)}")

    selected = visits[-seq_len:]

    images: list[torch.Tensor] = []
    nums: list[float] = []
    for visit in selected:
        image_path = Path(visit.image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image file not found: {image_path}")

        image = Image.open(image_path)
        images.append(image_to_tensor(image))
        nums.append(float(visit.se))

    image_tensor = torch.stack(images, dim=0).unsqueeze(0).to(device)
    num_tensor = torch.tensor(nums, dtype=torch.float32).unsqueeze(0).unsqueeze(-1).to(device)
    return image_tensor, num_tensor
