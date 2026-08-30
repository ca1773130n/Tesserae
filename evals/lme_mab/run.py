"""Runner for LongMemEval-MAB: estimate, ingest, answer, score, report.

    # what would it cost? prints the banner and stops — reads nothing
    uv run python -m evals.lme_mab.run --parquet <Accurate_Retrieval.parquet>

    # ONE group first: 60 questions, a fifth of the bill. The intended first run.
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 \
        --work ~/.blackhole/Tesserae/lme-mab/work --i-know-this-costs-money --yes

    # stage the corpus and stop — no compile, no LLM, no network
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 --stage-only

    # the two baselines: recall@10 and MRR of the gold session, and NOTHING
    # else. No banner, no consent, no key, no LLM, no money — and no network
    # either: the dense arm loads the local model with the Hugging Face hub
    # switched off, and refuses rather than downloading when it is not cached.
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 \
        --arms bm25,dense --retrieval-only

    # re-score a saved answers file. Offline.
    uv run python -m evals.lme_mab.run --score answers.json

``--arms`` chooses which memory systems are measured, and ONE predicate — is
``tesserae`` among them — decides whether this run can spend anything at all.
The baselines are arithmetic over the same bytes the Tesserae arm stages
(``Session.render()``), so a run without it must not be asked to approve a bill
it will never incur, and must not SKIP for a key it will never use. Every guard
that protects a BILL is therefore conditioned on that predicate; the CI guard
and the input prerequisites are conditioned on nothing, because a missing
parquet stops every arm and CI stops all of them on purpose.

Four things stand between an invocation and a bill, in the order they fire:

1. **CI.** ``CI`` set in the environment prints SKIP and exits 0, whatever was
   asked for — including the free arms. This must never run in CI: it compiles
   a 400k-token haystack, and a benchmark that runs in CI for *one* set of
   flags is one edit away from running for all of them.
2. **The cost banner.** Printed before anything is read, from the figures
   ``README.md`` measured off the real parquet.
3. **Explicit consent to spend.** Anything that reaches an LLM refuses without
   ``--i-know-this-costs-money``, and then asks for a typed confirmation unless
   ``--yes``. There is no default that spends quota.
4. **Prerequisites**, on ``evals/qa/run_qa_eval.py``'s model — a missing
   parquet, a work directory inside the repo, or ``--embedding-prefer openai``
   without a key each print ``SKIP: <what>`` plus the command that fixes it,
   and exit 0. A
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..qa.run_qa_eval import Skip, _num, _rate, _table, load_answers_file
from ..qa.scorer import score_system
from .adapter import (
    EVIDENCE_SOURCE_CHARS,
    PROTOCOL_BACKBONE,
    PROTOCOL_CONTROLS,
    PROTOCOL_EMBEDDING_MODEL,
    PROTOCOL_EMBEDDING_PREFER,
    PROTOCOL_JUDGE,
    PROTOCOL_K,
    PROTOCOL_VALUES,
    IngestResult,
    MabMemory,
    RefusedToCompileInRepo,
    guard_work_dir,
    protocol_blockers,
    split_sessions,
)
from .baselines import LOCAL_EMBEDDING_PREFER, DenseArm, LexicalArm
from .retrieval import (
    NOT_COMPARABLE,
    align_gold,
    embedder_refusal,
    require_k,
    score_retrieval,
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

#: The memory systems ``--arms`` accepts, in the order §6 prints them. Exactly
#: one of them spends: ``tesserae`` compiles a haystack and answers with an
#: LLM, while ``bm25`` and ``dense`` are arithmetic over the staged bytes. That
#: split is the whole reason ``--arms`` exists, and it is read once, in
#: :func:`main`, as ``spends``.
ARMS = ("tesserae", "bm25", "dense")

#: The two arms that need nothing but the parquet. ``adapter.MabMemory`` is not
#: in here: it is constructed once per RUN and ingests per group, where these
#: are constructed per group from ``split_sessions`` and index in memory.
_BASELINE_ARMS = {"bm25": LexicalArm, "dense": DenseArm}

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


def parse_arms(value: str) -> List[str]:
    """``--arms`` as a de-duplicated list in :data:`ARMS` order.

    Canonical order rather than the order typed, because the report must be
    byte-identical for the same run and ``--arms dense,bm25`` is the same run as
    ``--arms bm25,dense``. An unrecognised name refuses rather than being
    ignored: silently dropping ``--arms bm52`` would print a one-row table that
    looks like a two-arm comparison.
    """
    names = {name.strip().lower() for name in value.split(",") if name.strip()}
    unknown = sorted(name for name in names if name not in ARMS)
    if unknown:
        raise Skip(
            f"no such arm(s): {', '.join(unknown)}",
            f"--arms takes a comma list of {', '.join(ARMS)}",
        )
    if not names:
        raise Skip("--arms is empty, so there is nothing to measure",
                   f"--arms {','.join(ARMS)}")
    return [name for name in ARMS if name in names]


def require_parquet(path: Path) -> Path:
    if not path.is_file():
        raise Skip(
            f"MemoryAgentBench parquet not found at {path}",
            "download the Accurate_Retrieval split of ai-hyz/MemoryAgentBench to a "
            "scratch dir and pass --parquet <file>; it is a ~20MB file and is "
            "deliberately not in this repo",
        )
    return path


def require_openai_key(prefer: str) -> None:
    """Demand the key only when ``prefer`` is the backend that bills for one.

    The gate used to fire for every Tesserae run, on the reasoning that the
    published protocol fixes an OpenAI embedder. That stopped being true when
    ``--embedding-prefer`` began defaulting to the local backend so §6 could
    hold one embedder still across arms: the default run now demands a key it
    then never uses, and refuses the three-arm local comparison that is the
    whole point of that section. The old remedy text even offered "or accept
    the local embedder" as an alternative no flag provided.
    """
    if prefer != PROTOCOL_EMBEDDING_PREFER:
        return
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        raise Skip(
            f"--embedding-prefer {PROTOCOL_EMBEDDING_PREFER} asks for "
            f"{PROTOCOL_EMBEDDING_MODEL}, which bills per call and needs "
            f"OPENAI_API_KEY",
            f"export OPENAI_API_KEY=... — or drop the flag and run on "
            f"{LOCAL_EMBEDDING_PREFER}, which is the default, costs nothing, "
            f"and is what §6 compares arms under (it makes the run "
            f"internal-only; see evals/lme_mab/README.md)",
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
    expand_evidence: bool = True,
) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
    """Ask every question in ``group``. Returns ``(answer rows, retrieved)``.

    ONE search per question, through :meth:`MabMemory.query_hits`, and both
    halves come off it: the evidence the backbone read, and the session indices
    §6 scores. Calling ``query`` and then ``search_documents`` would search
    twice, record the shortfall twice, and score a ranking the answer never saw.

    ``retrieved[i]`` is empty when the search itself raised — a question whose
    retrieval failed retrieved nothing, which is what it scores as. The answer
    row records the error and the other 59 questions survive.

    The two calls are caught SEPARATELY, and that separation is the whole point.
    One ``try`` around both would let a 429, a timeout or a content filter on the
    answer call erase a search that had already ranked gold at position 1, and §6
    would score that question ``recall@K = 0.000, RR = 0.000`` while still
    counting it in ``n_scored`` — a total retrieval miss, recorded for a
    retrieval that worked. Only the Tesserae arm answers with an LLM, so the
    deflation would land on exactly the arm the table exists to measure.
    """
    rows: List[Dict[str, Any]] = []
    retrieved: List[List[int]] = []
    types = list(group.question_types)
    for i, question in enumerate(group.questions):
        if progress:
            print(f"[group {group.index}] [{i + 1}/{len(group.questions)}] {question}",
                  file=sys.stderr)
        try:
            hits = memory.query_hits(question, k=k)
        except Exception as exc:  # recorded, not raised: one bad question
            hits, evidence, answer = [], [], f"Error: {exc}"  # keep the other 59
        else:
            # By default NOT ``[hit.text ...]``: that handed the backbone a
            # 234-character node summary of a 14,042-character session it had
            # just ranked on 8,000 of those characters — see
            # ``MabMemory.answer_evidence``. ``expand_evidence=False`` restores
            # it deliberately, as the control arm the difference is measured
            # against, and only when --answer-evidence summary asks for it.
            evidence = memory.answer_evidence(hits, expand=expand_evidence)
            try:
                answer = answer_fn(question, evidence)
            except Exception as exc:  # the backbone failed, the search did not
                answer = f"Error: {exc}"
        retrieved.append(memory.documents_of(hits))
        gold = list(group.answers[i]) if i < len(group.answers) else []
        rows.append({
            "question": question,
            "answer": answer,
            "gold": gold,
            "stratum": types[i] if i < len(types) else "unspecified",
            "group": group.index,
            "n_evidence": len(evidence),
            # Items stopped being a description of the budget the moment they
            # stopped carrying comparable amounts of text. Given that an
            # IDENTICAL generative config has swung 0.043 token F1 between two
            # runs in this repo, what the backbone actually read is the one
            # thing worth persisting per row.
            "evidence_chars": sum(len(text) for text in evidence),
            # Which CONTENT, not just how much of it. A replicate scored months
            # later off answers.json has no other way to tell the two arms
            # apart, and they differ by a factor of ten in prompt size.
            "evidence": "source" if expand_evidence else "summary",
        })
    return rows, retrieved


def retrieve_group(
    arm: Any,
    group: Any,
    *,
    k: int = PROTOCOL_K,
    progress: bool = False,
) -> List[List[int]]:
    """``arm.search_documents`` for every question, in question order.

    Duck-typed on purpose — see ``baselines._Arm``'s docstring. The three arms
    share this one method and nothing else, and a ``Protocol`` carrying it would
    be checked here and nowhere else.
    """
    retrieved: List[List[int]] = []
    for i, question in enumerate(group.questions):
        if progress:
            print(f"[group {group.index}] [{i + 1}/{len(group.questions)}] {question}",
                  file=sys.stderr)
        retrieved.append(arm.search_documents(question, k=k))
    return retrieved


def retrieval_rows(
    group: Any,
    gold: Sequence[Sequence[int]],
    retrieved: Sequence[Sequence[int]],
) -> List[Dict[str, Any]]:
    """One scoreable retrieval row per question — see ``retrieval.score_retrieval``.

    ``gold`` is :class:`evals.lme_mab.retrieval.GoldAlignment`'s, so both
    columns are ``Session.index`` values and neither side ever formats a
    document name.
    """
    types = list(group.question_types)
    return [{
        "question": question,
        "stratum": types[i] if i < len(types) else "unspecified",
        "group": group.index,
        "gold": list(gold[i]) if i < len(gold) else [],
        "retrieved": list(retrieved[i]) if i < len(retrieved) else [],
    } for i, question in enumerate(group.questions)]


def arm_declaration(
    name: str,
    *,
    corpus: str,
    memory: Optional[MabMemory],
    arm: Any,
    n_unmatched: int,
) -> Dict[str, Any]:
    """What an arm's §6 row declares, read off the LIVE object.

    Never a hardcoded string — that is the failure ``evals/qa/benchmark_tesserae.py``
    documents: a declared embedder makes the control check pass while the run
    used something else. ``corpus`` is the runner's because it spans the groups,
    and ``n_unmatched`` rides on every row because gold is aligned once per group
    and all three arms score against that one result.
    """
    if name == "tesserae":
        assert memory is not None
        declared: Dict[str, Any] = {
            "corpus": corpus,
            "retriever": "Tesserae hybrid_search over the compiled graph",
            "embedder": getattr(memory.embedding_backend(), "name", None),
            # Cumulative over every query, so this is read after the last one.
            "n_unmapped_hits": memory.n_unmapped_hits,
        }
    else:
        declared = {**arm.meta, "corpus": corpus}
    declared["n_unmatched"] = n_unmatched
    return declared


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _corpus_section(ingests: Sequence[IngestResult]) -> List[str]:
    if not ingests:
        return ["No ingest in this run — nothing was staged. Either the answers "
                "came from a saved file, or `--arms` did not include `tesserae`: "
                "it is the only arm that stages a corpus, and the baselines index "
                "the same `Session.render()` bytes in memory."]
    rows = [[
        str(r.group_index), f"{r.documents:,}", f"{r.turns:,}", f"{r.chars:,}",
        f"{r.dated_sessions:,}", r.session_source,
        {True: "yes", False: "**NO**", None: "n/a"}[r.views_agree],
        "yes" if r.compiled else ("reused (earlier run)" if r.reused
                                  else "**staged only**"),
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


def _evidence_chars(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Mean / median / max characters of evidence per question, as declarations.

    Empty when no row recorded any, so a ``--score`` re-run of an answers file
    written before this existed declares nothing rather than declaring zero.
    """
    sizes = sorted(int(r.get("evidence_chars") or 0) for r in rows
                   if r.get("evidence_chars") is not None)
    if not sizes:
        return {}
    mid = len(sizes) // 2
    median = sizes[mid] if len(sizes) % 2 else (sizes[mid - 1] + sizes[mid]) // 2
    return {
        "evidence_chars_mean": round(sum(sizes) / len(sizes)),
        "evidence_chars_median": median,
        "evidence_chars_max": sizes[-1],
    }


