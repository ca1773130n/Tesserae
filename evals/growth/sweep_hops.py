"""Sweep the hop budget in ONE compile pass.

Running run.py once per hop budget re-extracts the corpus each time (~2 min per
slice in a fresh work dir). The compiles are identical across budgets — only the
path search differs — so compile once per slice and evaluate every budget against
that graph.
"""
from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run as R  # noqa: E402

BUDGETS = [1, 2, 3, 4]


def main() -> int:
    work = Path.home() / ".blackhole" / "Tesserae" / date.today().isoformat() / "kg-sweep"
    papers = R.paper_dates()
    cuts = R.slice_points(papers, 6)
    questions = R.load_questions()

    shutil.rmtree(work, ignore_errors=True)
    (work / "corpus").mkdir(parents=True)

    staged, staged_arxiv, n = set(), set(), 0
    table = {h: [] for h in BUDGETS}

    for i, cut in enumerate(cuts):
        for iso, d, arxiv in papers[n:cut]:
            shutil.copytree(d, work / "corpus" / d.name)
            staged.add(d)
            staged_arxiv.add(arxiv)
        n = cut
        R.compile_slice(work, first=(i == 0))
        graph = R.load_graph(work)

        for h in BUDGETS:
            R.MAX_HOPS = h                      # evaluate() reads the module global
            rows = R.evaluate(graph, questions, staged, staged_arxiv)
            real = [r for r in rows if not r["control"]]
            ctrl = [r for r in rows if r["control"]]
            table[h].append({
                "n": cut,
                "ans": sum(r["answerable"] for r in real),
                "tot": len(real),
                "ctl": sum(r["connected"] for r in ctrl),
            })
        print(f"  N={cut:>3}  " + "   ".join(
            f"h{h}:{table[h][-1]['ans']}/{table[h][-1]['tot']}(ctl {table[h][-1]['ctl']})"
            for h in BUDGETS), flush=True)

    print("\n=== VERDICT ===")
    for h in BUDGETS:
        rows = table[h]
        ctl = sum(r["ctl"] for r in rows)
        final = rows[-1]["ans"]
        curve = " -> ".join(str(r["ans"]) for r in rows)
        gate = "PASS" if ctl == 0 else f"FAIL ({ctl} control firings)"
        print(f"  MAX_HOPS={h}: controls {gate}  curve {curve}  final {final}/15")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
