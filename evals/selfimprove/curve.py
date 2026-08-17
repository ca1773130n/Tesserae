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
)
from ..lme_mab.adapter import RefusedToCompileInRepo, guard_work_dir
from ..qa.run_qa_eval import Skip

#: Evidence budget every arm shares. Ten pieces of evidence for the graph arm
#: and ten documents for a baseline: a different K per arm would measure the
#: budget rather than the memory. Same constant, same reason, as
#: ``evals/lme_mab``'s ``PROTOCOL_K``.
K = 10

#: Nodes `hybrid_search` hands PPR as its personalization vector.
#:
#: UNSWEPT AND LOAD-BEARING. The whole graph arm hangs on it, and it has had
#: none of the treatment `MAX_HOPS` got in `evals/growth/sweep_hops.py`. It is
#: also the one constant that must NOT be chosen after seeing which way the
#: overlay verdict went — that is tuning to the sample. Sweep it against the
#: controls, the way the hop budget was, before quoting any number as final.
SEED_K = 25

#: Reported cut-offs. K stays the shared budget; the rest are diagnostics.
#: R@10 saturates near 1.0, so MRR and R@3/R@5 are the headline and R@10 is
#: only there to show the ceiling being approached.
RANK_KS = (1, 3, 5, 10)

#: Offset used to build the shuffled-gold null: question i is scored against
#: question (i + this) % n's gold set. Coprime with most question counts, so it
#: is a derangement in practice rather than a near-identity permutation.
NULL_OFFSET = 7

QUESTIONS = Path(__file__).parent / "questions.yaml"


def load_questions() -> List[dict]:
    """This experiment's OWN question set, not ``evals/growth``'s.

    Growth's set measures cumulative slices, so every question being answerable
    at the full corpus is its intended end state. Reusing it froze experiment 1
    at 15/15 with nowhere to rise, and growth's hop budget is swept against that
    set specifically — adding to it would invalidate a calibration this module
    does not own. See ``questions.yaml`` for how these were authored and
    verified.
    """
    import yaml

    return yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))["questions"]


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


def _raw(item: Any) -> dict:
    """One node or edge as the plain mapping ``evals/growth`` indexes into.

    ``ResearchNode``/``ResearchEdge`` serialise through ``model_dump``. An
    earlier version guessed ``to_dict`` and fell back to passing the object
    through untouched when the attribute was missing — which is exactly what
    happened, and the mistake surfaced three frames away as
    ``'ResearchNode' object has no attribute 'get'`` inside growth's
    ``grounded_sources``. An unknown shape raises here instead.
    """
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None) or getattr(item, "to_dict", None)
    if dump is None:
        raise TypeError(
            f"cannot serialise {type(item).__name__} for evals/growth: it has "
            "neither model_dump() nor to_dict()"
        )
    return dump()


def as_dict(graph: Any) -> dict:
    """``evals/growth``'s evaluate() takes the raw dict shape."""
    return {
        "nodes": [_raw(n) for n in graph.nodes],
        "edges": [_raw(e) for e in graph.edges],
    }


def staged_corpus() -> tuple[List[Path], Set[Path], Set[str]]:
    """The whole corpus, in the three shapes `evaluate()` wants.

    ``corpus_docs()`` yields ``(iso_date, path, arxiv_id, kind)`` and the id is
    the THIRD field. Reading the first one lands on the date, no ``requires``
    entry matches something shaped like ``2016-07-09``, ``have_sources`` is
    False for every question, and the Tesserae column reports ``0/15`` while
    ``connected`` sits at 12 — which reads as the architecture failing rather
    than as an unpacking mistake. That is what this harness printed on its
    first real run, so the unpacking lives here where a test can hold it.

    Nothing is sliced: this experiment freezes the corpus, so every document is
    staged at every cycle by definition.
    """
    entries = corpus_docs()
    docs = [path for _, path, _, _ in entries]
    return docs, set(docs), {arxiv for _, _, arxiv, _ in entries}


# --------------------------------------------------------------------------
# ranked retrieval: the metric that can go DOWN


def doc_index() -> Dict[str, Path]:
    """Corpus unit name -> unit path. Verified collision-free at 73/73 units.

    Both arms are scored on this one universe. Without it the graph arm sees
    the 50 paper directories and the baselines see 85 markdown files, and that
    asymmetry alone was enough to flip the overlay verdict in an early probe.
    """
    units, _, _ = staged_corpus()
    return {p.name: p for p in units}


def doc_id(source_path: Optional[str], index: Dict[str, Path]) -> Optional[str]:
    """The corpus unit a ``source_path`` belongs to, by name anywhere in it.

    Prefix-agnostic deliberately: a scratch project keeps ``corpus/papers/<dir>/``
    while ``evals/growth/run.py::main`` flattens to ``work/corpus/<src.name>/``,
    so any fixed-depth ``relative_to`` is wrong for one of them. Growth's own
    ``grounded_sources`` matches on path parts for the same reason.
    """
    for part in Path(source_path or "").parts:
        if part in index:
            return part
    return None