def _evidence_budget_note(meta: Mapping[str, Any]) -> List[str]:
    """What K=10 items actually cost in characters. See ``_evidence_chars``.

    K counts ITEMS, and items stopped being interchangeable when the document
    anchors among them started carrying their own session text. A reader
    comparing this run's evidence budget with a published top-10 needs the
    second number, so §5 prints it beside the shortfalls rather than leaving
    "the full K=10 evidence items" to imply a budget it no longer describes.
    """
    mean = meta.get("evidence_chars_mean")
    if mean is None:
        return []
    sentence = (
        f"**Evidence size, in characters rather than items.** Mean "
        f"{int(mean):,} per question, median "
        f"{int(meta.get('evidence_chars_median') or 0):,}, max "
        f"{int(meta.get('evidence_chars_max') or 0):,}."
    )
    cap = meta.get("evidence_source_chars")
    if not cap:
        # The control arm. Saying nothing here would let §5 imply the default
        # budget; saying the default sentence would declare a cap this run
        # never applied. Both are the same lie in opposite directions.
        sentence += (
            f" Every item is a retrieved node's name and description and "
            f"nothing else (`--answer-evidence summary`): the pre-#193 "
            f"content, kept as a measurable control. On group 0 that is 1.7% "
            f"of the text the retriever scored in order to rank it."
        )
        return ["", sentence]
    if cap is not None:
        sentence += (
            f" An item is a retrieved node's name and description, PLUS — for "
            f"the node that IS a staged session, and only for it — the first "
            f"{int(cap):,} characters of that session's own file. Before that "
            f"expansion the same K={PROTOCOL_K} was ~2,300 characters, 1.7% of "
            f"the text the retriever had already scored in order to rank it; "
            f"arXiv:2410.10813 §5.2 measures exactly that substitution — "
            f"sessions replaced by summaries or facts — as a LOSS. The cap is "
            f"not the ranking cap; `adapter.EVIDENCE_SOURCE_CHARS` says why "
            f"2,400 rather than 8,000."
        )
    return ["", sentence]


