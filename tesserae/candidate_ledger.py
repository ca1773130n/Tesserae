"""The candidate ledger — a human's "no, these are different" survives the recompile.

:meth:`tesserae.canonicalization.GraphCanonicalizer._build_review_items` and its
embedding sibling re-derive the same candidate pairs from the same graph every
time they run, and :meth:`~tesserae.canonicalization.ReviewQueue.apply_decisions`
consumes verdicts that had nowhere durable to live. So a reviewer who answered
"these are different" was asked the identical question on the next run, forever,
and the queue's length tracked corpus size rather than unresolved work.

This module is the third state that fixes it: every candidate pair carries a
``pending`` / ``confirmed`` / ``rejected`` verdict in ``.tesserae/`` beside
``discovered_links.json``, whose accumulating-overlay shape this borrows.

Three decisions here have a tempting wrong answer, so all three are stated.

* **ACCUMULATED, never pruned — the opposite of the merge ledger.**
  :func:`tesserae.merge_ledger.publish_merge_ledger` validates its records
  against the published graph and drops what no longer applies, because a merge
  is derived state re-computed every compile. A verdict is not: it is the one
  thing in the pipeline that a machine cannot re-derive. If a rejected pair fell
  out of the ledger the moment it stopped being surfaced — a score dipping under
  the threshold for one run, a block cap truncating it, a source temporarily
  absent — it would come back **un-rejected** the moment it reappeared, and the
  human would be asked again. That is the precise failure this exists to
  prevent, so nothing is ever removed here.

* **Keyed on the sorted node-id pair, and on nothing else.** Node ids are
  :func:`tesserae.research_graph.stable_id`, a digest of ``(type, name)`` only —
  so an edited description, a new source file, a changed edge or a re-run with a
  different embedding backend all leave the key untouched, and the verdict
  survives them. A RENAME does change the key, and should: "Adam" and "AdamW"
  answered for is not an answer about "Adam" and "Lion". Score, reason and
  backend are deliberately NOT part of the key — they are exactly the churn a
  verdict must outlive.

* **No auto-merge band, at any threshold.** Only the ``pending`` third state is
  borrowed from agent-memory's 0.95 / 0.85 policy; the auto-merge band is
  refused, because ``canonicalization._build_embedding_review_items`` carries
  the measurement that kills it — 'Edwin Aldrin'~'Buzz Aldrin' 0.665, the one
  TRUE merge, sits BELOW 'GPT-4'~'GPT-3' 0.959 and 'Llama 2'~'Llama 3' 0.957.
  There is no threshold to tune. A ``confirmed`` row is a human's verdict
  recorded, and it is still :meth:`ReviewQueue.apply_decisions` that merges.

``score`` is the score the pair was FIRST seen at and is never rewritten while
pending. Drift is therefore measured against the original observation rather
than against last run's, and an unchanged corpus rewrites the file to identical
bytes instead of jittering with every backend nudge.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: Bumped only when the record shape changes. A reader that does not recognise
#: the version treats the ledger as absent rather than guessing at its fields.
CANDIDATE_LEDGER_SCHEMA_VERSION = 1

#: Sidecar filename under ``.tesserae/``.
CANDIDATE_LEDGER_FILENAME = "candidate-same-as.json"

#: The three states. ``pending`` is the borrowed one: an unanswered question,
#: distinguishable from both "a human said yes" and "a human said no".
STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"

CANDIDATE_STATUSES = frozenset({STATUS_PENDING, STATUS_CONFIRMED, STATUS_REJECTED})

#: Which candidate pass surfaced the pair. Named after the signal rather than
#: after the method, so a reader can tell a shared-token pair from an embedding
#: neighbour without reading canonicalization.py.
SOURCE_TOKEN = "token"
SOURCE_EMBEDDING = "embedding"

CANDIDATE_SOURCES = frozenset({SOURCE_TOKEN, SOURCE_EMBEDDING})


def pair_key(a: str, b: str) -> Tuple[str, str]:
    """Canonical ``(a, b)`` for a pair, order-independent.

    The two review passes emit their endpoints in different orders (the token
    pass by inverted-index position, the embedding pass by cosine rank), so a
    key that carried emission order would file one pair under two names and
    remember a verdict for only one of them.
    """
    left, right = str(a), str(b)
    return (left, right) if left <= right else (right, left)


@dataclass(frozen=True)
class CandidateVerdict:
    """One candidate pair and what a human has (or has not) said about it."""

    a: str
    b: str
    score: float
    source: str
    status: str = STATUS_PENDING
    #: Written by whoever applies the decision, never minted here. An
    #: unattributed verdict records the empty string rather than a guessed
    #: identity: "we do not know who decided this" and "$USER decided this" are
    #: different claims and must not be collapsed.
    decided_by: str = ""
    decided_at: str = ""
    #: Stamped when the verdict is recorded, so a bad release is attributable
    #: after the fact. Absent on a pending row, which is an observation rather
    #: than a decision.
    tesserae_version: str = ""

    @property
    def key(self) -> Tuple[str, str]:
        return pair_key(self.a, self.b)

    @property
    def decided(self) -> bool:
        return self.status in (STATUS_CONFIRMED, STATUS_REJECTED)

    def as_json(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["score"] = round(float(self.score), 4)
        return payload


class CandidateLedger:
    """In-memory view of ``.tesserae/candidate-same-as.json``.

    Empty when the sidecar is absent or unreadable: a missing ledger means "no
    verdict has ever been recorded for this project", which must read as "every
    pair is still open", never as an error on a review path.
    """

    def __init__(self, records: Iterable[CandidateVerdict] = ()) -> None:
        self._by_pair: Dict[Tuple[str, str], CandidateVerdict] = {}
        for record in records:
            if not record.a or not record.b or record.a == record.b:
                continue  # a pair of one is not a pair; same rule the reader applies
            # First record for a pair wins, which is how :func:`merge_verdicts`
            # expresses "the stored human verdict beats a fresh observation"
            # simply by putting the stored records first.
            self._by_pair.setdefault(record.key, record)

    def __len__(self) -> int:
        return len(self._by_pair)

    def __bool__(self) -> bool:
        return bool(self._by_pair)

    @property
    def records(self) -> List[CandidateVerdict]:
        return [self._by_pair[key] for key in sorted(self._by_pair)]

    def record_for(self, a: str, b: str) -> Optional[CandidateVerdict]:
        return self._by_pair.get(pair_key(a, b))

    def status_for(self, a: str, b: str) -> str:
        """Status of a pair — ``pending`` for one nobody has ruled on."""
        record = self.record_for(a, b)
        return record.status if record else STATUS_PENDING

    def is_rejected(self, a: str, b: str) -> bool:
        return self.status_for(a, b) == STATUS_REJECTED

    def prior_score(self, a: str, b: str) -> Optional[float]:
        """The score this pair was first surfaced at, or ``None`` if new."""
        record = self.record_for(a, b)
        return None if record is None else float(record.score)

    def pending(self) -> List[CandidateVerdict]:
        return [r for r in self.records if r.status == STATUS_PENDING]


def merge_verdicts(
    stored: Iterable[CandidateVerdict],
    observed: Iterable[CandidateVerdict],
) -> List[CandidateVerdict]:
    """Union ``stored`` and ``observed``, with the STORED record always winning.

    Deliberately asymmetric with :func:`tesserae.merge_ledger.publish_merge_ledger`,
    where this compile's observation wins. There a record is a re-derivable fact
    about the current graph; here it may be a human's answer, and an observation
    is only ever a re-asking of the question. Letting the fresh row win would
    overwrite a rejection with a pending every single run — the bug this module
    exists to prevent, in one line.

    Keeping the stored row also freezes ``score`` at the first observation, so
    the drift a reviewer sees is measured from where the pair entered the queue.
    """
    return CandidateLedger([*stored, *observed]).records


def candidate_ledger_path(project_root: str | Path) -> Path:
    """Return ``<project_root>/.tesserae/candidate-same-as.json``."""
    return Path(project_root) / ".tesserae" / CANDIDATE_LEDGER_FILENAME


def load_candidate_ledger(project_root: str | Path) -> CandidateLedger:
    """Load a project's candidate ledger. Never raises — an absent or corrupt
    sidecar is an EMPTY ledger, because every caller is on a path where "no
    verdict recorded" is a correct answer and an exception is not.
    """
    return CandidateLedger(_read_ledger_file(candidate_ledger_path(project_root)))


def publish_candidate_ledger(
    path: str | Path,
    observed: Sequence[CandidateVerdict],
) -> int:
    """Add newly observed pairs to the ledger at ``path``. Returns its size.

    Additive only. Nothing is pruned against the graph and nothing is rewritten:
    a stored verdict — including a plain ``pending`` — survives an observation
    of the same pair untouched, and a pair that stops being surfaced keeps its
    row. See the module docstring for why removing a stale row is the one thing
    this ledger must never do.

    Byte-stable given the same record set: sorted by pair, sorted keys, and no
    clock except the one already frozen into a decided row. Published through
    the same PID+random tmp file the graph artifacts use, so two reviewers on a
    shared disk contend only over the atomic ``os.replace``.
    """
    target = Path(path)
    kept = merge_verdicts(_read_ledger_file(target), observed)
    _write_ledger_file(target, kept)
    return len(kept)


def record_decisions(
    path: str | Path,
    decisions: Sequence[Tuple[str, str, str]],
    *,
    decided_by: str = "",
    decided_at: Optional[str] = None,
    tesserae_version: Optional[str] = None,
) -> int:
    """Record ``(a, b, status)`` verdicts into the ledger. Returns how many landed.

    Called by whoever APPLIES a decision, which is what keeps ``decided_by`` and
    ``decided_at`` from being aspirational keys nothing writes. A decision
    overwrites whatever the pair held before — including an earlier decision, so
    a reviewer can change their mind — which is the one case where a stored row
    does not win, and it is a human overwriting a human.

    An unknown pair with no prior observation is still recorded: the verdict is
    the point, and the score it was decided at is simply unknown (0.0) rather
    than invented.
    """
    target = Path(path)
    stamped_at = decided_at or datetime.now(timezone.utc).isoformat()
    if tesserae_version is None:
        from .cli_tree import package_version

        tesserae_version = package_version()

    existing = CandidateLedger(_read_ledger_file(target))
    decided: List[CandidateVerdict] = []
    for a, b, status in decisions:
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"Unknown candidate status: {status!r}")
        key = pair_key(a, b)
        prior = existing.record_for(*key)
        decided.append(
            CandidateVerdict(
                a=key[0],
                b=key[1],
                score=float(prior.score) if prior else 0.0,
                source=prior.source if prior else SOURCE_TOKEN,
                status=status,
                decided_by=str(decided_by or ""),
                decided_at=stamped_at,
                tesserae_version=str(tesserae_version or ""),
            )
        )
    # Decisions FIRST so they beat the stored row for the same pair — the
    # inverse of :func:`publish_candidate_ledger`, and the only place the
    # inversion is correct.
    _write_ledger_file(target, merge_verdicts(decided, existing.records))
    return len(decided)


def _write_ledger_file(target: Path, records: Sequence[CandidateVerdict]) -> None:
    payload = {
        "schema_version": CANDIDATE_LEDGER_SCHEMA_VERSION,
        "records": [record.as_json() for record in records],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    # Imported here rather than at module scope: ``project`` imports
    # ``research_graph``, which imports the sibling merge ledger, so a top-level
    # import would close a cycle. Same lazy-import shape ``merge_ledger`` uses,
    # and for the same reason a fixed ``.tmp`` name is not good enough: two
    # writers sharing one scratch path interleave into it and rename the mixture
    # into place, so only the PID+random suffix leaves ``os.replace`` as the one
    # thing they contend over.
    from .project import _publish_atomically

    _publish_atomically(
        target,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _read_ledger_file(path: Path) -> List[CandidateVerdict]:
    """Parse one ledger file into records, tolerating everything.

    This file is HUMAN-EDITABLE — flipping a ``status`` by hand is a supported
    way to answer the queue — so every field is untrusted. Corruption is silence
    rather than an exception because both callers are on paths where "no verdict
    recorded" is a correct answer; ``lint``'s ``PENDING_REVIEW`` probe is the
    surface that reports an unreadable ledger loudly instead.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict) or raw.get("schema_version") != CANDIDATE_LEDGER_SCHEMA_VERSION:
        return []
    rows = raw.get("records")
    if not isinstance(rows, list):
        return []
    records: List[CandidateVerdict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        a, b = row.get("a"), row.get("b")
        if not isinstance(a, str) or not isinstance(b, str) or not a or not b or a == b:
            continue
        status = row.get("status")
        if status not in CANDIDATE_STATUSES:
            # An unrecognised verdict is NOT silently downgraded to pending:
            # that would re-ask a question a typo had already answered. The row
            # is dropped, and the pair reads as never-seen.
            continue
        try:
            score = round(float(row.get("score") or 0.0), 4)
        except (TypeError, ValueError):
            score = 0.0
        source = row.get("source")
        records.append(
            CandidateVerdict(
                a=a,
                b=b,
                score=score,
                source=source if source in CANDIDATE_SOURCES else SOURCE_TOKEN,
                status=status,
                decided_by=str(row.get("decided_by") or ""),
                decided_at=str(row.get("decided_at") or ""),
                tesserae_version=str(row.get("tesserae_version") or ""),
            )
        )
    return records


__all__ = [
    "CANDIDATE_LEDGER_FILENAME",
    "CANDIDATE_LEDGER_SCHEMA_VERSION",
    "CANDIDATE_SOURCES",
    "CANDIDATE_STATUSES",
    "CandidateLedger",
    "CandidateVerdict",
    "SOURCE_EMBEDDING",
    "SOURCE_TOKEN",
    "STATUS_CONFIRMED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "candidate_ledger_path",
    "load_candidate_ledger",
    "merge_verdicts",
    "pair_key",
    "publish_candidate_ledger",
    "record_decisions",
]
