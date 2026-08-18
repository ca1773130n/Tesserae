"""Runner for the QA benchmark: stage, answer, score, report.

    # score answers that already exist — no LLM, no network, no corpus
    uv run python -m evals.qa.run_qa_eval --score answers/*.json \
        --out ~/.blackhole/Tesserae/qa/report.md

    # phase 1: stage the corpus for Tesserae. Writes files. Compiles nothing.
    uv run python -m evals.qa.run_qa_eval --system tesserae --stage-only \
        --project ~/.blackhole/Tesserae/<date>/qa-run

    # phase 2 (after YOU have compiled): answer the questions. Costs LLM quota.
    uv run python -m evals.qa.run_qa_eval --system tesserae --answer \
        --project ~/.blackhole/Tesserae/<date>/qa-run --i-know-this-costs-money

Three guards, in the order they fire:

1. **CI.** If ``CI`` is set in the environment the runner prints SKIP and exits
   0, whatever else was asked for. This must never run in CI: the answering
   phase costs LLM quota and the staging phase writes a corpus directory.
2. **Prerequisites**, on the ``evals/growth/probe_anchors.py`` model — a missing
   corpus, a missing vendored ABC, an uncompiled project each print
   ``SKIP: <what>`` plus the exact command that satisfies it, and exit 0. A
   benchmark that fails loudly on a missing optional input gets wired into CI by
   someone trying to make the build green, and then it runs.
3. **Explicit consent to spend.** ``--answer`` refuses without
   ``--i-know-this-costs-money``. There is no default that reaches an LLM.

**The runner never ingests.** For Tesserae, staging writes documents to a corpus
directory and stops; the compile between the phases is a command a human types.
That is the difference between a harness you can leave lying around and one that
starts a two-hour extraction because someone ran it to see what it did.

The report is written in the shape of ``evals/federation/report.md``: a
provenance paragraph, then numbered sections of markdown tables. No timestamps —
re-running over the same answers must produce the same bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .scorer import fairness_blockers, rank_systems, score_system
from .vendor_base import CORPUS_JSON, QA_PAIRS_JSON, MissingPrerequisite

HERE = Path(__file__).resolve().parent
UNANSWERABLE_JSON = HERE / "unanswerable.json"

#: Where a report goes when ``--out`` is not given: the project's scratch root,
#: **outside the repository**. It used to default to ``evals/qa/report.md``,
#: which this branch had just un-gitignored so the rest of the directory could
#: be checked in — so the first ``git add -A`` after a run would have committed
#: a comparative table naming a competitor, and ``_first_party_docs()`` never
#: scans ``evals/`` so no guard would have caught it. A generated number is
#: scratch until a human decides to publish it; the default now says so.
#: ``evals/qa/report*.md`` is gitignored as well, for the operator who passes
#: the old path explicitly or copies a command out of git history.
#:
#: No date in the path: this module is held to byte-identical re-runs, and a
#: default output path that moves at midnight is a wall clock in the harness.
DEFAULT_REPORT = Path.home() / ".blackhole" / "Tesserae" / "qa" / "report.md"

SYSTEMS = ("tesserae", "null", "bm25", "hybrid")


class Skip(Exception):
    """A prerequisite is missing. Prints SKIP and exits 0 — see the module doc."""

    def __init__(self, what: str, remedy: str) -> None:
        super().__init__(what)
        self.what = what
        self.remedy = remedy

    @classmethod
    def from_prerequisite(cls, exc: MissingPrerequisite) -> "Skip":
        return cls(exc.what, exc.remedy)

    def emit(self) -> int:
        print(f"SKIP: {self.what}\n      {self.remedy}")
        return 0


# --------------------------------------------------------------------- inputs


def load_questions(
    pairs_file: Optional[Path], unanswerable_file: Optional[Path]
) -> List[Dict[str, Any]]:
    """The question set: gold-answer pairs plus the unanswerable probes.

    Both halves are needed. Exact match and token F1 alone cannot see a system
    that answers everything confidently, and the refusal rate alone cannot see
    one that refuses everything — the two halves are each other's control.
    """
    questions: List[Dict[str, Any]] = []
    if pairs_file is not None:
        if not pairs_file.is_file():
            raise Skip(
                f"question set not found at {pairs_file}",
                "it ships inside the vendored cognee clone: "
                "git clone https://github.com/topoteretes/cognee evals/cognee",
            )
        for row in json.loads(pairs_file.read_text(encoding="utf-8")):
            questions.append({
                "question": row["question"],
                "gold": row.get("answer"),
                "stratum": row.get("level") or "unspecified",
            })
    if unanswerable_file is not None:
        if not unanswerable_file.is_file():
            raise Skip(
                f"unanswerable probe set not found at {unanswerable_file}",
                "it is checked in at evals/qa/unanswerable.json — restore it from git",
            )
        for row in json.loads(unanswerable_file.read_text(encoding="utf-8")):
            # gold stays None: that IS the unanswerable marker the scorer reads.
            questions.append({
                "question": row["question"],
                "gold": None,
                "stratum": row.get("level") or "unanswerable",
            })
    if not questions:
        raise Skip("no questions selected", "drop --no-unanswerable, or pass --questions")
    return questions


def load_corpus(corpus_file: Path) -> List[str]:
    if not corpus_file.is_file():
        raise Skip(
            f"corpus not found at {corpus_file}",
            "it ships inside the vendored cognee clone: "
            "git clone https://github.com/topoteretes/cognee evals/cognee",
        )
    return json.loads(corpus_file.read_text(encoding="utf-8"))


def _build_benchmark(system: str, corpus: Sequence[str], questions: Sequence[Mapping[str, Any]],
                     args: argparse.Namespace) -> Any:
    """Construct the requested system's harness, translating a missing
    prerequisite into a SKIP."""
    qa_pairs = [{"question": q["question"], "answer": q["gold"]} for q in questions]
    try:
        if system == "tesserae":
            from .benchmark_tesserae import QABenchmarkTesserae, TesseraeConfig

            if not args.project:
                raise Skip("--system tesserae needs --project",
                           "pass a scratch project dir, NEVER the repo root: "
                           "--project ~/.blackhole/Tesserae/$(date +%F)/qa-run")
            return QABenchmarkTesserae(
                list(corpus), qa_pairs,
                TesseraeConfig(
                    project_root=str(Path(args.project).expanduser()),
                    backend=args.backend, route=args.route, top_k=args.top_k,
                    no_llm=args.no_llm, print_results=False,
                    results_file=str(args.answers_out) if args.answers_out else "",
                ),
            )
        from .null_model import NullModelConfig, QABenchmarkNullModel

        if system in ("bm25", "hybrid"):
            from .benchmark_retrieval import QABenchmarkRetrieval, RetrievalConfig

            return QABenchmarkRetrieval(
                corpus, questions,
                RetrievalConfig(lane=system, top_k=args.top_k,
                                model=args.model, provider=getattr(args, "provider", None)),
            )
        return QABenchmarkNullModel(
            list(corpus), qa_pairs,
            NullModelConfig(
                model=args.model, print_results=False,
                results_file=str(args.answers_out) if args.answers_out else "",
            ),
        )
    except MissingPrerequisite as exc:
        raise Skip.from_prerequisite(exc) from exc
    except ImportError as exc:
        raise Skip(f"cannot import the {system} harness: {exc}",
                   "uv sync --python 3.11 --all-extras") from exc


# --------------------------------------------------------------------- phases


def stage_only(benchmark: Any, project: Path) -> int:
    """Write the corpus to disk and stop. No compile, no LLM, no network."""
    import asyncio

    asyncio.run(benchmark.load_corpus_to_rag())
    staged = getattr(benchmark, "staged", [])
    directory = getattr(benchmark, "staging_dir", project / "corpus")
    print(f"staged {len(staged)} documents to {directory}")
    print("\nNOTHING HAS BEEN INGESTED. The compile is yours to run, and it is "
          "hours of LLM extraction:\n"
          f"    cd {project} && tesserae init --yes --source ./corpus && tesserae compile\n"
          "Then re-run this with --answer.")
    return 0


def answer_phase(benchmark: Any, questions: Sequence[Mapping[str, Any]],
                 answers_out: Optional[Path],
                 run_meta: Optional[Mapping[str, Any]] = None,
                 ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Ask every question, and return ``(rows, meta)``. This phase costs money.

    The meta is resolved **here**, after ``initialize_rag()`` and before the
    client is torn down, and returned rather than passed in. That ordering is
    load-bearing, not tidiness: ``QABenchmarkTesserae.declared_meta()`` reads
    the project's model pins off ``rag_client``, which the vendored base sets to
    ``None`` in ``__init__``. Asking for the declarations first — as this runner
    used to — produced ``llm_model: None`` on every Tesserae run, wrote that
    into the answers file permanently, and made §4 block with a statement that
    was false about the run. It failed closed, so it published nothing wrong;
    it also meant the strict half of the gate had never once executed.

    ``run_meta`` carries the declarations only the *runner* knows (which
    question set, which corpus) and is merged over the system's own.
    """
    import asyncio

    async def _run() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        benchmark.rag_client = await benchmark.initialize_rag()
        try:
            # After the client exists — see the docstring.
            meta = {**_meta_of(benchmark), **dict(run_meta or {})}
            rows: List[Dict[str, Any]] = []
            for index, question in enumerate(questions, start=1):
                text = question["question"]
                print(f"[{index}/{len(questions)}] {text}", file=sys.stderr)
                try:
                    answer = await benchmark.query_rag(text)
                except Exception as exc:  # recorded, not raised: one bad question
                    answer = f"Error: {exc}"  # must not lose the other 35
                rows.append({"question": text, "answer": answer,
                             "gold": question["gold"], "stratum": question["stratum"]})
            return rows, meta
        finally:
            await benchmark.cleanup_rag()

    rows, meta = asyncio.run(_run())
    if answers_out is not None:
        payload = {"system": benchmark.system_name, "meta": meta, "rows": rows}
        answers_out.parent.mkdir(parents=True, exist_ok=True)
        answers_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        print(f"wrote {answers_out}")
    return rows, meta