def _shortfall_section(shortfalls: Sequence[Mapping[str, Any]], n_questions: int,
                       meta: Optional[Mapping[str, Any]] = None) -> List[str]:
    meta = meta or {}
    if not shortfalls:
        return [f"Every one of the {n_questions} queries returned the full K="
                f"{PROTOCOL_K} evidence items."] + _evidence_budget_note(meta)
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
    return lines + _evidence_budget_note(meta)


def _retrieval_footnotes(reports: Sequence[Mapping[str, Any]]) -> List[str]:
    """What the §6 table's numbers do NOT say, one bullet per thing."""
    overall = reports[0]["overall"]
    shared = dict(reports[0].get("meta") or {})
    notes = [
        f"- `n` counts only the questions that HAVE a gold session. "
        f"{int(overall['n_no_gold'])} of {int(overall['n'])} carried none and "
        f"are excluded from both metrics rather than scored zero — retrieval of "
        f"a gold that does not exist is not a thing to score. Gold is aligned to "
        f"the corpus by CONTENT SIGNATURE and never by position; measured, the "
        f"two views of the haystack do not agree on either order or count. "
        f"{int(shared.get('n_unmatched') or 0)} haystack session(s) matched no "
        f"session in the dated `context` view and were counted, not guessed at.",
    ]
    for report in reports:
        system = str(report["system"])
        meta = dict(report.get("meta") or {})
        if meta.get("bm25_impl"):
            notes.append(
                f"- **{system}** ran `{meta['bm25_impl']}`. `hybrid._bm25_scores` "
                f"prefers `rank_bm25.BM25Okapi` whenever it imports and otherwise "
                f"runs the repo's local Okapi; the two are different formulas — "
                f"rank_bm25's IDF has an epsilon floor the local one does not — so "
                f"this row is not reproducible without knowing which ran.")
        if meta.get("n_unmapped_hits") is not None:
            notes.append(
                f"- **{system}**'s row is a LOWER BOUND, twice over. A retrieved "
                f"node carries one `source_path`, and "
                f"`tesserae.canonicalization.merge_node_group` keeps the canonical "
                f"node's when it collapses a concept extracted from many sessions, "
                f"so some gold sessions are unreachable through provenance however "
                f"well the memory retrieved; "
                f"{int(meta['n_unmapped_hits'])} hit(s) mapped to no staged "
                f"document at all and were dropped rather than resolved to a "
                f"plausible index. And K hits de-duplicate to FEWER than K "
                f"documents when several nodes come from one session, which is the "
                f"budget working — topping the list up would hand this arm more "
                f"evidence than the baselines got.")
    short = [f"**{r['system']}** on {int(r['overall']['n_under_k'])} of "
             f"{int(r['overall']['n'])} question(s)"
             for r in reports if int(r["overall"]["n_under_k"])]
    if short:
        notes.append(
            f"- Fewer than K **distinct documents** came back for "
            f"{'; '.join(short)}. That counts documents AFTER de-duplication and "
            f"is not the same measurement as `MabMemory.shortfalls` in §5, which "
            f"counts a search that matched fewer than K nodes: a full K hits "
            f"drawn from four sessions is four documents and no shortfall at "
            f"all, and for the Tesserae arm that is the ordinary case rather "
            f"than a fault. A lane that scored nothing above zero lands in this "
            f"same count, so read it beside §5 and beside `n` — this number on "
            f"its own cannot tell de-duplication from an empty lane. The list is "
            f"never padded either way, and the recall denominator stays "
            f"`min(|G|, K)`: shrinking it to what actually came back would score "
            f"an arm 1.0 for returning one document.")
    return notes


