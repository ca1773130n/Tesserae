"""Memory primitives — decay scoring and supersede-edge minting.

A-MEM / MemoryBank inspired layer that sits on top of the session-finding
nodes minted by :mod:`tesserae.session_graph`. See :mod:`tesserae.memory.decay`
for the Ebbinghaus-style score and :mod:`tesserae.memory.supersede` for the
post-compile near-duplicate detection pass.
"""

from __future__ import annotations

from .decay import compute_decay_score
from .supersede import (
    SUPERSEDE_EDGE,
    SupersedeJudgement,
    run_supersede_pass,
    supersede_pass_enabled,
)

__all__ = [
    "compute_decay_score",
    "SUPERSEDE_EDGE",
    "SupersedeJudgement",
    "run_supersede_pass",
    "supersede_pass_enabled",
]
