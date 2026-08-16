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

from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

#: The split holding the LongMemEval-derived groups, and the marker in each
#: record's ``metadata.source`` that identifies them. ``Accurate_Retrieval``
#: also carries ruler_* and eventqa_* records, which are NOT this benchmark.
SPLIT = "Accurate_Retrieval"
SOURCE_MARKER = "longmemeval"


@dataclass(frozen=True)
class MabGroup:
    """One shared haystack plus the questions asked against it."""

    index: int
    source: str
    context: str
    questions: Sequence[str]
    answers: Sequence[Sequence[str]]

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
        source = str((row.get("metadata") or {}).get("source", ""))
        if SOURCE_MARKER not in source.lower():
            continue
        groups.append(
            MabGroup(
                index=len(groups),
                source=source,
                context=row.get("context") or "",
                questions=list(row.get("questions") or []),
                answers=[list(a) for a in (row.get("answers") or [])],
            )
        )
    if not groups:
        raise ValueError(
            f"no records with source containing {SOURCE_MARKER!r} in "
            f"{parquet_path} — is this the {SPLIT} split of "
            f"ai-hyz/MemoryAgentBench?"
        )
    return groups
