"""Tokens-to-correct-answer: build every prompt, count it, spend it, report it.

    # FREE. Builds every request for every arm at every rung, counts the
    # tokens, writes the prompts, and reports the ladder's shape. No LLM call.
    uv run python -m evals.locomo.run_context --dry-run \
        --work ~/.blackhole/Tesserae/2026-08-21/locomo-run/work \
        --conversations conv-26 --budgets 512,2048,8192

    # THE MEASUREMENT. One canary call, then one call per prompt per replicate.
    uv run python -m evals.locomo.run_context \
        --work ~/.blackhole/Tesserae/2026-08-21/locomo-run/work \
        --conversations conv-26 --budgets 512,2048,8192 \
        --arms bm25_docs,bm25_compiled,graph_only,closed_book \
        --replicates 3 --i-know-this-costs-money --yes

    # Re-score prompts+answers already on disk. Offline under the
    # deterministic judge, and the reason the prompts are persisted at all.
    uv run python -m evals.locomo.run_context --score answers-context.json

**This runner never compiles anything.** It reads a corpus and a graph a
previous run staged and compiled, and refuses when either is missing. Compiling
is :mod:`evals.locomo.run`'s job and it overwrites a project directory; a
token-efficiency runner that could silently rebuild the thing it measures is a
runner whose numbers describe an unknown corpus.

It is a second ENTRY POINT over one set of components, not a second harness: the
dataset, the judge, the three-number decomposition, the replicate spread, the
answering system prompt and the backbone canary all come from the modules beside
it. What is new is the axis — a token budget, a fitted prompt, and a curve.

The order of operations is the point. Every prompt is BUILT AND PERSISTED before
any of them is sent, so a run that dies halfway still leaves an auditable record
of exactly what would have been asked, and a token claim can be re-derived from
disk without re-spending the run. The previously shipped answers file recorded
``evidence_chars`` and never the prompt, and no token number in it can be
checked today.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..qa.run_qa_eval import Skip, _num, _rate, _table
from . import efficiency
from .context_arms import (
    ARM_NAMES,
    COMPILE_BUDGET_GRID,
    DEFAULT_REGION_K,
    build_arms,
    parse_arms,
    parse_budgets,
    staged_bodies,
)
from .dataset import (
    ADVERSARIAL_CATEGORY,
    Conversation,
    dataset_revision,
    load_conversations,
)
from .judge import DeadBackbone, DeadJudge, Judge, build_judge
from .adapter import PROTOCOL_BACKBONE
from .run import (
    DEFAULT_DATA,
    _SYSTEM_PROMPT,
    _grade_rows,
    canary_backbone,
    require_data,
    select_or_skip,
)
from .scoring import GradedRow, decompose, question_key, replicate_spread
from .tokens import (
    SCHEMA_NAME,
    Prompt,
    count_tokens,
    serialized_request,
    tokenizer_controls,
    user_turn,
)

DEFAULT_WORK = (Path.home() / ".blackhole" / "Tesserae" / "2026-08-21"
                / "locomo-run" / "work")
DEFAULT_OUT = (Path.home() / ".blackhole" / "Tesserae" / "context-eval"
               / "report-context.md")

#: The rungs. NOT measured by this commit — carried from the read-only
#: instrument pass at ``~/.blackhole/Tesserae/2026-08-22/context-eval/
#: instrument.md``, which reports the share of an earlier run's 199 questions
#: whose evidence already fit under each candidate rung as 512 -> 0.000,
#: 2,048 -> 0.276, 8,192 -> 0.975, i.e. above 8,192 the ladder stops
#: discriminating. What THIS commit measured on conv-26 is that the whole staged
#: corpus serialises to 19,906 tokens, so the ladder's top rung is well inside
#: it. A ``--budgets`` flag exists because the rungs are a declared choice.
DEFAULT_BUDGETS = "512,2048,8192"

#: The accuracy T@tau is reported against. A DECLARED threshold, not a measured
#: one; the report prints the whole curve beside it so a reader can pick another.
DEFAULT_TAU = 0.5

#: Arms in the order the report prints them.
DEFAULT_ARMS = "bm25_docs,bm25_compiled,graph_only,closed_book"


# --------------------------------------------------------------------------
# The backbone, taking a prompt rather than building one
# --------------------------------------------------------------------------


def build_prompt_backbone(model: str) -> Callable[[Prompt], str]:
    """A ``Prompt -> short answer`` callable.

    :func:`evals.locomo.run.build_backbone` builds its user turn inside a
    closure and returns only the answer, so a caller cannot see the request it
    sent. Here the request is the input, which is what makes the token count on
    every row a measurement of the thing that was actually sent rather than a
    reconstruction of it.
    """
    from tesserae.llm_json import build_default_json_client

    client = build_default_json_client(model=model)
    if client is None:
        raise Skip(
            f"no LLM client available for the {model} backbone",
            "configure a provider, or run --dry-run, which builds and counts "
            "every prompt and spends nothing",
        )

    def answer(prompt: Prompt) -> str:
        payload = client.complete_json(
            system=prompt.system, user=prompt.user,
            schema_name=prompt.schema_name)
        if isinstance(payload, Mapping) and payload.get("answer") is not None:
            return str(payload["answer"])
        return ""

    return answer


def canary_shim(answer_fn: Callable[[Prompt], str]
                ) -> Callable[[str, Sequence[str]], str]:
    """Adapt a prompt backbone to the shape :func:`run.canary_backbone` grades.

    The canary is reused rather than rewritten because a second canary is a
    second thing that can pass while the first would have failed. The evidence
    framing is :func:`evals.locomo.tokens.user_turn`, which is the same framing
    every arm here uses — so the canary exercises the path the run will take.
    """

    def answer(question: str, evidence: Sequence[str]) -> str:
        return answer_fn(Prompt(system=_SYSTEM_PROMPT,
                                user=user_turn(question, list(evidence)),
                                schema_name=SCHEMA_NAME, items=list(evidence)))

    return answer


# --------------------------------------------------------------------------
# Phase 1 — build and persist every prompt. Free.
# --------------------------------------------------------------------------


#: The question draw every agentic measurement on this corpus has used, so a
#: number here is comparable with the ones already on record (spec §22: the
#: one-shot pipeline at token F1 0.370, the agentic path at 0.323). Categories
#: 1-4 and not 5, because category 5's gold answer IS a refusal — an arm
#: starved of context scores that whole category for free, and mixing it into a
#: 16-question denominator would let the closed-book control look competent.
#: The frozen set it reproduces is
#: ``~/.blackhole/Tesserae/2026-08-21/provenance/questions16.json``.
PROTOCOL_SEED = 20260822
PROTOCOL_SAMPLE = 16
PROTOCOL_CATEGORIES = (1, 2, 3, 4)


def select_questions(
    conversation: Conversation,
    *,
    sample: int,
    seed: int,
    categories: Sequence[int],
) -> tuple:
    """Draw ``sample`` questions, and return them WITH their original indices.

    Filter, then shuffle, then truncate — in that order, because shuffling
    first and filtering afterwards draws a different set from the same seed.
    That ordering is the identity of the question set, so it is pinned by a
    test rather than left to read off the source.

    The indices are returned rather than discarded because
    :func:`evals.locomo.scoring.question_key` is ``<conversation>#<index>``.
    Re-enumerating a subset from zero mints keys that name a different question
    in every other run on this corpus, which would silently mispair the
    question-level comparisons.
    """
    import random

    wanted = tuple(int(c) for c in categories)
    pool = [(i, q) for i, q in enumerate(conversation.questions)
            if q.category in wanted]
    if len(pool) < sample:
        raise Skip(
            f"{conversation.sample_id} has {len(pool)} questions in categories "
            f"{','.join(str(c) for c in wanted)}, fewer than the {sample} "
            f"requested",
            "lower --sample, widen --sample-categories, or add a conversation "
            "— answering fewer questions than the protocol declares reports a "
            "denominator nobody chose",
        )
    random.Random(seed).shuffle(pool)
    pool = pool[:sample]
    indices = [i for i, _ in pool]
    return replace(conversation,
                   questions=[q for _, q in pool]), indices


def build_prompts(
    conversation: Conversation,
    arms: Sequence[Any],
    budgets: Sequence[int],
    *,
    indices: Optional[Sequence[int]] = None,
    progress: bool = False,
) -> List[Dict[str, Any]]:
    """One row per (question, arm, rung), carrying the full request text.

    Prompts do not vary between replicates — the arms are deterministic — so
    they are built once and answered N times, which is also what stops the
    context an arm supplies from drifting between replicates of a run whose
    whole purpose is to isolate the generative variance.

    An unbudgeted arm (``closed_book``, ``whole_corpus``) is built ONCE at
    :data:`efficiency.UNBUDGETED` rather than once per rung: repeating an
    identical request at three rungs would triple its cost and add three
    identical points to a curve that is supposed to show a response to budget.
    """
    keyed = list(zip(
        list(indices) if indices is not None
        else range(len(conversation.questions)),
        conversation.questions))
    rows: List[Dict[str, Any]] = []
    for arm in arms:
        unbudgeted = arm.name in ("closed_book", "whole_corpus")
        rungs: Sequence[Optional[int]] = (
            [None] if unbudgeted else list(budgets))
        for rung in rungs:
            for position, (index, question) in enumerate(keyed):
                if progress:
                    print(f"[{conversation.sample_id}] {arm.name} "
                          f"@{rung or 'unbudgeted'} "
                          f"[{position + 1}/{len(keyed)}]",
                          file=sys.stderr)
                built = arm.prompt(question.question, budget_tokens=rung)
                rows.append({
                    "key": question_key(question, index),
                    "arm": arm.name,
                    "budget": int(rung) if rung else efficiency.UNBUDGETED,
                    "conversation": conversation.sample_id,
                    "question_index": index,
                    "question": question.question,
                    "category": question.category,
                    # THE REQUEST, verbatim. Not a summary of it, not its size.
                    "system": built.system,
                    "user": built.user,
                    "schema_name": built.schema_name,
                    "request": built.request,
                    **built.as_row(),
                })
    return rows


def write_prompts(path: Path, rows: Sequence[Mapping[str, Any]],
                  meta: Mapping[str, Any]) -> None:
    """Persist prompts BEFORE anything is sent. One JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"meta": dict(meta)}, sort_keys=True) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# Phase 2 — answer
