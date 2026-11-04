"""Stage 2 — update: does the supersede pass retire the right memory?

The systems under test are ``tesserae.memory.supersede.run_supersede_pass``
(deterministic, or LLM-arbitrated with ``--llm``) and
``tesserae.memory.contradiction.detect_contradicting_pairs``, run over a graph
of Insight nodes built from gold memories — so this stage scores UPDATE in
isolation, with extraction held perfect.

Two datasets, one scoring rule:

* **HaluMem** — per user, one node per target memory point across all
  sessions, ordered by session. Every ``is_update`` point is paired with each
  of its ``original_memories``; an original that is not itself an earlier
  memory point verbatim (2,868 of 3,788 are paraphrases) is minted as its own
  node just before the updating node. A pair whose "newer" point is not
  later in session order (an update restating an earlier point) is set aside
  as ``inverted_gold``, not scored: recency cannot order it.
* **FactConsolidation** — one node per numbered fact, ordered by index, gold
  pairs from :func:`evals.lifecycle.datasets.conflict_pairs` (silver: derived,
  not shipped by MemoryAgentBench).

Each gold ``(newer, older)`` pair is labelled:

* ``correct`` — an edge ``newer supersedes older`` was minted;
* ``reversed`` — ``older supersedes newer``: the current fact is now hidden.
  This is the update HALLUCINATION, and the costly one;
* ``omitted`` — no edge either way: both stay live and the reader sees a
  conflict.

``spurious`` counts minted edges between two nodes that are NOT a gold pair,
and ``wrongly_hidden`` the nodes such edges retire although no gold pair says
they are stale — the user-facing cost, since a retired memory drops out of
``fresh_insights``. ``candidate_recall`` is the share of gold
pairs the Jaccard > threshold candidate generator even offers to the arbiter,
which separates "never compared" from "compared and got wrong".

``contradiction_pairs`` reports what the contradiction pass detects on the same
graph. It only considers PerformanceClaim/ComparisonClaim nodes carrying
``outperforms`` markers, so on persona memories and world facts it is 0 by
construction — recorded, because a lifecycle pass with zero coverage of the
data it would need to resolve is a finding, not a bug in this harness.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .datasets import FactGroup, HaluUser, conflict_pairs


@dataclass
class UpdateCase:
    """One graph to run the passes over, plus its gold pairs."""

    unit: str  # user uuid or FactConsolidation source
    nodes: List[Tuple[str, str, str]]  # (node id, text, session-order key)
    gold: List[Tuple[str, str]]  # (newer id, older id)
    #: gold pairs whose "newer" node is not later in session order — set
    #: aside unscored, since no recency rule could get them right
    inverted: List[Tuple[str, str]] = field(default_factory=list)


def halumem_case(user: HaluUser, *, max_sessions: Optional[int] = None) -> UpdateCase:
    nodes: List[Tuple[str, str, str]] = []
    by_text: Dict[str, str] = {}
    order: Dict[str, str] = {}
    gold: List[Tuple[str, str]] = []
    inverted: Set[Tuple[str, str]] = set()
    sessions = user.sessions[:max_sessions] if max_sessions else user.sessions
    for session in sessions:
        si = session.index
        for pos, mp in enumerate(session.memory_points):
            if not mp.is_target:
                continue
            if mp.content in by_text:
                # A verbatim restatement is the same memory, not a second node:
                # two identical names would mint a "supersedes" edge between
                # copies and be scored as a spurious update.
                new_id = by_text[mp.content]
            else:
                new_id = f"m-{si:04d}-{pos:03d}"
                nodes.append((new_id, mp.content, f"s{si:04d}b"))
                by_text[mp.content] = new_id
                order[new_id] = f"s{si:04d}b"
            if not mp.is_update:
                continue
            for k, original in enumerate(mp.original_memories):
                old_id = by_text.get(original)
                if old_id is None:
                    # Just before the NEWER node, not before this session: a
                    # restated update reuses an earlier session's node, and an
                    # original minted after it would make the gold pair point
                    # backwards in time.
                    key = order[new_id]
                    key = key[:-1] + "a" if key.endswith("b") else key
                    old_id = f"o-{si:04d}-{pos:03d}-{k}"
                    nodes.append((old_id, original, key))
                    by_text[original] = old_id
                    order[old_id] = key
                if old_id == new_id:
                    continue
                if order[new_id] <= order[old_id]:
                    # HaluMem says new_id updates old_id, but old_id was stated
                    # no earlier: the recency arbiter cannot be right here,
                    # so the pair is set aside and counted, not scored.
                    inverted.add((new_id, old_id))
                else:
                    gold.append((new_id, old_id))
    return UpdateCase(unit=user.uuid, nodes=nodes, gold=sorted(set(gold)), inverted=sorted(inverted))


def factconsolidation_case(group: FactGroup) -> UpdateCase:
    nodes = [(f"f{i:05d}", text, f"{i:06d}") for i, text in enumerate(group.facts)]
    gold = [(f"f{new:05d}", f"f{old:05d}") for new, old in conflict_pairs(group.facts)]
    return UpdateCase(unit=group.source, nodes=nodes, gold=gold)


def _graph(case: UpdateCase):
    from tesserae.research_graph import (
        SESSION_FINDING_KIND_TO_TYPE, ResearchGraph, ResearchNode,
    )

    insight = SESSION_FINDING_KIND_TO_TYPE["insight"]
    return ResearchGraph(nodes=[
        ResearchNode(id=nid, name=text, type=insight, metadata={"session_id": order})
        for nid, text, order in case.nodes
    ])


def supersede_edges(case: UpdateCase, client: Any = None, *, threshold: float = 0.55) -> Set[Tuple[str, str]]:
    """``(newer, older)`` edges the supersede pass mints over ``case``."""
    from tesserae.memory.supersede import SUPERSEDE_EDGE, run_supersede_pass

    # A fresh verdict cache per call: a shared one would replay run 1's
    # verdicts into run 2 and make test-retest agreement 1.0 by construction.
    with tempfile.TemporaryDirectory(prefix="lifecycle-supersede-") as cache:
        graph = run_supersede_pass(_graph(case), json_client=client, cache_dir=Path(cache),
                                   similarity_threshold=threshold)
    return {(e.source, e.target) for e in graph.edges if e.type == SUPERSEDE_EDGE}


def run_case(case: UpdateCase, client: Any = None, *, threshold: float = 0.55) -> dict:
    """Run both passes over ``case`` and label every gold pair."""
    from tesserae.memory.contradiction import detect_contradicting_pairs
    from tesserae.memory.supersede import jaccard

    contradictions = len(detect_contradicting_pairs(_graph(case)))
    edges = supersede_edges(case, client, threshold=threshold)
    text = {nid: t for nid, t, _ in case.nodes}
    gold_set = set(case.gold)
    gold_undirected = gold_set | {(b, a) for a, b in gold_set}
    labels: Dict[str, str] = {}
    reachable = 0
    for newer, older in case.gold:
        if jaccard(text[newer], text[older]) > threshold:
            reachable += 1
        if (newer, older) in edges:
            label = "correct"
        elif (older, newer) in edges:
            label = "reversed"
        else:
            label = "omitted"
        labels[f"{case.unit}:{newer}>{older}"] = label
    # An edge either way across a set-aside pair is neither spurious nor
    # wrongly hiding anything: that pair is unscored, not absent.
    aside = set(case.inverted) | {(b, a) for a, b in case.inverted}
    scored_edges = [e for e in edges if e not in aside]
    spurious = sum(1 for e in scored_edges if e not in gold_undirected)
    gold_older = {older for _, older in case.gold}
    wrongly_hidden = len({target for _, target in scored_edges} - gold_older)
    return {
        "unit": case.unit,
        "nodes": len(case.nodes),
        "gold_pairs": len(case.gold),
        "inverted_gold": len(case.inverted),
        "edges_minted": len(edges),
        "spurious": spurious,
        "wrongly_hidden": wrongly_hidden,
        "candidate_reachable": reachable,
        "contradiction_pairs": contradictions,
        "labels": labels,
    }


def aggregate(per_unit: Sequence[dict]) -> dict:
    def ratio(num: float, den: float) -> Optional[float]:
        return round(num / den, 4) if den else None

    counts = {"correct": 0, "reversed": 0, "omitted": 0}
    for unit in per_unit:
        for label in unit["labels"].values():
            counts[label] += 1
    gold = sum(counts.values())
    edges = sum(u["edges_minted"] for u in per_unit)
    return {
        "units": len(per_unit),
        "nodes": sum(u["nodes"] for u in per_unit),
        "gold_pairs": gold,
        "inverted_gold_set_aside": sum(u["inverted_gold"] for u in per_unit),
        "correct_rate": ratio(counts["correct"], gold),
        "hallucination_rate": ratio(counts["reversed"], gold),
        "omission_rate": ratio(counts["omitted"], gold),
        "candidate_recall": ratio(sum(u["candidate_reachable"] for u in per_unit), gold),
        "edges_minted": edges,
        "spurious_edges": sum(u["spurious"] for u in per_unit),
        "wrongly_hidden_nodes": sum(u["wrongly_hidden"] for u in per_unit),
        "edge_precision": ratio(counts["correct"], edges),
        "contradiction_pairs": sum(u["contradiction_pairs"] for u in per_unit),
    }


def run_once(cases: Sequence[UpdateCase], client: Any = None, *, threshold: float = 0.55) -> dict:
    per_unit = [run_case(c, client, threshold=threshold) for c in cases]
    labels: Dict[str, str] = {}
    for unit in per_unit:
        labels.update(unit["labels"])
    unit_rates = {
        u["unit"]: round(sum(1 for v in u["labels"].values() if v == "correct") / len(u["labels"]), 4)
        for u in per_unit if u["labels"]
    }
    return {
        "metrics": aggregate(per_unit),
        "per_unit": [{k: v for k, v in u.items() if k != "labels"} for u in per_unit],
        "labels": labels,
        "unit_rates": unit_rates,
    }
