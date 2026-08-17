"""Does a memory improve when the corpus does NOT change?

Every system answers more questions once it is given more documents, so an
accumulation curve says nothing about architecture. The claim that separates a
memory with a consolidation loop from an index is narrower and harder:

    quality rises across cycles while the corpus stands still.

So this harness freezes a compiled corpus and runs consolidation over it,
ingesting nothing. Tesserae's `associate` pass discovers edges from what is
already there; BM25 and dense retrieval have no mechanism to improve without new
input, and their flat line is the comparison rather than a shortcoming of it.

Two measured properties shape what this can honestly claim. The pass costs
**no LLM quota** — discovery is cosine similarity over the local embedder — so
the curve is free and repeatable. And it **saturates**: discovery is
deterministic and persistence dedups, so a second pass over an unchanged graph
adds nothing. On a frozen corpus the honest shape is a STEP, not a ramp. A step
still beats a baseline pinned at zero, but it is not "improves forever without
input", and this harness plots enough cycles to show the plateau rather than
stopping at the flattering point.

The question set, the anchor resolution, the grounding rule and the controls are
`evals/growth/`'s — reused rather than restated, because that module already
argued each of them and a second copy would drift. See its `questions.yaml` for
why grounding is checked separately from node existence (the extractor mints a
node for every paper named in a related-work section, and counting those would
make any curve rise for free).

    # what would it cost? prints the plan and stops
    uv run python -m evals.selfimprove.curve --work <scratch dir>

    # measure T0, run N cycles, measure after each. Free: no LLM, no network.
    uv run python -m evals.selfimprove.curve --work <scratch dir> \
        --cycles 3 --cycles-run

`--work` must be OUTSIDE this repository; `guard_work_dir` refuses anything else
for the reason `evals/growth/run.py` gives — a compile at the repo root
overwrites the project's own graph.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from ..growth.run import (
    corpus_docs,
    evaluate,
    load_questions,
)
from ..lme_mab.adapter import RefusedToCompileInRepo, guard_work_dir
from ..qa.run_qa_eval import Skip

#: Evidence budget every arm shares. Ten pieces of evidence for the graph arm
#: and ten documents for a baseline: a different K per arm would measure the
#: budget rather than the memory. Same constant, same reason, as
#: ``evals/lme_mab``'s ``PROTOCOL_K``.
K = 10


# --------------------------------------------------------------------------
# the graph under test, with the overlay the consolidation loop actually writes


def load_graph_with_overlay(work: Path) -> Any:
    """The compiled graph PLUS the accumulated association overlay.

    This is the whole experiment's correctness hinge. ``associate`` — the one
    consolidation op that can move a connectivity metric on a frozen corpus —
    writes discovered edges to a **sidecar overlay** under ``.tesserae``, never
    into ``graph.json`` (``docs/engine-consolidation.md`` §3). The merge is done
    in memory at read time.

    ``evals/growth/run.py::load_graph`` reads ``graph.json`` directly, which is
    correct there (it measures what compiling more documents produces) and would
    be silently wrong here: every cycle would score identically and the run
    would read as "consolidation does nothing" — a null result manufactured by
    the loader rather than observed in the system.
    """
    from tesserae.memory.associate import apply_overlay
    from tesserae.project import load_graph_file

    graph = load_graph_file(work / ".tesserae" / "graph.json")
    return apply_overlay(work, graph)


def as_dict(graph: Any) -> dict:
    """``evals/growth``'s evaluate() takes the raw dict shape."""
    return {
        "nodes": [n.to_dict() if hasattr(n, "to_dict") else n for n in graph.nodes],
        "edges": [e.to_dict() if hasattr(e, "to_dict") else e for e in graph.edges],
    }


# --------------------------------------------------------------------------
# one measurement


@dataclass
class Point:
    """One row of the curve: what the memory could answer at this cycle."""

    cycle: int
    arm: str
    answerable: int
    connected: int
    controls_fired: int
    n_questions: int
    nodes: int
    edges: int
    llm_calls: int = 0
    notes: List[str] = field(default_factory=list)

    def as_row(self) -> Dict[str, Any]:
        return {
            "cycle": self.cycle,
            "arm": self.arm,
            "answerable": self.answerable,
            "connected": self.connected,
            "controls_fired": self.controls_fired,
            "n_questions": self.n_questions,
            "nodes": self.nodes,
            "edges": self.edges,
            "llm_calls": self.llm_calls,
        }