# --------------------------------------------------------------------------


def answer_prompts(
    prompts: Sequence[Mapping[str, Any]],
    answer_fn: Callable[[Prompt], str],
    *,
    replicates: int,
    progress: bool = True,
) -> List[Dict[str, Any]]:
    """One answer row per prompt per replicate. A failure is recorded, not raised.

    A backbone failure on one question is caught and stored as its answer, which
    ``evals.qa.scorer.is_error`` keeps out of both the refusal count and the
    correct count — so one 429 does not erase the rest of the ladder. The token
    count travels with the row either way: the request was built and counted
    before the call, so a failed call still has a measured cost.
    """
    rows: List[Dict[str, Any]] = []
    total = len(prompts) * replicates
    done = 0
    for replicate in range(replicates):
        for row in prompts:
            done += 1
            if progress:
                print(f"[{done}/{total}] rep {replicate} {row['arm']}"
                      f"@{row['budget']} {row['question'][:60]}",
                      file=sys.stderr)
            prompt = Prompt(system=str(row["system"]), user=str(row["user"]),
                            schema_name=str(row["schema_name"]))
            try:
                answer = answer_fn(prompt)
            except Exception as exc:  # noqa: BLE001 — recorded, not raised
                answer = f"Error: {exc}"
            rows.append({
                **{k: v for k, v in row.items()
                   if k not in ("system", "user", "request")},
                "replicate": replicate,
                "answer": answer,
            })
    return rows


