"""Transaction time for temporal facts — when Tesserae LEARNED a fact.

Two clocks, and nothing here may read one off the other:

* **Valid time** answers *when was the world this way*. It is
  ``TemporalFact.valid_from`` / ``valid_to``, derived by
  :func:`tesserae.temporal._source_ts` from timestamps the SOURCES carry, so
  it is a pure function of ``graph.json`` and can live in the artifact.
* **Transaction time** answers *when did we learn this*. It is
  ``first_compile_at`` / ``last_seen_compile_at`` here, stamped from
  ``datetime.now(timezone.utc)`` once per compile. No source can answer it,
  so it can only come from a wall clock.

Graphiti's rule is taken verbatim: its ``invalid_at`` comes from the other
edge's ``valid_at`` (event time) while its ``expired_at`` comes from
``utc_now()`` (transaction time), never both from one clock. Tesserae shipped
only the first axis while the MCP schema called the surface "bitemporal" — one
clock advertised as two. This module is the other clock; ``as_of``'s
description now says valid-time, and both halves are needed for the wording to
be true.

State lives ONLY in the ``fact_observed`` SQLite table (see
:mod:`tesserae.graph_stores.sqlite`), never in ``graph.json`` and never in
``temporal_facts.jsonl``. A wall clock inside either artifact is the
byte-idempotence leak this repo has hit four times: the same sources would
compile to different bytes on Tuesday than on Monday. The ``node_memory``
precedent is binding and this is the same class of value.

The two pivots are structurally impossible to conflate, which is the point
rather than a nicety. :func:`tesserae.temporal.facts_as_of` reads nothing but
the facts handed to it; :func:`facts_observed_as_of` CANNOT be computed from a
fact at all and refuses to run without the ledger. A caller holding only facts
can never accidentally receive a transaction-time answer — the same guarantee
``facts_since`` and ``facts_as_of`` got by never being implemented in terms of
each other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from .graph_stores.sqlite import SqliteGraphStore
from .temporal import TemporalFact, _parse_iso

PathLike = Union[str, Path]

_LOG = logging.getLogger(__name__)

#: A fact's stable identity on the transaction axis.
FactKey = Tuple[str, str, str]


def transaction_now() -> str:
    """The transaction clock: real UTC wall time, read exactly once per compile.

    Deliberately NOT ``Project._compile_reference_timestamp``, which is
    content-derived precisely so decay stays byte-stable. That instant is a
    fact about the SOURCES; this one is a fact about US, and reusing the
    source-derived one here would collapse the two axes back into one — the
    defect this module exists to remove.

    Calling ``now()`` is safe here and only here because the result never
    reaches an artifact; it goes to ``fact_observed`` and stops.
    """
    return datetime.now(timezone.utc).isoformat()


def fact_key(fact: TemporalFact) -> FactKey:
    """``(subject_id, predicate, object_id)`` — the key ``fact_observed`` uses.

    NOT ``TemporalFact.id``, which hashes the evidence span as well: an edge
    whose evidence was re-extracted with different wording is the same fact,
    learned when it was first learned, and re-keying it would silently reset
    its first sighting to today.
    """
    return (fact.subject_id, fact.predicate, fact.object_id)


@dataclass(frozen=True)
class FactObservation:
    """When one fact entered the graph, and when it was last still in it.

    ``last_seen_compile_at`` is the only trace of a fact that LEFT the graph:
    the projection can only show what is in ``graph.json`` now, so a row whose
    last sighting is old is a fact that stopped being produced. It is recorded
    for that reason and is not a second filter bound — see
    :func:`facts_observed_as_of`.
    """

    subject_id: str
    predicate: str
    object_id: str
    first_compile_at: str
    last_seen_compile_at: str

    @property
    def key(self) -> FactKey:
        return (self.subject_id, self.predicate, self.object_id)


class FactObservationLedger:
    """Typed accessor over the ``fact_observed`` sidecar.

    Mirrors :mod:`tesserae.memory.store` and
    :mod:`tesserae.retrieval.vector_cache`: one accessor so no call site
    embeds raw SQL, and one place where "which clock is this" is answered.
    """

    def __init__(self, db_path: PathLike) -> None:
        self.db_path = Path(db_path)

    # -- construction ------------------------------------------------- #

    @classmethod
    def for_project(cls, project_root: Optional[PathLike]) -> Optional["FactObservationLedger"]:
        """Ledger in ``<project_root>/.tesserae/sqlite.db``, or ``None``.

        ``None`` when there is no project root or no ``.tesserae`` directory:
        an ad-hoc or store-backed graph has nowhere to keep a sidecar, and a
        READ must not create one as a side effect. Callers turn the ``None``
        into a refusal rather than into an unpivoted answer.
        """
        if project_root is None:
            return None
        root = Path(project_root)
        if not (root / ".tesserae").is_dir():
            return None
        return cls(root / ".tesserae" / "sqlite.db")

    # -- write (compile boundary) ------------------------------------- #

    def record(self, facts: Iterable[TemporalFact], observed_at: str) -> int:
        """Stamp ``observed_at`` on every fact key; returns the number of keys.

        Called once per compile with one ``observed_at``, so every fact of a
        compile shares an instant and the clock ticks per compile rather than
        per row. Duplicate keys inside one projection collapse — two edges
        differing only in evidence are one fact on this axis.
        """
        keys: List[FactKey] = []
        seen = set()
        for fact in facts:
            key = fact_key(fact)
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
        if not keys:
            return 0
        return SqliteGraphStore(self.db_path).write_fact_observed_many(keys, observed_at)

    # -- read --------------------------------------------------------- #

    def read(self) -> Dict[FactKey, FactObservation]:
        """Every observation row, keyed by :func:`fact_key`."""
        raw = SqliteGraphStore(self.db_path).read_fact_observed()
        return {
            key: FactObservation(
                subject_id=key[0],
                predicate=key[1],
                object_id=key[2],
                first_compile_at=first,
                last_seen_compile_at=last,
            )
            for key, (first, last) in raw.items()
        }

    def count(self) -> int:
        """How many fact keys the ledger holds (0 when it has never been written)."""
        return SqliteGraphStore(self.db_path).count_fact_observed()


def facts_observed_as_of(
    facts: Iterable[TemporalFact],
    observations: Dict[FactKey, FactObservation],
    observed_as_of: str,
) -> Tuple[List[TemporalFact], int]:
    """Transaction-time filter: the facts we had already LEARNED by ``observed_as_of``.

    Returns ``(kept, unobserved_included)``. A fact is kept when its
    ``first_compile_at`` is unknown OR ``first_compile_at <= observed_as_of``.

    This is not :func:`tesserae.temporal.facts_as_of` on a different column,
    and the two must never be implemented in terms of each other. ``as_of``
    reads a fact's own validity interval and asks what was TRUE then;
    ``observed_as_of`` reads the ledger and asks what we KNEW then. Composing
    them is the whole point — "what did we believe on DATE, as we knew it on
    DATE2" — which is also why this function takes the ledger as an argument
    it cannot do without: a caller who has only facts cannot get an answer on
    this axis by accident.

    Only ``first_compile_at`` bounds the filter. ``last_seen_compile_at`` is
    NOT an upper bound: ``facts`` comes from projecting the graph as it is
    now, so every fact in hand was seen in the latest compile, and a bound
    that can never exclude anything would be a filter in name only.

    A fact with no ledger row is INCLUDED and COUNTED, mirroring how
    ``facts_as_of`` models an unknown ``valid_from`` as -infinity. The count
    comes back to the caller for the reason ``undated_included`` does: on a
    ledger written before this fact existed, an "as we knew it on DATE"
    answer may be mostly rows the ledger cannot speak for, and the caller has
    to be able to say so instead of shipping a thin answer that looks
    complete.

    Raises ``ValueError`` on an unparseable pivot rather than silently
    answering over the whole corpus.
    """
    pivot = _parse_iso(observed_as_of)
    if pivot is None:
        raise ValueError(f"Unparseable observed_as_of timestamp: {observed_as_of!r}")
    kept: List[TemporalFact] = []
    unobserved_included = 0
    for fact in facts:
        row = observations.get(fact_key(fact))
        first = _parse_iso(row.first_compile_at) if row else None
        if first is None:
            kept.append(fact)
            unobserved_included += 1
            continue
        if first <= pivot:
            kept.append(fact)
    return kept, unobserved_included


def record_fact_observations(
    db_path: PathLike, facts: Iterable[TemporalFact], observed_at: str
) -> int:
    """Compile-boundary write-through; never raises into the compile.

    A transaction-time ledger is observability, not a compile output: a
    locked or unreadable sidecar must not be able to fail a compile that has
    already written every artifact. Logged with a traceback rather than
    swallowed, matching the ``write_memory`` call site one block above it.
    """
    try:
        return FactObservationLedger(db_path).record(facts, observed_at)
    except Exception:  # pragma: no cover — defensive; missing/locked db
        _LOG.exception("fact_observed write failed at %s; continuing", db_path)
        return 0