def _meta_of(benchmark: Any) -> Dict[str, Any]:
    declared = getattr(benchmark, "declared_meta", None)
    return dict(declared()) if callable(declared) else {}


# ---------------------------------------------------------------------- report


def load_answers_file(path: Path) -> Dict[str, Any]:
    """Read one saved answers file into a scoreable ``{system, meta, rows}``.

    Accepts the vendored ABC's own results shape too — a bare list of
    ``{question, answer, golden_answer}`` — so a competitor run saved by
    ``QABenchmarkRAG.save_results`` can be scored without being reshaped by
    hand. That file records no model pins, so its fairness declarations come out
    empty, and :func:`~evals.qa.scorer.fairness_blockers` will refuse to publish
    a comparison involving it. That is the correct outcome, not a bug: nobody
    wrote down what answered.
    """
    if not path.is_file():
        raise Skip(f"answers file not found at {path}",
                   "produce one with --system <name> --answer, or pass a different path")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"system": path.stem, "meta": {}, "rows": payload}
    return {"system": payload.get("system") or path.stem,
            "meta": payload.get("meta") or {},
            "rows": payload.get("rows") or []}


def _pct(value: float) -> str:
    return f"{100.0 * float(value):.1f}%"


def _rate(value: float, denominator: int) -> str:
    """A percentage, or ``n/a`` when it would be a rate over nothing.

    ``scorer.summarize`` fills every slot with a float, so an empty stratum
    yields ``0.0`` — and printing that is how a report comes to say a system
    "never hallucinates" on the strength of zero unanswerable questions. It read
    as a claim about a competitor's product and was supported by an empty
    denominator. Every rate in this report goes through here, and every rate is
    printed on a row that also carries its denominator.
    """
    return _pct(value) if denominator else "n/a"


