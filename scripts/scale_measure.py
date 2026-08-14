"""Time and peak-RSS sweep of the JSON-artifact + SQLite-sidecar model.

Produced the table in ``docs/superpowers/specs/2026-08-14-scale-measurement.md``.
Kept in the repo so the claim in that report stays falsifiable: re-run it and
the numbers should reproduce, on the same machine, within noise.

Usage::

    uv run --python 3.11 python scripts/scale_measure.py 47132 100000 250000
    uv run --python 3.11 python scripts/scale_measure.py 250000 --ceiling-gb 5.5

Each size runs in its own subprocess. That is not tidiness — peak RSS is a
process high-water mark, so measuring several sizes in one process reports the
largest size's peak for every size, and a 1M-node run that dies takes the whole
sweep's results with it. One process per size also hands the graph back to the
OS between sizes, which is what keeps the sweep off swap.

Two guards keep it there, because swapping would not merely slow a phase but
invalidate it — the numbers would describe the swap device rather than the code:

* the parent projects peak RSS from the sizes already measured and refuses to
  start one that would exceed ``MEMORY_HEADROOM`` of available memory;
* the child holds a hard RSS ceiling and abandons the size at a phase boundary
  if it is crossed, writing out whatever phases did complete.

An honest partial curve beats an OOM.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

#: Fraction of currently-available memory a projected peak may occupy before the
#: parent refuses to start a size at all. This is the OUTER of two guards: the
#: child additionally enforces a hard RSS ceiling and abandons the size at a
#: phase boundary if it is crossed, so a projection that turns out optimistic
#: costs a partial row rather than a swapping machine. Without that second
#: guard this number would have to be far more pessimistic than it is.
#:
#: Swapping would not merely slow the run, it would invalidate it: the phases
#: would be timing the swap device instead of the code under test.
MEMORY_HEADROOM = 0.70

def corpus_query(graph, tokenize, terms: int = 5) -> str:
    """A query built from the corpus's OWN frequent terms.

    A fixed natural-language query ("retrieval graph context evidence") shares
    no vocabulary with a synthetic corpus, and a query that matches nothing is
    the cheapest query there is: the first sweep run this way reported the BM25
    lane scoring 0 documents, which measured the empty-postings path rather than
    retrieval. Sampling the corpus's own frequent terms puts every lane on a
    query with real posting lists, which is the case worth timing.

    Frequencies come from a bounded sample rather than the whole corpus so that
    picking the query does not itself become one of the costs being measured.
    """
    import collections

    counts: collections.Counter = collections.Counter()
    step = max(1, len(graph.nodes) // 2000)
    for node in graph.nodes[::step]:
        counts.update(set(tokenize(node.name)))
    return " ".join(term for term, _ in counts.most_common(terms))


# --------------------------------------------------------------------------- #
# child: one size, one process
# --------------------------------------------------------------------------- #


class RssSampler:
    """Peak RSS between ``start()`` and ``stop()``, sampled off-thread.

    ``resource.getrusage`` reports a process-lifetime high-water mark, which
    cannot attribute a peak to the phase that caused it — once serialization
    has touched 6 GB, every later phase inherits that number. Sampling current
    RSS gives a per-phase figure. The 5 ms interval is short against every
    phase measured here; a phase that ran faster than that would report its
    entry RSS, so the runner records phase duration alongside and any phase
    under ~50 ms should be read as "peak not resolved".
    """

    def __init__(self, interval: float = 0.005) -> None:
        import psutil

        self._proc = psutil.Process(os.getpid())
        self._interval = interval
        self._peak = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                rss = self._proc.memory_info().rss
            except Exception:
                return
            if rss > self._peak:
                self._peak = rss
            self._stop.wait(self._interval)

    def start(self) -> "RssSampler":
        self._peak = self._proc.memory_info().rss
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return self._peak


class CeilingReached(Exception):
    """Raised at a phase boundary once RSS has crossed the run's ceiling."""


