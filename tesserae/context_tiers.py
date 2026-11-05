"""Progressive-disclosure tiers: an L0 line and an L1 overview per node (R7b).

``compile_context`` packs whole bodies until its budget runs out, so the
budget buys depth on a handful of nodes and nothing at all on the rest. With
``tiers=True`` it packs full bodies for the top ranks and one L0 line (plus the
node id, for drilling down) for the ranked tail — breadth at a fraction of the
tokens.

* **L0** — one extractive line, at most :data:`L0_MAX_CHARS` (~25 tokens):
  the node's name and the first sentence of its description.
* **L1** — an extractive overview, at most :data:`L1_MAX_CHARS` (~150 tokens):
  the leading sentences of the description.

Community overviews are covered by the same entries: a community's summary is
a ``CommunitySummary`` node, so it gets an L0 and L1 like any other node
whenever the community-summary pass has run.

Both tiers are pure functions of node content, so they can always be computed
on the fly. The sidecar (``.tesserae/context_tiers.json``) exists so a compile
pays for them once and a later, non-extractive tier (an LLM-written L1) has
somewhere to live. It is keyed on the sha256 of the content the tiers were
derived from, never on the node id: a renamed or rewritten node simply misses
and is recomputed, and no entry can describe content that no longer exists
under its key. ``graph.json`` is untouched, so it stays byte-idempotent.

OPT-IN. The compile writes the sidecar only when ``TESSERAE_CONTEXT_TIERS`` is
truthy, and ``compile_context`` packs tiers only when asked: whether tiered
bundles keep answer accuracy at fewer tokens has not been measured (the
LME-S / 148-paper accuracy-vs-tokens sweep in the roadmap has not been run).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .research_graph import ResearchGraph, ResearchNode

_LOG = logging.getLogger(__name__)

#: Env flag that makes ``compile`` write the tier sidecar.
CONTEXT_TIERS_ENV = "TESSERAE_CONTEXT_TIERS"
SIDECAR_NAME = "context_tiers.json"
SIDECAR_VERSION = 1
#: ~25 tokens at 4 chars per token.
L0_MAX_CHARS = 100
#: ~150 tokens at 4 chars per token.
L1_MAX_CHARS = 600

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MARKUP = re.compile(r"[#*_`>|]+")
_ELLIPSIS = "…"

Tiers = Dict[str, str]


def content_key(node: ResearchNode) -> str:
    """sha256 of exactly the content the tiers are extracted from."""
    text = f"{node.type.value}\n{node.name}\n{node.description or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean(text: str) -> str:
    return " ".join(_MARKUP.sub(" ", text or "").split())


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - len(_ELLIPSIS)]
    space = head.rfind(" ")
    if space > limit // 2:
        head = head[:space]
    return head.rstrip(" ,;:-") + _ELLIPSIS


def l0_line(node: ResearchNode) -> str:
    """One extractive line: the name and the description's first sentence."""
    name = _clean(node.name) or node.id
    sentences = [s for s in _SENTENCE_END.split(_clean(node.description or "")) if s]
    if not sentences:
        return _cut(name, L0_MAX_CHARS)
    first = sentences[0]
    line = first if first.lower().startswith(name.lower()) else f"{name}: {first}"
    return _cut(line, L0_MAX_CHARS)


def l1_overview(node: ResearchNode) -> str:
    """The leading sentences of the description, up to :data:`L1_MAX_CHARS`."""
    text = _clean(node.description or "")
    if not text:
        return l0_line(node)
    out = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > L1_MAX_CHARS:
            break
        out = candidate
    return out or _cut(text, L1_MAX_CHARS)


def compute_tiers(node: ResearchNode) -> Tiers:
    return {"l0": l0_line(node), "l1": l1_overview(node)}


def build_tiers(graph: ResearchGraph) -> Dict[str, Tiers]:
    return {content_key(node): compute_tiers(node) for node in graph.nodes}


def render_sidecar(graph: ResearchGraph) -> str:
    """The sidecar's bytes — sorted, so the same graph always writes the same file."""
    payload = {"version": SIDECAR_VERSION, "entries": build_tiers(graph)}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def sidecar_path(project_root: Path) -> Path:
    return Path(project_root) / ".tesserae" / SIDECAR_NAME


def load_sidecar(project_root: Optional[Path]) -> Optional[Dict[str, Tiers]]:
    """The sidecar's entries, or ``None`` when there is none to read.

    A sidecar that exists but cannot be used is logged at WARNING and treated
    as absent: tiers are recomputable, so a damaged cache must cost time, not
    the answer.
    """
    if project_root is None:
        return None
    path = sidecar_path(project_root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _LOG.warning("context tiers sidecar %s unreadable (%s); recomputing", path, exc)
        return None
    if not isinstance(payload, dict) or payload.get("version") != SIDECAR_VERSION:
        _LOG.warning("context tiers sidecar %s has an unknown version; recomputing", path)
        return None
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else None


def tier_lookup(
    project_root: Optional[Path],
) -> Tuple[Callable[[ResearchNode], Tiers], str]:
    """A ``node -> tiers`` function and where its answers come from.

    The source is ``"sidecar"`` when a sidecar was loaded (misses still fall
    back to extraction, per node) and ``"computed"`` when there is none.
    """
    entries = load_sidecar(project_root)
    if entries is None:
        return compute_tiers, "computed"

    def lookup(node: ResearchNode) -> Tiers:
        hit = entries.get(content_key(node))
        if isinstance(hit, dict) and isinstance(hit.get("l0"), str):
            return hit
        return compute_tiers(node)

    return lookup, "sidecar"
