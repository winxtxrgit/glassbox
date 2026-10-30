"""White-box lenses — the capstone, and the reason the weights are kept local.

Phases 1-4 interrogated a model through its output alone. This package opens a
small local model up and looks at the mechanism directly: how the prediction
forms layer by layer (logit lens), and which input tokens actually move it
(occlusion attribution — the inside-the-model echo of the counterfactual erasure
in :mod:`glassbox.faithfulness`).

This is the one piece that needs real model weights, so it lives behind an
optional extra: ``pip install -e ".[whitebox]"`` (torch + transformers). The core
package stays dependency-free; import this only when you mean to.
"""

from __future__ import annotations

from .lenses import (
    LayerPrediction,
    LensResult,
    TokenAttribution,
    analyze,
    available,
    occlude,
)

__all__ = [
    "LayerPrediction",
    "LensResult",
    "TokenAttribution",
    "analyze",
    "available",
    "occlude",
]
