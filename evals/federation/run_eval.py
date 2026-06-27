"""Federation semantic-link eval: data-back the defaults (min_cosine, edge weight).

Run:  .venv/bin/python -m evals.federation.run_eval   (needs tesserae[semantic])

Two measurements, both with the REAL embedding backend (model2vec):

1. THRESHOLD sweep — over the labeled fixture, link nodes at each ``min_cosine``
   and score the produced cross-project links against the gold same-idea pairs:
   precision / recall / F1. Recommends the F1-maximizing threshold and shows
   where the shipped default (0.55) sits.

2. WEIGHT sweep — on a controlled bridge scenario (a query that lexically matches
   project A, whose relevant content lives in project B reachable ONLY via a
   semantic bridge), report the PPR rank of the bridged B-content vs A's own
   neighbour at several ``shares_concept_with`` weights — so the default 0.50
   "nudge" can be seen to surface B without swamping A.

``compute_threshold_rows`` / ``compute_weight_rows`` are imported by the
regression test; ``main`` also writes evals/federation/report.md.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import List, Tuple

from tesserae.federation import (
    DEFAULT_SEMANTIC_MIN_COSINE,
    SEMANTIC_BRIDGE_PPR_WEIGHT,
    federate_graphs,
)
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.retrieval.hybrid import active_embedding_backend
from tesserae.retrieval.ppr import personalized_pagerank

from .fixture import CONCEPTS, gold_cross_project_pairs

_THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
_WEIGHTS = [0.0, 0.25, 0.5, 1.0, 2.0]  # 0.0 == no bridge (identity-only)
# Mirror the SHIPPED defaults (imported, not re-declared) so the eval + its
# regression test always score the values production actually uses.
DEFAULT_MIN_COSINE = DEFAULT_SEMANTIC_MIN_COSINE
DEFAULT_EDGE_WEIGHT = SEMANTIC_BRIDGE_PPR_WEIGHT


def _model2vec_backend():
    """Force model2vec (fail-loud if it can't construct) — the eval's numbers
    are only meaningful with real embeddings, never the hash stub."""
    return active_embedding_backend("model2vec")


def _fixture_graphs():
    by_project = defaultdict(list)
    for index, (project, name, desc, _cluster) in enumerate(CONCEPTS):
        by_project[project].append(
            ResearchNode(id=f"Concept:{index}", name=name, type=ResearchNodeType.CONCEPT, description=desc)
        )
    return [(p, ResearchGraph(nodes=ns, edges=[])) for p, ns in sorted(by_project.items())]


def compute_threshold_rows(backend=None) -> List[dict]:
    backend = backend or _model2vec_backend()
    named = _fixture_graphs()
    gold = gold_cross_project_pairs(lambda project, index: f"{project}::Concept:{index}")
    rows = []
    for threshold in _THRESHOLDS:
        fed, _ = federate_graphs(
            named, semantic=True, semantic_backend=backend, semantic_min_cosine=threshold,
        )
        links = {
            frozenset((e.source, e.target)) for e in fed.edges if e.type == "shares_concept_with"
        }
        tp = len(links & gold)
        fp = len(links - gold)
        fn = len(gold - links)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        rows.append({"threshold": threshold, "tp": tp, "fp": fp, "fn": fn,
                     "precision": precision, "recall": recall, "f1": f1})
    return rows


def _bridge_graph(backend):
    a = ResearchGraph(
        nodes=[
            ResearchNode(id="Concept:rw", name="Random-walk graph ranking",
                         type=ResearchNodeType.CONCEPT, description="ranking graph nodes by repeated random walks"),
            ResearchNode(id="Concept:spec", name="Spectral graph methods",
                         type=ResearchNodeType.CONCEPT, description="eigenvector-based analysis of a graph's structure"),
        ],
        # internal edge is 'references' (PPR weight 1.5, fixed) so the weight
        # sweep isolates the cross-project shares_concept_with bridge.
        edges=[ResearchEdge(source="Concept:rw", target="Concept:spec", type="references")],
    )
    b = ResearchGraph(
        nodes=[
            ResearchNode(id="Concept:ppr", name="Personalized PageRank",
                         type=ResearchNodeType.CONCEPT, description="pagerank scores with a teleport restart distribution"),
            ResearchNode(id="Concept:tele", name="Teleport restart vector",
                         type=ResearchNodeType.CONCEPT, description="the restart distribution that personalizes the ranking"),
            ResearchNode(id="Concept:ban", name="Banana bread",
                         type=ResearchNodeType.CONCEPT, description="a baking recipe with ripe bananas"),
        ],
        edges=[ResearchEdge(source="Concept:ppr", target="Concept:tele", type="references")],
    )
    return federate_graphs([("a", a), ("b", b)], semantic=True, semantic_backend=backend, semantic_min_cosine=0.4)


def compute_weight_rows(backend=None) -> Tuple[List[dict], dict]:
    """PPR rank (1 = top) of key nodes, seeding project A's node, per edge weight.

    ``a::Concept:spec`` is A's own neighbour (should stay near the top);
    ``b::Concept:ppr`` is the bridged B node; ``b::Concept:tele`` is B content
    reachable ONLY through the bridge; ``b::Concept:ban`` is unrelated B noise.
    """
    backend = backend or _model2vec_backend()
    fed, _ = _bridge_graph(backend)
    seed = ["a::Concept:rw"]
    watch = {"A_neighbour": "a::Concept:spec", "B_bridged": "b::Concept:ppr",
             "B_far(bridge-only)": "b::Concept:tele", "B_unrelated": "b::Concept:ban"}
    rows = []
    for weight in _WEIGHTS:
        # Pass the weight explicitly (incl. 0.0 = bridge off); None would mean
        # "use defaults" (1.0), not zero.
        etw = {"shares_concept_with": weight}
        ranked = personalized_pagerank(fed, seed, alpha=0.15, top_k=len(fed.nodes), edge_type_weights=etw)
        rank_of = {nid: i + 1 for i, (nid, _score) in enumerate(ranked)}
        rows.append({"weight": weight, **{k: rank_of.get(v, None) for k, v in watch.items()}})
    bridge_present = any(e.type == "shares_concept_with" and "federation_semantic" in (e.metadata or {})
                         and {"a::Concept:rw", "b::Concept:ppr"} == {e.source, e.target} for e in fed.edges)
    return rows, {"bridge_linked": bridge_present}


def _fmt_threshold(rows) -> str:
    best = max(rows, key=lambda r: (r["f1"], r["precision"]))
    out = ["| min_cosine | TP | FP | FN | precision | recall | F1 |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = "  ⬅ default" if abs(r["threshold"] - DEFAULT_MIN_COSINE) < 1e-9 else ""
        star = " ⭐" if r is best else ""
        out.append(f"| {r['threshold']:.2f}{mark}{star} | {r['tp']} | {r['fp']} | {r['fn']} | "
                   f"{r['precision']:.2f} | {r['recall']:.2f} | {r['f1']:.2f} |")
    out.append("")
    default = next(r for r in rows if abs(r["threshold"] - DEFAULT_MIN_COSINE) < 1e-9)
    out.append(f"**Best F1** at min_cosine={best['threshold']:.2f} (F1={best['f1']:.2f}). "
               f"**Default {DEFAULT_MIN_COSINE:.2f}** → F1={default['f1']:.2f}, precision={default['precision']:.2f}, "
               f"recall={default['recall']:.2f} (Δ vs best F1 = {best['f1'] - default['f1']:.2f}).")
    return "\n".join(out)


def _fmt_weight(rows, meta) -> str:
    cols = ["A_neighbour", "B_bridged", "B_far(bridge-only)", "B_unrelated"]
    out = [f"Bridge link a::rw ↔ b::ppr formed: **{meta['bridge_linked']}**. Seed = A's `Random-walk graph ranking`. Rank (1=top):",
           "",
           "| shares_concept_with weight | " + " | ".join(cols) + " |",
           "|---" * (len(cols) + 1) + "|"]
    for r in rows:
        label = f"{r['weight']:.2f}" + (" (no bridge)" if r["weight"] == 0.0 else "") + \
                ("  ⬅ default" if r["weight"] == DEFAULT_EDGE_WEIGHT else "")
        out.append("| " + label + " | " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def main() -> int:
    backend = _model2vec_backend()
    name = getattr(backend, "name", type(backend).__name__)
    t_rows = compute_threshold_rows(backend)
    w_rows, w_meta = compute_weight_rows(backend)
    report = (
        f"# Federation semantic-link eval\n\n"
        f"Embedding backend: `{name}`. Fixture: {len(CONCEPTS)} concepts across 3 projects, "
        f"{len(gold_cross_project_pairs(lambda p, i: f'{p}::{i}'))} gold cross-project pairs + "
        f"domain-adjacent hard negatives. Regenerate: `python -m evals.federation.run_eval`.\n\n"
        f"## 1. Threshold (min_cosine) — link precision/recall\n\n{_fmt_threshold(t_rows)}\n\n"
        f"## 2. Edge weight — does the bridge surface B without swamping A?\n\n{_fmt_weight(w_rows, w_meta)}\n"
    )
    out_path = Path(__file__).parent / "report.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