def gold_set(question: dict) -> Set[str]:
    """The documents that must be retrieved, as corpus unit names."""
    return {f"arxiv-{r.replace('.', '-')}" for r in question.get("requires") or []}


def score_ranking(
    ranked: Sequence[str], gold: Set[str], ks: Sequence[int] = RANK_KS
) -> Dict[str, float]:
    """Recall at each cut-off, plus reciprocal rank of the first gold hit.

    A gold document missing from the ranking entirely contributes 0 to both,
    never ``None`` and never a dropped row, so the denominator stays the full
    question count and a collapse cannot hide as a smaller sample.

    Both numbers are reported because each is blind to something. MRR sees only
    the FIRST gold hit, so it cannot tell that the second gold document fell out
    of the top ten; recall sees that and cannot tell that the first one slipped
    from rank 1 to rank 9.
    """
    scores = {f"R@{k}": len(gold & set(ranked[:k])) / len(gold) for k in ks} if gold else {
        f"R@{k}": 0.0 for k in ks
    }
    scores["RR"] = next((1.0 / i for i, d in enumerate(ranked, 1) if d in gold), 0.0)
    return scores


def rank_documents_graph(
    graph: Any,
    query: str,
    index: Dict[str, Path],
    node_index: Dict[str, Any],
    *,
    backend: Any,
    vector_cache: Any,
    seed_k: int = SEED_K,
    walk: bool = True,
) -> List[str]:
    """Documents ranked by hybrid search seeding a personalized PageRank walk.

    This is where edges enter the score. ``walk=False`` drops the walk and ranks
    by hybrid search alone — the edge-blind null, which must score IDENTICALLY
    on a graph with and without the association overlay. If the real arm ever
    matches it, edges are contributing nothing and a rising curve is the
    embedder rather than the memory.

    First occurrence wins when several nodes map to one document: that is
    max-score-per-document. Summing node scores instead would reward a document
    merely for having minted more nodes, which is a property of the extractor.
    """
    from tesserae.retrieval.hybrid import hybrid_search

    res = hybrid_search(
        graph, query, top_k=seed_k, backend=backend,
        vector_cache=vector_cache, mode="hybrid",
    )
    seeds = [s.node.id for s in res.scored]
    if walk:
        from tesserae.retrieval.ppr import personalized_pagerank

        ordered = [nid for nid, _ in personalized_pagerank(
            graph, seeds, alpha=0.15, top_k=len(graph.nodes)
        )]
    else:
        ordered = seeds

    out: List[str] = []
    for node_id in ordered:
        node = node_index.get(node_id)
        if node is None:
            continue
        did = doc_id(getattr(node, "source_path", None), index)
        if did and did not in out:
            out.append(did)
    return out


def rank_documents_baseline(
    scores: Sequence[float], files: Sequence[Path], index: Dict[str, Path]
) -> List[str]:
    """Documents ranked by a baseline's per-file scores, deduped to units.

    Keeps ``_top_k``'s conventions exactly — drop non-positive scores, break
    ties by corpus order — so switching from a set-intersection test to a ranked
    list changes what is measured without changing how the baselines order.
    """
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    out: List[str] = []
    for i in order:
        if scores[i] <= 0.0:
            continue
        did = doc_id(str(files[i]), index)
        if did and did not in out:
            out.append(did)
    return out


def _aggregate(per_question: List[Dict[str, float]]) -> Dict[str, float]:
    if not per_question:
        return {f"R@{k}": 0.0 for k in RANK_KS} | {"RR": 0.0}
    keys = per_question[0].keys()
    return {k: sum(r[k] for r in per_question) / len(per_question) for k in keys}


# --------------------------------------------------------------------------
# one measurement


