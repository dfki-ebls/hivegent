"""Runtime patch for the transformers RT-DETR layout model on Apple Silicon.

``transformers`` 5.9.0 hardcodes ``torch.float64`` in RT-DETR's sinusoidal
position embedding (``build_2d_sinusoidal_position_embedding``), which the MPS
backend cannot allocate. This crashes docling's layout stage on Apple Silicon
with ``Cannot convert a MPS Tensor to float64``.

Upstream fix (still open): https://github.com/huggingface/transformers/pull/46174.
Until it ships we mirror its approach: run the float64 frequency arithmetic on
CPU, then move the result to the requested device. The wrapper is a no-op on
non-MPS devices, so CPU and CUDA behaviour is unchanged.
"""

import torch
from transformers.models.rt_detr_v2 import modeling_rt_detr_v2

__all__ = ["apply_rt_detr_mps_patch"]

_original = modeling_rt_detr_v2.build_2d_sinusoidal_position_embedding


def _mps_safe_position_embedding(
    height: int,
    width: int,
    embed_dim: int = 256,
    temperature: float = 10000.0,
    cls_token: bool = False,
    device: torch.device | None = None,
    dtype: torch.dtype = torch.float32,
) -> torch.Tensor:
    """Build the embedding off-device on MPS, where float64 is unavailable."""
    if device is not None and torch.device(device).type == "mps":
        embedding = _original(
            height,
            width,
            embed_dim,
            temperature,
            cls_token,
            device=torch.device("cpu"),
            dtype=dtype,
        )
        return embedding.to(device=device)
    return _original(
        height, width, embed_dim, temperature, cls_token, device=device, dtype=dtype
    )


def apply_rt_detr_mps_patch() -> None:
    """Idempotently install the MPS-safe position embedding into transformers."""
    if modeling_rt_detr_v2.build_2d_sinusoidal_position_embedding is not _original:
        return
    # ty models the attribute as the exact original function, so replacing it
    # with our identically-typed wrapper reads as an invalid assignment.
    modeling_rt_detr_v2.build_2d_sinusoidal_position_embedding = (
        _mps_safe_position_embedding  # ty: ignore[invalid-assignment]
    )
