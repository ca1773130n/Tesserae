"""Does the knowledge graph get smarter as documents accumulate, or just bigger?

Compiles the demo corpus in chronological slices and, after each one, asks
whether a fixed set of multi-hop questions has become answerable. "More docs ->
more nodes" is arithmetic; the claim worth testing is that a question which is
*unanswerable* at N=10 becomes answerable at N=40 because the connecting
evidence finally exists.

Run:
    uv run python evals/growth/run.py --out evals/growth/report.md

Cost: the slices are cumulative and compiled into one project, so the LLM
extractor pays for each document exactly once across the whole run (~50s/doc,
~90 min for the full corpus). Re-running with a warm ~/.tesserae/llm_cache is
minutes.

NEVER point --work at the repo root: a compile there overwrites the project's
real .tesserae/graph.json with this experiment's much smaller one.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import deque
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "examples" / "demo-corpus" / "data" / "research"
QUESTIONS = Path(__file__).parent / "questions.yaml"

#: Hop budget, chosen by sweeping 1-4 against the controls (evals/growth/
#: sweep_hops.py). Do not raise it without re-running that sweep.
#:
#:     MAX_HOPS=1  controls PASS   curve 0->3->7->9->12->13   13/15
#:     MAX_HOPS=2  controls PASS   curve 0->3->7->9->12->13   13/15
#:     MAX_HOPS=3  controls PASS   curve 0->3->7->9->12->15   15/15   <- chosen
#:     MAX_HOPS=4  controls FAIL (3 firings)                  15/15
#:
#: 4 admits a spurious path that the control catches:
#:     Algorithm:"Direct Sparse Odometry" -> Model:DeepV2D -> Metric:LPIPS
#:       -> Paper:"Magic3D"
#: — "both papers report LPIPS" is shared infrastructure, not a relationship.
#:
#: The gap between 2 and 3 is real and only appears at the full corpus: through
#: N=40 every budget scores identically, so multi-hop looks like dead weight. At
#: N=50 two questions become answerable only at 3 hops. Multi-hop synthesis in
#: this graph emerges at scale rather than being present throughout — which is
#: why the sweep has to run to the last slice before drawing a conclusion.
MAX_HOPS = 3


# --------------------------------------------------------------------------
# corpus


def paper_dates() -> List[Tuple[str, Path, str]]:
    """(iso_date, paper_dir, arxiv_id) for every dated paper, oldest first."""
    out = []
    for d in sorted((CORPUS / "papers").iterdir()):
        abstract = d / "abstract.md"
        if not abstract.is_file():
            continue
        iso = arxiv = ""
        for line in abstract.read_text(encoding="utf-8").splitlines()[:30]:
            if line.startswith("date:"):
                iso = line.split(":", 1)[1].strip()
            elif line.startswith("arxiv:"):
                arxiv = line.split(":", 1)[1].strip().strip('"')
            if iso and arxiv:
                break
        if iso:
            out.append((iso, d, arxiv))
    return sorted(out)


def slice_points(papers: List[Tuple[str, Path, str]], n_slices: int) -> List[int]:
    """Cut counts, evenly spaced, always ending at the full corpus."""
    total = len(papers)
    step = max(1, total // n_slices)
    cuts = list(range(step, total, step))[: n_slices - 1] + [total]
    return sorted(set(cuts))


# --------------------------------------------------------------------------
# graph


def load_graph(work: Path) -> dict:
    return json.loads((work / ".tesserae" / "graph.json").read_text(encoding="utf-8"))


def grounded_sources(graph: dict, staged: Set[Path]) -> Set[str]:
    """source_path values that point at a document actually staged in this slice.

    The extractor mints nodes for papers cited in related-work sections, so a
    7-paper graph already contains a Paper node named "Mip-NeRF 360" with no
    content behind it. Scoring those as knowledge would make the curve rise for
    free, so every node is checked against the staged files.
    """
    names = {p.name for p in staged}
    found = set()
    for n in graph.get("nodes", []):
        sp = n.get("source_path") or ""
        if sp and any(part in names for part in Path(sp).parts):
            found.add(sp)
    return found


def resolve_anchor(graph: dict, anchor: str, grounded: Set[str]) -> Set[str]:
    """Node ids whose name/alias/description matches `anchor` AND are grounded."""
    a = anchor.lower()
    hits = set()
    for n in graph.get("nodes", []):
        if (n.get("source_path") or "") not in grounded:
            continue
        hay = " ".join([
            str(n.get("name") or ""),
            " ".join(n.get("aliases") or []),
            str(n.get("description") or ""),
        ]).lower()
        if a in hay:
            hits.add(n["id"])
    return hits


#: Node types that aggregate rather than assert. A CommunitySummary is linked to
#: everything it summarises, so routing through one makes any two nodes in the
#: corpus 2 hops apart — the control proved it, connecting Direct Sparse Odometry
#: to text-to-3D via `Person:Engel -> CommunitySummary -> ApproachFamily`. Person
#: is excluded for the same reason in reverse: sharing an author is not a
#: topical relationship.
HUB_TYPES = {"CommunitySummary", "Synthesis", "Trend", "ResearchTopic", "Person"}


def adjacency(graph: dict) -> Dict[str, Set[str]]:
    hubs = {n["id"] for n in graph.get("nodes", []) if n.get("type") in HUB_TYPES}
    adj: Dict[str, Set[str]] = {}
    for e in graph.get("edges", []):
        s, t = e.get("source"), e.get("target")
        if not s or not t or s in hubs or t in hubs:
            continue
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)   # undirected: "is X connected to Y"
    return adj


def shortest_path(adj: Dict[str, Set[str]], starts: Set[str], goals: Set[str],
                  max_hops: int) -> Optional[List[str]]:
    if not starts or not goals:
        return None
    if starts & goals:
        # A single node satisfying BOTH anchors is the strongest possible
        # answer, not a degenerate one: at N=50 the overlap includes
        # `ContributionClaim: "GS-SLAM is among the first 3DGS-based dense RGB-D
        # SLAM systems"` — the answer to gs-slam, stated outright. The first
        # version of this function returned None here as a "vague anchor" guard,
        # which meant the better the graph answered a question the more surely it
        # scored zero. Eight of fifteen questions were suppressed that way.
        return [sorted(starts & goals)[0]]   # zero hops: one node bridges both
    prev: Dict[str, Optional[str]] = {s: None for s in starts}
    q = deque((s, 0) for s in starts)
    while q:
        node, depth = q.popleft()
        if depth >= max_hops:
            continue
        for nxt in adj.get(node, ()):
            if nxt in prev:
                continue
            prev[nxt] = node
            if nxt in goals:
                path, cur = [], nxt
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                return list(reversed(path))
            q.append((nxt, depth + 1))
    return None


# --------------------------------------------------------------------------
# questions


def load_questions() -> List[dict]:
    import yaml
    return yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))["questions"]


def evaluate(graph: dict, questions: List[dict], staged: Set[Path],
             staged_arxiv: Set[str]) -> List[dict]:
    grounded = grounded_sources(graph, staged)
    adj = adjacency(graph)
    rows = []
    for q in questions:
        a1 = resolve_anchor(graph, q["anchors"][0], grounded)
        a2 = resolve_anchor(graph, q["anchors"][1], grounded)
        path = shortest_path(adj, a1, a2, MAX_HOPS)
        # Required papers must be staged. A question whose sources are absent
        # cannot be legitimately answerable; if a path turns up anyway, that is
        # a false positive worth seeing rather than suppressing.
        have_sources = all(r in staged_arxiv for r in (q.get("requires") or []))
        rows.append({
            "id": q["id"],
            "control": bool(q.get("control")),
            "anchor_hits": (len(a1), len(a2)),
            "have_sources": have_sources,
            "connected": path is not None,
            "hops": (len(path) - 1) if path else None,
            "answerable": bool(path) and have_sources,
            # Connected before the required sources arrived. NOT a checker bug
            # and not necessarily wrong: at N=7 "signed distance function" and
            # "volume rendering" already connect via IGR and NeRF, two hops, no
            # NeuS or VolSDF in sight. The concepts genuinely co-occur; the
            # specific synthesis the question asks about does not yet exist.
            # Tracked separately so the gap between "concepts touch" and "the
            # answer is present" stays visible instead of being scored either way.
            "connected_early": bool(path) and not have_sources,
        })
    return rows


# --------------------------------------------------------------------------
# driver


def compile_slice(work: Path, first: bool) -> float:
    py = str(REPO / ".venv" / "bin" / "python")
    t0 = time.time()
    if first:
        subprocess.run([py, "-m", "tesserae", "init", "--yes", "--source", "./corpus"],
                       cwd=work, check=True, capture_output=True)
    r = subprocess.run([py, "-m", "tesserae", "compile"],
                       cwd=work, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"compile failed in {work}:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path,
                    default=Path.home() / ".blackhole" / "Tesserae"
                    / date.today().isoformat() / "kg-growth")
    ap.add_argument("--slices", type=int, default=6)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if (args.work / "pyproject.toml").exists() or args.work.resolve() == REPO:
        sys.exit("refusing to compile inside the repo — that overwrites .tesserae/graph.json")

    papers = paper_dates()
    cuts = slice_points(papers, args.slices)
    print(f"{len(papers)} dated papers, slices at {cuts}", flush=True)

    work = args.work
    shutil.rmtree(work, ignore_errors=True)
    (work / "corpus").mkdir(parents=True)

    questions = load_questions()
    results, staged, staged_arxiv, staged_n = [], set(), set(), 0

    for i, cut in enumerate(cuts):
        for iso, d, arxiv in papers[staged_n:cut]:
            shutil.copytree(d, work / "corpus" / d.name)
            staged.add(d)
            staged_arxiv.add(arxiv)
        staged_n = cut
        secs = compile_slice(work, first=(i == 0))
        graph = load_graph(work)
        rows = evaluate(graph, questions, staged, staged_arxiv)
        real = [r for r in rows if not r["control"]]
        ctrl = [r for r in rows if r["control"]]
        results.append({
            "n_papers": cut,
            "through": papers[cut - 1][0],
            "nodes": len(graph.get("nodes", [])),
            "edges": len(graph.get("edges", [])),
            "answerable": sum(r["answerable"] for r in real),
            "total": len(real),
            "controls_fired": sum(r["connected"] for r in ctrl),
            "connected_early": sum(r["connected_early"] for r in real),
            "seconds": round(secs),
            "rows": rows,
        })
        s = results[-1]
        print(f"  N={cut:>3} through {s['through']}  nodes={s['nodes']:>5} "
              f"edges={s['edges']:>5}  answerable={s['answerable']}/{s['total']} "
              f"controls={s['controls_fired']}  {s['seconds']}s", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results, questions), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


def render(results: List[dict], questions: List[dict]) -> str:
    qs = {q["id"]: q for q in questions}
    L = [f"# Does the knowledge graph get smarter as documents accumulate?",
         "",
         f"Generated {date.today().isoformat()} by `evals/growth/run.py`. "
         f"Corpus: `examples/demo-corpus/data/research/papers`, compiled in "
         f"cumulative chronological slices.", "",
         "## Growth curve", "",
         "| papers | through | nodes | edges | edges/node | answerable | controls fired | connected early |",
         "|---|---|---|---|---|---|---|"]
    for s in results:
        L.append(f"| {s['n_papers']} | {s['through']} | {s['nodes']} | {s['edges']} | "
                 f"{s['edges']/max(1,s['nodes']):.2f} | "
                 f"**{s['answerable']}/{s['total']}** | {s['controls_fired']} |")

    L += ["", "`controls fired` must stay 0. The controls ask questions this corpus "
          "cannot answer; if a path ever appears between their anchors, the checker "
          "is finding spurious connections and every number in this table is "
          "suspect.", "",
          "## When each question became answerable", "",
          "| question | first answerable at | unlocked by |", "|---|---|---|"]

    for q in questions:
        if q.get("control"):
            continue
        first = next((s for s in results
                      if next(r for r in s["rows"] if r["id"] == q["id"])["answerable"]),
                     None)
        when = f"N={first['n_papers']} ({first['through']})" if first else "never"
        L.append(f"| {q['id']} | {when} | {', '.join(q.get('requires') or []) or '—'} |")

    L += ["", "## Reading this honestly", "",
          "`answerable` means the graph contains a path of at most "
          f"{MAX_HOPS} hops between the question's two anchor concepts, where both "
          "anchors resolve to nodes grounded in documents actually present in the "
          "slice, and every document the question requires is present.", "",
          "It does **not** mean an agent produced a correct answer. It means the "
          "graph holds the connection an answer would have to traverse — a "
          "precondition, not a demonstration. Grounding is checked separately "
          "because the extractor mints nodes for papers cited in related-work "
          "sections: a 7-paper graph already contains a `Paper` node for "
          "Mip-NeRF 360 with no content behind it, and counting those would make "
          "this curve rise for free.", "",
          "## Per-slice detail", ""]

    for s in results:
        L += [f"### N={s['n_papers']} — through {s['through']} "
              f"({s['nodes']} nodes, {s['edges']} edges, {s['seconds']}s)", "",
              "| question | anchors resolved | sources present | connected | hops |",
              "|---|---|---|---|---|"]
        for r in s["rows"]:
            tag = " *(control)*" if r["control"] else ""
            L.append(f"| {r['id']}{tag} | {r['anchor_hits'][0]}/{r['anchor_hits'][1]} | "
                     f"{'yes' if r['have_sources'] else 'no'} | "
                     f"{'yes' if r['connected'] else 'no'} | {r['hops'] if r['hops'] is not None else '—'} |")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
