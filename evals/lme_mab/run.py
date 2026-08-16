"""Runner for LongMemEval-MAB: estimate, ingest, answer, score, report.

    # what would it cost? prints the banner and stops — reads nothing
    uv run python -m evals.lme_mab.run --parquet <Accurate_Retrieval.parquet>

    # ONE group first: 60 questions, a fifth of the bill. The intended first run.
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 \
        --work ~/.blackhole/Tesserae/lme-mab/work --i-know-this-costs-money --yes

    # stage the corpus and stop — no compile, no LLM, no network
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 --stage-only

    # re-score a saved answers file. Offline.
    uv run python -m evals.lme_mab.run --score answers.json

Four things stand between an invocation and a bill, in the order they fire:

1. **CI.** ``CI`` set in the environment prints SKIP and exits 0, whatever was
   asked for. This must never run in CI: it compiles a 400k-token haystack.
2. **The cost banner.** Printed before anything is read, from the figures
   ``README.md`` measured off the real parquet.
3. **Explicit consent to spend.** Anything that reaches an LLM refuses without
   ``--i-know-this-costs-money``, and then asks for a typed confirmation unless
   ``--yes``. There is no default that spends quota.
4. **Prerequisites**, on ``evals/qa/run_qa_eval.py``'s model — a missing
   parquet, a missing ``OPENAI_API_KEY``, a work directory inside the repo each
   print ``SKIP: <what>`` plus the command that fixes it, and exit 0. A
   benchmark that fails loudly on a missing optional input gets wired into CI by
   someone making the build green, and then it runs.

The report is written in the shape of ``evals/qa/``'s: provenance paragraph,
then numbered sections of markdown tables, no timestamps — re-running over the
same answers must produce the same bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..qa.run_qa_eval import Skip, _num, _rate, _table, load_answers_file
from ..qa.scorer import score_system
from .adapter import (
    PROTOCOL_BACKBONE,
    PROTOCOL_CONTROLS,
    PROTOCOL_EMBEDDING_MODEL,
    PROTOCOL_JUDGE,
    PROTOCOL_K,
    PROTOCOL_VALUES,
    IngestResult,
    MabMemory,
    RefusedToCompileInRepo,
    guard_work_dir,
    protocol_blockers,
)

#: Where a report goes when ``--out`` is not given: the project's scratch root,
#: **outside the repository**, and with no date in the path. A default output
#: path that moves at midnight is a wall clock in a harness held to byte-identical
#: re-runs — the same reasoning as ``evals/qa/run_qa_eval.DEFAULT_REPORT``.
DEFAULT_REPORT = Path.home() / ".blackhole" / "Tesserae" / "lme-mab" / "report.md"
DEFAULT_WORK = Path.home() / ".blackhole" / "Tesserae" / "lme-mab" / "work"
DEFAULT_PARQUET = (
    Path.home() / ".blackhole" / "Tesserae" / "lme-mab" / "Accurate_Retrieval.parquet"
)

#: The answer shape this runner asks for. ``short-span`` and not Tesserae's
#: house ``prose-cited``: exact match and token F1 are computed over the whole
#: answer string, so cited prose scores near zero against a one-word gold answer
#: however right it is (measured — see ``evals/qa/scorer.py``). The published
#: table is EM/F1 on short answers, so a prose run would not be comparable with
#: it under any caveat.
ANSWER_SHAPE = "short-span"

_SYSTEM_PROMPT = (
    "You answer questions about a long chat history using only the evidence "
    "given. Reply with the shortest exact answer — a name, a date, a number, "
    "yes/no — and nothing else. If the evidence does not contain the answer, "
    "reply exactly: I don't know."
)


# --------------------------------------------------------------------------
# Cost, from the figures README.md measured off the real parquet
# --------------------------------------------------------------------------

#: Every number below is measured, not modelled. See ``README.md``:
#: 8,140,368 characters over 5 groups, 300 questions, ~509 extraction calls at
#: ~4k tokens of dialogue each, and a codex column dominated by a per-call fixed
#: overhead of 15,090 tokens measured on a trivial ``codex exec``.
MEASURED = {
    "groups": 5,
    "chars": 8_140_368,
    "questions": 300,
    "extraction_calls": 509,
    "codex_ingest_tokens": 10_200_000,
    "codex_query_tokens": 5_400_000,
    "api_ingest_tokens": 2_500_000,
    "api_query_tokens": 900_000,
    "codex_fixed_overhead_per_call": 15_090,
}


@dataclass(frozen=True)
class CostEstimate:
    """A budget for ``n_groups``, scaled linearly off the measured totals.

    Linear scaling is honest here and would not be for an arbitrary subset: the
    five groups are within 4% of each other in size (1,588,305 to 1,715,268
    characters, measured), and every one carries exactly 60 questions.
    """

    n_groups: int
    chars: int
    tokens: int
    questions: int
    extraction_calls: int
    codex_tokens: int
    api_tokens: int


def estimate_cost(n_groups: int) -> CostEstimate:
    share = n_groups / MEASURED["groups"]
    chars = round(MEASURED["chars"] * share)
    codex = round((MEASURED["codex_ingest_tokens"] + MEASURED["codex_query_tokens"]) * share)
    api = round((MEASURED["api_ingest_tokens"] + MEASURED["api_query_tokens"]) * share)
    return CostEstimate(
        n_groups=n_groups,
        chars=chars,
        tokens=chars // 4,
        questions=round(MEASURED["questions"] * share),
        extraction_calls=round(MEASURED["extraction_calls"] * share),
        codex_tokens=codex,
        api_tokens=api,
    )


def cost_banner(estimate: CostEstimate) -> str:
    """The estimate, as the operator sees it before anything is read."""
    ingest_share = MEASURED["codex_ingest_tokens"] / (
        MEASURED["codex_ingest_tokens"] + MEASURED["codex_query_tokens"]
    )
    codex_ingest = round(estimate.codex_tokens * ingest_share)
    codex_query = estimate.codex_tokens - codex_ingest
    api_ingest_share = MEASURED["api_ingest_tokens"] / (
        MEASURED["api_ingest_tokens"] + MEASURED["api_query_tokens"]
    )
    api_ingest = round(estimate.api_tokens * api_ingest_share)
    api_query = estimate.api_tokens - api_ingest
    overhead = MEASURED["codex_fixed_overhead_per_call"] * (
        estimate.extraction_calls + estimate.questions
    )
    overhead_pct = 100.0 * overhead / estimate.codex_tokens if estimate.codex_tokens else 0.0
    return "\n".join([
        "─" * 72,
        f"LongMemEval-MAB — ESTIMATED COST for {estimate.n_groups} of "
        f"{MEASURED['groups']} group(s), before anything runs",
        "─" * 72,
        f"  dialogue        {estimate.chars:>12,} chars  ≈ {estimate.tokens:>10,} tokens",
        f"  questions       {estimate.questions:>12,}",
        f"  ingest          {estimate.extraction_calls:>12,} extraction calls "
        f"(~4k tokens of dialogue each)",
        f"                  via codex ≈ {codex_ingest:>11,} tok   |   "
        f"via OpenAI API ≈ {api_ingest:>10,} tok",
        f"  queries         via codex ≈ {codex_query:>11,} tok   |   "
        f"via OpenAI API ≈ {api_query:>10,} tok",
        f"  TOTAL           via codex ≈ {estimate.codex_tokens:>11,} tok   |   "
        f"via OpenAI API ≈ {estimate.api_tokens:>10,} tok",
        "",
        f"  ~{overhead_pct:.0f}% of the codex column is fixed per-call harness "
        f"overhead ({MEASURED['codex_fixed_overhead_per_call']:,} tok/call,",
        "  measured). It changes cost and latency, not answers — this protocol "
        "scores accuracy",
        "  and F1, not latency, so it is a quota decision and not a validity one.",
        "",
        "  Figures are README.md's, measured off the real parquet and scaled by "
        "group count.",
        "─" * 72,
    ])


# --------------------------------------------------------------------------
# Prerequisites
# --------------------------------------------------------------------------


def require_parquet(path: Path) -> Path:
    if not path.is_file():
        raise Skip(
            f"MemoryAgentBench parquet not found at {path}",
            "download the Accurate_Retrieval split of ai-hyz/MemoryAgentBench to a "
            "scratch dir and pass --parquet <file>; it is a ~20MB file and is "
            "deliberately not in this repo",
        )
    return path


def require_openai_key() -> None:
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        raise Skip(
            f"the protocol fixes {PROTOCOL_EMBEDDING_MODEL} for retrieval and that "
            f"needs OPENAI_API_KEY",
            "export OPENAI_API_KEY=... — or accept that Tesserae's default "
            "model2vec embedder varies the embedder AND the architecture at once, "
            "which makes the run internal-only (see evals/lme_mab/README.md)",
        )


def require_work_dir(work: Path) -> Path:
    try:
        return guard_work_dir(work)
    except RefusedToCompileInRepo as exc:
        raise Skip(
            str(exc),
            "pass --work ~/.blackhole/Tesserae/lme-mab/work",
        ) from exc


def load_groups_or_skip(parquet: Path) -> List[Any]:
    from .dataset import load_groups

    try:
        return load_groups(parquet)
    except ImportError as exc:  # pyarrow is an optional dep
        raise Skip(f"cannot read the parquet: {exc}",
                   "uv sync --python 3.11 --all-extras") from exc
    except ValueError as exc:
        raise Skip(str(exc), "pass the Accurate_Retrieval split, not another one") from exc


def select_groups(groups: Sequence[Any], indices: Optional[Sequence[int]],
                  limit: Optional[int]) -> List[Any]:
    """``--groups`` wins over ``--limit-groups``; both default to all five."""
    chosen = list(groups)
    if indices:
        by_index = {g.index: g for g in groups}
        missing = [i for i in indices if i not in by_index]
        if missing:
            raise Skip(
                f"no such group(s): {', '.join(str(i) for i in missing)} "
                f"(the file holds {len(groups)})",
                "drop --groups to run them all, or pass an index that exists",
            )
        chosen = [by_index[i] for i in indices]
    elif limit is not None:
        chosen = chosen[:limit]
    if not chosen:
        raise Skip("no groups selected", "drop --groups / --limit-groups")
    return chosen


# --------------------------------------------------------------------------
# Answering
# --------------------------------------------------------------------------


def build_backbone(model: str) -> Callable[[str, Sequence[str]], str]:
    """An ``(question, evidence) -> short answer`` callable on ``model``.

    Returned as a closure rather than a class so the runner's tests can pass any
    callable and never construct an LLM client.
    """
    from tesserae.llm_json import build_default_json_client

    client = build_default_json_client(model=model)
    if client is None:
        raise Skip(
            f"no LLM client available for the {model} backbone",
            "configure a provider (see README) — the protocol fixes "
            f"{PROTOCOL_BACKBONE} for construction and answering alike",
        )

    def answer(question: str, evidence: Sequence[str]) -> str:
        numbered = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(evidence, start=1))
        payload = client.complete_json(
            system=_SYSTEM_PROMPT,
            user=f"Evidence:\n{numbered}\n\nQuestion: {question}",
            schema_name="lme_mab_answer",
        )
        if isinstance(payload, dict) and payload.get("answer") is not None:
            return str(payload["answer"])
        # No answer key is not a refusal and not an answer. "" scores as a
        # refusal, which is the honest reading of "the system returned nothing".
        return ""

    return answer


def answer_group(
    memory: MabMemory,
    group: Any,
    answer_fn: Callable[[str, Sequence[str]], str],
    *,
    k: int = PROTOCOL_K,
    progress: bool = True,
) -> List[Dict[str, Any]]:
    """Ask every question in ``group`` and return scoreable rows."""
    rows: List[Dict[str, Any]] = []
    types = list(group.question_types)
    for i, question in enumerate(group.questions):
        if progress:
            print(f"[group {group.index}] [{i + 1}/{len(group.questions)}] {question}",
                  file=sys.stderr)
        try:
            evidence = memory.query(question, k=k)
            answer = answer_fn(question, evidence)
        except Exception as exc:  # recorded, not raised: one bad question
            evidence, answer = [], f"Error: {exc}"  # must not lose the other 59
        gold = list(group.answers[i]) if i < len(group.answers) else []
        rows.append({
            "question": question,
            "answer": answer,
            "gold": gold,
            "stratum": types[i] if i < len(types) else "unspecified",
            "group": group.index,
            "n_evidence": len(evidence),
        })
    return rows


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _corpus_section(ingests: Sequence[IngestResult]) -> List[str]:
    if not ingests:
        return ["No ingest in this run — the answers were scored from a saved file."]
    rows = [[
        str(r.group_index), f"{r.documents:,}", f"{r.turns:,}", f"{r.chars:,}",
        f"{r.dated_sessions:,}", r.session_source,
        {True: "yes", False: "**NO**", None: "n/a"}[r.views_agree],
        "yes" if r.compiled else "**staged only**",
    ] for r in ingests]
    lines = _table(
        ["group", "documents (sessions)", "turns", "chars", "dated sessions",
         "split from", "views agree", "compiled"],
        rows,
    )
    lines += [
        "",
        "One document per **session**, not per turn window — see "
        "`evals/lme_mab/adapter.py`. The unit is part of the protocol: K=10 "
        "evidence items over ~112 sessions and K=10 over ~1,290 turns are "
        "different experiments.",
        "",
        "**split from** names which view of the haystack was used. `context` "
        "carries the `Chat Time:` header and is the only one that does; "
        "`haystack_sessions` is dateless, and a dateless corpus cannot answer "
        "the `temporal-reasoning` stratum. **views agree** is whether the two "
        "views held the same number of sessions; a `NO` means one of them is "
        "not the whole haystack.",
    ]
    return lines


def _scores_section(report: Mapping[str, Any]) -> List[str]:
    o = report["overall"]
    answerable, unanswerable = int(o["n_answerable"]), int(o["n_unanswerable"])
    lines = _table(
        ["n answerable", "exact match", "token F1 (macro)", "token F1 (micro)",
         "gold coverage", "refusals"],
        [[
            str(answerable),
            _rate(o["exact_match"], answerable),
            _num(o["f1_macro"], answerable),
            _num(o["f1_micro"], answerable),
            _num(o["gold_coverage"], answerable),
            _rate(o["refusal_rate"], answerable),
        ]],
    )
    if unanswerable:
        lines += ["", f"{unanswerable} question(s) carried no gold answer and are "
                      f"excluded from every rate above."]
    lines += ["", "### Per question type", ""]
    strata_rows = []
    for name, summary in sorted(report["strata"].items()):
        n = int(summary["n_answerable"])
        strata_rows.append([
            name, str(n),
            _rate(summary["exact_match"], n),
            _num(summary["f1_macro"], n),
            _num(summary["gold_coverage"], n),
            _rate(summary["refusal_rate"], n),
        ])
    lines += _table(
        ["question type", "n", "exact match", "token F1 (macro)", "gold coverage",
         "refusals"],
        strata_rows,
    )
    lines += [
        "",
        "The strata are LongMemEval's own `question_types`. An aggregate that "
        "hides which KIND of question failed says very little about a memory "
        "system: `temporal-reasoning` and `single-session-user` fail for "
        "different reasons and only one of them is about retrieval.",
    ]
    return lines


def _comparable_section(report: Mapping[str, Any], blockers: Sequence[str]) -> List[str]:
    """The quotable table — withheld entirely when any control is unmet.

    Printed above the reasons rather than below them, on the same logic as
    ``evals/qa/run_qa_eval._ranking_section``: this is the part of the report
    that gets screenshotted, so an invalid number must not appear at all rather
    than appear with a retraction underneath.
    """
    if blockers:
        failed = ", ".join(sorted({b.split(":", 1)[0] for b in blockers}))
        return [
            "**Withheld — see the next section.** These numbers were produced "
            "under a protocol that does not match the published one, so printing "
            "them in the published table's shape would state a comparison this "
            "run does not support. The numbers above stand as an INTERNAL "
            f"measurement and nothing more. Unmet controls: {failed}.",
            "",
            "The published baselines (BM25, Dense, RAPTOR, MemTree, A-MEM, Mem0, "
            "HippoRAG, SegTreeMem) are **not** reproduced here — this repo holds "
            "none of their numbers, and quoting figures it never measured is the "
            "thing #178 retracted.",
        ]
    o = report["overall"]
    n = int(o["n_answerable"])
    return _table(
        ["method", "backbone", "embedder", "K", "token F1 (macro)", "exact match", "n"],
        [["Tesserae", PROTOCOL_BACKBONE, PROTOCOL_EMBEDDING_MODEL, str(PROTOCOL_K),
          _num(o["f1_macro"], n), _rate(o["exact_match"], n), str(n)]],
    ) + [
        "",
        "Every control in the next section is met, so this row is in the same "
        "units as the published table (arXiv:2606.04555 §5.2-5.3). The "
        "baselines' own numbers are not reproduced here; read them from the "
        "paper.",
    ]


def _controls_section(meta: Mapping[str, Any], blockers: Sequence[str]) -> List[str]:
    rows = []
    for key in PROTOCOL_CONTROLS:
        declared = meta.get(key)
        required = PROTOCOL_VALUES[key]
        ok = declared not in (None, "") and str(declared) == required
        rows.append([key, required, str(declared) if declared not in (None, "") else "—",
                     "met" if ok else "**UNMET**"])
    lines = _table(["control", "protocol fixes", "this run declared", "status"], rows)
    if blockers:
        lines += ["", "**This run is NOT comparable with the published baselines.** "
                      "Each line below is sufficient on its own:", ""]
        lines += [f"- {blocker}" for blocker in blockers]
    else:
        lines += ["", "Every control matches the published protocol."]
    return lines


def _shortfall_section(shortfalls: Sequence[Mapping[str, Any]], n_questions: int) -> List[str]:
    if not shortfalls:
        return [f"Every one of the {n_questions} queries returned the full K="
                f"{PROTOCOL_K} evidence items."]
    rows = [[str(s["question"])[:80], str(s["requested"]), str(s["returned"]),
             str(s.get("total_matches", "—"))] for s in shortfalls[:20]]
    lines = _table(["question", "requested", "returned", "candidates"], rows)
    if len(shortfalls) > 20:
        lines += ["", f"...and {len(shortfalls) - 20} more."]
    lines += [
        "",
        f"**{len(shortfalls)} of {n_questions} queries returned fewer than K="
        f"{PROTOCOL_K}.** The evidence list is never padded — a padded list "
        "would make an under-filled budget indistinguishable from a full one, "
        "and the budget is the control the whole comparison rests on. A "
        "shortfall means the graph held fewer matching nodes than the protocol "
        "budget allows, so this run gave itself LESS context than the baselines "
        "had, not more.",
    ]
    return lines


def build_report(
    report: Mapping[str, Any],
    *,
    ingests: Sequence[IngestResult] = (),
    shortfalls: Sequence[Mapping[str, Any]] = (),
    parquet: str = "undeclared",
    groups: str = "undeclared",
) -> str:
    """The markdown report. No timestamps: same answers in, same bytes out."""
    meta = dict(report.get("meta") or {})
    blockers = protocol_blockers(meta)
    n_questions = int(report["overall"]["n"])
    lines = [
        "# LongMemEval-MAB — Tesserae",
        "",
        f"Dataset: `{parquet}` (ai-hyz/MemoryAgentBench, `Accurate_Retrieval`, "
        f"`longmemeval_s*`). Groups: {groups}. Questions: {n_questions}. "
        f"Scorer: `evals/qa/scorer.py` (exact match + token F1 via the shared "
        f"`evals.metrics.prf1`). Regenerate: "
        f"`python -m evals.lme_mab.run --score <answers.json>`.",
        "",
        "**Latency is not measured and must not be inferred from this run.** The "
        "protocol scored here is accuracy and token F1; LongMemEval-V2's LAFS "
        "metric scores latency directly and this harness would not be valid for "
        "it.",
        "",
        "## 1. Corpus",
        "",
    ]
    lines += _corpus_section(ingests)
    lines += ["", "## 2. Scores", ""]
    lines += _scores_section(report)
    lines += ["", "## 3. Comparable result", ""]
    lines += _comparable_section(report, blockers)
    lines += ["", "## 4. Protocol controls", ""]
    lines += _controls_section(meta, blockers)
    lines += ["", "## 5. Retrieval shortfalls", ""]
    lines += _shortfall_section(shortfalls, n_questions)
    lines += ["", "### Declared", ""]
    # ``shortfalls`` rides along in a saved answers file so a --score re-run can
    # reproduce §5, but it is a list of records and not a declaration; dumping
    # its repr into this table buries the four values a reader is here for.
    keys = sorted(k for k in meta if k != "shortfalls")
    if keys:
        lines += _table(["key", "value"], [[k, str(meta[k])] for k in keys])
    else:
        lines += ["Nothing declared — an undeclared run cannot be published."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET,
                        help=f"MemoryAgentBench Accurate_Retrieval parquet "
                             f"(default: {DEFAULT_PARQUET})")
    parser.add_argument("--groups", type=int, nargs="+", default=None,
                        help="group indices to run, e.g. --groups 0 (60 questions, "
                             "a fifth of the bill — the intended first run)")
    parser.add_argument("--limit-groups", type=int, default=None,
                        help="run only the first N groups")
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK,
                        help=f"scratch project root, NEVER inside this repo "
                             f"(default: {DEFAULT_WORK})")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT,
                        help=f"where to write the report (default: {DEFAULT_REPORT}, "
                             f"outside the repo — a generated number is scratch "
                             f"until a human decides to publish it)")
    parser.add_argument("--answers-out", type=Path, default=None)
    parser.add_argument("--score", nargs="+", type=Path, default=None,
                        help="score a saved answers file — no LLM, no network")
    parser.add_argument("--stage-only", action="store_true",
                        help="write the session documents and stop: no compile, "
                             "no LLM, no network")
    parser.add_argument("--i-know-this-costs-money", action="store_true",
                        help="required for anything that reaches an LLM")
    parser.add_argument("--yes", action="store_true",
                        help="skip the typed confirmation after the cost banner")
    parser.add_argument("--backbone", default=PROTOCOL_BACKBONE,
                        help=f"answering model (protocol fixes {PROTOCOL_BACKBONE})")
    parser.add_argument("--judge", default="",
                        help=f"judge model (protocol fixes {PROTOCOL_JUDGE}; empty "
                             f"means no judge ran, which blocks the comparison)")
    parser.add_argument("--k", type=int, default=PROTOCOL_K,
                        help=f"evidence budget. NOT a tuning knob — the protocol "
                             f"fixes K={PROTOCOL_K} and any other value blocks the "
                             f"comparison")
    parser.add_argument("--embedding-prefer", default="openai",
                        help="embedding backend preference passed to "
                             "active_embedding_backend")
    return parser


def _confirm(estimate: CostEstimate, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("SKIP: not a terminal, so the cost above cannot be confirmed\n"
              "      re-run with --yes if you have read the estimate")
        return False
    reply = input(f"Proceed and spend roughly the above on {estimate.n_groups} "
                  f"group(s)? type 'yes': ").strip().lower()
    if reply != "yes":
        print("SKIP: not confirmed — nothing was ingested")
        return False
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # Guard 1 — CI, before anything reads a file.
    if os.environ.get("CI"):
        print("SKIP: CI is set — LongMemEval-MAB never runs in CI\n"
              "      it compiles a 400k-token haystack per group and spends LLM "
              "quota on 60 questions each; run it by hand instead")
        return 0

    try:
        if args.score:
            payloads = [load_answers_file(path) for path in args.score]
            rows = [row for p in payloads for row in p["rows"]]
            meta: Dict[str, Any] = {}
            for p in payloads:
                meta.update(p["meta"])
            report = score_system(rows, system="Tesserae", meta=meta)
            text = build_report(
                report,
                shortfalls=meta.get("shortfalls") or (),
                parquet=str(meta.get("dataset") or "undeclared"),
                groups=str(meta.get("groups") or "undeclared"),
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(text)
            print(f"wrote {args.out}")
            return 0

        # Guard 2 — the estimate, before any input is read. Group selection is
        # not known yet, so the banner is scaled off the REQUEST, which is what
        # the operator is being asked to approve.
        requested = (len(args.groups) if args.groups
                     else args.limit_groups or MEASURED["groups"])
        estimate = estimate_cost(max(1, min(int(requested), MEASURED["groups"])))
        print(cost_banner(estimate))

        # Guard 3 — explicit consent to spend, BEFORE the prerequisites. It has
        # to fire ahead of the SKIPs or an operator who forgot the flag learns
        # about a missing parquet instead of about the flag.
        if not args.stage_only and not args.i_know_this_costs_money:
            print("SKIP: this run compiles a haystack and answers every question — "
                  "both spend LLM quota\n"
                  "      re-run with --i-know-this-costs-money, or --stage-only to "
                  "write the documents and stop")
            return 0

        # Guard 4 — prerequisites.
        parquet = require_parquet(args.parquet)
        work = require_work_dir(args.work)
        if not args.stage_only:
            require_openai_key()
        groups = select_groups(load_groups_or_skip(parquet), args.groups, args.limit_groups)

        if not args.stage_only and not _confirm(estimate, args.yes):
            return 0

        memory = MabMemory(embedding_prefer=args.embedding_prefer)
        ingests: List[IngestResult] = []
        rows: List[Dict[str, Any]] = []
        answer_fn = None if args.stage_only else build_backbone(args.backbone)

        for group in groups:
            ingests.append(memory.ingest(group, work=work,
                                         compile_project=not args.stage_only))
            print(f"group {group.index}: staged {ingests[-1].documents} sessions "
                  f"to {ingests[-1].corpus_dir}", file=sys.stderr)
            if args.stage_only:
                continue
            assert answer_fn is not None
            rows += answer_group(memory, group, answer_fn, k=args.k)

        if args.stage_only:
            print(f"\nNOTHING HAS BEEN COMPILED. {sum(i.documents for i in ingests)} "
                  f"session documents are in {work / 'corpus'}.\n"
                  f"Re-run without --stage-only (and with "
                  f"--i-know-this-costs-money) to compile and answer.")
            return 0

        meta = {
            "answer_shape": ANSWER_SHAPE,
            "llm_model": args.backbone,
            "embedding_model": getattr(memory.embedding_backend(), "name", None),
            "embedding_dim": getattr(memory.embedding_backend(), "dim", None),
            "judge": args.judge,
            "evidence_budget": args.k,
            "dataset": str(parquet),
            "groups": ",".join(str(g.index) for g in groups),
            "protocol": "arXiv:2606.04555 §5.2-5.3",
        }
        report = score_system(rows, system="Tesserae", meta=meta)
        text = build_report(report, ingests=ingests, shortfalls=memory.shortfalls,
                            parquet=str(parquet), groups=meta["groups"])
        if args.answers_out:
            args.answers_out.parent.mkdir(parents=True, exist_ok=True)
            args.answers_out.write_text(
                json.dumps({"system": "Tesserae",
                            "meta": {**meta, "shortfalls": memory.shortfalls},
                            "rows": rows}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            print(f"wrote {args.answers_out}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(text)
        print(f"wrote {args.out}")
        return 0
    except Skip as skip:
        return skip.emit()


if __name__ == "__main__":
    raise SystemExit(main())
