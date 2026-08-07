"""Memory primitives — decay and supersede over session findings.

A-MEM / MemoryBank inspired layer that sits on top of the session-finding
nodes minted by :mod:`tesserae.session_graph`. See:

* :mod:`tesserae.memory.decay` — Ebbinghaus-style freshness score.
* :mod:`tesserae.memory.supersede` — post-compile near-duplicate detection.

``insight_symbol_link`` used to live here too: feature H, ``discusses`` edges
from session findings to the code symbols they mention, resolved against
``code-graph.json``. It is gone with the code layer, and it never worked —
every one of the 15,873 ``discusses`` edges in the compiled store dangled,
because the two producers of ``code-graph.json`` minted node ids under
incompatible schemes.
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
