"""Post-compile ``discusses`` edge pass — SessionFinding ↔ CodeSymbol linker.

Feature H. Wave-1 research flagged "almost nobody links prose decisions to
the code symbols they discuss" as a strategic moat: agent memory systems
mostly stop at "this session touched this file", losing the symbol-level
grounding that lets a future agent jump from a year-old decision
straight to the function it was about.

Pass shape (default-on; opt-out via ``TESSERAE_INSIGHT_SYMBOL_LINK=false``):

1. Load the project's code graph from ``.tesserae/code-graph.json``
   (produced by ``tesserae project ingest-code``). Missing file => skip
   with a log warning; the pass is purely additive so callers that
   never ran ``ingest-code`` keep working.
2. Build a ``name -> [code-symbol nodes]`` index over the code graph.
   One name can map to multiple nodes — ``tesserae/code_graph_extractor.py``
   intentionally preserves same-display-name symbols across files via
   module-qualified ``id_seed`` (see the CODE_SYMBOL_TYPES "do not
   collapse by display name" invariant on the graph-builder side).
3. For each session-finding node in the input ResearchGraph, scan its
   ``name`` (the body text) for two candidate shapes:

   * **Backtick identifiers** — ``\\`foo\\``
   * **Dotted paths** — ``Class.method`` / ``pkg.module.fn``

   Pure-stopword false-positives (``len``, ``int``, ``True``, ``self``,
   ...) and single-character names are blocked.
4. Resolve each candidate against the index. Exact match wins; for
   ambiguous matches (same name in multiple files) we mint a
   ``discusses`` edge to **every** candidate — downstream surfaces
   can disambiguate.
5. De-duplicate against edges already on the input graph so reruns
   are idempotent.

The pass is strictly additive: no nodes/edges are deleted. Mints onto
``graph.edges`` in-place and returns the same graph for chaining (matches
``run_supersede_pass`` shape).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..research_graph import (
    SESSION_FINDING_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)

logger = logging.getLogger(__name__)


DISCUSSES_EDGE = "discusses"
"""Edge kind minted by this pass. Listed in :data:`ALLOWED_EDGE_TYPES`."""


# Symbol granularities we link findings to.
#
# Included: CodeFunction / CodeClass / CodeMethod plus the broader
# CodeGraph-adapter additions (CodeInterface, CodeTrait, CodeStruct,
# CodeEnum, CodeEnumMember, CodeTypeAlias, CodeVariable, CodeConstant,
# CodeRoute, CodeComponent, CodeField, CodeNamespace, CodeSymbol).
#
# Excluded:
#   * CodeFile / CodeModule — bare filenames and dotted module paths
#     appear in finding text as "this happened in X" framing far more
#     than as discussions of the file/module itself; linking them would
#     dominate signal.
#   * CodeParameter — function-parameter names like ``request``,
#     ``response``, ``data`` are dense noise and almost never the
#     subject of a session insight.
LINKABLE_CODE_SYMBOL_TYPES: frozenset = frozenset(
    {
        # original Python-AST extractor types
        "CodeFunction",
        "CodeClass",
        "CodeMethod",
        # CodeGraph-adapter additions
        "CodeInterface",
        "CodeTrait",
        "CodeStruct",
        "CodeEnum",
        "CodeEnumMember",
        "CodeTypeAlias",
        "CodeVariable",
        "CodeConstant",
        "CodeRoute",
        "CodeComponent",
        "CodeField",
        "CodeNamespace",
        "CodeSymbol",  # generic fallback for any kind not mapped above
    }
)

# Types whose `name` may be stored as a dotted qualified form
# (``Owner.member``) and SHOULD ALSO be indexed under their bare
# tail (``member``). CodeGraph stores methods, fields, and enum
# members this way. Functions, classes, traits, etc. typically use
# their bare name already.
_DOTTED_TAIL_TYPES: frozenset = frozenset(
    {"CodeMethod", "CodeField", "CodeEnumMember"}
)


# Python keywords + builtins + common idiom tokens that masquerade as
# identifiers but should never resolve to a project symbol. Keep this
# list short and high-signal — the goal is to nuke trivial false
# positives, not to gate the whole match pipeline.
_STOPWORDS: frozenset = frozenset(
    {
        # builtins / keywords
        "len", "int", "str", "dict", "list", "set", "tuple",
        "print", "range", "type", "bool", "float", "bytes",
        "True", "False", "None",
        # idiom tokens
        "self", "cls",
        # common short noise
        "id", "fn", "do", "is", "or", "to", "in", "on",
    }
)


# Backticked identifier — `foo`, `Foo`, `_bar`. Allows underscores and
# digits after the first character. Identifier must be at least 2
# chars; single-char names are blocked because they're almost always
# loop variables that happened to be backticked.
_BACKTICK_IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

# Dotted path — Class.method, pkg.module.fn, A.B.C. Must have at least
# one dot to fire.
_DOTTED_PATH = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b"
)

# Bare identifier — any standalone word-like token. Matches a LOT of
# noise (every English word that happens to be a valid identifier),
# but the symbol-index lookup is the real filter: only candidates that
# also exist as a CodeFunction / CodeClass / CodeMethod in the project
# get an edge. Without this pass we miss unbackticked symbol mentions
# like ``"use the global bar"`` — a common shape in prose decisions.
_BARE_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")


# ---------------------------------------------------------------------------
# Env flag — default-on, opt-out via TESSERAE_INSIGHT_SYMBOL_LINK=false
# ---------------------------------------------------------------------------


def insight_symbol_link_enabled() -> bool:
    """Decide whether to run the insight↔symbol link pass.

    Default-on (post-v0.3.0). Unlike the LLM-backed passes
    (community summaries, supersedes) this is pure-Python text
    scanning + an in-memory index lookup, so it's cheap enough to
    run unconditionally. When no code graph exists at
    ``.tesserae/code-graph.json`` the pass no-ops cleanly, so
    projects without CodeGraph pay nothing.

    To opt out explicitly, set ``TESSERAE_INSIGHT_SYMBOL_LINK`` to
    one of: ``false``, ``0``, ``no``, ``off``. Unset / any other
    value means enabled.
    """
    raw = (os.environ.get("TESSERAE_INSIGHT_SYMBOL_LINK") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


# ---------------------------------------------------------------------------
# Symbol index
# ---------------------------------------------------------------------------


def load_code_graph_nodes(code_graph_path: Path) -> List[Dict[str, Any]]:
    """Read the raw node payloads from ``code-graph.json``.

    We intentionally do NOT round-trip through ``load_graph_file`` here —
    that helper instantiates ``ResearchNodeType`` and ``ResearchNode``
    objects we never need. A raw dict-walk is enough and avoids dragging
    the full enum coercion path into a best-effort lookup. Returns ``[]``
    on any I/O or JSON error and logs at WARNING — the pass is
    advisory, not load-bearing.
    """
    try:
        payload = json.loads(code_graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "insight_symbol_link: failed to read %s (%s); skipping pass",
            code_graph_path, exc,
        )
        return []
    raw_nodes = payload.get("nodes") or []
    if not isinstance(raw_nodes, list):
        return []
    return [n for n in raw_nodes if isinstance(n, dict)]


def build_symbol_index(
    code_graph_nodes: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Group linkable code-symbol nodes by their display ``name``.

    Same name can map to multiple nodes — that's a feature, not a bug
    (see module docstring step 2). The returned dict preserves insertion
    order so resolution is deterministic across runs.
    """
    index: Dict[str, List[Dict[str, Any]]] = {}
    for node in code_graph_nodes:
        node_type = str(node.get("type") or "")
        if node_type not in LINKABLE_CODE_SYMBOL_TYPES:
            continue
        name = str(node.get("name") or "").strip()
        if not name:
            continue
        index.setdefault(name, []).append(node)
        # Some types are stored as dotted qualified forms (``Owner.member``).
        # Also key the bare tail so a finding that just says ``\`member\`\``
        # resolves to it when no other top-level ``member`` exists. Doesn't
        # shadow the qualified form — both keys live in the index.
        if node_type in _DOTTED_TAIL_TYPES and "." in name:
            bare = name.rsplit(".", 1)[-1]
            if bare and bare != name:
                index.setdefault(bare, []).append(node)
    return index


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------


