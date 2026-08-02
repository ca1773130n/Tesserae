"""Is the anchor matcher still measuring the graph, or has it replaced it?

`run.py`'s two controls are necessary and not sufficient. Three candidate
matchers were compared for the `hash-encoding` fix; all three reached 15/15 with
both controls silent, and so did a deliberately crude null model. What separated
them is here.

The decisive one is `MAX_HOPS=0`: how much of the score survives when the graph
is taken away entirely. A matcher generous enough that the two anchor phrases
select overlapping node sets scores well with zero traversal — at which point
the eval has stopped asking "does the graph hold the connection" and started
asking "do these two phrases share vocabulary". One rejected candidate scored
14/15 at zero hops. The shipped one scores exactly what the original substring
matcher scored, which is the point: its extra nodes buy nothing lexically, and
the question it fixes is fixed by traversal.

Run against any compiled growth work dir (`--work`, the same layout run.py
builds: `<work>/.tesserae/graph.json` plus `<work>/corpus/<paper-dir>/`):

    uv run python evals/growth/probe_anchors.py --work ~/.blackhole/Tesserae/<date>/kg-growth

The ceilings below are loosening budgets, not targets: a matcher change that
stays under all of them has not turned into keyword soup. They are corpus-specific
— see the table at the constants. Exits non-zero on any breach; exits 0 with SKIP
if no compiled graph is available, since a compile is ~2 hours of LLM extraction
and this is deliberately not CI-wired.
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path
from typing import List, Set

sys.path.insert(0, str(Path(__file__).parent))
import run as R  # noqa: E402

#: Anchors invented from outside 3D vision, plus lexical decoys sharing exactly
#: one token with a real anchor ("training wheels", "implicit bias", "sparse
#: attention"). None of them has anything to do with radiance fields, so any
#: that reaches "radiance field" inside the hop budget is a false positive the
#: two controls happen not to cover.
PROBES = [
    "protein folding", "supply chain", "quantum error correction",
    "renal dialysis", "monetary policy", "phylogenetic tree",
    "options pricing", "seismic tomography", "immune response",
    "legal precedent", "training wheels", "train station",
    "speedy adoption", "sparse attention", "implicit bias",
    "sorting algorithm", "dense crowd", "explicit consent",
    "register allocation", "surface tension", "dynamic pricing",
    "field trial", "sparse matrix", "single malt", "feed additive",
    "topological insulator",
]

#: Ceilings. These guard a change to `resolve_anchor`: run before and after, and
#: a matcher that loosened too far breaks one. They are NOT corpus-independent —
#: every one of them scales with how much graph there is — so they are stated per
#: corpus, and re-baselining them is only legitimate when the *corpus* changed.
#:
#: 2026-08-02, first run over the full 73-document corpus (3394 nodes, 9106
#: edges) against the previous 50-paper graph (2387 / 6169). The matcher did not
#: change between them; the corpus did:
#:
#:                        50 papers    73 documents
#:     h=0 answerable        10             10        unchanged — good
#:     h=1 answerable        13             14        <- the number that matters
#:     median anchor set     21.5           39.5
#:     total anchor slots    945           1382
#:     all-pairs 3-hop       84.7%          87.3%
#:     DSO anchor reach      11/27          18/27
#:     early connections    119/50 pfx     163/73 pfx
#:
#: Read that h=1 row before trusting a 15/15. Fourteen of fifteen questions
#: answer in a SINGLE hop on the bigger corpus, and 87% of arbitrary anchor pairs
#: connect within three. Answerability inflates with corpus size because anchor
#: resolution is set-based: more nodes means bigger anchor sets, more starting
#: points, shorter paths. The graph may well be smarter; this instrument can no
#: longer tell you that from "there is simply more of it". Fixing that — capping
#: anchor sets, normalising by graph size, or requiring the path to be
#: evidence-supported — is the top open item on the eval itself.
#:
#: The ceilings below are re-baselined to the 73-document corpus so the file
#: still works as a gate on matcher changes. h=1 is deliberately NOT relaxed to
#: its measured 14: it is left where it was, failing, because that failure is the
#: finding and hiding it would make the probe agree with a claim it disproved.
MAX_ZERO_HOP = 11      # [10] instrument integrity — the criterion that mattered
MAX_ONE_HOP = 13       # [14, FAILING] see above; do not raise to make it pass
MAX_MEDIAN_SET = 42    # [39.5] was 25 on the 50-paper corpus
MAX_TOTAL_SLOTS = 1450 # [1382] was 1010
MAX_PAIR_RATE = 89.0   # [87.3%] was 86.0 — everything-connects is not a smarter graph
MIN_ANSWERABLE = 14    # [15] a band: extraction varies, see run.py's MAX_HOPS note
MAX_DSO_REACH = 20     # [18 of 27] was 13 on the 50-paper corpus; see KNOWN_FALSE_POSITIVE
MAX_EARLY_TOTAL = 175  # [163 over 73 prefixes] was 125 over 50

#: A false positive the label-subset layer introduces, recorded rather than
#: hidden. Built by the second control's own construction — an anchor from one
#: research thread against one from another — but with a partner the shipped
#: control does not use:
#:
#:     Paper:"Direct Sparse Odometry" -> ResearchField:"Visual SLAM and MVS"
#:       -> Paper:"Co-SLAM" -> ContributionClaim:"...hybrid hash-grid..."
#:
#: No paper connects DSO to hash encoding; the bridge is field membership, the
#: same "shared infrastructure, not a relationship" pattern MAX_HOPS=4 was
#: rejected over. The foothold is the ContributionClaim, which layer 2 admits and
#: whose degree is 2 — so the max-degree-8 argument in run.py cannot see this:
#: the hub is one hop PAST the node the loosening added.
#:
#: It is not promoted into questions.yaml as a third control. A control that is
#: known to fire turns the headline "controls fired: 0" into a number nobody can
#: read. It is checked here instead, where a reader gets the finding and the
#: reason together. Context, in fairness: under the substring matcher DSO
#: already reached 10 of the other 27 anchors within the budget, so the shipped
#: control's silence was the luck of its partner, not discrimination. The matcher
#: took it to 11 of 27; the 73-document corpus takes it to 18.
KNOWN_FALSE_POSITIVE = ("Direct Sparse Odometry", "hash encoding")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True,
                    help="a compiled growth work dir (.tesserae/graph.json + corpus/)")
    args = ap.parse_args()

    gpath = args.work / ".tesserae" / "graph.json"
    if not gpath.is_file():
        print(f"SKIP: no compiled graph at {gpath}\n"
              f"      build one with: uv run python evals/growth/run.py "
              f"--out /tmp/report.md --work {args.work}")
        return 0

    graph = json.loads(gpath.read_text(encoding="utf-8"))
    # Files as well as directories: open questions are staged as single .md
    # files, and skipping them made every node they source look ungrounded.
    staged = set((args.work / "corpus").iterdir())
    names = {p.name for p in staged}
    arxiv = {ax for _, d, ax, _ in R.corpus_docs() if ax and d.name in names}
    questions = R.load_questions()
    grounded = R.grounded_sources(graph, staged)
    adj = R.adjacency(graph)

    anchors: List[str] = []
    for q in questions:
        for a in q["anchors"]:
            if a not in anchors:
                anchors.append(a)
    sets = {a: R.resolve_anchor(graph, a, grounded) for a in anchors}

    print(f"{len(graph.get('nodes', []))} nodes, {len(graph.get('edges', []))} edges, "
          f"{len(staged)} papers staged, {len(anchors)} distinct anchors\n")
    fails: List[str] = []

    def check(ok: bool, msg: str) -> None:
        print(("  ok   " if ok else "  FAIL ") + msg)
        if not ok:
            fails.append(msg)

    # --- hop sweep: score and controls at every budget up to the shipped one
    print("hop sweep")
    budget = R.MAX_HOPS
    try:
        for h in range(0, budget + 1):
            R.MAX_HOPS = h              # evaluate() reads the module global
            rows = R.evaluate(graph, questions, staged, arxiv)
            real = [r for r in rows if not r["control"]]
            ans = sum(r["answerable"] for r in real)
            ctl = sum(r["connected"] for r in rows if r["control"])
            check(ctl == 0, f"h={h}: controls fired {ctl}, must be 0")
            if h == 0:
                check(ans <= MAX_ZERO_HOP,
                      f"h=0: {ans}/{len(real)} answerable with the graph removed "
                      f"(<= {MAX_ZERO_HOP}) — above this the matcher IS the eval")
            elif h == 1:
                check(ans <= MAX_ONE_HOP, f"h=1: {ans}/{len(real)} (<= {MAX_ONE_HOP})")
            elif h == budget:
                check(ans >= MIN_ANSWERABLE,
                      f"h={h}: {ans}/{len(real)} answerable (>= {MIN_ANSWERABLE})")
            else:
                print(f"       h={h}: {ans}/{len(real)}")
    finally:
        R.MAX_HOPS = budget

    # --- out-of-domain probes
    print("\nout-of-domain probes")
    goal = sets.get("radiance field") or R.resolve_anchor(graph, "radiance field", grounded)
    bad = [p for p in PROBES
           if R.shortest_path(adj, R.resolve_anchor(graph, p, grounded), goal, budget)]
    check(not bad, f"{len(bad)}/{len(PROBES)} reach 'radiance field' in {budget} hops"
                   + (f": {bad}" if bad else ""))

    # --- breadth: how much of the graph an anchor claims
    print("\nanchor breadth")
    sizes = sorted(len(s) for s in sets.values())
    med, total = statistics.median(sizes), sum(sizes)
    check(med <= MAX_MEDIAN_SET, f"median anchor set {med} (<= {MAX_MEDIAN_SET})")
    check(total <= MAX_TOTAL_SLOTS, f"total anchor slots {total} (<= {MAX_TOTAL_SLOTS})")

    pairs = list(itertools.combinations(anchors, 2))
    hit = sum(R.shortest_path(adj, sets[x], sets[y], budget) is not None for x, y in pairs)
    rate = 100.0 * hit / max(1, len(pairs))
    check(rate <= MAX_PAIR_RATE,
          f"all-pairs {budget}-hop rate {rate:.1f}% over {len(pairs)} pairs "
          f"(<= {MAX_PAIR_RATE}%) — everything-connects is not a smarter graph")

    # --- the known false positive, and how far its anchor reaches
    print("\nknown false positive")
    x, y = KNOWN_FALSE_POSITIVE
    ax_set = sets.get(x) or R.resolve_anchor(graph, x, grounded)
    ay_set = sets.get(y) or R.resolve_anchor(graph, y, grounded)
    p = R.shortest_path(adj, ax_set, ay_set, budget)
    print(f"  {'FIRES' if p else 'clear'}  {x!r} <-> {y!r}"
          + (f" in {len(p) - 1} hops — expected, see KNOWN_FALSE_POSITIVE" if p
             else " — narrower than when this was written, update the comment"))
    reach = sum(R.shortest_path(adj, ax_set, sets[a], budget) is not None
                for a in anchors if a != x)
    check(reach <= MAX_DSO_REACH,
          f"{x!r} reaches {reach}/{len(anchors) - 1} anchors (<= {MAX_DSO_REACH})")

    # --- early connections, swept over every prefix rather than the 6 cuts
    #     run.py reports at. The shift this catches (a question connecting two
    #     prefixes earlier than before) is invisible at --slices 6 and shows up
    #     at 5, 8 and 10. PROXY, and the difference matters: grounding is
    #     restricted to the first k papers but the graph is the full N=50 one,
    #     so nodes a real k-paper compile would never have minted are present
    #     and merely filtered. It bounds the direction of the error, not its size.
    print("\nearly connections (all prefixes, proxy)")
    papers = [(iso, d, ax_id) for iso, d, ax_id, _ in R.corpus_docs() if d.name in names]
    early = 0
    for k in range(1, len(papers) + 1):
        st = {d for _, d, _ in papers[:k]}
        rows = R.evaluate(graph, questions, st, {a for _, _, a in papers[:k]})
        early += sum(r["connected_early"] for r in rows if not r["control"])
    check(early <= MAX_EARLY_TOTAL,
          f"{early} connections before their sources over {len(papers)} prefixes "
          f"(<= {MAX_EARLY_TOTAL})")

    print("\n" + ("all probes pass" if not fails else f"{len(fails)} FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
