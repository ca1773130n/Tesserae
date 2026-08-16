"""LongMemEval-MAB: load the five shared-haystack groups.

MemoryAgentBench reformulates LongMemEval(S) so memory is built ONCE over an
extended dialogue and then queried repeatedly, instead of rebuilding a haystack
per question. That reformulation is what published baselines are scored on, so
it is the unit this module hands back — not raw LongMemEval instances.

Measured on the real parquet (``ai-hyz/MemoryAgentBench``, split
``Accurate_Retrieval``, source ``longmemeval_s*``):

    group 0  1,600,183 chars  60 questions
    group 1  1,589,693 chars  60 questions
    group 2  1,715,268 chars  60 questions
    group 3  1,588,305 chars  60 questions
    group 4  1,646,919 chars  60 questions
    -------------------------------------
    total    8,140,368 chars (~2.04M tokens), 300 questions

Those group sizes matter and are easy to get wrong: LongMemEval_S is ~115k
tokens per INSTANCE, but MAB concatenates instances into shared haystacks, so a
group is ~400k tokens — roughly 3.5x what the per-instance figure suggests.
Sizing an ingest budget off the wrong one under-counts by that factor.

This module reads only. It never downloads, never compiles and never calls a
model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Sequence

#: The split holding the LongMemEval-derived groups, and the marker in each
#: record's ``metadata.source`` that identifies them. ``Accurate_Retrieval``
#: also carries ruler_* and eventqa_* records, which are NOT this benchmark.
SPLIT = "Accurate_Retrieval"
SOURCE_MARKER = "longmemeval"


@dataclass(frozen=True)
class MabGroup:
    """One shared haystack plus the questions asked against it.

    ``context`` and ``haystack_sessions`` are two views of the SAME dialogue and
    neither is complete on its own — measured on the real parquet, not assumed:

    * ``context`` is the ``repr`` of a flat Python list that alternates
      ``'Chat Time: 2022/11/17 (Thu) 12:04'`` with a list of turn dicts, so it
      is the only view carrying **session dates**;
    * ``metadata.haystack_sessions`` is already parsed — a list per question, of
      sessions, of ``{role, content, has_answer}`` turns — but carries **no
      date at all**, and ``has_answer`` marks the gold evidence turn, which no
      retrieval path may read.

    The two views agree on the dialogue — the haystack's turn text is 96.7% of
    the context's characters, the remainder being the ``repr`` scaffolding and
    the date headers — and disagree about everything else. Measured through
    :func:`evals.lme_mab.adapter.split_sessions`, ``context`` yields
    111/107/116/**111**/**110** sessions for groups 0-4 against the flattened
    haystack's 111/107/116/**112**/**113**: a question's session slices overlap
    and repeat, so the flattened count is not a session count. Nor do the two
    agree on ORDER where they agree on count. **Never bridge them by position**
    — :func:`evals.lme_mab.retrieval.align_gold` matches on content signature,
    and the README's §"What the parquet actually holds" carries the full table.

    ``split_sessions`` prefers ``context`` because it is the only view with
    dates, and the benchmark's ``temporal-reasoning`` stratum is unanswerable
    without them.
    """

    index: int
    source: str
    context: str
    questions: Sequence[str]
    answers: Sequence[Sequence[str]]
    #: ``metadata.haystack_sessions`` verbatim: sessions grouped per question.
    #: Empty when the parquet did not carry it — the adapter then has only the
    #: ``context`` view, and says so.
    haystack_sessions: Sequence[Sequence[Sequence[Mapping[str, Any]]]] = field(default_factory=tuple)
    #: ``metadata.question_types`` — ``multi-session``, ``knowledge-update``,
    #: ``temporal-reasoning``, ``single-session-user`` and friends. Carried
    #: because they are the benchmark's own strata, and an aggregate that hides
    #: which KIND of question failed says almost nothing about a memory system.
    question_types: Sequence[str] = field(default_factory=tuple)

    @property
    def approx_tokens(self) -> int:
        """Rough token count for budgeting. Chars/4 — deliberately crude, and
        never used for anything but printing an estimate."""
        return len(self.context) // 4


def load_groups(parquet_path: Path) -> List[MabGroup]:
    """Read the LongMemEval groups from a MemoryAgentBench parquet.

    Raises rather than returning an empty list when the file holds no matching
    records: "zero groups" is indistinguishable from "wrong file" at the call
    site, and a benchmark that silently scores nothing is worse than one that
    stops.
    """
    import pyarrow.parquet as pq  # optional dep; only this module needs it

    rows = pq.read_table(str(parquet_path)).to_pylist()
    groups: List[MabGroup] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        source = str(metadata.get("source", ""))
        if SOURCE_MARKER not in source.lower():
            continue
        groups.append(
            MabGroup(
                index=len(groups),
                source=source,
                context=row.get("context") or "",
                questions=list(row.get("questions") or []),
                answers=[list(a) for a in (row.get("answers") or [])],
                haystack_sessions=[
                    [[dict(turn) for turn in session] for session in per_question]
                    for per_question in (metadata.get("haystack_sessions") or [])
                ],
                question_types=[str(t) for t in (metadata.get("question_types") or [])],
            )
        )
    if not groups:
        raise ValueError(
            f"no records with source containing {SOURCE_MARKER!r} in "
            f"{parquet_path} — is this the {SPLIT} split of "
            f"ai-hyz/MemoryAgentBench?"
        )
    return groups
