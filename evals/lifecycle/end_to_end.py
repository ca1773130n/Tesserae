"""Stage 3 — end to end: knowledge-update and abstention questions answered
from memory the lifecycle built, graded by LongMemEval's own rubric.

Per LongMemEval question (``knowledge-update`` ∪ ``_abs``, 102 of 500):

1. **Memory.** ``--memory lifecycle`` runs Tesserae's session extractor over
   each haystack session (date order), mints the findings as Insight nodes and
   runs the supersede pass; superseded findings are dropped. ``--memory raw``
   is the control — the haystack turns themselves, no extraction, no update —
   so a lifecycle number always has the thing it is supposed to beat beside it.
2. **Retrieval.** Every surviving item when ``--top-k`` is unset (right for the
   ``oracle`` variant, whose haystack is only the evidence sessions);
   otherwise the ``top_k`` items with the most question content tokens.
3. **Answer** with the backbone, under :data:`~evals.lifecycle.llm.READER_SYSTEM`.
4. **Grade** with LongMemEval's ``get_anscheck_prompt`` text, verbatim (pinned
   below), and its own parse: correct iff ``"yes"`` is in the reply.

The backbone builds memory AND answers, and the judge is a different, named
model — the result file records both, the rubric's source revision and the
dataset revision, so two results are comparable only when that block matches.

FactConsolidation QA (``--fc-source``) reuses the same arms over numbered
facts: ``lifecycle`` keeps only facts the supersede pass left live, ``raw``
keeps all of them. It is graded by normalised substring match against any gold
alias (MemoryAgentBench's SubEM), with no judge.
"""

from __future__ import annotations

import logging
import re
import string
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .datasets import FactGroup, LmeQuestion
from .extraction import content_tokens
from .llm import QA_JUDGE_SYSTEM, READER_SYSTEM
from .update import UpdateCase, factconsolidation_case, supersede_edges

logger = logging.getLogger(__name__)

#: ``src/evaluation/evaluate_qa.py::get_anscheck_prompt`` in
#: github.com/xiaowu0162/LongMemEval at this commit, copied verbatim.
LME_RUBRIC_SOURCE = "xiaowu0162/LongMemEval@9e0b455f4ef0e2ab8f2e582289761153549043fc src/evaluation/evaluate_qa.py"