def _num(value: float, denominator: int, spec: str = ".3f") -> str:
    """As :func:`_rate`, for the metrics that are not percentages."""
    return format(float(value), spec) if denominator else "n/a"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return out


def _overall_section(reports: Sequence[Mapping[str, Any]]) -> List[str]:
    """§1. Every rate carries its own denominator on the same row.

    ``n unanswerable`` is a column and not a footnote because the hallucination
    rate is meaningless without it, and because the header sentence that used to
    carry the counts is replaced by a warning as soon as two systems disagree —
    which is exactly when a reader most needs the numbers.
    """
    header = ["system", "n answerable", "n unanswerable", "exact match",
              "token F1 (macro)", "token F1 (micro)", "gold coverage",
              "refusals on answerable", "hallucination (unanswerable)", "errors"]
    rows = []
    for report in reports:
        o = report["overall"]
        answerable, unanswerable = int(o["n_answerable"]), int(o["n_unanswerable"])
        rows.append([
            str(report["system"]), str(answerable), str(unanswerable),
            _rate(o["exact_match"], answerable),
            _num(o["f1_macro"], answerable), _num(o["f1_micro"], answerable),
            _num(o["gold_coverage"], answerable),
            _rate(o["refusal_rate"], answerable),
            _rate(o["hallucination_rate"], unanswerable),
            _rate(o["error_rate"], int(o["n"])),
        ])
    lines = _table(header, rows)
    lines += [
        "",
        "**gold coverage** is the share of the gold answer's tokens that appear "
        "anywhere in the prediction. It is the one column here that survives an "
        "answer-shape mismatch — a correct fact buried in a paragraph scores "
        "1.000 on it and near zero on exact match — so it is what tells you "
        "whether two systems differed on the FACT or only on the FORM. It is a "
        "diagnostic and not a ranking: it rises with answer length, and a system "
        "that returns the whole corpus scores a perfect 1.000. §3 ranks on token "
        "F1 (macro), and only when §4 is clear.",
    ]
    return lines


