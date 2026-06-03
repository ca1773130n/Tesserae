"""Structural post-extraction guidance filter for the deterministic extractor.

The deterministic extractor cannot re-run an LLM with feedback baked in, so it
honors extraction guidance by applying a *structural* post-filter to the graph
it already produced. We parse each guidance bullet for the small set of
STRUCTURAL directives below and apply them as deterministic graph transforms:

  * "don't extract X as a Concept" / "drop nodes named X" / "remove node X"
        -> remove every node whose name matches X (and its incident edges).
  * "replace X with Y" / "rename X to Y"
        -> rename ``node.name`` X -> Y (case-insensitive exact match).
  * "remove link A -> B" / "drop edge A -> B"
        -> drop edges whose endpoint *names* match A and B.

Semantic guidance (e.g. "be more specific", "prefer canonical titles") is OUT
of scope for the deterministic filter — those only shape the Claude
sub-extractor's prompt (05-RESEARCH Open Question 2). Unrecognized bullets are
ignored, never guessed at.

Contract: :func:`apply_guidance_filter` is PURE and DETERMINISTIC. The same
graph + same bullets always yields an identical output graph, and an empty
bullet list returns the input graph unchanged (byte-identical no-op — the
default path when no feedback has accumulated).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Dict, List, Sequence

from .research_graph import ResearchEdge, ResearchGraph, ResearchNode


# Directive patterns. Each is anchored to a normalized (lower-cased, collapsed
# whitespace) bullet line. We keep these conservative so we never silently
# delete nodes on an ambiguous phrasing.
_DROP_RE = re.compile(
    r"(?:don'?t extract|do not extract|drop node[s]?|remove node[s]?|"
    r"stop extracting)\s+(?:named\s+)?[\"'`]?(?P<name>.+?)[\"'`]?"
    r"(?:\s+as\s+(?:a|an)\s+\w+)?$"
)
_REPLACE_RE = re.compile(
    r"(?:replace|rename)\s+[\"'`]?(?P<old>.+?)[\"'`]?\s+(?:with|to|->)\s+"
    r"[\"'`]?(?P<new>.+?)[\"'`]?$"
)
_REMOVE_LINK_RE = re.compile(
    r"(?:remove link|drop link|remove edge|drop edge)\s+"
    r"[\"'`]?(?P<a>.+?)[\"'`]?\s*(?:->|to)\s*[\"'`]?(?P<b>.+?)[\"'`]?$"
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def _strip_bullet(text: str) -> str:
    s = text.strip()
    if s.startswith("- "):
        s = s[2:]
    return s.strip()


def apply_guidance_filter(
    graph: ResearchGraph, bullets: Sequence[str]
) -> ResearchGraph:
    """Apply structural guidance bullets to ``graph`` deterministically.

    Pure: returns a NEW :class:`ResearchGraph`; the input is never mutated.
    Empty ``bullets`` returns ``graph`` unchanged (identity no-op). Bullets
    that match no structural directive are ignored.
    """
    if not bullets:
        return graph

    drop_names: set[str] = set()
    renames: List[tuple[str, str]] = []  # (old_norm, new_value) preserve order
    remove_links: List[tuple[str, str]] = []  # (a_norm, b_norm)

    for raw in bullets:
        line = _strip_bullet(raw)
        if not line:
            continue
        norm = _norm(line)
        if (m := _REMOVE_LINK_RE.match(norm)):
            remove_links.append((m.group("a").strip(), m.group("b").strip()))
        elif (m := _REPLACE_RE.match(norm)):
            # Keep the new value's original casing by re-matching on the raw line.
            raw_m = _REPLACE_RE.match(re.sub(r"\s+", " ", line.strip()))
            new_val = raw_m.group("new").strip() if raw_m else m.group("new").strip()
            renames.append((m.group("old").strip(), new_val))
        elif (m := _DROP_RE.match(norm)):
            drop_names.add(m.group("name").strip())

    if not (drop_names or renames or remove_links):
        return graph

    # 1. Drop matching nodes; collect their ids so we can prune incident edges.
    dropped_ids: set[str] = set()
    kept_nodes: List[ResearchNode] = []
    for node in graph.nodes:
        if _norm(node.name) in drop_names:
            dropped_ids.add(node.id)
            continue
        kept_nodes.append(node)

    # 2. Rename matching node names (case-insensitive exact match on name).
    rename_map: Dict[str, str] = {old.lower(): new for old, new in renames}
    if rename_map:
        renamed: List[ResearchNode] = []
        for node in kept_nodes:
            new_name = rename_map.get(node.name.lower())
            if new_name is not None and new_name != node.name:
                renamed.append(replace(node, name=new_name))
            else:
                renamed.append(node)
        kept_nodes = renamed

    # 3. Drop edges incident to a dropped node, plus explicit remove-link
    #    directives matched on endpoint NAMES (resolved via the original graph).
    name_by_id = {n.id: _norm(n.name) for n in graph.nodes}
    link_pairs = {(a.lower(), b.lower()) for a, b in remove_links}
    kept_edges: List[ResearchEdge] = []
    for edge in graph.edges:
        if edge.source in dropped_ids or edge.target in dropped_ids:
            continue
        if link_pairs:
            pair = (name_by_id.get(edge.source, ""), name_by_id.get(edge.target, ""))
            if pair in link_pairs:
                continue
        kept_edges.append(edge)

    return ResearchGraph(nodes=kept_nodes, edges=kept_edges)