def _extract_candidates(text: str) -> Tuple[List[str], List[str]]:
    """Return ``(strong, weak)`` candidate lists in document order.

    * **Strong** — backticked identifiers and dotted paths. High-signal
      shapes; resolved against the index unconditionally.
    * **Weak** — bare identifiers. Resolved ONLY against the project's
      symbol index (the index lookup is the real false-positive filter
      for these); never matched by name alone.

    Order is preserved so logs/edges are stable across runs. Stopwords
    and 1-char names are dropped at this layer.
    """
    if not text:
        return [], []
    seen: Set[str] = set()
    strong: List[str] = []
    weak: List[str] = []

    for match in _BACKTICK_IDENT.finditer(text):
        cand = match.group(1)
        if _accept(cand) and cand not in seen:
            seen.add(cand)
            strong.append(cand)
    for match in _DOTTED_PATH.finditer(text):
        cand = match.group(1)
        if _accept(cand) and cand not in seen:
            seen.add(cand)
            strong.append(cand)
        # Also try the ``Class.method`` 2-segment tail for longer paths
        # — common when someone writes ``pkg.mod.A.foo``. The bare
        # ``A.foo`` is what the CodeMethod index keys on.
        parts = cand.split(".")
        if len(parts) > 2:
            tail = ".".join(parts[-2:])
            if _accept(tail) and tail not in seen:
                seen.add(tail)
                strong.append(tail)
    for match in _BARE_IDENT.finditer(text):
        cand = match.group(1)
        if _accept(cand) and cand not in seen:
            seen.add(cand)
            weak.append(cand)
    return strong, weak