@dataclass
class Point:
    """One row of the curve: how well the memory RANKED at this cycle.

    ``mrr`` and ``recall_at`` are the headline and they are two-sided — an
    association that pulls a distractor above a gold document lowers them. The
    old headline, ``answerable``, is kept as ``connected``/``answerable``
    diagnostics precisely because it is NOT two-sided: it is monotone in edges,
    so it can only rise, and a metric that cannot fall was measuring density.
    """

    cycle: int
    arm: str
    mrr: float
    recall_at: Dict[int, float]
    #: Shuffled-gold null. Each question's own ranking scored against a
    #: different question's gold set. If this converges on ``mrr``, the ranking
    #: carries no question-specific signal and every number above is noise.
    mrr_null: float
    #: Edge-blind null: the same arm with the PPR walk removed. It must differ
    #: between a graph with and without the overlay; if the real arm ever equals
    #: it, edges contribute nothing and the curve is the embedder talking.
    mrr_edge_blind: float
    answerable: int
    connected: int
    controls_fired: int
    n_questions: int
    nodes: int
    edges: int
    llm_calls: int = 0
    notes: List[str] = field(default_factory=list)

    def as_row(self) -> Dict[str, Any]:
        row = {
            "cycle": self.cycle,
            "arm": self.arm,
            "mrr": round(self.mrr, 4),
            "mrr_null": round(self.mrr_null, 4),
            "mrr_edge_blind": round(self.mrr_edge_blind, 4),
            "answerable": self.answerable,
            "connected": self.connected,
            "controls_fired": self.controls_fired,
            "n_questions": self.n_questions,
            "nodes": self.nodes,
            "edges": self.edges,
            "llm_calls": self.llm_calls,
        }
        row.update({f"r_at_{k}": round(v, 4) for k, v in sorted(self.recall_at.items())})
        return row


def measure_graph(
    work: Path, questions: List[dict], staged: Set[Path], staged_arxiv: Set[str],
    *, cycle: int, llm_calls: int = 0,
) -> Point:
    from tesserae.retrieval.hybrid import active_embedding_backend
    from tesserae.retrieval.vector_cache import VectorCache

    graph = load_graph_with_overlay(work)
    raw = as_dict(graph)
    rows = evaluate(raw, questions, staged, staged_arxiv)
    live_rows = [r for r in rows if not r["control"]]
    control_rows = [r for r in rows if r["control"]]

    backend = active_embedding_backend("model2vec")
    if not backend.name.startswith("model2vec:"):
        # Same guard the dense arm has always had. Scoring the graph arm against
        # the hash stub produces numbers that look like retrieval and are not.
        raise Skip(
            f"the graph arm resolved {backend.name}, not model2vec",
            "install the semantic extra: uv sync --all-extras",
        )
    cache = VectorCache.for_project(work)
    index = doc_index()
    node_index = {n.id: n for n in graph.nodes}
    live = [q for q in questions if not q.get("control")]

    def rank(q: dict, *, walk: bool) -> List[str]:
        return rank_documents_graph(
            graph, q["text"], index, node_index,
            backend=backend, vector_cache=cache, walk=walk,
        )

    rankings = [rank(q, walk=True) for q in live]
    golds = [gold_set(q) for q in live]
    scored = _aggregate([score_ranking(r, g) for r, g in zip(rankings, golds)])
    # Shuffled gold: the SAME rankings against someone else's answer key.
    shifted = [golds[(i + NULL_OFFSET) % len(golds)] for i in range(len(golds))]
    null = _aggregate([score_ranking(r, g) for r, g in zip(rankings, shifted)])
    blind = _aggregate([
        score_ranking(rank(q, walk=False), g) for q, g in zip(live, golds)
    ])

    return Point(
        cycle=cycle,
        arm="Tesserae",
        mrr=scored["RR"],
        recall_at={k: scored[f"R@{k}"] for k in RANK_KS},
        mrr_null=null["RR"],
        mrr_edge_blind=blind["RR"],
        answerable=sum(1 for r in live_rows if r["answerable"]),
        connected=sum(1 for r in live_rows if r["connected"]),
        # Diagnostic, not the headline. A path between a control's anchors means
        # the connectivity checker is finding spurious links — worth seeing, but
        # it is the ranked score above that can register the HARM those links do.
        controls_fired=sum(1 for r in control_rows if r["connected"]),
        n_questions=len(live),
        nodes=len(raw["nodes"]),
        edges=len(raw["edges"]),
        llm_calls=llm_calls,
    )


# --------------------------------------------------------------------------
# the baselines, which cannot move