class PhaseRecorder:
    """Runs phases in order, abandoning the size once RSS crosses ``ceiling``.

    The check is at phase boundaries, not inside a phase, because the phases
    that dominate peak RSS (``json_dumps``, the embedding lane) spend their time
    in C with no interruptible bytecode boundary — there is no honest way to
    stop them mid-call. Boundary checking still prevents the realistic failure,
    which is not one phase blowing past a projection but several phases in
    sequence each leaving more resident than the last until the machine swaps.
    """

    def __init__(self, ceiling_bytes: float) -> None:
        self.rows: List[Dict[str, object]] = []
        self.ceiling = ceiling_bytes

    def run(self, name: str, fn, *, note: str = ""):
        import gc

        import psutil

        gc.collect()
        current = psutil.Process(os.getpid()).memory_info().rss
        if current > self.ceiling:
            raise CeilingReached(
                f"RSS {current / 1e9:.2f} GB crossed the {self.ceiling / 1e9:.2f} GB "
                f"ceiling before {name!r}; abandoning this size rather than swapping"
            )
        sampler = RssSampler().start()
        baseline = sampler._proc.memory_info().rss
        started = time.perf_counter()
        try:
            result = fn()
            failure = ""
        except Exception as exc:  # a phase that cannot run is data, not a crash
            result = None
            failure = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        peak = sampler.stop()
        self.rows.append(
            {
                "phase": name,
                "seconds": round(elapsed, 4),
                "peak_rss_mb": round(peak / 1e6, 1),
                "delta_rss_mb": round((peak - baseline) / 1e6, 1),
                "note": note,
                "error": failure,
            }
        )
        status = failure or f"{elapsed:8.3f}s  peak {peak / 1e6:8.1f} MB"
        print(f"  {name:<26} {status} {note}", flush=True)
        return result


def child_main(n_nodes: int, out_path: str, workdir: str) -> None:
    from tesserae.context_compiler import compile_context
    from tesserae.graph_stores.sqlite import SqliteGraphStore
    from tesserae.research_graph import graph_from_payload
    from tesserae.retrieval.bm25_index import Bm25Index
    from tesserae.retrieval.hybrid import (
        _node_text,
        _tokenize,
        active_embedding_backend,
        hybrid_search,
    )
    from tesserae.retrieval.ppr import personalized_pagerank
    from tesserae.retrieval.vector_cache import VectorCache
    from tests.scale_graph import generate_graph

    ceiling = float(os.environ.get("SCALE_CEILING_BYTES", 0)) or available_bytes() * 0.75
    rec = PhaseRecorder(ceiling)
    print(f"  (ceiling {ceiling / 1e9:.2f} GB)", flush=True)
    root = Path(workdir)
    (root / ".tesserae").mkdir(parents=True, exist_ok=True)
    graph_path = root / ".tesserae" / "graph.json"
    db_path = root / ".tesserae" / "sqlite.db"

    summary: Dict[str, object] = {"n_nodes": n_nodes, "n_edges": 0, "abandoned": ""}
    try:
        _child_phases(
            rec,
            summary,
            n_nodes,
            root,
            graph_path,
            db_path,
            generate_graph=generate_graph,
            graph_from_payload=graph_from_payload,
            SqliteGraphStore=SqliteGraphStore,
            VectorCache=VectorCache,
            Bm25Index=Bm25Index,
            active_embedding_backend=active_embedding_backend,
            hybrid_search=hybrid_search,
            personalized_pagerank=personalized_pagerank,
            compile_context=compile_context,
            _node_text=_node_text,
            _tokenize=_tokenize,
        )
    except CeilingReached as exc:
        # A size that does not fit is a RESULT, so the phases that did fit are
        # written out rather than lost with the process.
        summary["abandoned"] = str(exc)
        print(f"  ABANDONED: {exc}", flush=True)

    summary["phases"] = rec.rows
    Path(out_path).write_text(json.dumps(summary, indent=1), encoding="utf-8")