def _refusal_notice(
    refusals: Optional[Mapping[str, Skip]], *, n_scored: int
) -> List[str]:
    """The arms that are MISSING from the table, named above it.

    An arm can refuse for a reason that has nothing to do with the arms beside
    it — the dense arm's model is not in this machine's cache, say — and when it
    did, the whole run used to end there: the refusal propagated to ``main``'s
    ``except Skip``, which printed it and exited 0 without writing a report, so
    BM25's completed numbers were thrown away by an unrelated arm's problem.
    They are kept now, which makes this notice necessary: a table that silently
    lists fewer arms than were asked for is a comparison a reader cannot see the
    edge of.
    """
    if not refusals:
        return []
    total = n_scored + len(refusals)
    one = len(refusals) == 1
    lines = [
        f"**{len(refusals)} of the {total} arms asked for "
        f"{'is' if one else 'are'} missing from this table — "
        f"{'it' if one else 'they'} refused, and the rest of the run was "
        f"kept.** An arm that cannot run does not invalidate the arms that did, "
        f"but it does mean this is not the comparison the invocation asked for:",
        "",
    ]
    lines += [f"- **{system}** refused: {skip.what} — {skip.remedy}"
              for system, skip in refusals.items()]
    lines.append("")
    return lines


def _retrieval_section(
    reports: Sequence[Mapping[str, Any]],
    refusals: Optional[Mapping[str, Skip]] = None,
) -> List[str]:
    """§6. Everything that qualifies the table goes ABOVE it, on
    ``_comparable_section``'s logic.

    This is the part of the report that gets screenshotted, and a caveat below
    the crop is not a caveat. Three things follow from that one rule:

    * :data:`evals.lme_mab.retrieval.NOT_COMPARABLE` is printed first — one
      owner, imported, never restated, because two prose versions of a
      limitation drift and the weaker one gets quoted;
    * an arm that REFUSED is named above the table too. A crop showing two rows
      where three arms were asked for reads as a comparison that ran;
    * and Tesserae's lower bound is in its own method CELL rather than only in a
      footnote. The footnote explains it, but the cell is the part that travels
      with the number — into a screenshot, into a paste, into a slide.

    The table itself does not print at all when the arms did not share one local
    embedder: see :func:`evals.lme_mab.retrieval.embedder_refusal`.
    """
    if not reports:
        return ["No retrieval was scored in this run."]
    withheld = embedder_refusal(reports, local=LOCAL_EMBEDDING_PREFER)
    if withheld:
        return [withheld]
    k = int(reports[0]["k"])
    rows = []
    for report in reports:
        meta = dict(report.get("meta") or {})
        o = report["overall"]
        n = int(o["n_scored"])
        # The label rides in the cell, not under the table. What makes it a
        # lower bound is `n_unmapped_hits` — the count only the arm whose hits
        # carry a provenance path declares — and the footnote below says why.
        method = str(report["system"])
        if meta.get("n_unmapped_hits") is not None:
            method += " (lower bound)"
        rows.append([
            method,
            str(meta.get("corpus") or "—"),
            str(meta.get("retriever") or "—"),
            str(meta.get("embedder") or "—"),
            str(report["k"]),
            _num(o["recall_at_k"], n),
            _num(o["mrr"], n),
            str(n),
        ])
    lines = [NOT_COMPARABLE, ""]
    lines += _refusal_notice(refusals, n_scored=len(reports))
    lines += _table(
        ["method", "corpus", "retriever", "embedder", "K", f"recall@{k}", "MRR", "n"],
        rows,
    )
    lines += ["", *_retrieval_footnotes(reports)]
    return lines