LME_KU_TEMPLATE = (
    "I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
    "response contains the correct answer. Otherwise, answer no. If the response contains some previous "
    "information along with an updated answer, the response should be considered as correct as long as the "
    "updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)
LME_DEFAULT_TEMPLATE = (
    "I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
    "response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct "
    "answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If "
    "the response only contains a subset of the information required by the answer, answer no. \n\n"
    "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or "
    "no only."
)
LME_TEMPORAL_TEMPLATE = (
    "I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
    "response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct "
    "answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If "
    "the response only contains a subset of the information required by the answer, answer no. In addition, "
    "do not penalize off-by-one errors for the number of days. If the question asks for the number of "
    "days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer "
    "is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: "
    "{}\n\nIs the model response correct? Answer yes or no only."
)
LME_ABSTENTION_TEMPLATE = (
    "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes "
    "if the model correctly identifies the question as unanswerable. The model could say that the information "
    "is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\n"
    "Explanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? "
    "Answer yes or no only."
)


def rubric_prompt(q: LmeQuestion, response: str) -> str:
    """The grader prompt LongMemEval would send for ``q``. Abstention wins over
    the question type, as it does upstream (``'_abs' in question_id``)."""
    if q.is_abstention:
        template = LME_ABSTENTION_TEMPLATE
    elif q.question_type == "knowledge-update":
        template = LME_KU_TEMPLATE
    elif q.question_type == "temporal-reasoning":
        template = LME_TEMPORAL_TEMPLATE
    elif q.question_type in ("single-session-user", "single-session-assistant", "multi-session"):
        template = LME_DEFAULT_TEMPLATE
    else:
        raise NotImplementedError(f"no LongMemEval rubric for {q.question_type!r} in this harness")
    return template.format(q.question, q.answer, response)


def _lme_date(text: str) -> datetime:
    """``2023/05/30 (Tue) 04:10`` → datetime. Raises on any other shape: the
    date is the update order, and a guessed one silently reorders memory."""
    return datetime.strptime(re.sub(r"\s*\([A-Za-z]{3}\)\s*", " ", text).strip(), "%Y/%m/%d %H:%M")


# ---------------------------------------------------------------------------
# Memory arms
# ---------------------------------------------------------------------------


def raw_memory(q: LmeQuestion) -> List[Tuple[str, str]]:
    items = []
    for date, turns in sorted(q.sessions, key=lambda s: _lme_date(s[0])):
        for turn in turns:
            if turn["text"].strip():
                items.append((date, f"{turn['role']}: {turn['text']}"))
    return items


def lifecycle_memory(q: LmeQuestion, extractor: Any, update_client: Any = None) -> Tuple[List[Tuple[str, str]], dict]:
    """Extract → supersede → keep the live findings, dated by their session."""
    from tesserae.harness_sessions import HarnessSession
    from tesserae.session_graph_llm import extract_with_llm

    nodes: List[Tuple[str, str, str]] = []
    dates: Dict[str, str] = {}
    stats: dict = {}
    ordered = sorted(q.sessions, key=lambda s: _lme_date(s[0]))
    for rank, (date, turns) in enumerate(ordered):
        hs = HarnessSession(
            id=f"lme-{q.question_id}-{rank}", slug=f"lme-{q.question_id}-{rank}",
            harness="lifecycle-eval", agent_label="LongMemEval", project_name="lifecycle-eval",
            project_root="", started_at=date,
        )
        findings = extract_with_llm(hs, list(turns), [], extractor, cache_key=None, stats=stats)
        for k, f in enumerate(findings):
            nid = f"q{rank:03d}-{k:03d}"
            nodes.append((nid, f.body, f"{rank:04d}"))
            dates[nid] = date
    edges = supersede_edges(UpdateCase(unit=q.question_id, nodes=nodes, gold=[]), update_client)
    retired = {older for _, older in edges}
    live = [(dates[nid], text) for nid, text, _ in nodes if nid not in retired]
    return live, {"findings": len(nodes), "retired": len(retired), **stats}


def retrieve(items: Sequence[Tuple[str, str]], question: str, top_k: Optional[int]) -> List[Tuple[str, str]]:
    if top_k is None or len(items) <= top_k:
        return list(items)
    qtoks = content_tokens(question)
    scored = sorted(range(len(items)), key=lambda i: (-len(qtoks & content_tokens(items[i][1])), i))
    keep = sorted(scored[:top_k])
    return [items[i] for i in keep]


def reader_prompt(question: str, question_date: str, items: Sequence[Tuple[str, str]]) -> str:
    body = "\n".join(f"[{date}] {text}" for date, text in items) or "(no memories)"
    return f"Memories:\n{body}\n\nCurrent date: {question_date}\nQuestion: {question}\nAnswer:"


def run_once_lme(
    questions: Sequence[LmeQuestion],
    *,
    reader: Any,
    judge: Any,
    memory: str,
    extractor: Any = None,
    update_client: Any = None,
    top_k: Optional[int] = None,
) -> dict:
    rows: List[dict] = []
    labels: Dict[str, str] = {}
    excluded: List[dict] = []
    for q in questions:
        if memory == "raw":
            items, mem_stats = raw_memory(q), {}
        elif memory == "lifecycle":
            items, mem_stats = lifecycle_memory(q, extractor, update_client)
        else:
            raise ValueError(f"unknown memory arm {memory!r}")
        context = retrieve(items, q.question, top_k)
        response = reader.complete_text(system=READER_SYSTEM,
                                        user=reader_prompt(q.question, q.question_date, context))
        if response is None:
            excluded.append({"question_id": q.question_id, "reason": "reader call failed"})
            logger.warning("e2e: reader failed on %s; excluded", q.question_id)
            continue
        verdict = judge.complete_text(system=QA_JUDGE_SYSTEM, user=rubric_prompt(q, response))
        if verdict is None:
            excluded.append({"question_id": q.question_id, "reason": "judge call failed"})
            logger.warning("e2e: judge failed on %s; excluded", q.question_id)
            continue
        correct = "yes" in verdict.lower()
        labels[q.question_id] = "yes" if correct else "no"
        rows.append({"question_id": q.question_id, "subsets": list(q.subsets),
                     "memory_items": len(items), "context_items": len(context),
                     "correct": correct, "response": response[:300], **mem_stats})
    return {"metrics": _lme_metrics(rows), "rows": rows, "labels": labels, "excluded": excluded,
            "unit_rates": None}


def _lme_metrics(rows: Sequence[dict]) -> dict:
    out: Dict[str, Any] = {"questions": len(rows)}
    for subset in ("knowledge-update", "abstention"):
        hits = [r["correct"] for r in rows if subset in r["subsets"]]
        out[f"{subset}_n"] = len(hits)
        out[f"{subset}_accuracy"] = round(sum(hits) / len(hits), 4) if hits else None
    out["accuracy"] = round(sum(r["correct"] for r in rows) / len(rows), 4) if rows else None
    return out


# ---------------------------------------------------------------------------
# FactConsolidation QA
# ---------------------------------------------------------------------------

_ARTICLES = re.compile(r"\b(a|an|the)\b")


def normalize_answer(text: str) -> str:
    text = text.lower()
    text = "".join(ch for ch in text if ch not in set(string.punctuation))
    return " ".join(_ARTICLES.sub(" ", text).split())


def substring_match(response: str, golds: Sequence[str]) -> bool:
    got = normalize_answer(response)
    return any(normalize_answer(g) and normalize_answer(g) in got for g in golds)


FC_READER_PREFIX = (
    "Facts are numbered; a larger number is a newer fact and overrides an older one about the same thing. "
    "Answer with the entity name only."
)


def run_once_fc(group: FactGroup, *, reader: Any, memory: str, update_client: Any = None,
                limit: Optional[int] = None) -> dict:
    if memory == "raw":
        live = list(range(len(group.facts)))
        retired_n = 0
    elif memory == "lifecycle":
        edges = supersede_edges(factconsolidation_case(group), update_client)
        retired = {int(older[1:]) for _, older in edges}
        live = [i for i in range(len(group.facts)) if i not in retired]
        retired_n = len(retired)
    else:
        raise ValueError(f"unknown memory arm {memory!r}")
    facts_block = "\n".join(f"{i}. {group.facts[i]}" for i in live)
    rows, labels, excluded = [], {}, []
    pairs = list(zip(group.questions, group.answers))[:limit]
    for n, (question, golds) in enumerate(pairs):
        response = reader.complete_text(
            system=READER_SYSTEM,
            user=f"{FC_READER_PREFIX}\n\n{facts_block}\n\nQuestion: {question}\nAnswer:",
        )
        qid = f"{group.source}:{n}"
        if response is None:
            excluded.append({"question_id": qid, "reason": "reader call failed"})
            logger.warning("fc-qa: reader failed on %s; excluded", qid)
            continue
        correct = substring_match(response, golds)
        labels[qid] = "yes" if correct else "no"
        rows.append({"question_id": qid, "correct": correct, "response": response[:200]})
    accuracy = round(sum(r["correct"] for r in rows) / len(rows), 4) if rows else None
    return {"metrics": {"source": group.source, "hops": group.hops, "questions": len(rows),
                        "facts_live": len(live), "facts_retired": retired_n, "accuracy": accuracy},
            "rows": rows, "labels": labels, "excluded": excluded, "unit_rates": None}
