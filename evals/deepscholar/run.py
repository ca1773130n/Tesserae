"""Runner for DeepScholar-Bench: stage, compile, render, hand over to their CLI.

    # what would it cost? prints the banner and stops
    uv run python -m evals.deepscholar.run --dataset <clone>/dataset --file-ids 0-4

    # stage corpora and paper.csv and stop — no compile, no LLM, no network
    uv run python -m evals.deepscholar.run --dataset <clone>/dataset --file-ids 0-4 \
        --work ~/.blackhole/Tesserae/deepscholar/work \
        --output ~/.blackhole/Tesserae/deepscholar/out --stage-only

    # both arms, one backbone call each per query
    uv run python -m evals.deepscholar.run --dataset <clone>/dataset --file-ids 0-4 \
        --work ~/.blackhole/Tesserae/deepscholar/work \
        --output ~/.blackhole/Tesserae/deepscholar/out \
        --arms tesserae,bm25 --i-know-this-costs-money --yes

Then score each arm with the benchmark's own CLI, unchanged and unforked::

    .venv/bin/python -m eval.main --modes deepscholar_base \\
        --evals cite_p claim_coverage nugget_coverage \\
        --input-folder <output>/tesserae --file-id 0 --output-folder <results>/tesserae

Four things stand between an invocation and a bill, on
:mod:`evals.locomo.run`'s model:

1. **CI.** ``CI`` in the environment prints SKIP and exits 0 whatever was asked
   for.
2. **The cost banner**, in units this phase measured — queries, papers,
   characters, and backbone calls. Backbone calls are counted, not estimated:
   both arms make exactly one per query, plus one more for any query whose
   first reply cited outside the corpus.
3. **Explicit consent to spend.** Anything reaching an LLM refuses without
   ``--i-know-this-costs-money`` and then asks for a typed confirmation unless
   ``--yes``.
4. **Prerequisites.** A missing dataset, a work directory inside the repo, or
   an unknown arm prints ``SKIP: <what>`` plus the command that fixes it and
   exits 0.

The manifest carries no timestamps: the same inputs must produce the same bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..qa.run_qa_eval import Skip, _table
from .control import bm25_cards
from .dataset import CORPUS_ALL, CORPUS_CHOICES, WORD_CAP, Query, load_queries
from .evidence import EvidenceBudget, EvidenceCard, graph_cards
from .stage import (
    RefusedToCompileInRepo,
    default_compile,
    graph_path,
    load_graph,
    stage_query,
    verify_staged,
    write_intro,
)
from .writer import PROTOCOL_BACKBONE, RenderResult, openai_backbone, render

ARM_TESSERAE = "tesserae"
ARM_BM25 = "bm25"
ARMS = (ARM_TESSERAE, ARM_BM25)


def parse_file_ids(spec: str) -> List[int]:
    """``"0-4,7"`` -> ``[0, 1, 2, 3, 4, 7]``. Order preserved, duplicates dropped."""
    out: List[int] = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk[1:]:
            lo, _, hi = chunk.partition("-")
            values = range(int(lo), int(hi) + 1)
        else:
            values = [int(chunk)]
        for value in values:
            if value not in out:
                out.append(value)
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.deepscholar.run",
        description="Render DeepScholar-Bench related-work sections from a compiled graph.",
    )
    parser.add_argument("--dataset", required=True, type=Path,
                        help="the benchmark clone's dataset/ directory")
    parser.add_argument("--file-ids", default="", help='row indices, e.g. "0-4,7"')
    parser.add_argument("--work", type=Path, default=None,
                        help="scratch root for staged corpora and compiled graphs")
    parser.add_argument("--output", type=Path, default=None,
                        help="root for <arm>/<file-id>/{intro.md,paper.csv}")
    parser.add_argument("--arms", default=",".join(ARMS),
                        help=f"comma-separated subset of {', '.join(ARMS)}")
    parser.add_argument("--corpus", default=CORPUS_ALL, choices=list(CORPUS_CHOICES),
                        help="which citation table is the retrievable corpus")
    parser.add_argument("--paper-budget", type=int, default=None,
                        help="max papers in the evidence table (default: all)")
    parser.add_argument("--lines-per-paper", type=int, default=3)
    parser.add_argument("--evidence-chars", type=int, default=60_000)
    parser.add_argument("--no-fill", dest="fill", action="store_false",
                        help="do not top the Tesserae arm's cards up from the "
                             "abstract; the arms then differ in evidence volume "
                             "as well as selection")
    parser.add_argument("--word-cap", type=int, default=WORD_CAP)
    parser.add_argument("--model", default=PROTOCOL_BACKBONE)
    parser.add_argument("--extractor", default="deterministic",
                        choices=["deterministic", "llm"])
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--reuse-compile", action="store_true",
                        help="reuse an existing graph, after proving it is this query's")
    parser.add_argument("--i-know-this-costs-money", dest="consent",
                        action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser


def _budget(args: argparse.Namespace) -> EvidenceBudget:
    return EvidenceBudget(
        papers=args.paper_budget,
        lines_per_paper=args.lines_per_paper,
        chars=args.evidence_chars,
    )


def _banner(queries: Sequence[Query], arms: Sequence[str], args) -> List[str]:
    papers = sum(len(q.corpus) for q in queries)
    chars = sum(len(p.abstract) for q in queries for p in q.corpus)
    lines = [
        "DeepScholar-Bench run",
        f"  queries           {len(queries)}  (file ids {', '.join(q.file_id for q in queries)})",
        f"  corpus table      {args.corpus}",
        f"  cited papers      {papers}  ({chars:,} abstract chars)",
        f"  arms              {', '.join(arms)}",
        f"  backbone          {args.model}  (1 call per query per arm)",
        f"  backbone calls    {len(queries) * len(arms)} minimum, "
        f"+1 per query that cites outside its corpus",
    ]
    if ARM_TESSERAE in arms:
        lines.append(
            f"  extraction calls  0 (--extractor {args.extractor})"
            if args.extractor == "deterministic"
            else f"  extraction calls  ~{papers} (--extractor llm)"
        )
    return lines


def _confirm(args) -> None:
    if not args.consent:
        raise Skip(
            "this run spends money on a backbone",
            "re-run with --i-know-this-costs-money (and --yes to skip the prompt)",
        )
    if args.yes:
        return
    reply = input("type 'spend' to continue: ").strip()
    if reply != "spend":
        raise Skip("not confirmed", "re-run with --yes to skip the prompt")


def _cards_for(
    arm: str, query: Query, *, work: Path, budget: EvidenceBudget,
    reuse: bool, extractor: str, fill: bool,
) -> List[EvidenceCard]:
    if arm == ARM_BM25:
        return bm25_cards(query, budget=budget)
    staged_work = Path(work) / query.file_id
    path = graph_path(staged_work)
    if reuse:
        verify_staged(query, staged_work / "corpus")
        if not path.is_file():
            raise Skip(
                f"--reuse-compile: no graph at {path}",
                "re-run without --reuse-compile",
            )
    elif not path.is_file():
        default_compile(staged_work, extractor=extractor)
    return graph_cards(
        load_graph(path), query, root=staged_work, budget=budget,
        fill_from_abstract=fill,
    )


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if os.environ.get("CI"):
        print("SKIP: DeepScholar-Bench does not run in CI (it compiles and spends).")
        return 0
    try:
        return _run(args)
    except Skip as skip:
        return skip.emit()
    except RefusedToCompileInRepo as refusal:
        print(f"SKIP: {refusal}")
        return 0


def _run(args: argparse.Namespace) -> int:
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for arm in arms:
        if arm not in ARMS:
            raise Skip(f"unknown arm {arm!r}", f"--arms {','.join(ARMS)}")
    if not args.dataset.is_dir():
        raise Skip(
            f"no DeepScholar dataset at {args.dataset}",
            "clone stanford-mast/deepscholar-bench and pass its dataset/ directory",
        )
    file_ids = parse_file_ids(args.file_ids) or None
    queries = load_queries(args.dataset, file_ids=file_ids, corpus=args.corpus)
    empty = [q.file_id for q in queries if not q.corpus]
    if empty:
        raise Skip(
            f"file ids {', '.join(empty)} have no arXiv-linked cited abstract",
            "drop them from --file-ids; nothing can be cited for them",
        )

    for line in _banner(queries, arms, args):
        print(line)
    print()

    if args.work is None or args.output is None:
        raise Skip(
            "nothing was staged (dry run)",
            "pass --work and --output to stage; add --i-know-this-costs-money to render",
        )

    budget = _budget(args)
    staged = [stage_query(q, args.work) for q in queries]
    # Every arm gets the SAME paper.csv, copied from the one the stager wrote
    # from the dataset. Writing it per arm would let the two answer keys drift.
    for query, item in zip(queries, staged):
        for arm in arms:
            directory = Path(args.output) / arm / query.file_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "paper.csv").write_bytes(item.paper_csv.read_bytes())
    print(f"staged {len(staged)} corpora under {Path(args.work).resolve()}")
    if args.stage_only:
        print("--stage-only: no compile, no backbone call, nothing spent.")
        return 0

    _confirm(args)
    backbone = openai_backbone(args.model)
    if not getattr(backbone, "available", True):
        raise Skip("no OPENAI_API_KEY in the environment", "export OPENAI_API_KEY=...")

    rows: List[List[str]] = []
    manifest: Dict[str, object] = {
        "corpus": args.corpus, "model": args.model, "word_cap": args.word_cap,
        "budget": {"papers": budget.papers, "lines_per_paper": budget.lines_per_paper,
                   "chars": budget.chars, "fill_from_abstract": args.fill},
        "arms": {},
    }
    for arm in arms:
        results: List[RenderResult] = []
        for query in queries:
            cards = _cards_for(
                arm, query, work=args.work, budget=budget,
                reuse=args.reuse_compile, extractor=args.extractor, fill=args.fill,
            )
            result = render(query, cards, backbone=backbone, word_cap=args.word_cap)
            if not result.ok:
                print(f"[deepscholar] {arm}/{query.file_id}: {result.error}",
                      file=sys.stderr)
            write_intro(result.text, Path(args.output) / arm, query.file_id)
            results.append(result)
            lines_shown = sum(len(c.lines) for c in cards)
            claim_lines = sum(c.claim_lines for c in cards)
            rows.append([
                arm, query.file_id, str(len(query.corpus)), str(len(cards)),
                str(lines_shown), str(sum(c.chars for c in cards)),
                f"{claim_lines}/{lines_shown}" if lines_shown else "0/0",
                str(len(result.cited)), str(result.repaired_citations),
                str(result.stripped_citations), str(result.calls),
            ])
        manifest["arms"][arm] = [
            {
                "file_id": r.file_id, "cited": list(r.cited), "calls": r.calls,
                "stripped_citations": r.stripped_citations,
                "repaired_citations": r.repaired_citations,
                "cards": len(r.cards), "chars": len(r.text),
                "evidence_lines": sum(len(c.lines) for c in r.cards),
                "evidence_chars": sum(c.chars for c in r.cards),
                "claim_lines": sum(c.claim_lines for c in r.cards),
                "origins": sorted({c.origin for c in r.cards}),
                "error": r.error,
            }
            for r in results
        ]

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print()
    for line in _table(
        ["arm", "file", "corpus", "cards", "lines", "ev chars", "claim lines",
         "cited", "repaired", "stripped", "calls"],
        rows,
    ):
        print(line)
    print(f"\nmanifest: {out / 'run.json'}")
    print("score with the benchmark's own CLI:")
    for arm in arms:
        print(
            f"  python -m eval.main --modes deepscholar_base "
            f"--evals cite_p claim_coverage nugget_coverage "
            f"--input-folder {out / arm} --file-id <id> --output-folder <results>/{arm}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