def measure_graph(
    work: Path, questions: List[dict], staged: Set[Path], staged_arxiv: Set[str],
    *, cycle: int, llm_calls: int = 0,
) -> Point:
    graph = load_graph_with_overlay(work)
    raw = as_dict(graph)
    rows = evaluate(raw, questions, staged, staged_arxiv)
    live = [r for r in rows if not r["control"]]
    controls = [r for r in rows if r["control"]]
    return Point(
        cycle=cycle,
        arm="Tesserae",
        answerable=sum(1 for r in live if r["answerable"]),
        connected=sum(1 for r in live if r["connected"]),
        # Must stay 0. The controls ask what the corpus cannot answer; a path
        # between their anchors means the checker is finding spurious
        # connections and every number in the table is suspect.
        controls_fired=sum(1 for r in controls if r["connected"]),
        n_questions=len(live),
        nodes=len(raw["nodes"]),
        edges=len(raw["edges"]),
        llm_calls=llm_calls,
    )


# --------------------------------------------------------------------------
# the baselines, which cannot move


def measure_baseline(
    arm: str, docs: Sequence[Path], questions: List[dict], *, cycle: int
) -> Point:
    """Joint anchor coverage in the top-K, for a memory with no notion of a path.

    BM25 and dense retrieval cannot answer "are these two concepts connected" —
    they rank documents. So the same question is scored as the nearest thing
    they CAN do: does the top-K jointly contain evidence for both anchors? Same
    K as the graph arm, so the budget is not what differs.

    This is recomputed every cycle rather than carried forward. That looks
    wasteful and is the point: it demonstrates the flat line was measured at
    each cycle rather than asserted once and copied down the column.
    """
    from tesserae.retrieval.hybrid import _bm25_scores, _tokenize

    texts = [p.read_text(encoding="utf-8", errors="ignore") for p in docs]
    corpus_tokens = [_tokenize(t) for t in texts]
    if arm == "Dense":
        from tesserae.retrieval.hybrid import active_embedding_backend
        from tesserae.retrieval.vector_cache import embed_texts

        backend = active_embedding_backend("model2vec")
        if not backend.name.startswith("model2vec:"):
            raise Skip(
                f"the dense arm resolved {backend.name}, not model2vec",
                "install the semantic extra: uv sync --all-extras",
            )
        vectors = embed_texts(backend, texts)

    live = [q for q in questions if not q.get("control")]
    controls = [q for q in questions if q.get("control")]

    def covered(q: dict) -> bool:
        a1, a2 = q["anchors"]
        if arm == "BM25":
            s1 = _bm25_scores(_tokenize(a1), corpus_tokens)
            s2 = _bm25_scores(_tokenize(a2), corpus_tokens)
        else:
            s1 = _cosine_scores(backend, vectors, a1)
            s2 = _cosine_scores(backend, vectors, a2)
        top1 = {i for i in _top_k(s1)}
        top2 = {i for i in _top_k(s2)}
        # Evidence for BOTH anchors inside ONE budget of K documents: the
        # baseline's analogue of "a path exists between them".
        return bool(top1 & top2) or len(top1 | top2) <= K

    return Point(
        cycle=cycle,
        arm=arm,
        answerable=sum(1 for q in live if covered(q)),
        connected=sum(1 for q in live if covered(q)),
        controls_fired=sum(1 for q in controls if covered(q)),
        n_questions=len(live),
        nodes=len(docs),
        edges=0,
        llm_calls=0,
        notes=["no mechanism to improve without new documents"],
    )


def _top_k(scores: Sequence[float]) -> List[int]:
    ranked = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return [i for i in ranked[:K] if scores[i] > 0.0]


def _cosine_scores(backend: Any, vectors: Sequence[Sequence[float]], query: str) -> List[float]:
    import math

    qv = backend.embed([query])[0]
    qn = math.sqrt(sum(v * v for v in qv)) or 1.0
    out = []
    for dv in vectors:
        dn = math.sqrt(sum(v * v for v in dv)) or 1.0
        out.append(sum(a * b for a, b in zip(qv, dv)) / (qn * dn))
    return out


# --------------------------------------------------------------------------
# report