def _accept(candidate: str) -> bool:
    """Reject single-char / stopword candidates.

    Dotted candidates are accepted as long as no segment is a stopword
    on its own — a name like ``A.foo`` is fine (single-char ``A`` is a
    legitimate class), but ``self.foo`` is rejected because ``self``
    poisons it. Bare identifiers must be ≥2 chars to filter loop-var
    chatter.
    """
    if not candidate:
        return False
    if "." in candidate:
        segments = candidate.split(".")
        if any(not seg or seg in _STOPWORDS for seg in segments):
            return False
        return True
    if len(candidate) < 2:
        return False
    if candidate in _STOPWORDS:
        return False
    return True


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def find_symbol_mentions(
    finding: ResearchNode,
    symbol_index: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Return the list of code-symbol nodes mentioned in ``finding.name``.

    Pure function — exposed so the MCP ``find_code_symbol_mentions`` tool
    can call it without re-running the whole pass. Result entries are
    the raw code-graph node payload dicts; callers can pull
    ``id`` / ``name`` / ``type`` directly. Both strong (backticked /
    dotted) and weak (bare) candidates are resolved against the index;
    weak candidates are silently filtered out when no project symbol
    exists, so noise from prose is bounded by the code graph itself.
    """
    strong, weak = _extract_candidates(finding.name)
    matches: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for cand in (*strong, *weak):
        for node in symbol_index.get(cand, ()):
            nid = str(node.get("id") or "")
            if not nid or nid in seen_ids:
                continue
            seen_ids.add(nid)
            matches.append(node)
    return matches


def run_insight_symbol_link_pass(
    graph: ResearchGraph,
    *,
    code_graph_path: Path,
) -> ResearchGraph:
    """Mint ``discusses`` edges from session findings to code symbols.

    Idempotent: re-running on the same inputs is a no-op because we
    de-duplicate against the input graph's edges before appending. The
    pass is purely additive — no nodes/edges are removed.
    """
    if not code_graph_path.exists():
        logger.warning(
            "insight_symbol_link: %s not found; skipping (run "
            "`tesserae project ingest-code` to populate it)",
            code_graph_path,
        )
        return graph

    raw_nodes = load_code_graph_nodes(code_graph_path)
    if not raw_nodes:
        return graph
    symbol_index = build_symbol_index(raw_nodes)
    if not symbol_index:
        return graph

    finding_type_values: Set[str] = {t.value for t in SESSION_FINDING_TYPES}
    existing_edges: Set[Tuple[str, str, str]] = {
        (e.source, e.type, e.target) for e in graph.edges
    }

    minted = 0
    for node in graph.nodes:
        kind = node.type.value if hasattr(node.type, "value") else str(node.type)
        if kind not in finding_type_values:
            continue
        for symbol in find_symbol_mentions(node, symbol_index):
            target_id = str(symbol.get("id") or "")
            if not target_id:
                continue
            edge_key = (node.id, DISCUSSES_EDGE, target_id)
            if edge_key in existing_edges:
                continue
            graph.edges.append(
                ResearchEdge(
                    source=node.id,
                    target=target_id,
                    type=DISCUSSES_EDGE,
                    evidence=f"{node.id} mentions {symbol.get('name')}",
                    metadata={
                        "extractor": "memory.insight_symbol_link",
                        "symbol_name": str(symbol.get("name") or ""),
                        "symbol_type": str(symbol.get("type") or ""),
                        "finding_kind": kind,
                    },
                )
            )
            existing_edges.add(edge_key)
            minted += 1

    if minted:
        logger.info(
            "memory.insight_symbol_link: minted %d discusses edges", minted
        )
    return graph


__all__ = [
    "DISCUSSES_EDGE",
    "LINKABLE_CODE_SYMBOL_TYPES",
    "build_symbol_index",
    "find_symbol_mentions",
    "insight_symbol_link_enabled",
    "load_code_graph_nodes",
    "run_insight_symbol_link_pass",
]