def _strata_section(reports: Sequence[Mapping[str, Any]]) -> List[str]:
    names: List[str] = []
    for report in reports:
        for name in report["strata"]:
            if name not in names:
                names.append(name)
    header = ["stratum", "system", "n answerable", "n unanswerable", "exact match",
              "token F1 (macro)", "gold coverage", "refusal", "hallucination"]
    rows = []
    for name in sorted(names):
        for report in reports:
            summary = report["strata"].get(name)
            if summary is None:
                continue
            answerable = int(summary["n_answerable"])
            unanswerable = int(summary["n_unanswerable"])
            rows.append([
                name, str(report["system"]), str(answerable), str(unanswerable),
                _rate(summary["exact_match"], answerable),
                _num(summary["f1_macro"], answerable),
                _num(summary["gold_coverage"], answerable),
                # On an all-unanswerable stratum the interesting refusal rate is
                # the one over the unanswerable rows — there is no other.
                _rate(summary["refusal_rate"], answerable) if answerable
                else _rate(summary["unanswerable_refusal_rate"], unanswerable),
                _rate(summary["hallucination_rate"], unanswerable),
            ])
    return _table(header, rows)


def _ranking_section(reports: Sequence[Mapping[str, Any]],
                     blockers: Sequence[str]) -> List[str]:
    """§3. Withheld entirely when §4 has anything to say.

    A ranking is the one part of this report that is quotable on its own — it is
    what gets screenshotted — so printing it above a "not publishable" notice
    puts the invalid claim in front of the reader and the retraction behind it.
    """
    if blockers:
        failed = ", ".join(sorted({blocker.split(":", 1)[0] for blocker in blockers}))
        return [
            "**Withheld — see §4.** The systems above cannot be compared, so "
            "ordering them would state a result that this run does not support. "
            "The per-system numbers in §1 stand on their own; the ordering "
            f"between them does not. Failing preconditions: {failed}.",
        ]
    ranking = rank_systems(reports, key="f1_macro")
    rows = [[str(entry["rank"]), entry["system"], f"{entry['score']:.4f}",
             "tied" if entry["tied"] else ""] for entry in ranking]
    lines = _table(["rank", "system", "token F1 (macro)", "tie"], rows)
    if any(entry["tied"] for entry in ranking):
        lines += ["", "Tied systems share a rank. A 24-question set does not "
                      "distinguish scores that round the same at 4 decimals, and "
                      "reporting them as ordered would be a claim the data does "
                      "not support."]
    return lines


