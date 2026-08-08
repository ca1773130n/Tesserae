"""Memory primitives — decay, supersede, and SessionFinding ↔ CodeSymbol links.

A-MEM / MemoryBank inspired layer that sits on top of the session-finding
nodes minted by :mod:`tesserae.session_graph`. See:

* :mod:`tesserae.memory.decay` — Ebbinghaus-style freshness score.
* :mod:`tesserae.memory.supersede` — post-compile near-duplicate detection.
* :mod:`tesserae.memory.insight_symbol_link` — feature H, ``discusses``
  edges from session findings to the code symbols (CodeFunction /
  CodeClass / CodeMethod) they mention.
"""

from __future__ import annotations

from .decay import compute_decay_score
from .insight_symbol_link import (
    DISCUSSES_EDGE,
    build_symbol_index,
    find_symbol_mentions,
    insight_symbol_link_enabled,
    run_insight_symbol_link_pass,
)
from .supersede import (
    SUPERSEDE_EDGE,
    SupersedeJudgement,
    run_supersede_pass,
    supersede_pass_enabled,
)

__all__ = [
    "compute_decay_score",
    "DISCUSSES_EDGE",
    "build_symbol_index",
    "find_symbol_mentions",
    "insight_symbol_link_enabled",
    "run_insight_symbol_link_pass",
    "SUPERSEDE_EDGE",
    "SupersedeJudgement",
    "run_supersede_pass",
    "supersede_pass_enabled",
]
