"""What cosine threshold should the `associate` pass actually use?

`associate` links concept nodes inside one graph by embedding similarity, and it
takes its threshold from ``DEFAULT_SEMANTIC_MIN_COSINE = 0.55``. That constant
was calibrated in ``evals/federation/`` — precision 1.00, recall 0.70, F1 0.82 —
for **cross-project federation**, where the candidates are identity matches
between two projects' entities. Associate's candidate population is every node
pair in one graph, and "correct" means a relationship a reader would accept
rather than the same entity seen twice. A threshold with perfect precision on
one of those has no claim on the other, and there has never been an
associate-specific number.

So this sweeps one, the way ``evals/growth/sweep_hops.py`` swept the hop budget.

**It is deliberately not swept on the controls.** A control firing is what first
drew attention here, and picking the threshold that silences that particular
control would be tuning to the sample — the same mistake as choosing a hop
budget after seeing which question it rescued. The criterion is the ranked
metric over all 59 live questions, which can move in both directions; the
control column is printed beside it as a witness, not as the objective.

    uv run python -m evals.selfimprove.sweep_cosine --work <scratch dir>

Each threshold rebuilds the overlay from scratch, so the run is O(len(GRID))
association passes. They cost no LLM calls — association is cosine similarity
over the local embedder — so the whole sweep is free and repeatable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..lme_mab.adapter import RefusedToCompileInRepo, guard_work_dir
from ..qa.run_qa_eval import Skip
from .curve import (
    RANK_KS,
    _aggregate,
    doc_index,
    evaluate,
    gold_set,
    as_dict,
    load_graph_with_overlay,
    load_questions,
    rank_documents_graph,
    score_ranking,
    staged_corpus,
)

#: The range worth looking at. Below 0.50 the pass admits nearly anything; above
#: 0.75 it stops finding links at all on a corpus this size. The shipped default
#: (0.55) is inside the grid so the table always contains the status quo.
GRID = (0.50, 0.55, 0.58, 0.60, 0.62, 0.65, 0.70, 0.75)


def _clear_overlay(work: Path) -> None:
    from tesserae.memory.associate import _overlay_path

    path = _overlay_path(work)
    if path.exists():
        path.unlink()


def measure_at(
    work: Path, threshold: Optional[float], questions: List[dict],
    staged, staged_arxiv, *, backend, cache, index,
) -> Dict[str, Any]:
    """Rebuild the overlay at one threshold and score the graph arm.

    ``threshold=None`` means "no association pass at all" — the baseline row the
    whole table is read against. Without it a reader cannot tell a good
    threshold from one that merely does less damage than its neighbours.
    """
    from tesserae.memory.associate import consolidate_associations
    from tesserae.project import load_graph_file

    _clear_overlay(work)
    added = 0
    if threshold is not None:
        base = load_graph_file(work / ".tesserae" / "graph.json")
        stats = consolidate_associations(
            work, base, backend=backend, min_cosine=threshold
        )
        added = int(stats.get("associate_added", 0) or 0)
        if stats.get("associate_skipped"):
            raise Skip(
                f"associate refused at {threshold}: {stats['associate_skipped']}",
                "install the semantic extra: uv sync --all-extras",
            )

    graph = load_graph_with_overlay(work)
    raw = as_dict(graph)
    node_index = {n.id: n for n in graph.nodes}
    live = [q for q in questions if not q.get("control")]

    rankings = [
        rank_documents_graph(
            graph, q["text"], index, node_index,
            backend=backend, vector_cache=cache, walk=True,
        )
        for q in live
    ]
    golds = [gold_set(q) for q in live]
    scored = _aggregate([score_ranking(r, g) for r, g in zip(rankings, golds)])

    rows = evaluate(raw, questions, staged, staged_arxiv)
    return {
        "threshold": threshold,
        "overlay_edges": added,
        "edges": len(raw["edges"]),
        "mrr": scored["RR"],
        **{f"r_at_{k}": scored[f"R@{k}"] for k in RANK_KS},
        "controls_fired": sum(1 for r in rows if r["control"] and r["connected"]),
        "answerable": sum(1 for r in rows if not r["control"] and r["answerable"]),
    }


def render(rows: Sequence[Dict[str, Any]]) -> str:
    out = [
        "# What threshold should `associate` use?",
        "",
        "`DEFAULT_SEMANTIC_MIN_COSINE = 0.55` comes from `evals/federation/` — "
        "precision 1.00, recall 0.70 on **cross-project identity matching**. "
        "`associate` borrows it for a different task over a different candidate "
        "population. This table is the first associate-specific measurement.",
        "",
        "Chosen on MRR and R@5 over 59 live questions, NOT on the control column "
        "— a threshold picked to silence a control that had already been seen "
        "firing would be tuned to that control.",
        "",
        "| min_cosine | overlay edges | MRR | R@1 | R@3 | R@5 | R@10 | answerable | controls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        label = "**none**" if r["threshold"] is None else f"{r['threshold']:.2f}"
        out.append(
            f"| {label} | {r['overlay_edges']} | {r['mrr']:.3f} | "
            + " | ".join(f"{r[f'r_at_{k}']:.3f}" for k in RANK_KS)
            + f" | {r['answerable']} | {r['controls_fired']} |"
        )

    base = next((r for r in rows if r["threshold"] is None), None)
    swept = [r for r in rows if r["threshold"] is not None]
    out += ["", "## Reading this table", ""]
    if base and swept:
        best_mrr = max(swept, key=lambda r: r["mrr"])
        best_r5 = max(swept, key=lambda r: r["r_at_5"])
        out += [
            f"- No association at all: MRR {base['mrr']:.3f}, R@5 {base['r_at_5']:.3f}.",
            f"- Best MRR: **{best_mrr['threshold']:.2f}** at {best_mrr['mrr']:.3f} "
            f"(Δ vs none {best_mrr['mrr'] - base['mrr']:+.3f}).",
            f"- Best R@5: **{best_r5['threshold']:.2f}** at {best_r5['r_at_5']:.3f} "
            f"(Δ vs none {best_r5['r_at_5'] - base['r_at_5']:+.3f}).",
        ]
        beats = [r for r in swept if r["mrr"] > base["mrr"] and r["r_at_5"] >= base["r_at_5"]]
        out.append(
            "- Thresholds that beat doing nothing on BOTH MRR and R@5: "
            + (", ".join(f"{r['threshold']:.2f}" for r in beats) if beats else
               "**none** — on this corpus the association pass does not pay for "
               "itself at any threshold in the grid, and that is the finding.")
        )
    return "\n".join(out) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--work", type=Path, required=True,
                   help="scratch project root, OUTSIDE this repo")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--grid", default=",".join(str(g) for g in GRID))
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        work = guard_work_dir(args.work)
    except RefusedToCompileInRepo as exc:
        print(f"SKIP: {exc}", file=sys.stderr)
        return 0
    if not (work / ".tesserae" / "graph.json").is_file():
        print(f"SKIP: no compiled graph at {work}/.tesserae/graph.json", file=sys.stderr)
        return 0

    from tesserae.retrieval.hybrid import active_embedding_backend
    from tesserae.retrieval.vector_cache import VectorCache

    backend = active_embedding_backend("model2vec")
    if not backend.name.startswith("model2vec:"):
        print(f"SKIP: resolved {backend.name}, not model2vec", file=sys.stderr)
        return 0

    questions = load_questions()
    _, staged, staged_arxiv = staged_corpus()
    cache = VectorCache.for_project(work)
    index = doc_index()

    grid: List[Optional[float]] = [None] + [float(g) for g in args.grid.split(",") if g]
    rows = []
    for t in grid:
        try:
            rows.append(measure_at(
                work, t, questions, staged, staged_arxiv,
                backend=backend, cache=cache, index=index,
            ))
        except Skip as exc:
            print(f"SKIP: {exc}", file=sys.stderr)
            return 0
        label = "none" if t is None else f"{t:.2f}"
        print(f"  {label}: mrr={rows[-1]['mrr']:.3f} r@5={rows[-1]['r_at_5']:.3f} "
              f"edges={rows[-1]['overlay_edges']} controls={rows[-1]['controls_fired']}",
              file=sys.stderr)

    report = render(rows)
    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        args.out.with_suffix(".json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    # Leave no overlay behind: the sweep's last threshold is not a state anyone
    # asked to keep, and a stale overlay silently changes the next measurement.
    _clear_overlay(work)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