def render(points: Sequence[Point]) -> str:
    lines = [
        "# Does the memory improve when the corpus does not?",
        "",
        "Every arm below sees the SAME frozen corpus for the whole run: no "
        "document is added, removed or re-compiled after cycle 0. A rising "
        "column is therefore the memory improving itself, and a flat one is a "
        "memory that cannot.",
        "",
        "| cycle | arm | answerable | connected | nodes | edges | LLM calls | controls fired |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p in points:
        lines.append(
            f"| {p.cycle} | {p.arm} | {p.answerable}/{p.n_questions} | "
            f"{p.connected} | {p.nodes} | {p.edges} | {p.llm_calls} | {p.controls_fired} |"
        )
    lines += ["", "## Reading this table", ""]

    by_arm: Dict[str, List[Point]] = {}
    for p in points:
        by_arm.setdefault(p.arm, []).append(p)
    for arm, ps in by_arm.items():
        if len(ps) < 2:
            continue
        delta = ps[-1].answerable - ps[0].answerable
        lines.append(
            f"- **{arm}**: {ps[0].answerable} → {ps[-1].answerable} answerable "
            f"over {len(ps) - 1} cycle(s), Δ **{delta:+d}**, "
            f"{sum(p.llm_calls for p in ps)} LLM call(s) spent."
        )
    fired = sum(p.controls_fired for p in points)
    lines += [
        "",
        f"**Controls fired: {fired}.** These questions ask what the corpus "
        "cannot answer. Anything but 0 means the checker is finding spurious "
        "connections and every number above is suspect."
        if fired
        else "",
        "",
        "The baselines are recomputed at every cycle rather than carried "
        "forward, so their flat line is measured rather than assumed.",
    ]
    return "\n".join(l for l in lines if l is not None) + "\n"


# --------------------------------------------------------------------------
# driver


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--work", type=Path, required=True,
                   help="scratch project root, OUTSIDE this repo")
    p.add_argument("--cycles", type=int, default=3,
                   help="consolidation cycles to run after T0 (default: 3)")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--arms", default="tesserae,bm25,dense")
    p.add_argument("--cycles-run", action="store_true",
                   help="run the consolidation cycles. Without it the run "
                        "measures T0 and stops. NOT a money gate: the "
                        "associate pass is cosine similarity over the local "
                        "embedder and spends no LLM quota — the flag exists so "
                        "a measurement cannot mutate the overlay by accident")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if (os.environ.get("CI") or "").strip():
        print("SKIP: CI is set — this compiles a corpus and spends LLM quota\n"
              "      run it by hand instead")
        return 0
    try:
        work = guard_work_dir(args.work)
    except RefusedToCompileInRepo as exc:
        print(f"SKIP: {exc}\n      pass --work ~/.blackhole/Tesserae/selfimprove")
        return 0
    if not (work / ".tesserae" / "graph.json").is_file():
        print(f"SKIP: no compiled graph at {work}/.tesserae/graph.json\n"
              f"      compile the corpus there first — this harness never "
              f"compiles, it measures")
        return 0

    questions = load_questions()
    docs = [path for _, path, _, _ in corpus_docs()]
    staged = set(docs)
    staged_arxiv = {ref for ref, _, _, _ in corpus_docs()}

    arms = [a.strip().lower() for a in args.arms.split(",") if a.strip()]
    points: List[Point] = []

    if "tesserae" in arms:
        points.append(measure_graph(work, questions, staged, staged_arxiv, cycle=0))
    for arm, key in (("BM25", "bm25"), ("Dense", "dense")):
        if key in arms:
            points.append(measure_baseline(arm, docs, questions, cycle=0))

    if not args.cycles_run:
        print(render(points))
        print("T0 only — no overlay was written. Re-run with --cycles-run to "
              "measure the curve (free: no LLM, no network).")
        return 0

    for cycle in range(1, args.cycles + 1):
        stats = run_consolidation_cycle(work)
        added = int(stats.get("associate_links_added") or stats.get("links_added") or 0)
        if "tesserae" in arms:
            pt = measure_graph(work, questions, staged, staged_arxiv, cycle=cycle)
            pt.notes.append(f"associate added {added} link(s), 0 LLM calls")
            points.append(pt)
        for arm, key in (("BM25", "bm25"), ("Dense", "dense")):
            if key in arms:
                points.append(measure_baseline(arm, docs, questions, cycle=cycle))

    report = render(points)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


def run_consolidation_cycle(work: Path) -> Dict[str, Any]:
    """One association pass over the frozen corpus.

    Only `associate` runs. The other four ops either need new input to have
    anything to do (distill), or move a metric this harness does not score
    (summarize, brief), or shrink the graph on a schedule that would confound a
    fixed-corpus reading (LRU decay). Isolating one op is what makes a rising
    curve attributable to a mechanism rather than to a bundle.

    **It spends no LLM quota.** Discovery is cosine similarity over the local
    model2vec backend (``_is_real_backend`` refuses the hash stub rather than
    inventing links from noise), so the whole curve is free and repeatable —
    the same property that makes `evals/lme_mab`'s baseline arms runnable.

    **And it saturates.** Discovery is deterministic and persistence dedups, so
    a second pass over an unchanged graph finds what the first one did and adds
    nothing. On a frozen corpus the honest shape is a step, not a ramp: one
    lift, then flat. That is still an architectural difference a plain index
    cannot show — a step beats a line at zero — but it is not "improves
    forever without input", and the report says so rather than implying
    otherwise by plotting only T0 and T1.
    """
    from tesserae.memory.associate import consolidate_associations
    from tesserae.project import load_graph_file
    from tesserae.retrieval.hybrid import active_embedding_backend

    graph = load_graph_file(work / ".tesserae" / "graph.json")
    backend = active_embedding_backend("model2vec")
    stats = consolidate_associations(work, graph, backend=backend)
    return dict(stats or {})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