def _declared_or(reports: Sequence[Mapping[str, Any]], key: str) -> str:
    """The value every system declared for ``key``, or a note that they did not.

    Never falls back to the runner's own defaults: a report that names a
    question set the answers never declared is a fabricated provenance line.
    """
    values = {str((r.get("meta") or {}).get(key)) for r in reports
              if (r.get("meta") or {}).get(key)}
    if len(values) == 1:
        return values.pop()
    if not values:
        return "undeclared — see §4"
    return "differs per system — see §4"


def _counts_phrase(reports: Sequence[Mapping[str, Any]]) -> str:
    """"N answerable, M unanswerable" — or a warning when the systems disagree.

    Systems that answered different numbers of questions are not comparable at
    all, and that is worth saying at the top rather than leaving a reader to
    diff two rows of §1.
    """
    counts = {(r["overall"]["n_answerable"], r["overall"]["n_unanswerable"])
              for r in reports}
    if len(counts) == 1:
        answerable, unanswerable = counts.pop()
        return f"{answerable} answerable, {unanswerable} unanswerable probes"
    return ("**the systems answered different numbers of questions** — see §1; "
            "they are not comparable")


def build_report(reports: Sequence[Mapping[str, Any]], *, question_set: str,
                 corpus: str) -> str:
    """The markdown report, in the shape of ``evals/federation/report.md``."""
    lines = [
        "# QA benchmark",
        "",
        f"Question set: `{question_set}` — {_counts_phrase(reports)}. "
        f"Corpus: `{corpus}`. "
        f"Scorer: `evals/qa/scorer.py` (exact match + token F1 via the shared "
        f"`evals.metrics.prf1`). Regenerate: "
        f"`python -m evals.qa.run_qa_eval --score <answers.json>...`.",
        "",
        "**Latency is not measured and must not be inferred from this run.** "
        "See `evals/qa/README.md`.",
        "",
        "**Exact match and token F1 compare answer shape as much as answer "
        "correctness**, because both are computed over the whole predicted "
        "string. They mean something across systems only when those systems "
        "were asked for the same shape — which §4 checks and blocks on. A "
        "prose system and a short-span system are not made comparable by these "
        "metrics under any caveat; that comparison needs a judge that reads the "
        "answer, which this harness does not have.",
        "",
        "## 1. Overall",
        "",
    ]
    blockers = fairness_blockers(reports)
    lines += _overall_section(reports)
    lines += ["", "## 2. Per stratum", ""]
    lines += _strata_section(reports)
    if len(reports) > 1:
        lines += ["", "## 3. Ranking", ""]
        lines += _ranking_section(reports, blockers)
    lines += ["", "## 4. Fairness preconditions", ""]
    if not blockers:
        lines += ["Every declaration matches across the systems above." if len(reports) > 1
                  else "Single system — nothing to compare, so nothing to block."]
    else:
        lines += ["**These numbers are NOT publishable as a comparison.** "
                  "Each line below invalidates it:", ""]
        lines += [f"- {blocker}" for blocker in blockers]
    lines += ["", "### Declared", ""]
    keys: List[str] = []
    for report in reports:
        for key in (report.get("meta") or {}):
            if key not in keys:
                keys.append(key)
    if keys:
        lines += _table(["system", *keys],
                        [[str(r["system"])] + [str((r.get("meta") or {}).get(k, "—"))
                                               for k in keys] for r in reports])
    else:
        lines += ["Nothing declared. See `evals/qa/README.md` — an undeclared run "
                  "cannot be published."]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------ main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--system", choices=SYSTEMS,
                        help="which system to drive (omit with --score)")
    parser.add_argument("--score", nargs="+", type=Path, default=None,
                        help="score saved answers file(s) — no LLM, no network")
    parser.add_argument("--stage-only", action="store_true",
                        help="write the corpus to the project's staging dir and stop")
    parser.add_argument("--answer", action="store_true",
                        help="ask the questions (costs LLM quota)")
    parser.add_argument("--i-know-this-costs-money", action="store_true",
                        help="required by --answer")
    parser.add_argument("--project", default=None,
                        help="Tesserae project root — a scratch dir, NEVER the repo root")
    parser.add_argument("--questions", type=Path, default=QA_PAIRS_JSON)
    parser.add_argument("--corpus", type=Path, default=CORPUS_JSON)
    parser.add_argument("--unanswerable", type=Path, default=UNANSWERABLE_JSON)
    parser.add_argument("--no-unanswerable", action="store_true",
                        help="drop the refusal probes (the report will say so)")
    parser.add_argument("--answers-out", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT,
                        help=f"where to write the report (default: {DEFAULT_REPORT}, "
                             f"outside the repo — a generated comparison is scratch "
                             f"until a human decides to publish it)")
    parser.add_argument("--backend", default="wiki", choices=("wiki", "auto", "raganything"))
    parser.add_argument("--route", default="auto", choices=("auto", "lookup", "graph"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--no-llm", action="store_true",
                        help="Tesserae retrieval only — measures excerpts, not answers")
    parser.add_argument("--model", default=None, help="null-model LLM model id")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # Guard 1 — CI, before anything reads a file.
    if os.environ.get("CI"):
        print("SKIP: CI is set — the QA benchmark never runs in CI\n"
              "      it writes a corpus directory and its answer phase spends LLM "
              "quota; run it by hand instead")
        return 0

    try:
        if args.score:
            payloads = [load_answers_file(path) for path in args.score]
            reports = [score_system(p["rows"], system=p["system"], meta=p["meta"])
                       for p in payloads]
            # Label the report from what the ANSWERS declare, not from this
            # invocation's defaults — the answers may have come from anywhere,
            # and printing --questions here would attribute them to a set they
            # never saw.
            report = build_report(reports,
                                  question_set=_declared_or(reports, "question_set"),
                                  corpus=_declared_or(reports, "corpus"))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(report, encoding="utf-8")
            print(report)
            print(f"wrote {args.out}")
            return 0

        if not args.system:
            print("SKIP: nothing to do — pass --score <answers.json> or --system <name>\n"
                  "      python -m evals.qa.run_qa_eval --help")
            return 0

        # Guard 3 — explicit consent to spend, BEFORE any input is read. It has
        # to fire ahead of the prerequisite SKIPs or an operator who forgot the
        # flag learns about a missing corpus instead of about the flag.
        if args.answer and not args.i_know_this_costs_money:
            print("SKIP: --answer spends LLM quota on every question\n"
                  "      re-run with --i-know-this-costs-money if that is what you want")
            return 0

        questions = load_questions(
            args.questions, None if args.no_unanswerable else args.unanswerable
        )
        corpus = load_corpus(args.corpus)
        benchmark = _build_benchmark(args.system, corpus, questions, args)

        if args.stage_only:
            if args.system != "tesserae":
                raise Skip(f"--stage-only is only meaningful for tesserae, not {args.system}",
                           "the null model discards documents by construction; "
                           "run it with --answer")
            return stage_only(benchmark, Path(args.project).expanduser())

        if not args.answer:
            print("SKIP: neither --stage-only nor --answer given\n"
                  "      --stage-only writes the corpus; --answer spends LLM quota")
            return 0

        # The question set and corpus this run actually used become part of the
        # system's fairness declaration, so a later --score across two answer
        # files can catch "they did not answer the same questions".
        question_set = str(args.questions) + ("" if args.no_unanswerable
                                              else f" + {args.unanswerable}")
        # The system's OWN declarations are resolved inside answer_phase, after
        # its client exists — see that function's docstring. Only the two things
        # the runner alone knows are passed in.
        rows, meta = answer_phase(
            benchmark, questions, args.answers_out,
            {"question_set": question_set, "corpus": str(args.corpus)},
        )
        report = score_system(rows, system=benchmark.system_name, meta=meta)
        text = build_report([report], question_set=question_set,
                            corpus=str(args.corpus))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(text)
        print(f"wrote {args.out}")
        return 0
    except Skip as skip:
        return skip.emit()
    except MissingPrerequisite as exc:
        return Skip.from_prerequisite(exc).emit()


if __name__ == "__main__":
    raise SystemExit(main())