# --------------------------------------------------------------------------
# Phase 3 — the report
# --------------------------------------------------------------------------


def _cell_arm(arm: str, budget: int) -> str:
    """The label a rung's three-number decomposition is keyed on."""
    return arm if budget == efficiency.UNBUDGETED else f"{arm}@{budget}"


def _ladder_section(built: efficiency.Curve,
                    scored: Optional[efficiency.Curve]) -> List[str]:
    """§1. Tokens on every row, correctness beside them when there is any.

    Two curves and not one: ``built`` is every prompt that was constructed and
    counted, ``scored`` is the subset that was answered and graded. A dry run
    has the first and not the second, and printing an accuracy column of zeros
    there would be a measurement-shaped hole.
    """
    out = ["## 1. The ladder — accuracy at a fixed token budget", ""]
    header = ["arm", "budget", "n", "mean tokens", "max tokens", "over budget",
              "truncated", "accuracy", "token F1", "refusals"]
    rows = []
    by_key = {(p.arm, p.budget): p for p in (scored.points if scored else [])}
    for point in sorted(built.points, key=lambda p: (p.arm, p.budget)):
        graded = by_key.get((point.arm, point.budget))
        rows.append([
            point.arm,
            "unbudgeted" if point.budget == efficiency.UNBUDGETED
            else f"{point.budget:,}",
            str(point.n),
            f"{point.mean_tokens:,.0f}",
            f"{point.max_tokens:,}",
            "YES" if point.over_budget else "no",
            str(point.n_truncated),
            _num(graded.accuracy, graded.n) if graded else "not answered",
            _num(graded.graded_score, graded.n) if graded else "not answered",
            (f"{graded.n_refused} ({_rate(graded.refusal_rate, graded.n)})"
             if graded else "n/a"),
        ])
    out += _table(header, rows)
    out += ["",
            "`over budget` is a request that exceeded the rung it was fitted "
            "to; a YES means that arm was not measured at that budget.",
            "`truncated` counts rows where the arm had to cut a unit of "
            "evidence to fit. A fixed-budget ladder measures truncation skill "
            "unless truncation is visible, so it is a column.",
            ""]
    return out