def _child_phases(
    rec: "PhaseRecorder",
    summary: Dict[str, object],
    n_nodes: int,
    root: Path,
    graph_path: Path,
    db_path: Path,
    **api,
) -> None:
    generate_graph = api["generate_graph"]
    graph_from_payload = api["graph_from_payload"]
    _node_text = api["_node_text"]
    _tokenize = api["_tokenize"]

    graph = rec.run("generate", lambda: generate_graph(n_nodes, seed=7))
    query = corpus_query(graph, _tokenize)
    summary["query"] = query
    print(f"      query: {query!r}", flush=True)
    summary["n_nodes"] = len(graph.nodes)
    summary["n_edges"] = len(graph.edges)
    payload = rec.run("model_dump", graph.model_dump)
    rec.run("canonicalized", graph.canonicalized)
    blob = rec.run("json_dumps", lambda: json.dumps(payload, ensure_ascii=False, indent=1))
    rec.run("json_write", lambda: graph_path.write_text(blob, encoding="utf-8"))
    size_mb = graph_path.stat().st_size / 1e6
    raw = rec.run("json_read", lambda: graph_path.read_text(encoding="utf-8"), note=f"{size_mb:.1f} MB")
    summary["graph_json_mb"] = round(size_mb, 1)
    reparsed = rec.run("json_loads", lambda: json.loads(raw))
    del raw, blob
    rehydrated = rec.run("graph_from_payload", lambda: graph_from_payload(reparsed))
    del reparsed, payload, rehydrated

    store = api["SqliteGraphStore"](db_path)
    rec.run("sidecar_nodes", lambda: store.upsert_many_nodes(graph.nodes))
    rec.run("sidecar_edges", lambda: store.upsert_many_edges(graph.edges))
    db_mb = db_path.stat().st_size / 1e6
    summary["sqlite_mb"] = round(db_mb, 1)

    backend = api["active_embedding_backend"]("auto")
    vector_cache = api["VectorCache"].for_project(root)
    bm25_index = api["Bm25Index"].for_project(root)

    # Warm both sidecar-backed lanes, then measure the warm query. The warming
    # cost is reported as its own phase because it is the compile-time price of
    # the warm read, not part of it.
    texts = [_node_text(node) for node in graph.nodes]
    rec.run(
        "bm25_warm_build",
        lambda: bm25_index.prepare(texts, _tokenize),
        note=f"db {db_mb:.1f} MB",
    )
    rec.run(
        "vector_warm_build",
        lambda: vector_cache.embed(backend, texts) if vector_cache else None,
        note=type(backend).__name__,
    )
    del texts

    # ``profile=True`` is the package's own per-lane cost accounting. It is
    # what turns "hybrid_search got slow" into a named lane, so the knee can be
    # attributed rather than guessed at. It cannot change the ranking.
    searched = rec.run(
        "hybrid_search_warm",
        lambda: api["hybrid_search"](
            graph,
            query,
            top_k=20,
            backend=backend,
            vector_cache=vector_cache,
            bm25_index=bm25_index,
            profile=True,
        ),
        note="all three lanes",
    )
    if searched is not None and searched.profile is not None:
        summary["lane_profile"] = searched.profile.to_dict()
        for name, lane in sorted(
            searched.profile.to_dict()["lanes"].items(),  # type: ignore[union-attr]
            key=lambda kv: -kv[1]["ms"],
        ):
            print(
                f"      lane {name:<10} {lane['ms']:9.1f} ms  scored={lane['scored']}",
                flush=True,
            )
    del searched

    seeds = [graph.nodes[i].id for i in range(0, len(graph.nodes), max(1, len(graph.nodes) // 5))][:5]
    rec.run("ppr", lambda: api["personalized_pagerank"](graph, seeds, top_k=20))
    rec.run(
        "compile_context",
        lambda: api["compile_context"](
            graph, project_root=str(root), query=query, depth=2, budget=32_000
        ),
    )


# --------------------------------------------------------------------------- #
# parent: memory gate + sweep
# --------------------------------------------------------------------------- #


def available_bytes() -> int:
    import psutil

    return int(psutil.virtual_memory().available)


def project_peak(results: List[Dict[str, object]], n_nodes: int) -> Optional[float]:
    """Least-squares linear projection of peak RSS onto ``n_nodes``.

    Fitted with an intercept rather than scaled from the largest measured
    point, because a fixed cost that has nothing to do with graph size — the
    interpreter, and the ~200 MB Model2Vec embedding model — otherwise gets
    multiplied by the node ratio too. At the sizes here that inflates the
    projection by around 15% and would refuse a size that comfortably fits.

    Every phase measured here is at worst linear in node count, so a line is
    the right family; the fit is used as a refusal threshold, not as a
    prediction anyone should quote.
    """
    points = [
        (float(r["n_nodes"]), max(float(p["peak_rss_mb"]) for p in r["phases"]))  # type: ignore[index]
        for r in results
    ]
    if not points:
        return None
    if len(points) == 1:
        x, y = points[0]
        return y * 1e6 * (n_nodes / x)
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    var = sum((x - mean_x) ** 2 for x, _ in points)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / var if var else 0.0
    return (mean_y + slope * (n_nodes - mean_x)) * 1e6


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sizes", nargs="+", type=int, help="node counts, ascending")
    parser.add_argument("--out", default="scale_results.json")
    parser.add_argument(
        "--ceiling-gb",
        type=float,
        default=0.0,
        help=(
            "Override the projected-peak refusal with a measured RSS ceiling, in GB. "
            "For the case where a direct measurement contradicts the projection: the "
            "fit is linear through few points and overshoots, and refusing a size "
            "already observed to fit would drop a real row from the curve. The child's "
            "hard ceiling still applies, so this trades a heuristic for a measurement "
            "rather than removing the guard."
        ),
    )
    parser.add_argument("--child", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--child-out", default="", help=argparse.SUPPRESS)
    parser.add_argument("--workdir", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.child:
        child_main(args.child, args.child_out, args.workdir)
        return 0

    results: List[Dict[str, object]] = []
    out = Path(args.out)
    if out.exists():
        results = json.loads(out.read_text())

    for n in sorted(args.sizes):
        avail = available_bytes()
        projected = project_peak(results, n)
        print(f"\n=== {n:,} nodes — {avail / 1e9:.1f} GB available ===", flush=True)
        ceiling = args.ceiling_gb * 1e9 or avail * 0.75
        if ceiling > avail * 0.85:
            print(
                f"    REFUSED: a {ceiling / 1e9:.1f} GB ceiling leaves too little of the "
                f"{avail / 1e9:.1f} GB available. Not starting this size.",
                flush=True,
            )
            break
        if projected is not None:
            print(f"    projected peak >= {projected / 1e9:.1f} GB", flush=True)
            if args.ceiling_gb:
                print(f"    projection overridden; ceiling {ceiling / 1e9:.1f} GB", flush=True)
            elif projected > avail * MEMORY_HEADROOM:
                print(
                    f"    REFUSED: projection exceeds {MEMORY_HEADROOM:.0%} of available "
                    f"memory. Stopping the sweep rather than risking swap.",
                    flush=True,
                )
                break
        with tempfile.TemporaryDirectory(prefix=f"scale{n}-") as tmp:
            child_out = Path(tmp) / "result.json"
            env = dict(os.environ, SCALE_CEILING_BYTES=str(ceiling))
            proc = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "0",
                    "--child",
                    str(n),
                    "--child-out",
                    str(child_out),
                    "--workdir",
                    tmp,
                ],
                cwd=str(REPO_ROOT),
                env=env,
            )
            if proc.returncode != 0 or not child_out.exists():
                print(f"    size {n} FAILED (rc={proc.returncode}); stopping sweep", flush=True)
                break
            row = json.loads(child_out.read_text())
            results.append(row)
        out.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"    -> {out}", flush=True)
        if row.get("abandoned"):
            # This size did not fit, so no larger one will. Every phase it did
            # complete is already recorded.
            print("    stopping sweep: the ceiling was reached at this size", flush=True)
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