#: What §2-§5 print when no arm answered a question. The sections keep their
#: numbers instead of collapsing: §6 is quoted BY NUMBER, and a heading that
#: moves with the flags makes two reports of the same run disagree about where
#: its result is.
_NOT_ANSWERED = ("No arm answered a question in this run — `--retrieval-only`, "
                 "or an `--arms` list without `tesserae`. The result is §6.")


def build_report(
    reports: Sequence[Mapping[str, Any]],
    *,
    retrieval: Sequence[Mapping[str, Any]] = (),
    refusals: Optional[Mapping[str, Skip]] = None,
    ingests: Sequence[IngestResult] = (),
    shortfalls: Sequence[Mapping[str, Any]] = (),
    parquet: str = "undeclared",
    groups: str = "undeclared",
) -> str:
    """The markdown report. No timestamps: same answers in, same bytes out.

    ``reports`` is the ANSWER scoring — ``evals.qa.scorer.score_system``'s shape,
    and a sequence on ``evals/qa/run_qa_eval.build_report``'s model, though only
    the Tesserae arm can ever fill it: §1-§5 are about answering a question and
    the baselines do not answer questions. ``retrieval`` is one
    ``retrieval.score_retrieval`` report per arm and is what §6 compares. Either
    may be empty; both empty would be a report about nothing and the runner does
    not get there. ``refusals`` is the arms that asked to be measured and could
    not be, keyed by the name §6 would have printed — they are named above the
    table rather than dropped silently.
    """
    answers: Optional[Mapping[str, Any]] = reports[0] if reports else None
    tesserae_ran = answers is not None or any(
        str(r.get("system")) == "Tesserae" for r in retrieval)
    meta = dict(answers.get("meta") or {}) if answers else {}
    blockers = protocol_blockers(meta)
    counted = answers or (retrieval[0] if retrieval else None)
    n_questions = int(counted["overall"]["n"]) if counted else 0
    systems = [str(r["system"]) for r in (retrieval or reports)] or ["Tesserae"]
    # A retrieval-only run writes no answers file, so pointing its reader at
    # --score would send them looking for one that does not exist.
    regenerate = ("`python -m evals.lme_mab.run --score <answers.json>`." if answers
                  else "re-run the same invocation — retrieval scoring reads no "
                       "clock and no network, and no model but the local "
                       "embedder, which loads from this machine's cache with "
                       "the Hugging Face hub switched off.")
    lines = [
        f"# LongMemEval-MAB — {', '.join(systems)}",
        "",
        f"Dataset: `{parquet}` (ai-hyz/MemoryAgentBench, `Accurate_Retrieval`, "
        f"`longmemeval_s*`). Groups: {groups}. Questions: {n_questions}. "
        f"Scorer: `evals/qa/scorer.py` (exact match + token F1 via the shared "
        f"`evals.metrics.prf1`) for §2, `evals/lme_mab/retrieval.py` (recall@K "
        f"and MRR of the gold session) for §6. Regenerate: {regenerate}",
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
    lines += _scores_section(answers) if answers else [_NOT_ANSWERED]
    lines += ["", "## 3. Comparable result", ""]
    lines += _comparable_section(answers, blockers) if answers else [_NOT_ANSWERED]
    lines += ["", "## 4. Protocol controls", ""]
    lines += _controls_section(meta, blockers)
    lines += ["", "### Declared", ""]
    # ``shortfalls`` rides along in a saved answers file so a --score re-run can
    # reproduce §5, but it is a list of records and not a declaration; dumping
    # its repr into this table buries the four values a reader is here for.
    keys = sorted(k for k in meta if k != "shortfalls")
    if keys:
        lines += _table(["key", "value"], [[k, str(meta[k])] for k in keys])
    elif retrieval:
        lines += ["Nothing answered, so there is nothing to declare here — these "
                  "are the ANSWERING declarations §4 checks. Each arm's retrieval "
                  "declaration is its own row in §6, read off the live object "
                  "rather than hardcoded."]
    else:
        lines += ["Nothing declared — an undeclared run cannot be published."]
    lines += ["", "## 5. Retrieval shortfalls", ""]
    lines += (_shortfall_section(shortfalls, n_questions, meta) if tesserae_ran else
              ["The Tesserae arm did not run, and §5 is about ITS graph "
               "retrieval. The baseline arms' shortfalls are in §6."])
    lines += ["", "## 6. Retrieval comparison (this machine's protocol)", ""]
    lines += _retrieval_section(retrieval, refusals)
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
    parser.add_argument("--arms", default="tesserae",
                        help=f"comma list of memory systems to measure "
                             f"({', '.join(ARMS)}; default: tesserae). Only "
                             f"tesserae spends — a list without it prints no cost "
                             f"banner, asks no consent and needs no API key")
    parser.add_argument("--retrieval-only", action="store_true",
                        help="score recall@K and MRR of the gold session and skip "
                             "answering entirely — no backbone, no judge")
    parser.add_argument("--fanout", action="store_true",
                        help="run the lanes a second time with corpus-ubiquitous "
                             "terms stripped and merge the rankings (free, local, "
                             "deterministic). Off by default: not the shipped "
                             "retrieval path")
    parser.add_argument("--reuse-compile", action="store_true",
                        help="measure against the graph ALREADY compiled in "
                             "--work instead of compiling again. Verifies the "
                             "staged corpus is byte-identical to what this "
                             "group would stage and refuses otherwise; writes "
                             "nothing. A compile is ~an hour per group, so this "
                             "is how a retrieval change is re-measured on a "
                             "group that has already been built")
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
    parser.add_argument("--answer-evidence", choices=("source", "summary"),
                        default="source",
                        help="what the backbone reads per retrieved hit: the "
                             "node summary PLUS its own session text (source, "
                             "the default), or the node summary alone "
                             "(summary — the pre-#193 control arm, kept so the "
                             "two can be measured against each other over one "
                             "retrieval)")
    parser.add_argument("--k", type=int, default=PROTOCOL_K,
                        help=f"evidence budget. NOT a tuning knob — the protocol "
                             f"fixes K={PROTOCOL_K} and any other value blocks the "
                             f"comparison")
    parser.add_argument("--embedding-prefer", default=LOCAL_EMBEDDING_PREFER,
                        help=f"embedding backend preference passed to "
                             f"active_embedding_backend (default: "
                             f"{LOCAL_EMBEDDING_PREFER}). §6 compares arms under "
                             f"ONE local embedder and says so above its table, so "
                             f"any other value makes it withhold that table "
                             f"rather than print a caveat its own rows falsify")
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
                [report],
                shortfalls=meta.get("shortfalls") or (),
                parquet=str(meta.get("dataset") or "undeclared"),
                groups=str(meta.get("groups") or "undeclared"),
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(text)
            print(f"wrote {args.out}")
            return 0

        # ONE predicate decides whether this invocation can spend anything, and
        # every money layer below reads it. BM25 and Dense are arithmetic over
        # the staged bytes: asking them to approve a bill, or SKIPping them for
        # a key they never use, would be a refusal layer protecting nothing and
        # blocking the only comparison this machine can actually run.
        arms = parse_arms(args.arms)
        # Both checks are about the invocation itself and read nothing, so they
        # fire before the banner and long before the 20MB parquet: a bad --k
        # that surfaces as a ZeroDivisionError two minutes in has told the
        # operator less than a SKIP naming the flag.
        args.k = require_k(args.k)
        spends = "tesserae" in arms
        if args.stage_only and not spends:
            raise Skip(
                f"--stage-only stages the Tesserae corpus, and --arms {args.arms} "
                f"does not include it",
                "add tesserae to --arms, or drop --stage-only — the baselines "
                "index Session.render() in memory and stage nothing",
            )

        if args.reuse_compile and args.stage_only:
            print("SKIP: --reuse-compile and --stage-only contradict each other — "
                  "one measures against an existing graph, the other refuses to "
                  "build or use one")
            return 0

        # Guard 2 — the estimate, before any input is read. Group selection is
        # not known yet, so the banner is scaled off the REQUEST, which is what
        # the operator is being asked to approve.
        requested = (len(args.groups) if args.groups
                     else args.limit_groups or MEASURED["groups"])
        estimate = estimate_cost(max(1, min(int(requested), MEASURED["groups"])))
        if spends:
            print(cost_banner(estimate))

        # Guard 3 — explicit consent to spend, BEFORE the prerequisites. It has
        # to fire ahead of the SKIPs or an operator who forgot the flag learns
        # about a missing parquet instead of about the flag. --retrieval-only
        # does NOT relax it for tesserae: retrieval still compiles the haystack,
        # which is the expensive half.
        if spends and not args.stage_only and not args.i_know_this_costs_money:
            print("SKIP: this run compiles a haystack and answers every question — "
                  "both spend LLM quota\n"
                  "      re-run with --i-know-this-costs-money, or --stage-only to "
                  "write the documents and stop")
            return 0

        # Guard 4 — prerequisites. The parquet, the work directory and the
        # groups are needed by every arm, so none of them is conditioned on
        # `spends`; the key is, because only the Tesserae arm's retrieval
        # embeds through an OpenAI backend.
        parquet = require_parquet(args.parquet)
        work = require_work_dir(args.work)
        if spends and not args.stage_only:
            require_openai_key(args.embedding_prefer)
        groups = select_groups(load_groups_or_skip(parquet), args.groups, args.limit_groups)

        # Gold alignment BEFORE anything is ingested. It refuses rather than
        # guessing (see retrieval.RefusedToAlignGold), and a refusal after a
        # compile would throw away the expensive half of the run to say
        # something that was knowable from the parquet alone.
        sessions_by_group = {g.index: split_sessions(g) for g in groups}
        alignments = {} if args.stage_only else {
            g.index: align_gold(g, sessions_by_group[g.index]) for g in groups}

        if spends and not args.stage_only and not _confirm(estimate, args.yes):
            return 0

        search_fn = None
        if args.fanout:
            # Same lever as the LoCoMo arm (#246): the lanes run a second time
            # with corpus-ubiquitous terms stripped and the rankings merge.
            # fanout_search takes every parameter hybrid_search takes, so the
            # arm's call site is untouched.
            from tesserae.retrieval.fanout import fanout_search
            search_fn = fanout_search
        memory = (MabMemory(embedding_prefer=args.embedding_prefer, search_fn=search_fn)
                  if spends else None)
        ingests: List[IngestResult] = []
        rows: List[Dict[str, Any]] = []
        arm_rows: Dict[str, List[Dict[str, Any]]] = {name: [] for name in arms}
        arm_objects: Dict[str, Any] = {}
        #: Arms that refused mid-run, in `arms` order. Kept rather than raised —
        #: see the `except Skip` in the loop below.
        refused: Dict[str, Skip] = {}
        answer_fn = (build_backbone(args.backbone)
                     if spends and not args.stage_only and not args.retrieval_only
                     else None)

        for group in groups:
            if memory is not None:
                ingests.append(memory.ingest(group, work=work,
                                             compile_project=not args.stage_only,
                                             reuse_compiled=args.reuse_compile))
                print(f"group {group.index}: staged {ingests[-1].documents} sessions "
                      f"to {ingests[-1].corpus_dir}", file=sys.stderr)
            if args.stage_only:
                continue
            gold = alignments[group.index].gold
            for name in arms:
                if name in refused:
                    continue
                try:
                    if name == "tesserae":
                        assert memory is not None
                        if answer_fn is None:
                            retrieved = retrieve_group(memory, group, k=args.k,
                                                       progress=True)
                        else:
                            answered, retrieved = answer_group(
                                memory, group, answer_fn, k=args.k,
                                expand_evidence=args.answer_evidence == "source")
                            rows += answered
                    else:
                        # One arm per GROUP: the corpus is a group's sessions, and
                        # an index carried across groups would rank a question
                        # against a haystack it was never asked about.
                        arm = _BASELINE_ARMS[name](sessions_by_group[group.index])
                        arm_objects[name] = arm
                        retrieved = retrieve_group(arm, group, k=args.k)
                except Skip as refusal:
                    # PER ARM. An arm refuses for reasons of its own — the dense
                    # arm's model is not in this machine's cache — and letting
                    # that propagate ended the run: main's `except Skip` printed
                    # it and exited 0 with no report, discarding a BM25 arm that
                    # had already scored every question. The arms that ran keep
                    # their numbers; §6 names the ones that did not, above the
                    # table, and the run still SKIPs if none survive.
                    refused[name] = refusal
                    arm_rows[name] = []   # partial groups are not a row
                    arm_objects.pop(name, None)
                    if name == "tesserae":
                        rows.clear()      # nor are partial answers a §2 score
                    print(f"SKIP ({name} arm): {refusal.what}\n"
                          f"      {refusal.remedy}", file=sys.stderr)
                    continue
                arm_rows[name] += retrieval_rows(group, gold, retrieved)

        if args.stage_only:
            print(f"\nNOTHING HAS BEEN COMPILED. {sum(i.documents for i in ingests)} "
                  f"session documents are in {work / 'corpus'}.\n"
                  f"Re-run without --stage-only (and with "
                  f"--i-know-this-costs-money) to compile and answer.")
            return 0

        # Scored AFTER the loop, so every declaration is an observation of an
        # object that has already answered every question.
        scored_arms = [name for name in arms if name not in refused]
        if not scored_arms:
            # Nothing survived, so there is no result to keep and the run SKIPs
            # exactly as it always did — keeping the arms that finished is not
            # the same as writing a report about none of them.
            raise next(iter(refused.values()))
        n_unmatched = sum(a.n_unmatched for a in alignments.values())
        corpus = (f"{sum(len(s) for s in sessions_by_group.values()):,} session "
                  f"documents (Session.render, {len(groups)} group(s))")
        retrieval = [
            score_retrieval(
                arm_rows[name],
                system=("Tesserae" if name == "tesserae" else arm_objects[name].name),
                k=args.k,
                meta=arm_declaration(name, corpus=corpus, memory=memory,
                                     arm=arm_objects.get(name),
                                     n_unmatched=n_unmatched),
            )
            for name in scored_arms
        ]
        # Keyed by the name §6 would have printed, so the notice above the table
        # and the rows in it name the arms the same way.
        refusals = {(("Tesserae" if name == "tesserae"
                      else _BASELINE_ARMS[name].name)): refusal
                    for name, refusal in refused.items()}

        meta = {
            "answer_shape": ANSWER_SHAPE,
            "llm_model": args.backbone,
            "embedding_model": getattr(memory.embedding_backend(), "name", None),
            "embedding_dim": getattr(memory.embedding_backend(), "dim", None),
            "judge": args.judge,
            "evidence_budget": args.k,
            # K alone no longer describes the budget: an evidence item is a
            # node summary OR a node summary plus up to EVIDENCE_SOURCE_CHARS
            # of its own session. Declaring the cap and the realised
            # distribution is what keeps §4 checking the control it names —
            # ``evidence_budget`` is a count of items, and a reader comparing
            # this run to a published top-10 needs to know how big an item got.
            "evidence_content": args.answer_evidence,
            # 0, not the constant, on the summary arm: declaring a 2,400-char
            # cap for a run whose items never expanded would be a false
            # declaration of the very control §4 exists to check.
            "evidence_source_chars": (EVIDENCE_SOURCE_CHARS
                                      if args.answer_evidence == "source" else 0),
            **_evidence_chars(rows),
            "dataset": str(parquet),
            "groups": ",".join(str(g.index) for g in groups),
            "protocol": "arXiv:2606.04555 §5.2-5.3",
        } if rows else {}
        reports = [score_system(rows, system="Tesserae", meta=meta)] if rows else []
        text = build_report(reports, retrieval=retrieval, refusals=refusals,
                            ingests=ingests,
                            shortfalls=memory.shortfalls if memory else (),
                            parquet=str(parquet),
                            groups=",".join(str(g.index) for g in groups))
        if args.answers_out and not rows:
            # An answers file with no answers would be re-scorable into a report
            # claiming a system answered nothing, which is not what happened.
            print("no answers to write — this run measured retrieval only",
                  file=sys.stderr)
        elif args.answers_out:
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