def _scalar_section(scored: Optional[efficiency.Curve], tau: float) -> List[str]:
    """§2. AULBC and T@tau, with what each of them hides printed underneath."""
    out = ["## 2. The scalars, and what they hide", ""]
    if scored is None or not scored.points:
        out += ["Nothing was answered, so there is no accuracy to integrate. "
                "The scalars are not printed rather than printed as zero.", ""]
        return out
    header = ["arm", "AULBC (log2 budget)", f"T@{tau:.2f}", "best accuracy",
              "best correctness / 1k tokens (any rung)"]
    rows = []
    for arm in scored.arms:
        points = scored.for_arm(arm)
        area = efficiency.aulbc(points)
        tau_result = efficiency.tokens_to_tau(points, tau)
        ratios = [efficiency.correctness_per_1k(p) for p in points]
        usable = [r for r in ratios if r is not None]
        rows.append([
            arm,
            "n/a (fewer than two rungs)" if area is None else f"{area:.4f}",
            ("censored — no rung reached it" if tau_result.censored
             else f"{tau_result.budget:,}"),
            f"{tau_result.best_accuracy:.3f}",
            "undefined" if not usable else f"{max(usable):.4f}",
        ])
    out += _table(header, rows)
    out += ["",
            "**AULBC is lossy on purpose.** It is the normalised area under "
            "accuracy against log2(budget); it hides WHERE on the ladder an "
            "advantage sits, and it is defined only against the rungs this run "
            "declared — a run with different rungs produces an incomparable "
            "number.",
            "",
            f"**T@{tau:.2f} is censored, never imputed.** An arm that never "
            "reaches the threshold has no T@tau; substituting the largest rung "
            "would make a failure read as an expensive success.",
            "",
            "**correctness / 1k tokens is not a headline and is guarded.** An "
            "arm that supplies nothing spends the fewest tokens, so a ratio "
            "with tokens underneath it rewards silence without bound. It is "
            "`undefined` rather than infinite when no tokens were spent.",
            ""]
    flags = efficiency.dominates_by_ratio_only(scored.points)
    if flags:
        out += ["The ratio and the accuracy curve disagree, which is the "
                "pathology above, observed:", ""]
        out += [f"- {flag}" for flag in flags] + [""]
    else:
        out += ["At every rung the ratio and the accuracy curve rank the same "
                "arm first, so the ratio adds nothing here — which is not a "
                "licence to quote it alone.", ""]
    return out


def _three_numbers_section(per_rung: Mapping[int, Any]) -> List[str]:
    """§3. All questions, the both-answered subset, and the refusal counts.

    Computed WITHIN a rung. Pooling the rungs would build a like-for-like subset
    across budgets, which compares each arm on a different question set and is
    the error the subset exists to prevent.
    """
    out = ["## 3. The three numbers, per rung", ""]
    if not per_rung:
        out += ["Nothing was answered.", ""]
        return out
    for budget in sorted(per_rung):
        decomposition = per_rung[budget]
        label = ("unbudgeted controls" if budget == efficiency.UNBUDGETED
                 else f"budget {budget:,} tokens")
        out += [f"### {label}", ""]
        if not decomposition.complete:
            out += [f"NOT COMPUTED: {reason}" for reason in decomposition.missing]
            out += [""]
            continue
        header = ["arm", "n (all)", "accuracy (all)", "token F1 (all)",
                  "n (both answered)", "accuracy (both answered)",
                  "refusals", "errors"]
        rows = []
        for arm in sorted(decomposition.all_questions):
            every = decomposition.all_questions[arm]
            shared = decomposition.like_for_like[arm]
            rows.append([
                arm, str(every.n), _num(every.accuracy, every.n),
                _num(every.graded_score, every.n),
                str(shared.n), _num(shared.accuracy, shared.n),
                f"{decomposition.refusals.get(arm, 0)} "
                f"({_rate(every.refusal_rate, every.n)})",
                str(decomposition.errors.get(arm, 0)),
            ])
        out += _table(header, rows)
        out += ["",
                f"Adversarial category {ADVERSARIAL_CATEGORY} is scored on its "
                f"own and never merged into the two columns above — a refusal "
                f"is its gold answer, so a starved arm scores it for free:",
                ""]
        adv_rows = [[arm, str(s.n), _num(s.accuracy, s.n),
                     _num(decomposition.adversarial_reference[arm].accuracy,
                          decomposition.adversarial_reference[arm].n)]
                    for arm, s in sorted(decomposition.adversarial.items())]
        if adv_rows:
            out += _table(["arm", "n", "accuracy (our rule)",
                           "accuracy (published rule)"], adv_rows)
        out += [""]
    return out