def _readable_docs(docs: Sequence[Path]) -> List[Path]:
    """Corpus entries as the individual files the baselines can retrieve.

    ``corpus_docs()`` yields one path per document *unit*, and for a paper that
    unit is a directory holding ``abstract.md`` and ``paper.md``. That is right
    for the graph arm — growth's grounding matches on path parts — and it is
    why the first run of this harness died on ``IsADirectoryError`` here.

    Directories expand to the same ``.md`` files Tesserae compiled, so both
    arms see one identical corpus. Concatenating each directory into a single
    blob would be the other way to make this run, and it would quietly change
    the retrieval unit between the arms while the shared K stayed the same.
    """
    out: List[Path] = []
    for p in docs:
        out.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    return out


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

    files = _readable_docs(docs)
    texts = [p.read_text(encoding="utf-8", errors="ignore") for p in files]
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
    index = doc_index()

    def scores_for(text: str) -> List[float]:
        if arm == "BM25":
            return _bm25_scores(_tokenize(text), corpus_tokens)
        return _cosine_scores(backend, vectors, text)

    def covered(q: dict) -> bool:
        """The old connectivity-analogue, kept as a diagnostic column."""
        a1, a2 = q["anchors"]
        top1 = set(_top_k(scores_for(a1)))
        top2 = set(_top_k(scores_for(a2)))
        return bool(top1 & top2) or len(top1 | top2) <= K

    rankings = [rank_documents_baseline(scores_for(q["text"]), files, index) for q in live]
    golds = [gold_set(q) for q in live]
    scored = _aggregate([score_ranking(r, g) for r, g in zip(rankings, golds)])
    shifted = [golds[(i + NULL_OFFSET) % len(golds)] for i in range(len(golds))]
    null = _aggregate([score_ranking(r, g) for r, g in zip(rankings, shifted)])

    return Point(
        cycle=cycle,
        arm=arm,
        mrr=scored["RR"],
        recall_at={k: scored[f"R@{k}"] for k in RANK_KS},
        mrr_null=null["RR"],
        # A baseline has no walk to remove, so it IS its own edge-blind null.
        # Reporting its real score here rather than 0.0 keeps the column
        # meaning "what this arm scores without edges" in every row.
        mrr_edge_blind=scored["RR"],
        answerable=sum(1 for q in live if covered(q)),
        connected=sum(1 for q in live if covered(q)),
        controls_fired=sum(1 for q in controls if covered(q)),
        n_questions=len(live),
        nodes=len(files),
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
        "The headline is MRR and recall of the gold documents each question "
        "names. Both can FALL: an association that lifts a distractor above a "
        "gold document lowers them. That is the whole reason they replaced "
        "`answerable`, which is monotone in edges and so could only ever rise.",
        "",
        "| cycle | arm | MRR | R@1 | R@3 | R@5 | R@10 | nodes | edges | LLM calls | MRR null | MRR edge-blind | answerable | controls |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for p in points:
        r = p.recall_at
        lines.append(
            f"| {p.cycle} | {p.arm} | {p.mrr:.3f} | "
            + " | ".join(f"{r.get(k, 0.0):.3f}" for k in RANK_KS)
            + f" | {p.nodes} | {p.edges} | {p.llm_calls} | {p.mrr_null:.3f} | "
            f"{p.mrr_edge_blind:.3f} | {p.answerable}/{p.n_questions} | {p.controls_fired} |"
        )
    lines += ["", "## Reading this table", ""]

    by_arm: Dict[str, List[Point]] = {}
    for p in points:
        by_arm.setdefault(p.arm, []).append(p)
    for arm, ps in by_arm.items():
        if len(ps) < 2:
            continue
        d_mrr = ps[-1].mrr - ps[0].mrr
        d_r5 = ps[-1].recall_at.get(5, 0.0) - ps[0].recall_at.get(5, 0.0)
        lines.append(
            f"- **{arm}**: MRR {ps[0].mrr:.3f} → {ps[-1].mrr:.3f} (Δ **{d_mrr:+.3f}**), "
            f"R@5 {ps[0].recall_at.get(5, 0.0):.3f} → {ps[-1].recall_at.get(5, 0.0):.3f} "
            f"(Δ **{d_r5:+.3f}**) over {len(ps) - 1} cycle(s), "
            f"{sum(p.llm_calls for p in ps)} LLM call(s) spent."
        )

    lines += ["", "### The two nulls, which are not decoration", ""]
    worst_null = max((p.mrr_null for p in points), default=0.0)
    best = max((p.mrr for p in points), default=0.0)
    lines.append(
        f"- **Shuffled gold**: the same rankings scored against another "
        f"question's answer key. Highest seen {worst_null:.3f} against a real "
        f"{best:.3f}. If these converge, the ranking carries no "
        "question-specific signal and nothing above means anything."
    )
    graph_pts = [p for p in points if p.arm == "Tesserae"]
    if graph_pts:
        gaps = [p.mrr - p.mrr_edge_blind for p in graph_pts]
        lines.append(
            f"- **Edge-blind**: the graph arm with the PPR walk removed, so "
            f"edges cannot contribute. Gap to the real arm ranges "
            f"{min(gaps):+.3f} to {max(gaps):+.3f}. A gap of 0 everywhere would "
            "mean the curve is the embedder rather than the memory."
        )

    fired = sum(p.controls_fired for p in points)
    lines += [
        "",
        f"**Controls fired: {fired}.** A path between a control's anchors means "
        "the connectivity diagnostic is finding spurious links. It no longer "
        "invalidates the headline — the ranked score is what registers the harm "
        "such links do — but it is the cheapest signal that they exist."
        if fired
        else "**Controls fired: 0.**",
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
    docs, staged, staged_arxiv = staged_corpus()

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
