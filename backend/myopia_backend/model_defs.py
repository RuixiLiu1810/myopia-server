from __future__ import annotations

from pathlib import Path
import sys

import torch
from torch import nn


class ResNet34Encoder(nn.Module):
    """Compatibility encoder for legacy checkpoints."""

    def __init__(self, pooling: str = "avg"):
        super().__init__()
        try:
            from torchvision import models
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("torchvision is required for ResNet34Encoder.") from exc

        # Keep weights=None to avoid network downloads in deployment environments.
        resnet34 = models.resnet34(weights=None)
        self.features = nn.Sequential(*list(resnet34.children())[:-2])

        if pooling == "avg":
            self.pooling = nn.AdaptiveAvgPool2d((1, 1))
        elif pooling == "max":
            self.pooling = nn.AdaptiveMaxPool2d((1, 1))
        else:
            raise ValueError("pooling must be 'avg' or 'max'")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.pooling(x)
        return x.view(x.size(0), -1)


class ImageSequenceModel(nn.Module):
    """Compatibility sequence model for legacy checkpoints."""

    def __init__(self, cnn_encoder: nn.Module, seq_len: int, hidden_size: int, output_size: int):
        super().__init__()
        self.cnn_encoder = cnn_encoder
        self.seq_len = seq_len
        self.rnn = nn.LSTM(input_size=512 + 1, hidden_size=hidden_size, batch_first=True)
        self.dropout = nn.Dropout(0.1)
        self.linear = nn.Linear(hidden_size, output_size)

    def forward(self, image_sequence: torch.Tensor, feature_sequence: torch.Tensor) -> torch.Tensor:
        cnn_features = []
        for t in range(self.seq_len):
            x = image_sequence[:, t, :, :, :]
            cnn_out = self.cnn_encoder(x)
            cnn_features.append(cnn_out)

        cnn_features = torch.stack(cnn_features, dim=1)
        rnn_input = torch.cat([cnn_features, feature_sequence], dim=-1)
        _, (h_n, _) = self.rnn(rnn_input)
        h_n = self.dropout(h_n)
        return self.linear(h_n.squeeze(0))


def register_notebook_classes_for_unpickle() -> None:
    main_mod = sys.modules.get("__main__")
    if main_mod is not None:
        setattr(main_mod, "ResNet34Encoder", ResNet34Encoder)
        setattr(main_mod, "ImageSequenceModel", ImageSequenceModel)


def build_xu_model(
    seq_len: int,
    hidden_size: int = 256,
    output_size: int = 1,
    pooling: str = "avg",
) -> ImageSequenceModel:
    if seq_len < 1 or seq_len > 5:
        raise ValueError(f"seq_len must be in [1,5], got {seq_len}")
    encoder = ResNet34Encoder(pooling=pooling)
    return ImageSequenceModel(
        cnn_encoder=encoder,
        seq_len=seq_len,
        hidden_size=hidden_size,
        output_size=output_size,
    )


def model_file_exists(path: str | Path) -> bool:
    return Path(path).exists()