def _free_lunch_section(scored: Optional[efficiency.Curve]) -> List[str]:
    out = ["## 4. The refusal free lunch", ""]
    if scored is None or not scored.points:
        out += ["Nothing was answered.", ""]
        return out
    if not any(p.arm == "closed_book" for p in scored.points):
        out += ["**The closed-book control was not run.** Without it there is "
                "no floor, and an arm's absolute accuracy cannot be read as "
                "evidence that its context bought anything. Re-run with "
                "`closed_book` in --arms.", ""]
        return out
    flags = efficiency.free_lunch(scored.points)
    if flags:
        out += ["Arms at or below the no-evidence floor:", ""]
        out += [f"- {flag}" for flag in flags] + [""]
    else:
        out += ["Every arm beat the no-evidence floor at every rung.", ""]
    return out


def _fitting_section(built: efficiency.Curve,
                     prompts: Sequence[Mapping[str, Any]]) -> List[str]:
    """§5. How each arm spent its own knob, and where the fitting failed."""
    out = ["## 5. Budget fitting", ""]
    header = ["arm", "budget", "n", "truncated", "over budget",
              "mean evidence chars", "mean fitted knob"]
    rows = []
    for point in sorted(built.points, key=lambda p: (p.arm, p.budget)):
        # ``point.arm`` is the CELL label (``bm25_docs@512``) and a prompt row
        # carries the bare arm name, so the two are joined through
        # :func:`_cell_arm` rather than compared directly — the earlier direct
        # comparison matched nothing and printed "no knob" for every budgeted
        # arm while looking like a finished table.
        group = [r for r in prompts
                 if _cell_arm(str(r["arm"]), int(r["budget"])) == point.arm]
        knobs = [r.get("fit", {}).get("requested_chars") for r in group]
        knobs = [k for k in knobs if isinstance(k, int)]
        kept = [r.get("fit", {}).get("n_kept") for r in group]
        kept = [k for k in kept if isinstance(k, int)]
        if knobs:
            knob = f"{sum(knobs) / len(knobs):,.0f} chars requested"
        elif kept:
            knob = f"{sum(kept) / len(kept):.2f} documents kept"
        else:
            knob = "no knob (unbudgeted)"
        rows.append([
            point.arm,
            "unbudgeted" if point.budget == efficiency.UNBUDGETED
            else f"{point.budget:,}",
            str(point.n), str(point.n_truncated),
            "YES" if point.over_budget else "no",
            f"{sum(int(r.get('evidence_chars') or 0) for r in group) / max(1, len(group)):,.0f}",
            knob,
        ])
    out += _table(header, rows)
    out += ["", "`mean fitted knob` is what the arm asked its own mechanism for "
            "after fitting: characters for the compiled arms, whole documents "
            "for the BM25 one. The arm turning its own knob is the condition "
            "under which a fixed budget measures compilation rather than "
            "truncation skill.", ""]
    return out


def _spread_section(spreads: Mapping[str, Any], replicates: int) -> List[str]:
    out = ["## 6. Within-arm replicate spread", ""]
    if not spreads:
        out += ["Nothing was answered.", ""]
        return out
    header = ["arm@budget", "replicates", "accuracies", "mean", "sd (pop.)",
              "spread"]
    rows = []
    for arm in sorted(spreads):
        spread = spreads[arm]
        rows.append([
            arm, str(spread.n),
            ", ".join(f"{v:.3f}" for v in spread.values) or "n/a",
            f"{spread.mean:.3f}" if spread.values else "n/a",
            "n/a (one replicate)" if spread.sd is None else f"{spread.sd:.4f}",
            "n/a (one replicate)" if spread.spread is None
            else f"{spread.spread:.4f}",
        ])
    out += _table(header, rows)
    if replicates < 3:
        out += ["", f"**{replicates} replicate(s).** One run per arm cannot "
                "separate an effect from resampling; three is the floor this "
                "harness reports against. Every difference below should be read "
                "beside the spread, not instead of it.", ""]
    else:
        out += ["", "Read every between-arm difference against the within-arm "
                "spread on this table. A gap smaller than the spread is not a "
                "result.", ""]
    return out


def _controls_section(meta: Mapping[str, Any]) -> List[str]:
    out = ["## 7. Controls", ""]
    rows = [[str(key), str(value)] for key, value in sorted(meta.items())
            if not isinstance(value, (dict, list))]
    out += _table(["control", "value"], rows)
    out += [""]
    return out


