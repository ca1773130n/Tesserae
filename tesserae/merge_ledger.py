"""The merge ledger — a dead node id resolves to its survivor, not to not-found.

Every compile collapses duplicate nodes three ways, and until now all three
threw away the ``loser -> survivor`` map they built:

* :meth:`tesserae.canonicalization.GraphCanonicalizer.canonicalize` builds a
  complete ``merged_nodes`` map from the exact alias key and discards it when
  the :class:`~tesserae.canonicalization.CanonicalizationResult` goes out of
  scope (``exact-key``).
* :func:`tesserae.research_graph._merge_same_type_aliased_duplicates` collapses
  same-type names that collide under the lossy ``_aggressive_dedup_key``
  (``aggressive-key``).
* :func:`tesserae.research_graph._merge_cross_type_duplicates` collapses the
  same casefolded name carried by two different types (``cross-type``).

All three drop the loser node and rewire its edges onto the survivor, so an
agent holding a node id from the previous compile whose node lost a merge got a
not-found from every read surface — with no way to learn it had been absorbed
rather than deleted. This module is the back-reference that closes that, and
:meth:`MergeLedger.resolve` is what a read surface calls on a miss.

Two decisions here have a tempting wrong answer, so both are stated:

* **This is DERIVED state, never history.** Tesserae's merge is a pure function
  of the input graph, re-derived on every compile, so there is no accumulated
  destructive history to recover from — which is exactly why the tombstone is
  worth taking from ``apoc.refactor.mergeNodes`` and its mutation is not. The
  ledger is therefore validated against the graph on every compile and pruned to
  it (:func:`publish_merge_ledger`), rather than appended to; nothing in it may
  be read as a record that outlives the graph it describes.
* **It is a ``.tesserae/`` sidecar and never node metadata.** An out-of-band
  metadata key survives an incremental compile and vanishes on a full one, and
  it would make a compile-local fact part of ``graph.json`` bytes — the
  byte-idempotence leak class this repo has now hit four times.

The loser's name and type are recorded because they are what makes a redirect
legible in a tool response without a second lookup. The loser's description and
metadata deliberately are NOT: the graph is recompiled from its sources, so
copying derived state in here would park it somewhere it can no longer be
re-derived from.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

#: Bumped only when the record shape changes. A reader that does not recognise
#: the version treats the ledger as absent rather than guessing at its fields.
MERGE_LEDGER_SCHEMA_VERSION = 1

#: Sidecar filename under ``.tesserae/``.
MERGE_LEDGER_FILENAME = "merge-ledger.json"

#: Which pass absorbed the loser. Named after the key each pass merges on, so a
#: reader can tell an exact-alias collapse from the lossy one without reading
#: the merge source.
BASIS_EXACT_KEY = "exact-key"
BASIS_AGGRESSIVE_KEY = "aggressive-key"
BASIS_CROSS_TYPE = "cross-type"

MERGE_BASES = frozenset({BASIS_EXACT_KEY, BASIS_AGGRESSIVE_KEY, BASIS_CROSS_TYPE})


@dataclass(frozen=True)
class MergeRecord:
    """One node absorbed into another during a single compile."""

    loser_id: str
    survivor_id: str
    basis: str
    loser_name: str = ""
    loser_type: str = ""

    def as_json(self) -> Dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


# --------------------------------------------------------------------------- #
# (1) Collection — a pure observer of the merge passes                        #
# --------------------------------------------------------------------------- #

# The merge passes are called from ~10 sites across ``merge_graphs``,
# ``ResearchGraphBuilder.build`` and the canonicalizer, and losers minted at an
# early site are gone by the last one — so collecting only at the final merge
# would produce a near-empty ledger. A context-scoped sink lets the compile
# collect ALL of them without threading an accumulator through every call site,
# and a ContextVar (not a module global) keeps a compile in one thread from
# seeing another thread's merges. The sink is write-only from the passes' point
# of view: nothing read back out of it can change what they return, which is
# what keeps this from being able to move a single byte of graph.json.
_COLLECTOR: ContextVar[Optional[List[MergeRecord]]] = ContextVar(
    "tesserae_merge_collector", default=None
)


@contextmanager
def collect_merges() -> Iterator[List[MergeRecord]]:
    """Collect every merge recorded inside the block, in the order it happened.

    Order matters and is preserved: when a loser's survivor later loses a merge
    of its own, the two records form a chain that :meth:`MergeLedger.resolve`
    walks. Re-entrant blocks nest by replacing the sink and restoring it, so an
    inner block's merges do NOT leak into an outer one — a compile that calls
    another compile's helpers gets its own ledger.
    """
    records: List[MergeRecord] = []
    token = _COLLECTOR.set(records)
    try:
        yield records
    finally:
        _COLLECTOR.reset(token)


def record_merge(
    loser_id: str,
    survivor_id: str,
    basis: str,
    *,
    loser_name: str = "",
    loser_type: str = "",
) -> None:
    """Record one absorption, or do nothing when no collector is active.

    A no-op outside :func:`collect_merges` on purpose: the merge passes are
    called from tests, exports and one-off CLI verbs that have no ledger to
    write, and none of them should pay for one or be able to accumulate into a
    stale sink.
    """
    sink = _COLLECTOR.get()
    if sink is None:
        return
    if not loser_id or not survivor_id or loser_id == survivor_id:
        return
    sink.append(
        MergeRecord(
            loser_id=str(loser_id),
            survivor_id=str(survivor_id),
            basis=str(basis),
            loser_name=str(loser_name or ""),
            loser_type=str(loser_type or ""),
        )
    )


# --------------------------------------------------------------------------- #
# (2) Resolution — the read surface's answer to a dead id                     #
# --------------------------------------------------------------------------- #


class MergeLedger:
    """In-memory view of ``.tesserae/merge-ledger.json``.

    Empty when the sidecar is absent or unreadable: a missing ledger means "this
    project has not been compiled since the ledger shipped", which must read as
    "no redirect known", never as an error on a read path.
    """

    def __init__(self, records: Iterable[MergeRecord] = ()) -> None:
        self._by_loser: Dict[str, MergeRecord] = {}
        for record in records:
            # First record for a loser wins. Within one compile that is a
            # formality — the pass that absorbs a node drops it, so it cannot
            # lose twice — but it is what lets ``publish_merge_ledger`` express
            # "this compile's observation beats the stored one" simply by
            # putting the fresh records first.
            self._by_loser.setdefault(record.loser_id, record)

    def __len__(self) -> int:
        return len(self._by_loser)

    def __bool__(self) -> bool:
        return bool(self._by_loser)

    @property
    def records(self) -> List[MergeRecord]:
        return sorted(self._by_loser.values(), key=lambda r: r.loser_id)

    def record_for(self, node_id: str) -> Optional[MergeRecord]:
        """Return the record that absorbed ``node_id``, if it lost a merge."""
        return self._by_loser.get(str(node_id))

    def resolve(self, node_id: str) -> Optional[str]:
        """Return the id ``node_id`` was ultimately absorbed into, else ``None``.

        Walks the chain, because a survivor of one pass can be the loser of the
        next (aggressive-key collapse feeding cross-type collapse is the
        ordinary case). The seen-set is a cycle guard rather than defensive
        decoration: the passes cannot mint a cycle, but a hand-edited or
        half-written ledger can, and an infinite loop on a read path is a worse
        failure than an unresolved id.
        """
        current = str(node_id)
        seen = {current}
        survivor: Optional[str] = None
        while True:
            record = self._by_loser.get(current)
            if record is None:
                return survivor
            current = record.survivor_id
            if current in seen:
                return survivor  # cycle: answer with the last sane hop
            seen.add(current)
            survivor = current


# --------------------------------------------------------------------------- #
# (3) Persistence — derived, rewritten whole, byte-stable                     #
# --------------------------------------------------------------------------- #


def merge_ledger_path(project_root: str | Path) -> Path:
    """Return ``<project_root>/.tesserae/merge-ledger.json``."""
    return Path(project_root) / ".tesserae" / MERGE_LEDGER_FILENAME


def publish_merge_ledger(
    path: str | Path,
    observed: Sequence[MergeRecord],
    live_node_ids: Iterable[str],
) -> int:
    """Merge ``observed`` into the ledger at ``path``, prune it against the
    published graph, and republish. Returns how many records landed.

    Two halves, and the first one is why this is not a plain overwrite. An
    INCREMENTAL compile feeds the previous compile's ``graph.json`` — which
    holds survivors only — plus the re-extracted changed files, so it never
    performs (and cannot observe) the merges that produced those survivors. A
    ledger rewritten from ``observed`` alone would therefore come out empty
    after every incremental compile and silently drop every redirect the full
    compile earned; ``tests/test_incremental_parity.py`` sees this as an
    incremental arm that is not byte-identical to a full one, which is exactly
    what it is for.

    So records union, with THIS compile's observation winning a conflict, and
    the second half keeps that union from being append-only history: a record
    survives only while its loser is absent from the graph this compile
    published AND the chain out of it lands on a node that is present. A loser
    that came back to life — its source changed back, or the merge stopped
    happening — is dropped, and so is a chain into a node that left the corpus.
    The ledger therefore stays a statement about the CURRENT graph — derived,
    self-correcting, and bounded by it — rather than an accumulating record of
    everything that has ever merged.

    Byte-stable given the same surviving record set: sorted by loser id, sorted
    keys, no wall clock, no counters. Published through the same PID+random tmp
    file the graph artifacts use, so two hosts compiling one project on a shared
    disk contend only over the atomic ``os.replace``.
    """
    target = Path(path)
    # Observations first: ``MergeLedger`` keeps the first record it sees for a
    # loser, so this compile's answer beats a stale one for the same id.
    union = MergeLedger([*observed, *_read_ledger_file(target)])
    live = {str(node_id) for node_id in live_node_ids}
    # Two passes, and the order is the whole point. First drop every record
    # whose LOSER is in the published graph: that id answers for itself, so a
    # redirect off it is worse than none. Doing that first also truncates any
    # chain running THROUGH a node that came back to life, which is why the
    # second pass re-resolves against the reduced set instead of the union —
    # otherwise a chain would sail past a live intermediate to a stale terminus.
    # Then keep only what still lands somewhere real.
    alive_losers_removed = [r for r in union.records if r.loser_id not in live]
    pruned = MergeLedger(alive_losers_removed)
    kept = [r for r in alive_losers_removed if pruned.resolve(r.loser_id) in live]
    payload = {
        "schema_version": MERGE_LEDGER_SCHEMA_VERSION,
        "records": [record.as_json() for record in kept],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    # Imported here, not at module scope: ``research_graph`` imports this module
    # and ``project`` imports ``research_graph``, so a top-level import would
    # close the cycle. Same lazy-import shape ``charter.py`` already uses.
    from .project import _publish_atomically

    _publish_atomically(
        target,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return len(kept)


def load_merge_ledger(project_root: str | Path) -> MergeLedger:
    """Load a project's merge ledger. Never raises — an absent or corrupt
    sidecar is an EMPTY ledger, because every caller is on a read path where
    "no redirect known" is the right answer and an exception is not.
    """
    return MergeLedger(_read_ledger_file(merge_ledger_path(project_root)))


def _read_ledger_file(path: Path) -> List[MergeRecord]:
    """Parse one ledger file into records, tolerating everything.

    Corruption is silence rather than an exception on purpose: both callers are
    on paths where "no redirect known" is a correct answer — a read surface
    answering not-found, and a compile that is about to republish the file
    anyway.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict) or raw.get("schema_version") != MERGE_LEDGER_SCHEMA_VERSION:
        return []
    rows = raw.get("records")
    if not isinstance(rows, list):
        return []
    records: List[MergeRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        loser_id, survivor_id = row.get("loser_id"), row.get("survivor_id")
        if not isinstance(loser_id, str) or not isinstance(survivor_id, str):
            continue
        if not loser_id or not survivor_id or loser_id == survivor_id:
            continue
        records.append(
            MergeRecord(
                loser_id=loser_id,
                survivor_id=survivor_id,
                basis=str(row.get("basis") or ""),
                loser_name=str(row.get("loser_name") or ""),
                loser_type=str(row.get("loser_type") or ""),
            )
        )
    return records


__all__ = [
    "BASIS_AGGRESSIVE_KEY",
    "BASIS_CROSS_TYPE",
    "BASIS_EXACT_KEY",
    "MERGE_BASES",
    "MERGE_LEDGER_FILENAME",
    "MERGE_LEDGER_SCHEMA_VERSION",
    "MergeLedger",
    "MergeRecord",
    "collect_merges",
    "load_merge_ledger",
    "merge_ledger_path",
    "publish_merge_ledger",
    "record_merge",
]
