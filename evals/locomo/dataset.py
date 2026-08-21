"""LoCoMo: read ``locomo10.json`` into conversations, sessions, turns and questions.

This module reads one file and computes nothing that needs a model, a clock or a
network. Everything below was **measured this session** by
``evals/locomo/dataset.py``'s own loader over
``snap-research/locomo`` at ``data/locomo10.json``::

    10 conversations   272 sessions   5,882 turns   1,986 QA pairs
    categories         {1: 282, 2: 321, 3: 96, 4: 841, 5: 446}
    turns with a BLIP caption                       1,226 of 5,882
    evidence elements                               2,815
    QA with empty evidence                          4
    malformed evidence strings                      6
    dia_ids naming a turn that does not exist       2

Per conversation, measured: 19-32 sessions, 369-689 turns, 105-260 QA.


The category integers
---------------------

``category`` is an integer 1-5 and the file says nothing about what they mean.
The mapping used here is :data:`CATEGORY_NAMES`, and it is derived rather than
guessed — the reference harness comments it directly. ``task_eval/evaluation.py``
in the published repo branches ``[2, 3, 4]`` as "single-hop, temporal,
open-domain", ``[1]`` as multi-hop and ``[5]`` as adversarial, and the counts
measured above disambiguate 2/3/4 against the paper's per-name totals. Mem0's
own harness spells the same map out as a dict.

Category 5 is the one that changes arithmetic. Measured here: 444 of its 446
entries carry **no** ``answer`` field at all, only ``adversarial_answer``, which
is the distractor rather than the gold. The gold behaviour on those questions is
to decline, so :attr:`LocomoQuestion.gold_answers` is empty for them and
:data:`JUDGED_CATEGORIES` excludes them from the answer-scoring denominator —
which is what makes that denominator 1,540 rather than 1,986. Both numbers are
printed by the report; neither is allowed to stand alone.


What counts as the corpus
-------------------------

**Dialogue text plus every BLIP caption, and nothing else.** The decision is
frozen here because the reference code contains two paths that disagree about
it: ``get_conversation_lengths`` gates captions on an ``img_file`` key that this
release of the data does not carry (so it counts zero), while
``prepare_for_rag`` counts all of them. Measured on this file, 1,226 of 5,882
turns carry a caption, so the two paths differ over a fifth of the corpus.

Never ingested, on the same freeze: ``img_url``, ``query``, ``observation``,
``session_summary`` and ``event_summary``. ``query`` is the annotator's image
search string — it is not something either speaker said, and a gold answer that
is reachable only through it is a gold answer no memory system can legitimately
retrieve.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

#: The published category map. Read off ``task_eval/evaluation.py``'s own
#: branch comments and corroborated by Mem0's ``CATEGORY_NAMES``; never inferred
#: from the counts alone, because two categories of similar size would then be
#: interchangeable.
CATEGORY_NAMES: Dict[int, str] = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}

#: The adversarial class. Its gold behaviour is ABSTENTION, which is the same
#: predicate ``evals.qa.scorer.is_refusal`` uses to detect a system that failed
#: to answer — so a dead backbone returning "" scores this category perfectly.
#: It is held out of every refusal decomposition for that reason, and scored
#: only beside an answerable control. See :mod:`evals.locomo.scoring`.
ADVERSARIAL_CATEGORY = 5

#: The categories an LLM-judge protocol scores. Measured: 1,540 of the 1,986
#: questions. This is not a convenience subset — the reference grader skips any
#: question with no ground truth, and 444 of the 446 adversarial questions have
#: none, so the published denominator is this one whether or not a harness says
#: so out loud.
JUDGED_CATEGORIES: Tuple[int, ...] = (1, 2, 3, 4)

#: ``category`` 3's gold is a ``;``-separated list and the reference harness
#: scores only the FIRST clause. Carried as a flag rather than applied at load
#: time: two ways of scoring the same 96 questions give two different numbers,
#: and the run declares which one it used.
SEMICOLON_GOLD_CATEGORY = 3

#: ``D<session>:<turn>``. Six evidence strings in this file are not exactly one
#: of these — measured — so evidence is parsed with ``findall`` and the leftovers
#: are counted rather than dropped silently. See :func:`parse_dia_ids`.
_DIA_ID = re.compile(r"D(\d+):(\d+)")

_SESSION_KEY = re.compile(r"^session_(\d+)$")


def parse_dia_ids(evidence: Any) -> List[Tuple[int, int]]:
    """Every ``D<session>:<turn>`` pair in one evidence element, in order.

    ``findall`` rather than a full-string match, because the annotations are not
    uniform and the failures are enumerable rather than systematic. Measured this
    phase, six of the 2,815 evidence elements are malformed: two ids joined by
    ``"; "``, three joined by spaces, one written ``D:11:26``, and one that is
    the bare string ``"D"``. This recovers four of the six.

    The other two recover NOTHING, deliberately. ``"D"`` names no turn at all,
    and ``"D:11:26"`` could be turn 26 of session 11 or turn 126 of session 1 —
    a parser that picked one would be repairing an answer key by guessing, and
    the guess would be indistinguishable from an annotation in the report.
    :func:`evals.locomo.retrieval.align_gold` counts both as unparseable.
    """
    return [(int(a), int(b)) for a, b in _DIA_ID.findall(str(evidence or ""))]


def is_malformed_evidence(evidence: Any) -> bool:
    """True when an evidence element is not exactly one ``D<n>:<t>`` and nothing else.

    Counted into the report. An annotation this harness had to repair is a fact
    about the answer key, and a benchmark that repairs one quietly has changed
    the key without saying so.
    """
    text = str(evidence or "").strip()
    return not bool(re.fullmatch(r"D\d+:\d+", text))


@dataclass(frozen=True)
class Turn:
    """One utterance. The evidence unit — gold is annotated per turn."""

    dia_id: str
    session: int
    speaker: str
    text: str
    #: The BLIP caption of an image shared in this turn, or ``""``. Part of the
    #: corpus by the freeze in the module docstring: measured, 1,226 of 5,882
    #: turns carry one, and dropping them removes text some gold answers are
    #: only reachable through.
    blip_caption: str = ""

    def render(self) -> str:
        """The turn as the corpus states it.

        Follows the published render — ``<speaker> said, "<text>" and shared
        <caption>`` — with the ``dia_id`` prefixed. The id is the corpus's own
        identifier for the turn and not part of the answer key: a question never
        contains one, so no lexical lane can match on it, and carrying it means
        a turn-level retrieval arm can be added later without re-staging.
        """
        body = f'{self.speaker} said, "{self.text}"'
        if self.blip_caption:
            body += f" and shared {self.blip_caption}"
        return f"[{self.dia_id}] {body}"


@dataclass(frozen=True)
class LocomoSession:
    """One session — the STAGING unit, and one document per session.

    Chosen over the turn and over the whole conversation, and the arithmetic is
    the argument. Measured this phase: a session holds 2,922 characters of turn
    text on average (median 2,670.5, max 6,008, min 1,179) and stages to a 3,553
    character document; a turn holds ~124; and a whole conversation staged as
    one file would be 57,807 to 116,077 — ALL TEN over
    ``tesserae.llm_chunking.CHUNK_CHAR_BUDGET`` (48,000), so a per-conversation
    document would be split by the compiler and lose the ``source_path``
    provenance every retrieval score here is computed from.

    ``number`` is the session's own number in the file (``session_1`` is 1), not
    a zero-based position. That is what makes gold alignment a dictionary
    lookup: a ``dia_id`` of ``D1:3`` names session 1 directly. Asserted over all
    5,882 turns of this file, the ``D<n>`` prefix always equals the ``session_n``
    key the turn lives under — zero violations.
    """

    number: int
    #: ``"1:56 pm on 8 May, 2023"``, verbatim from ``session_<n>_date_time``, or
    #: ``""`` when the file carried none. Never a clock reading.
    date: str
    turns: Sequence[Turn]

    @property
    def chars(self) -> int:
        return sum(len(t.text) + len(t.blip_caption) for t in self.turns)


@dataclass(frozen=True)
class LocomoQuestion:
    """One QA pair, with its category and its gold evidence turns."""

    question: str
    category: int
    #: The raw ``evidence`` list, unparsed. Resolution happens in
    #: :func:`evals.locomo.retrieval.align_gold`, which counts what it could not
    #: resolve; parsing here would throw that count away.
    evidence: Sequence[str]
    conversation: str
    #: ``answer`` verbatim, or ``None`` when the file carried none. Measured:
    #: 444 of the 446 adversarial questions carry none.
    answer: Optional[str] = None
    #: ``adversarial_answer`` — the DISTRACTOR, not the gold. Carried so a
    #: two-way multiple-choice variant of the adversarial stratum can be built
    #: later; never scored as a gold answer.
    adversarial_answer: Optional[str] = None

    @property
    def category_name(self) -> str:
        return CATEGORY_NAMES.get(self.category, f"category-{self.category}")

    @property
    def is_adversarial(self) -> bool:
        return self.category == ADVERSARIAL_CATEGORY

    def gold_answers(self, *, split_semicolon: bool = False) -> List[str]:
        """The accepted gold strings. Empty for an adversarial question.

        Empty rather than ``["None"]``: ``evals.qa.scorer`` reads an empty gold
        list as UNANSWERABLE, which is exactly what an adversarial question is —
        the correct response is to decline. Scoring the literal string "None" as
        the gold would give credit to a system that answered the word "none" and
        none to a system that correctly said it did not know.

        ``split_semicolon`` reproduces the reference harness's category-3 rule
        (first clause only). Off by default because it discards gold the
        annotator wrote; the run declares which rule it used, and the two give
        two different numbers over the same 96 questions.
        """
        if self.is_adversarial or self.answer is None:
            return []
        text = str(self.answer)
        if split_semicolon and self.category == SEMICOLON_GOLD_CATEGORY:
            return [text.split(";")[0].strip()]
        return [text]


@dataclass(frozen=True)
class Conversation:
    """One of the ten conversations: an isolated corpus and its questions.

    Isolated is not a stylistic choice. Speaker names repeat across
    conversations in this file, so a question about one conversation's speaker
    could retrieve another conversation's turns about a different person of the
    same name, and nothing in a reported score would show it. Each conversation
    therefore compiles into its own project directory — see
    :class:`evals.locomo.adapter.LocomoMemory`.
    """

    sample_id: str
    speaker_a: str
    speaker_b: str
    sessions: Sequence[LocomoSession]
    questions: Sequence[LocomoQuestion]

    @property
    def session_numbers(self) -> List[int]:
        return [s.number for s in self.sessions]

    def session(self, number: int) -> Optional[LocomoSession]:
        for candidate in self.sessions:
            if candidate.number == number:
                return candidate
        return None

    @property
    def n_turns(self) -> int:
        return sum(len(s.turns) for s in self.sessions)

    @property
    def chars(self) -> int:
        return sum(s.chars for s in self.sessions)

    def turn_ids(self) -> Dict[str, Turn]:
        """Every turn by ``dia_id``. The gold-alignment lookup table."""
        return {t.dia_id: t for s in self.sessions for t in s.turns}


def _sessions_of(payload: Mapping[str, Any]) -> List[LocomoSession]:
    conversation = payload.get("conversation") or {}
    sessions: List[LocomoSession] = []
    for key, value in conversation.items():
        match = _SESSION_KEY.match(str(key))
        # A ``session_<n>_date_time`` key can exist with no ``session_<n>``
        # beside it — measured on conv-26, which carries date stamps up to
        # session 35 and dialogue only to 19. Keying off the DIALOGUE list is
        # what keeps a session count from counting sessions that do not exist.
        if not match or not isinstance(value, list):
            continue
        number = int(match.group(1))
        date = str(conversation.get(f"session_{number}_date_time") or "")
        turns = [
            Turn(
                dia_id=str(turn.get("dia_id") or ""),
                session=number,
                speaker=str(turn.get("speaker") or ""),
                text=str(turn.get("text") or ""),
                blip_caption=str(turn.get("blip_caption") or ""),
            )
            for turn in value
            if isinstance(turn, dict)
        ]
        sessions.append(LocomoSession(number=number, date=date, turns=turns))
    return sorted(sessions, key=lambda s: s.number)


def load_conversations(path: Path) -> List[Conversation]:
    """Read ``locomo10.json``. Raises rather than returning an empty list.

    "Zero conversations" is indistinguishable from "wrong file" at the call
    site, and a benchmark that silently scores nothing is worse than one that
    stops.
    """
    payloads = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payloads, list) or not payloads:
        raise ValueError(
            f"{path} does not hold a list of conversations — is this "
            f"data/locomo10.json from snap-research/locomo?"
        )
    conversations: List[Conversation] = []
    for payload in payloads:
        conversation = payload.get("conversation") or {}
        sample_id = str(payload.get("sample_id") or "")
        questions = [
            LocomoQuestion(
                question=str(qa.get("question") or ""),
                category=int(qa.get("category") or 0),
                evidence=[str(e) for e in (qa.get("evidence") or [])],
                conversation=sample_id,
                answer=(None if qa.get("answer") is None else str(qa.get("answer"))),
                adversarial_answer=(
                    None if qa.get("adversarial_answer") is None
                    else str(qa.get("adversarial_answer"))
                ),
            )
            for qa in (payload.get("qa") or [])
        ]
        conversations.append(Conversation(
            sample_id=sample_id,
            speaker_a=str(conversation.get("speaker_a") or ""),
            speaker_b=str(conversation.get("speaker_b") or ""),
            sessions=_sessions_of(payload),
            questions=questions,
        ))
    if not any(c.sessions for c in conversations):
        raise ValueError(
            f"{path} parsed but no conversation held a session_<n> dialogue "
            f"list — the file's shape is not the one this loader was written "
            f"against"
        )
    return conversations


def select_conversations(
    conversations: Sequence[Conversation], names: Optional[Sequence[str]]
) -> List[Conversation]:
    """``--conversations`` as a list, in file order, or all of them.

    An unknown name refuses rather than being ignored: silently dropping
    ``--conversations conv-99`` would print a report about a subset nobody
    asked for.
    """
    if not names:
        return list(conversations)
    wanted = {str(n).strip() for n in names if str(n).strip()}
    by_id = {c.sample_id: c for c in conversations}
    missing = sorted(n for n in wanted if n not in by_id)
    if missing:
        raise KeyError(
            f"no such conversation(s): {', '.join(missing)} — this file holds "
            f"{', '.join(sorted(by_id))}"
        )
    return [c for c in conversations if c.sample_id in wanted]


def dataset_revision(path: Path) -> str:
    """``sha256:<12 hex>`` of the dataset file itself.

    A declared revision must be an OBSERVATION of the bytes that were read, not
    a version string somebody typed. A git tag names a checkout; this names the
    file, which is the thing that decides every number in the report.
    """
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return f"sha256:{digest[:12]}"


def iter_questions(conversations: Sequence[Conversation]) -> Iterator[LocomoQuestion]:
    for conversation in conversations:
        yield from conversation.questions


def category_counts(conversations: Sequence[Conversation]) -> Dict[int, int]:
    counts: Dict[int, int] = {}
    for question in iter_questions(conversations):
        counts[question.category] = counts.get(question.category, 0) + 1
    return dict(sorted(counts.items()))


__all__ = [
    "ADVERSARIAL_CATEGORY",
    "CATEGORY_NAMES",
    "JUDGED_CATEGORIES",
    "SEMICOLON_GOLD_CATEGORY",
    "Conversation",
    "LocomoQuestion",
    "LocomoSession",
    "Turn",
    "category_counts",
    "dataset_revision",
    "is_malformed_evidence",
    "iter_questions",
    "load_conversations",
    "parse_dia_ids",
    "select_conversations",
]