def _limits_section(built: efficiency.Curve) -> List[str]:
    """§8. What this corpus cannot show, printed whether or not it is convenient."""
    ceiling = [p for p in built.points if p.arm == "whole_corpus"]
    out = ["## 8. What this run cannot demonstrate", ""]
    if ceiling:
        tokens = max(p.max_tokens for p in ceiling)
        out += [
            f"The whole staged corpus serialises to {tokens:,} tokens under "
            f"this tokenizer, which fits in any current context window. On a "
            f"corpus that fits, 'paste everything' is a legal arm and a "
            f"context compiler is not needed — so this run can CALIBRATE the "
            f"instrument and cannot confirm the hundreds-of-millions-of-"
            f"documents claim. If the `whole_corpus` ceiling dominates the "
            f"ladder, that falsifies this corpus as evidence, not the claim.",
        ]
    else:
        out += ["The `whole_corpus` ceiling was not run, so this report cannot "
                "say whether the corpus simply fits in a context window — which "
                "on this dataset it does. Re-run with `whole_corpus` in --arms."]
    out += ["",
            "Token counts are a Qwen3-BPE proxy. The backbone returns no usage "
            "block, so absolute rung labels carry an unknown constant factor "
            "against the backbone's own tokenizer. One tokenizer is applied to "
            "every arm, so the comparison between arms is unaffected.",
            "",
            "BM25 is the retriever in two of the three arms and is never "
            "compared against Tesserae on rank. No recall@k or MRR appears in "
            "this report, deliberately.",
            ""]
    return out


