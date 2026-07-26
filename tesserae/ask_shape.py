"""Question-SHAPE router for ``ask``.

``ask_router`` picks WHICH project a question goes to. This module picks HOW it
is answered: the KG planner (a planner LLM call, up to 5 graph primitives, and a
synthesis prompt carrying up to 5x2500 chars of dated evidence) or the classic
BM25 wiki path (one synthesis call over top_k pages, no graph load, no temporal
projection over 15k edges). Both paths already exist and both already cite; the
router only picks between them.

Graph cues are checked BEFORE lookup prefixes, and an unmatched question defaults
to ``graph``. That asymmetry is the design: a mis-route to graph costs money and
still answers, a mis-route to lookup silently returns a shallower answer. Fail
toward the expensive side — the same "unsure never means wrong" stance
``ask_router`` takes with its federated fallback.

Deterministic: the only input is ``question``. No I/O, no env, no clock, no
registry; the leftmost regex match supplies the reason, so there is no
collect-into-a-set-and-join step whose order could drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Shape values mirror `tesserae ask --route`.
SHAPE_LOOKUP = "lookup"
SHAPE_GRAPH = "graph"


@dataclass(frozen=True)
class Shape:
    shape: str   # lookup | graph
    reason: str


# Cues that a question needs the graph: dated evidence, causality, multi-hop
# synthesis, or agent activity. Checked FIRST, over the whole question.
_GRAPH_CUE = re.compile(
    r"\b("
    # temporal — the benchmark gap where graphs beat vector RAG hardest
    r"recently|lately|latest|last (week|month)|since|currently|status|"
    r"what changed|changed|evolved|history|when did|over time|timeline|"
    # causal / decision
    r"why|rationale|reason|who decided|decided|decides|decide|chose|chosen|"
    r"trade-?off|instead of|"
    # multi-hop / corpus synthesis
    r"summari[sz]e|overview|across|relationship between|difference between|"
    r"compare|impact|depends on|affects|all the |all |list all|how many|"
    # activity
    r"session|commit|PR|worked on|progress"
    r")\b",
    re.I,
)

# Single-entity definitional lookups: BM25 over wiki pages answers these as well
# as the planner does, for one fewer LLM roundtrip. Only consulted when NO graph
# cue fired.
_LOOKUP_PREFIX = re.compile(
    r"^(what (is|are|'s)|who is|which (file|module)|where is|define|"
    r"definition of|meaning of|how (does|do|is))\b",
    re.I,
)


def classify_ask_shape(question: str) -> Shape:
    """Classify a question as ``lookup`` (BM25) or ``graph`` (KG planner)."""

    q = (question or "").strip().lower()
    cue = _GRAPH_CUE.search(q)
    if cue:
        return Shape(SHAPE_GRAPH, f"graph cue {cue.group(0).strip()!r}")
    prefix = _LOOKUP_PREFIX.match(q)
    if prefix:
        return Shape(SHAPE_LOOKUP, f"{prefix.group(0).strip()!r} lookup shape, no graph cue")
    return Shape(SHAPE_GRAPH, "default: graph (no lookup shape recognized)")


__all__ = ["SHAPE_GRAPH", "SHAPE_LOOKUP", "Shape", "classify_ask_shape"]