def build_report(
    *,
    conversations: Sequence[Conversation],
    built: efficiency.Curve,
    scored: Optional[efficiency.Curve],
    per_rung: Mapping[int, Any],
    spreads: Mapping[str, Any],
    prompts: Sequence[Mapping[str, Any]],
    replicates: int,
    tau: float,
    meta: Mapping[str, Any],
) -> str:
    lines = [
        "# LoCoMo — tokens to a correct answer",
        "",
        "The axis is tokens, not rank. Every arm is handed the same token "
        "budget, fits its own context into it with its own knob, and answers "
        "the same questions; the request each one sent was built, counted and "
        "written to disk before it was sent.",
        "",
        f"Conversations: {', '.join(c.sample_id for c in conversations)} "
        f"({sum(len(c.questions) for c in conversations):,} questions, "
        f"{sum(len(c.sessions) for c in conversations):,} sessions).",
        "",
    ]
    if built.missing:
        lines += ["**Incomplete:**"] + [f"- {m}" for m in built.missing] + [""]
    lines += _ladder_section(built, scored)
    lines += _scalar_section(scored, tau)
    lines += _three_numbers_section(per_rung)
    lines += _free_lunch_section(scored)
    lines += _fitting_section(built, prompts)
    lines += _spread_section(spreads, replicates)
    lines += _controls_section(meta)
    lines += _limits_section(built)
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def load_graph(work: Path, conversation: str) -> Any:
    graph_path = Path(work) / conversation / ".tesserae" / "graph.json"
    if not graph_path.is_file():
        raise Skip(
            f"no compiled graph at {graph_path}",
            "this runner never compiles — compile once with "
            "`python -m evals.locomo.run --conversations "
            f"{conversation} --i-know-this-costs-money`, then point --work here",
        )
    from tesserae.project import load_graph_file

    return load_graph_file(graph_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.locomo.run_context",
        description="Accuracy at a fixed token budget, over three context arms.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK,
                        help="a directory holding <conversation>/corpus and "
                             "<conversation>/.tesserae/graph.json. Never "
                             "written to.")
    parser.add_argument("--conversations", default="conv-26")
    parser.add_argument("--arms", default=DEFAULT_ARMS,
                        help=f"comma-separated, from {', '.join(ARM_NAMES)}")
    parser.add_argument("--budgets", default=DEFAULT_BUDGETS,
                        help="the token ladder, comma-separated")
    parser.add_argument("--region-k", type=int, default=DEFAULT_REGION_K,
                        help="how many BM25-ranked sessions seed the compiled "
                             "arm. Declared, not tuned.")
    parser.add_argument("--tau", type=float, default=DEFAULT_TAU)
    parser.add_argument("--replicates", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0,
                        help="answer only the first N questions per "
                             "conversation. A smoke test, never a result.")
    parser.add_argument("--sample", type=int, default=0,
                        help=f"draw N questions per conversation with "
                             f"--sample-seed, keeping their original indices. "
                             f"Pass {PROTOCOL_SAMPLE} with the default seed to "
                             f"reproduce the question set every other agentic "
                             f"measurement on this corpus used. Unlike --limit "
                             f"this is a RESULT-grade subset, not a prefix.")
    parser.add_argument("--sample-seed", type=int, default=PROTOCOL_SEED)
    parser.add_argument("--sample-categories",
                        default=",".join(str(c) for c in PROTOCOL_CATEGORIES),
                        help="categories the draw may pick from. Category "
                             f"{ADVERSARIAL_CATEGORY} is excluded by default "
                             "because its gold answer is a refusal, which an "
                             "arm given no context scores for free.")
    parser.add_argument("--backbone", default=PROTOCOL_BACKBONE,
                        help="the answering model. Declared by "
                             "evals.locomo.adapter.PROTOCOL_BACKBONE so both "
                             "runners name one protocol; pass another "
                             "explicitly and the report records the deviation "
                             "in its controls table.")
    parser.add_argument("--judge", default="deterministic")
    parser.add_argument("--dry-run", action="store_true",
                        help="build, count and persist every prompt, then stop. "
                             "Spends nothing.")
    parser.add_argument("--score", type=Path,
                        help="re-grade a saved answers file and rebuild the "
                             "report")
    parser.add_argument("--prompts-out", type=Path)
    parser.add_argument("--answers-out", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--i-know-this-costs-money", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser


def _score_curves(answers: Sequence[Mapping[str, Any]],
                  conversations: Sequence[Conversation],
                  judge: Judge) -> tuple:
    """Grade answers and fold the verdicts back onto their token counts."""
    graded: List[GradedRow] = _grade_rows(
        [{**row, "arm": _cell_arm(str(row["arm"]), int(row["budget"]))}
         for row in answers], conversations, judge)
    scored_rows = []
    for row, verdict in zip(answers, graded):
        scored_rows.append({
            **row,
            "correct": verdict.verdict.correct,
            "score": verdict.verdict.score,
            "refused": verdict.verdict.refused,
            "errored": verdict.verdict.errored,
        })
    per_rung: Dict[int, Any] = {}
    for budget in sorted({int(r["budget"]) for r in answers}):
        rung_rows = [g for g, r in zip(graded, answers)
                     if int(r["budget"]) == budget]
        per_rung[budget] = decompose(rung_rows)
    return scored_rows, graded, per_rung


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if os.environ.get("CI"):
        print("SKIP: CI is set — the LoCoMo context arms never run in CI")
        return 0

    try:
        judge = build_judge(args.judge)
        arm_names = parse_arms(args.arms)
        budgets = parse_budgets(args.budgets)
        data = require_data(args.data)
        conversations = load_conversations(data)
        revision = dataset_revision(data)

        if args.score:
            if not args.score.is_file():
                raise Skip(f"answers file not found at {args.score}",
                           "produce one with --answers-out")
            payload = json.loads(args.score.read_text(encoding="utf-8"))
            answers = list(payload.get("rows") or [])
            saved_meta = dict(payload.get("meta") or {})
            chosen = [c for c in conversations
                      if c.sample_id in {str(r.get("conversation"))
                                         for r in answers}]
            judge.canary()
            scored_rows, graded, per_rung = _score_curves(
                answers, chosen, judge)
            built = efficiency.curve([
                {**r, "arm": _cell_arm(str(r["arm"]), int(r["budget"]))}
                for r in answers])
            scored = efficiency.curve([
                {**r, "arm": _cell_arm(str(r["arm"]), int(r["budget"]))}
                for r in scored_rows])
            text = build_report(
                conversations=chosen, built=built, scored=scored,
                per_rung=per_rung,
                spreads=replicate_spread(graded),
                prompts=[{**r, "user": ""} for r in answers],
                replicates=len({int(r.get("replicate") or 0) for r in answers}),
                tau=args.tau,
                meta={**saved_meta, **judge.config,
                      "dataset_revision": revision})
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(text)
            print(f"wrote {args.out}")
            return 0

        chosen = select_or_skip(
            conversations,
            [n.strip() for n in str(args.conversations).split(",") if n.strip()])
        if args.replicates < 1:
            raise Skip(f"--replicates {args.replicates}: a run answers each "
                       f"question at least once", "pass --replicates 3")

        controls = tokenizer_controls()

        # ---------------------------------------------- phase 1, free
        prompts: List[Dict[str, Any]] = []
        for conversation in chosen:
            bodies = staged_bodies(args.work, conversation.sample_id)
            graph = (load_graph(args.work, conversation.sample_id)
                     if {"bm25_compiled", "graph_only"} & set(arm_names)
                     else None)
            ranker = None
            if {"bm25_docs", "bm25_compiled"} & set(arm_names):
                from ..lme_mab.baselines import LexicalArm
                from .run import staged_documents

                ranker = LexicalArm(staged_documents(conversation))
            indices: Optional[List[int]] = None
            if args.sample and args.limit:
                raise Skip(
                    "--sample and --limit both subset the questions",
                    "pass one. --limit is a prefix smoke test; --sample is the "
                    "seeded draw a reported result uses",
                )
            if args.sample:
                conversation, indices = select_questions(
                    conversation, sample=args.sample, seed=args.sample_seed,
                    categories=[int(c) for c
                                in str(args.sample_categories).split(",")
                                if c.strip()])
            elif args.limit:
                conversation = replace(
                    conversation,
                    questions=list(conversation.questions)[:args.limit])
            arms = build_arms(
                arm_names, conversation=conversation.sample_id,
                system=_SYSTEM_PROMPT, documents=bodies, ranker=ranker,
                graph=graph,
                project_root=str(Path(args.work) / conversation.sample_id),
                region_k=args.region_k)
            prompts += build_prompts(conversation, arms, budgets,
                                     indices=indices)

        built = efficiency.curve([
            {**row, "arm": _cell_arm(str(row["arm"]), int(row["budget"])),
             "correct": False, "score": 0.0}
            for row in prompts])

        meta: Dict[str, Any] = {
            **controls, **judge.config,
            "dataset": str(data),
            "dataset_revision": revision,
            "work": str(args.work),
            "conversations": ",".join(c.sample_id for c in chosen),
            "arms": ",".join(arm_names),
            "budgets": ",".join(str(b) for b in budgets),
            "region_k": args.region_k,
            "compile_budget_grid": ",".join(str(b) for b in COMPILE_BUDGET_GRID),
            "replicates": args.replicates,
            "backbone": args.backbone if not args.dry_run else "",
            "framing_tokens": count_tokens(serialized_request(
                _SYSTEM_PROMPT, user_turn("", []), SCHEMA_NAME)),
            "n_prompts": len(prompts),
            "question_limit": args.limit or "none",
            "question_sample": (
                f"{args.sample} of categories {args.sample_categories}, "
                f"seed {args.sample_seed}" if args.sample else "none"),
        }

        prompts_out = args.prompts_out or (args.out.parent / "prompts.jsonl")
        write_prompts(prompts_out, prompts, meta)
        print(f"wrote {prompts_out} ({len(prompts):,} prompts)",
              file=sys.stderr)

        if args.dry_run:
            text = build_report(
                conversations=chosen, built=built, scored=None, per_rung={},
                spreads={}, prompts=prompts, replicates=args.replicates,
                tau=args.tau,
                meta={**meta, "mode": "dry-run — no LLM call was made"})
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(text)
            print(f"wrote {args.out}\nNOTHING WAS ANSWERED — this is a token "
                  f"census, not a result.")
            return 0

        # ---------------------------------------------- phase 2, spends
        calls = len(prompts) * args.replicates
        print(f"\nThis run will spend {calls:,} backbone calls plus 1 canary "
              f"({len(prompts):,} prompts x {args.replicates} replicates).")
        if not args.i_know_this_costs_money:
            print("SKIP: answering spends LLM quota\n"
                  "      re-run with --i-know-this-costs-money, or --dry-run "
                  "to build and count every prompt for free")
            return 0
        if not args.yes:
            reply = input(f"type the number of calls ({calls}) to proceed: ")
            if reply.strip() != str(calls):
                print("not confirmed — nothing was spent")
                return 0

        answer_fn = build_prompt_backbone(args.backbone)
        canary_backbone(canary_shim(answer_fn))
        judge.canary()

        answers = answer_prompts(prompts, answer_fn,
                                 replicates=args.replicates)

        # ---------------------------------------------- phase 3
        scored_rows, graded, per_rung = _score_curves(answers, chosen, judge)
        scored = efficiency.curve([
            {**r, "arm": _cell_arm(str(r["arm"]), int(r["budget"]))}
            for r in scored_rows])
        meta["evidence"] = {"answer_calls": len(answers),
                            "canary_calls": 1,
                            "llm_judge_calls": judge.llm_calls}
        answers_out = args.answers_out or (args.out.parent / "answers-context.json")
        answers_out.parent.mkdir(parents=True, exist_ok=True)
        answers_out.write_text(
            json.dumps({"meta": meta, "rows": scored_rows},
                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {answers_out}", file=sys.stderr)

        text = build_report(
            conversations=chosen, built=built, scored=scored,
            per_rung=per_rung, spreads=replicate_spread(graded),
            prompts=prompts, replicates=args.replicates, tau=args.tau,
            meta=meta)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(text)
        print(f"wrote {args.out}")
        return 0
    except Skip as skip:
        return skip.emit()
    except (DeadBackbone, DeadJudge) as dead:
        print("\n" + "!" * 72, file=sys.stderr)
        print(f"CANARY FAILED — nothing was measured.\n\n{dead}", file=sys.stderr)
        print("!" * 72, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
