"""Temporal fact projection: bitemporal facts, provenance and fact search.

Builds Graphiti-style temporal facts with provenance and MegaMem-style
project/vault artifacts on top of Tesserae's controlled ontology, with
MCP-friendly fact search surfaces and a no-API-key local workflow.

Naming those projects says where a shape came from. It asserts nothing about
how Tesserae compares to either, because nothing here measures that — the
"competitive analysis helpers" this docstring used to advertise were a
hardcoded report that read no input, and they were deleted.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .memory.contradiction import RESOLVED_BY_EDGE
from .research_graph import (RETRACTION_EDGE_TYPES, ResearchGraph, ResearchNode,
                             ResearchNodeType, stable_id)


CLAIM_TYPES = {
    ResearchNodeType.CLAIM,
    ResearchNodeType.CONTRIBUTION_CLAIM,
    ResearchNodeType.PERFORMANCE_CLAIM,
    ResearchNodeType.COMPARISON_CLAIM,
    ResearchNodeType.LIMITATION_CLAIM,
    ResearchNodeType.CAUSAL_CLAIM,
    ResearchNodeType.OPEN_QUESTION,
}


# Predicates that END the node they point AT. ``invalidates`` is not in
# ALLOWED_EDGE_TYPES today; kept so the set matches the pre-existing
# invalidator check and needs no edit if the ontology grows one.
#
# ponytail: an interval boundary derived from these edges is only as
# trustworthy as the pass that minted them. ``supersedes`` carries almost all
# of them and its DEFAULT arbitration is credential-free — Jaccard > 0.55
# candidate generation plus session-id ordering (memory/supersede.py
# _deterministic_verdict), no LLM. Ceiling: a token-overlap heuristic decides
# when a fact stopped being true. Upgrade path, in order: (1) the already-wired
# LLM verdict path in the same pass, (2) the embedding candidates
# supersede.py deliberately deferred. Item (3), turn-level granularity, is DONE:
# ``session_graph._finding_first_seen_at`` dates a finding from its own turns'
# timestamps, so findings in one long-running session no longer share a
# boundary.
INVALIDATING_PREDICATES = {
    "contradicts_claim",
    "supersedes",
    "invalidates",
} | set(RETRACTION_EDGE_TYPES)

# Predicates that end their own SOURCE — the OPPOSITE orientation.
#
# ``memory.contradiction`` mints ``loser resolved_by winner``, so the losing
# claim is the edge's source. Folding it into INVALIDATING_PREDICATES would
# close the interval on the WINNER, which is a worse answer than the hole it
# was meant to fill. ``graph_filters.superseded_ids`` already encodes both
# orientations for the read surfaces; this is the same distinction on the
# temporal axis, and the two must be read together.
#
# Imported rather than re-spelled so the pass that mints the edge stays its
# one source of truth — the discipline RETRACTION_EDGE_TYPES established.
RESOLVING_PREDICATES = {RESOLVED_BY_EDGE}

#: Every predicate that closes an interval, in either orientation. Callers that
#: only need "does this edge end something" ask this; callers that need to know
#: WHICH endpoint ended ask :func:`_closing_roles`.
INTERVAL_CLOSING_PREDICATES = INVALIDATING_PREDICATES | RESOLVING_PREDICATES

# Timestamp ladder for ``valid_from`` — most-specific observation first.
#
# WHY these keys and not others: every one is SOURCE-derived and already
# inside graph.json. ``first_seen_at`` is stamped from the session's own
# ``started_at`` (session_graph.py), ``analysis_date`` from the paper front
# matter, ``ended_at``/``started_at`` from the session envelope.
#
# ``last_accessed_at`` is DELIBERATELY ABSENT even though
# context_compiler._recency_score reads it: it is mutable node_memory sidecar
# state, and letting it decide a written artifact's field is precisely the
# wall-clock leak that broke byte-idempotence four times in this repo. The
# ladder is the read direction of the same boundary _fact_from_edge already
# guards in the write direction.
#
# Rungs MEASURED and rejected, so nobody re-proposes them:
#
# * front-matter ``fetched_at`` (ingest/fetch.py writes it) — 0 of 2,524 corpus
#   files carry it, and ``extract_source_metadata``'s allowlist would rename it
#   anyway. The rung reaches nothing.
# * git commit date of ``source_path`` — ``data/`` is gitignored and holds
#   80.4% of the dated population, so it reaches 14.4% of nodes and is MORE
#   clone-fragile than the path rung it would sit beside, which is the exact
#   constraint that motivated proposing it. A shallow clone breaks it too.
# * bare ``date`` metadata — present on 1,337 nodes but LLM-transcribed from
#   prose, not parsed by anything deterministic: of 400 sampled, 22 disagree
#   with their own file, 148 have no such date in the file at all, and one is
#   the literal string "April 25". Promoting it would make valid_from a
#   function of model output and break the source-derived invariant above.
# * ``last_accessed_at`` — see the paragraph above; never.
#
# Propagating a timestamp ALONG edges (Claim <- evidenced_by <- EvidenceSpan
# <- contains <- SourceDocument) was also designed and dropped as unnecessary,
# not deferred: every Claim and EvidenceSpan already names its producing file
# in the top-level ``source_path`` FIELD (21,723 of 21,723), which the path
# rung below reads directly. Edge propagation caps at 29.8% because 70.2% of
# those nodes have no document parent under any provenance predicate, and it
# would drag in a topological order, a cycle rule (``supersedes`` alone adds
# 3,194 node-to-node edges) and a multi-parent tie-break to get there.
_TS_METADATA_KEYS: Tuple[str, ...] = (
    "first_seen_at",
    "analysis_date",
    "ended_at",
    "started_at",
    "updated_at",
    "created",
)

_LEADING_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# LAST rung: a date the ingest path already spells out. Research corpora land
# in dated directories (``data/research/daily/2026-04-06/papers/…``), and
# ``extract_source_metadata`` already reads exactly this segment — but only for
# the SourceDocument, never for the Claims, EvidenceSpans and Concepts minted
# from the same file. Those nodes carry NO timestamp metadata of their own, and
# on this corpus they are the graph: reading the date off their own
# ``source_path`` lifts node-level ladder coverage 7.6% -> 81.9% and, because
# ``_fact_from_edge`` dates an edge from EITHER endpoint, edge coverage
# 26.6% -> 88.1% (measured over the compiled graph, 46,924 nodes / 103,705 edges).
#
# SEMANTICS: this is the day Tesserae OBSERVED the document, not the day the
# underlying paper was published — the Graphiti valid-time reading, and the one
# ``first_seen_at`` already means everywhere else here. Never read it as a
# publication date.
#
# It is the last rung ON PURPOSE: a directory date is coarser than anything
# above it, so it may only fill a gap, never shadow one. And it is pure bytes —
# the path string is already inside graph.json, so no file, no git history and
# no clock is consulted, which is what keeps it byte-idempotent and portable to
# a fresh clone.
#
# TWO BOUNDS, both load-bearing, both learned the hard way:
#
# 1. ROOT-RELATIVE ONLY. ``source_path`` is stored ABSOLUTE (45,350 of 46,924
#    nodes), so scanning the whole string lets any dated ANCESTOR of the
#    checkout date every node beneath it. ``~/.agents/OPERATIONS.md`` mandates
#    ``~/.blackhole/<project>/<YYYY-MM-DD>/<slug>/`` for every agent worktree,
#    so an unbounded scan makes a compile run from one stamp the entire graph
#    with the worktree's creation date — a wall clock exactly one indirection
#    removed, which is the leak class named above. Only the part of the path
#    BELOW a project root the graph itself declares is scanned. A path under no
#    declared root is UNDATED by this rung: a relativisation that fails means
#    this project's ingest did not lay the path out, so no segment of it can be
#    read as this project's observation day, and guessing from the absolute
#    string is the defect itself.
#
# 2. A WHOLE DIRECTORY SEGMENT. ``docs/handoffs/2026-08-02-kg-growth.md`` is
#    this repo's own document-naming convention — an AUTHORING date, not an
#    ingest day — and it dated 854 live nodes. Requiring the date to be an
#    entire segment excludes it, and gives the match the right boundary it
#    lacked (``2026-04-25-extra/`` no longer reads as ``2026-04-25``). Cost of
#    both bounds together on the live corpus: node coverage 83.7% -> 81.9%,
#    fact coverage 89.2% -> 88.1%.
#
# READ-SIDE ON PURPOSE. The roadmap step behind this rung
# (docs/superpowers/specs/2026-08-08-cognitive-memory-roadmap.md §3) asked for
# ``metadata['first_seen_at']`` to be STAMPED at node construction. It is not,
# and the spec now records why: stamping would write a value derived from a
# directory name into graph.json for ~34,851 nodes, where a later correction to
# the rule could not reach it, and would overload a key that today means "the
# moment a session observed this" (written by session_graph / session_event,
# read by activity_summary and agent_distill) with a second, coarser
# provenance class. Consumers that need this day must CALL ``_source_ts`` —
# including CHARTER's ``_domain_clock`` when it is written.
#
# ADDITIVE PER NODE, NOT PER FACT. The rung only fills a gap in the node
# ladder, so no node whose date came from a higher rung changes. It is NOT
# additive at fact level: ``_latest_ts`` takes the MAX over both endpoints, so
# dating a previously-undated endpoint changes which endpoint wins for an edge
# that already had a date. Measured on the live graph: of 103,705 facts, 63,780
# gain a date they did not have, 1,426 have a real date REPLACED, and 38,499
# are unchanged. Every one of the 1,426 moves LATER; none moves earlier. That
# direction is what makes the replacement safe rather than lossy — under the
# max rule a fact cannot predate either endpoint, so the old value was too
# early precisely because one endpoint was unknown.
_PATH_DATE = re.compile(r"(?:^|[/\\])(\d{4}-\d{2}-\d{2})(?=[/\\])")


def graph_project_roots(graph: ResearchGraph) -> Tuple[str, ...]:
    """Project roots declared by the graph's own Session nodes, sorted.

    Derived from the graph rather than passed in, so every consumer of a
    compiled ``graph.json`` — projector, linter, OKF bundle, MCP server —
    resolves the same roots without an argument, an ``os.getcwd()`` or a
    filesystem probe. A graph with no Session node declares no root, which
    disables the path rung rather than falling back to the absolute path.
    """
    roots = {
        str((node.metadata or {}).get("project_root") or "").rstrip("/")
        for node in graph.nodes
        if node.type == ResearchNodeType.SESSION
    }
    return tuple(sorted(r for r in roots if r))


def relative_source_path(source_path: object, roots: Iterable[str]) -> Optional[str]:
    """``source_path`` made project-root-relative, else ``None``.

    Pure string work on bytes already in graph.json — no filesystem access, so
    it cannot depend on what happens to exist on the machine reading the graph.
    An already-relative path is returned as-is (it is relative to the root by
    construction); an absolute path under no declared root returns ``None``.

    Two callers depend on the same answer for different reasons: this module
    refuses to date a path it cannot place, and :mod:`tesserae.okf` refuses to
    emit one (§6.2 — a raw ``/Users/...`` would be read as bundle-relative and
    leaks a home directory).

    Roots are tried LONGEST FIRST, which matters only when one declared root
    nests inside another — and matters completely then. Taking the outermost
    match leaves the intervening directories in the "relative" path, so a
    project checked out at ``<workspace>/.blackhole/proj/2026-08-09/slug/``
    resolves against ``<workspace>`` and hands the date scanner a segment from
    the checkout location. That is the whole defect this relativisation exists
    to close, returned by the back door. A compiled graph carries one root
    today (Session nodes are admitted only on exact resolved-path equality), so
    this is unreachable now and cheap to keep closed for merged or federated
    graphs later.
    """
    if not isinstance(source_path, str) or not source_path.strip():
        return None
    path = source_path.strip()
    if not os.path.isabs(path):
        # A relative path is relative to the root by construction, so there is
        # nothing to strip — but it is NOT therefore safe to scan: ``../`` can
        # still walk above the root. The date scanner rejects the segments it
        # would reach; this function's contract is placement, not validation.
        return path.replace(os.sep, "/")
    for root in sorted((r for r in roots if r), key=len, reverse=True):
        if path == root:
            continue
        if path.startswith(root + "/"):
            return path[len(root) + 1:].replace(os.sep, "/")
    return None


def _parse_iso(value: object) -> Optional[datetime]:
    """Parse an ISO-8601 date/datetime into an aware UTC datetime, else None.

    Naive timestamps are assumed UTC — the same assumption
    ``context_compiler._parse_iso`` makes, kept consistent on purpose.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _source_ts(
    node: Optional[ResearchNode], roots: Iterable[str] = ()
) -> Optional[str]:
    """First source-derived timestamp on ``node``, VERBATIM.

    The ladder, most specific first: :data:`_TS_METADATA_KEYS`, then a leading
    date in the node's own name, then a dated directory segment of its
    ``source_path`` relative to one of ``roots`` (:func:`_source_path_date`).

    ``roots`` comes from :func:`graph_project_roots`. It defaults to empty, and
    empty DISABLES the path rung — a caller that does not know where the
    project lives cannot tell an ingest-chosen directory from the directory the
    checkout happens to sit in, and must not guess. Every in-tree caller passes
    the graph's own roots.

    Returns the raw string exactly as stored so the artifact never
    normalises a source format (a normalisation would drift between a
    date-only and a datetime-precision source). Parsing happens only in
    :func:`_parse_iso` for comparison.
    """
    if node is None:
        return None
    meta = getattr(node, "metadata", None) or {}
    for key in _TS_METADATA_KEYS:
        raw = meta.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    match = _LEADING_DATE.match(getattr(node, "name", "") or "")
    if match:
        return match.group(1)
    return _source_path_date(getattr(node, "source_path", None), roots)


def _source_path_date(
    source_path: object, roots: Iterable[str] = ()
) -> Optional[str]:
    """``YYYY-MM-DD`` named by a directory segment of ``source_path``, else None.

    Scans only the ROOT-RELATIVE path (bound 1 above), and only whole segments
    (bound 2). The DEEPEST match wins: a path may pass through an unrelated
    dated directory (``archive/2019-01-01/…``) on the way to the segment that
    actually dates the document, and the one nearest the file is the
    observation. Candidates are validated as real calendar dates so a segment
    that merely looks like one (``2026-13-45``, a version or an id) can never
    enter the ladder as an unorderable string.
    """
    relative = relative_source_path(source_path, roots)
    if relative is None:
        return None
    found: Optional[str] = None
    for match in _PATH_DATE.finditer(relative):
        candidate = match.group(1)
        try:
            date.fromisoformat(candidate)
        except ValueError:
            continue
        found = candidate
    return found


def _latest_ts(candidates: Iterable[Optional[str]]) -> Optional[str]:
    """Max over timestamp strings — a fact cannot predate EITHER endpoint.

    WHY max and not first-wins: an edge asserts a relationship between two
    observed things, so the earliest moment the assertion could hold is the
    moment the LATER endpoint was observed. Unparseable candidates cannot be
    ordered, so they only win when nothing parses (preserving the old
    first-string behaviour rather than dropping the value).
    """
    present = [c for c in candidates if c]
    if not present:
        return None
    parsed = [(dt, raw) for raw, dt in ((c, _parse_iso(c)) for c in present) if dt is not None]
    if not parsed:
        return present[0]
    # Tiebreak on the raw string so two equal instants order deterministically.
    return max(parsed, key=lambda item: (item[0], item[1]))[1]


def _end_sort_key(entry: Tuple[str, str, str]) -> Tuple[datetime, str, str]:
    """Order ``(timestamp, basis, superseder_id)`` entries deterministically.

    Unparseable timestamps sort LAST (``datetime.max``) so a parseable
    boundary always wins; ties break on superseder node id, never on dict
    or set iteration order.
    """
    ts, basis, superseder_id = entry
    dt = _parse_iso(ts) or datetime.max.replace(tzinfo=timezone.utc)
    return (dt, superseder_id, basis)


def _boundary_precedes_start(valid_from: Optional[str], valid_to: Optional[str]) -> bool:
    """True when a derived end boundary is NOT strictly after the start.

    This happens whenever two claims carry reasoning edges in both directions:
    for ``B criticizes F`` alongside ``F supersedes B``, ``valid_from`` is
    ``max(ts(B), ts(F)) == ts(F)`` and the derived ``valid_to`` is also
    ``ts(F)``. The half-open ``[from, to)`` in :func:`facts_as_of` is then
    empty at EVERY instant, so the fact silently vanishes from every
    time-travel query. An inverted boundary (``to < from``) is the same defect
    with a different sign.

    Unparseable/absent endpoints cannot be ordered, so they are not degenerate
    — the existing boundary stands rather than being second-guessed.
    """
    start, end = _parse_iso(valid_from), _parse_iso(valid_to)
    if start is None or end is None:
        return False
    return end <= start


def _closing_roles(
    predicate: str, subject_id: str, object_id: str
) -> Optional[Tuple[str, str]]:
    """``(loser_id, winner_id)`` for an interval-closing edge, else ``None``.

    The two orientations are NOT interchangeable and getting them backwards
    ends the survivor instead of the loser:

    - ``subject supersedes object`` (also ``contradicts_claim`` /
      ``invalidates`` / ``retracts``) — the TARGET lost.
    - ``subject resolved_by object`` — the SOURCE lost.

    INVALIDATING_PREDICATES is consulted first; the two sets are disjoint
    today and a predicate that ever landed in both would be an ontology bug,
    not a case to arbitrate here.
    """
    if predicate in INVALIDATING_PREDICATES:
        return object_id, subject_id
    if predicate in RESOLVING_PREDICATES:
        return subject_id, object_id
    return None


def _winner_precedes_loser(
    winner_ts: Optional[str], loser_ts: Optional[str]
) -> bool:
    """True when the winner is NOT strictly newer than the node it ends.

    Graphiti's contradiction resolution only invalidates when the surviving
    edge is strictly later, and the reason transfers: a winner observed at or
    before its loser cannot say when the loser stopped being true — it was
    already there. Inventing a boundary from it back-dates the loser's death
    to before its own birth.

    :func:`_boundary_precedes_start` catches the same inversion one layer
    down, but only after the earliest winner has already been chosen, so a
    back-dated winner would poison a boundary a later, legitimate winner could
    have supplied. Filtering candidates here keeps that boundary.

    Unorderable endpoints are not judged: an absent or unparseable timestamp
    means we cannot tell, and under-claiming a filter is safer than dropping a
    real boundary on a string we failed to parse.
    """
    win, lose = _parse_iso(winner_ts), _parse_iso(loser_ts)
    if win is None or lose is None:
        return False
    return win <= lose


@dataclass(frozen=True)
class TemporalFact:
    id: str
    subject_id: str
    subject_name: str
    subject_type: str
    predicate: str
    object_id: str
    object_name: str
    object_type: str
    evidence: Optional[str] = None
    # ONE axis, and it is VALID time: when the world was this way, derived by
    # _source_ts from timestamps the SOURCES carry. That is what lets it live
    # in an artifact — it is a pure function of graph.json.
    #
    # The other axis, TRANSACTION time (when WE learned the fact), is not here
    # and must never be: it can only come from a wall clock, and a wall clock
    # in graph.json or temporal_facts.jsonl is the byte-idempotence leak this
    # repo has hit four times. It lives in the fact_observed SQLite sidecar —
    # see tesserae.temporal_observed, which also carries the rule the two must
    # obey: never read one clock off the other.
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    # Which edge kind closed the interval: "supersedes" | "invalidates" |
    # "contradicts_claim" | "retracts" | "resolved_by" | None. Non-null exactly
    # when ``valid_to`` is. The enumerated set is INTERVAL_CLOSING_PREDICATES.
    valid_to_basis: Optional[str] = None
    current: bool = True
    invalidated_by: List[str] = field(default_factory=list)
    # May hold a numeric-as-text value ("0.75") for reinforced nodes sourced
    # from node_memory, or a label ("high"/"medium"/"low") otherwise.
    confidence: str = "medium"
    provenance: Dict[str, object] = field(default_factory=dict)
    metadata: Dict[str, object] = field(default_factory=dict)

    def model_dump(self) -> Dict[str, object]:
        return asdict(self)


class TemporalFactProjector:
    """Project validated ResearchGraph edges into temporal, provenance-rich facts."""

    def project(
        self,
        graph: ResearchGraph,
        *,
        memory_by_id: Optional[Dict[str, Any]] = None,
    ) -> List[TemporalFact]:
        nodes = {node.id: node for node in graph.nodes}
        # The path rung's bound, read off the graph itself so the projection
        # stays a pure function of graph.json (see _PATH_DATE, bound 1).
        roots = graph_project_roots(graph)
        facts: List[TemporalFact] = []
        edge_to_fact_id: Dict[tuple, str] = {}
        for edge in graph.edges:
            subject = nodes.get(edge.source)
            obj = nodes.get(edge.target)
            if not subject or not obj:
                continue
            fact = self._fact_from_edge(
                subject, edge.type, obj, edge.evidence, edge.metadata,
                memory_by_id=memory_by_id, roots=roots,
            )
            facts.append(fact)
            edge_to_fact_id[(fact.subject_id, fact.predicate, fact.object_id)] = fact.id

        # ``invalidators``: ended node id -> fact ids that ended it.
        # ``ended_by``: ended node id -> (earliest superseder timestamp, basis).
        # Both are built by iterating ``facts`` in list order (which follows
        # ``graph.edges`` list order), so no set/dict iteration reaches output.
        # ``invalidated_by`` is additionally SORTED before it is written, so it
        # does not inherit ``graph.edges`` ordering either.
        invalidators: Dict[str, List[str]] = {}
        # ended node id -> (timestamp, basis predicate, winner node id)
        ended_by: Dict[str, Tuple[str, str, str]] = {}
        for fact in facts:
            # Which endpoint lost depends on the predicate's orientation —
            # ``supersedes`` kills its target, ``resolved_by`` kills its own
            # source. See _closing_roles.
            roles = _closing_roles(fact.predicate, fact.subject_id, fact.object_id)
            if roles is None:
                continue
            loser_id, winner_id = roles
            invalidators.setdefault(loser_id, []).append(fact.id)
            # The superseded node ends when its EARLIEST superseder was
            # observed. An undated superseder cannot close the interval:
            # ``current`` still flips (we know it ended) but ``valid_to``
            # stays None (we do not know when) — never a guessed boundary.
            ts = _source_ts(nodes.get(winner_id), roots)
            if ts is None:
                continue
            if _winner_precedes_loser(ts, _source_ts(nodes.get(loser_id), roots)):
                continue
            entry = (ts, fact.predicate, winner_id)
            prior = ended_by.get(loser_id)
            if prior is None or _end_sort_key(entry) < _end_sort_key(prior):
                ended_by[loser_id] = entry

        updated: List[TemporalFact] = []
        for fact in facts:
            # An invalidating fact is never ended by its own target (that is
            # the whole point of the edge). Its SUBJECT can still be ended by
            # something else, so the subject side is always considered.
            # Checking the SUBJECT side at all is the fix: the supersede pass
            # mints edges between session findings, and a session finding is
            # overwhelmingly the SUBJECT of its facts (finding --discussed_in-->
            # doc), so an object-only check left every fact of a superseded
            # finding reading ``current: true``.
            #
            # Stated by orientation rather than by side: the surviving endpoint
            # is the WINNER, which is the subject of a ``supersedes`` fact and
            # the object of a ``resolved_by`` one.
            roles = _closing_roles(fact.predicate, fact.subject_id, fact.object_id)
            if roles is not None:
                endpoints = [roles[1]]
            else:
                endpoints = [fact.subject_id, fact.object_id]
            killers: List[str] = []
            for endpoint in endpoints:
                for fid in invalidators.get(endpoint, []):
                    if fid != fact.id and fid not in killers:
                        killers.append(fid)
            ends = [ended_by[e] for e in endpoints if e in ended_by]
            if ends:
                best = min(ends, key=_end_sort_key)
                valid_to, basis = best[0], best[1]
            else:
                valid_to, basis = None, None
            if _boundary_precedes_start(fact.valid_from, valid_to):
                # We know it ended (``current`` still flips below via
                # ``killers``) but the only boundary we can derive would make
                # the interval empty at every instant, which reads as "this
                # fact never held" — a stronger claim than the data supports.
                # Fall back to the SAME state an undated superseder produces:
                # valid_to None, basis None. Under-claim, never a guessed
                # boundary and never a silently empty one.
                valid_to, basis = None, None
            if not killers and valid_to is None:
                updated.append(fact)
                continue
            updated.append(
                TemporalFact(
                    **{
                        **fact.model_dump(),
                        "current": not killers,
                        # SORTED, not append order: ``killers`` accumulates in
                        # ``graph.edges`` order, which is stable today but is
                        # not a guarantee this artifact should inherit. Sorting
                        # makes temporal_facts.jsonl a pure function of the
                        # edge SET rather than the edge LIST.
                        "invalidated_by": sorted(killers),
                        "valid_to": valid_to,
                        "valid_to_basis": basis,
                    }
                )
            )
        return updated

    def write_jsonl(
        self,
        graph: ResearchGraph,
        path: str | Path,
        *,
        memory_by_id: Optional[Dict[str, Any]] = None,
    ) -> List[TemporalFact]:
        facts = self.project(graph, memory_by_id=memory_by_id)
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.writelines(json.dumps(fact.model_dump(), ensure_ascii=False, sort_keys=True) + "\n" for fact in facts)
        os.replace(tmp, output)
        return facts

    def _fact_from_edge(self, subject: ResearchNode, predicate: str, obj: ResearchNode, evidence: Optional[str], metadata: Dict[str, object], *, memory_by_id: Optional[Dict[str, Any]] = None, roots: Iterable[str] = ()) -> TemporalFact:
        # valid_from = MAX over (subject ts, object ts, edge-metadata ts).
        # Reading only ``analysis_date`` (which exists on Paper nodes and
        # essentially nowhere else) is why every session finding landed in the
        # single literal "undated" bucket and timeline() could not order the
        # corpus. See _TS_METADATA_KEYS for the ladder and its exclusions.
        valid_from = _latest_ts(
            (
                _source_ts(subject, roots),
                _source_ts(obj, roots),
                first_string(metadata.get("analysis_date")) if metadata else None,
            )
        )
        source_path = first_string(subject.source_path, obj.source_path, metadata.get("source_path"))
        # Consult the per-compile node_memory sidecar FIRST for a (numeric)
        # confidence. NodeMemoryRow.confidence is stored as text in SQLite
        # ("0.75") but format defensively for both str and float.
        # CRITICAL byte-idempotence: NEVER write mem_conf back onto
        # node.metadata — ResearchNode.model_dump() serialises metadata into
        # graph.json, and stamping confidence there reintroduces the 4x-broken
        # blind spot. The graph object MUST stay unmutated here.
        mem_conf = None
        if memory_by_id:
            for nid in (subject.id, obj.id):
                row = memory_by_id.get(nid)
                raw = getattr(row, "confidence", None) if row else None
                if raw is not None and str(raw) != "":
                    mem_conf = (
                        f"{float(raw):.2f}".rstrip("0").rstrip(".")
                        if isinstance(raw, (int, float))
                        else str(raw)
                    )
                    break
        confidence = (
            mem_conf
            or first_string(metadata.get("confidence"), subject.metadata.get("confidence"), obj.metadata.get("confidence"))
            or infer_confidence(subject, obj, evidence)
        )
        fact_id = stable_id("TemporalFact", f"{subject.id}|{predicate}|{obj.id}|{evidence or ''}")
        return TemporalFact(
            id=fact_id,
            subject_id=subject.id,
            subject_name=subject.name,
            subject_type=subject.type.value,
            predicate=predicate,
            object_id=obj.id,
            object_name=obj.name,
            object_type=obj.type.value,
            evidence=evidence,
            valid_from=valid_from or "undated",
            confidence=confidence,
            provenance={"source_path": source_path, "subject_source_path": subject.source_path, "object_source_path": obj.source_path},
            metadata=dict(metadata or {}),
        )


def first_string(*values: object) -> Optional[str]:
    for value in values:
        if value:
            return str(value)
    return None


def infer_confidence(subject: ResearchNode, obj: ResearchNode, evidence: Optional[str]) -> str:
    # KB-05: infer_confidence still honours a metadata-level confidence override
    # as the TEXTUAL fallback (when present it wins over the heuristic regardless
    # of caller). The numeric node_memory path now flows through _fact_from_edge's
    # memory_by_id arg and is NEVER stamped onto node.metadata (byte-idempotence).
    # When no override is present the path below is byte-identical to the original.
    override = first_string(
        subject.metadata.get("confidence"), obj.metadata.get("confidence")
    )
    if override:
        return override
    if subject.type in CLAIM_TYPES or obj.type in CLAIM_TYPES:
        return "medium" if evidence else "low"
    return "high" if evidence else "medium"


def facts_as_of(
    facts: Iterable[TemporalFact], as_of: str
) -> Tuple[List[TemporalFact], int]:
    """Time-travel filter: the facts whose validity interval covers ``as_of``.

    Returns ``(kept, undated_included)``. A fact is kept when

    * ``valid_from`` is unknown OR ``valid_from <= as_of``, AND
    * ``valid_to`` is None OR ``as_of < valid_to``.

    So ``as_of == valid_from`` is INCLUDED (the interval is half-open) and
    ``as_of == valid_to`` is EXCLUDED.

    Facts with an unknown/unparseable ``valid_from`` are INCLUDED but COUNTED,
    and the count comes back to the caller. An agent must never receive an
    "as of DATE" answer that is mostly undated rows without being told —
    coverage is thin today (``contradicts_claim`` and ``invalidates`` are
    empty on a real graph and ``resolved_by`` carries 17 edges, so almost
    every boundary rides on ``supersedes``),
    and this counter is what keeps the answer honest instead of complete-looking.

    Raises ``ValueError`` on an unparseable ``as_of`` rather than silently
    answering over the whole corpus.
    """
    pivot = _parse_iso(as_of)
    if pivot is None:
        raise ValueError(f"Unparseable as_of timestamp: {as_of!r}")
    kept: List[TemporalFact] = []
    undated_included = 0
    for fact in facts:
        start = _parse_iso(fact.valid_from)
        if start is not None and start > pivot:
            continue
        end = _parse_iso(fact.valid_to)
        if end is not None and pivot >= end:
            continue
        kept.append(fact)
        if start is None:
            undated_included += 1
    return kept, undated_included


def facts_since(
    facts: Iterable[TemporalFact], since: str
) -> Tuple[List[TemporalFact], int]:
    """Range filter: the facts whose interval STARTED on or after ``since``.

    Returns ``(kept, undated_excluded)``. A fact is kept when ``valid_from``
    parses and is ``>= since``, so ``since == valid_from`` is INCLUDED.

    This is NOT :func:`facts_as_of` with the comparison flipped, and the two
    must never be implemented in terms of each other. ``as_of`` asks what was
    BELIEVED at an instant and reads both bounds; ``since`` asks what STARTED
    inside a window and reads ``valid_from`` alone — ``valid_to`` is invisible
    to it. The undated policy inverts for the same reason: ``facts_as_of``
    models an unknown ``valid_from`` as -infinity and therefore includes it,
    and -infinity is never on or after a lower bound, so the same model drops
    it here.

    The dropped undated rows are COUNTED and the count comes back to the
    caller. On a corpus where most facts carry no date, a "what happened since
    DATE" answer is mostly a statement about what the window removed, and the
    caller must be able to say so instead of shipping a thin answer that looks
    complete.

    Raises ``ValueError`` on an unparseable ``since`` rather than silently
    answering over the whole corpus.
    """
    pivot = _parse_iso(since)
    if pivot is None:
        raise ValueError(f"Unparseable since timestamp: {since!r}")
    kept: List[TemporalFact] = []
    undated_excluded = 0
    for fact in facts:
        start = _parse_iso(fact.valid_from)
        if start is None:
            undated_excluded += 1
            continue
        if start >= pivot:
            kept.append(fact)
    return kept, undated_excluded


#: Hard ceiling on the PAGE :func:`search_facts` will return, whatever the
#: caller's ``limit``. It bounds the payload, never the answer: ``total_matches``
#: counts every match above it, so a caller can always tell a full page from a
#: complete one. Named rather than inlined so a caller reasoning about whether
#: it was handed everything reads the same number the function applied.
FACT_MATCH_CEILING = 100

#: The same bound for :func:`timeline`, which pages in whole events rather than
#: single facts and can afford more of them. Its own constant, not a multiple of
#: :data:`FACT_MATCH_CEILING`: the two page different things and there is no
#: relation between them to preserve.
TIMELINE_PAGE_CEILING = 200


#: The three states of the ``dated`` filter on :func:`search_facts` and
#: :func:`timeline`. Graphiti spells the same idea ``is_null`` / ``is_not_null``
#: inside a DNF filter language; three named states say it without asking an
#: agent to compose a predicate, and the vocabulary is one, not four.
DATED_FILTERS = ("any", "dated", "undated")


def is_dated(valid_from: object) -> bool:
    """True when ``valid_from`` is a timestamp the temporal filters can order.

    "Undated" is a PREDICATE over the value, never the literal ``"undated"``
    sentinel :meth:`TemporalFactProjector._fact_from_edge` writes for a missing
    timestamp. :func:`facts_as_of` and :func:`facts_since` already decide by
    parseability, and an unparseable non-sentinel would be exactly as
    unorderable as the sentinel is; testing for the string instead would let
    the filter, the sort bucket and the reported count drift apart while all
    three looked right.

    One spelling on purpose — ``mcp_server._undated_included`` and
    ``ask_planner._undated_shipped`` both call through here rather than
    re-deriving it.
    """
    return _parse_iso(valid_from) is not None


def fact_text(fact: TemporalFact) -> str:
    """The searchable CONTENT of a fact: what it says, not how it is stored.

    Ids, provenance, confidence and metadata are deliberately absent. Until
    this was fixed :func:`search_facts` scored over
    ``json.dumps(fact.model_dump())``, so a query term that appeared in a node
    id, a source path or a metadata key scored the fact as though the fact
    were about the query — searching ``SessionInsight`` matched every session
    fact in the graph, at the top, ahead of any real hit.
    """
    return " ".join(
        part
        for part in (fact.subject_name, fact.predicate, fact.object_name, fact.evidence or "")
        if part
    )


def _rank_facts(pool: List[TemporalFact], query: str) -> List[TemporalFact]:
    """Rank a fact pool against ``query`` over :func:`fact_text` alone.

    Two of ``hybrid_search``'s lanes, reused rather than re-spelled, with
    distinct jobs: BM25 RANKS, and the lexical lane ADMITS.

    * Ranking is BM25 (``hybrid._bm25_scores``), which is what the old
      term-presence counter lacked — a rare term now outweighs a common one
      and a short fact outranks a long one that mentions the term in passing.
    * Admission stays lexical: a fact matches when a query term occurs in its
      content, preserving the substring recall ("splat" finding "splatting")
      that BM25's whole-token matching would silently drop.

    Two lanes are deliberately NOT fused by ``hybrid._fuse``. RRF fuses lanes
    whose scores are not comparable, and it earns that over three lanes; over
    these two at equal weight it would cancel exactly whenever they disagree,
    because ``_rrf_ranks`` hands out distinct ranks even to equal scores, so a
    transposition costs one lane exactly what it pays the other and the tie
    falls back to projection order. Fusing here would look like ranking and
    behave like the input order.

    The embedding lane is left out too, and that is a decision rather than an
    omission: ``hybrid.hybrid_search`` takes ``ResearchNode``s and builds its
    corpus with ``_node_text``, which folds in node id, type and metadata —
    the very fields whose match is the defect this function removes — and its
    embedding lane would embed the whole fact corpus on every call, with no
    fact-level vector cache to read.
    """
    from .retrieval.hybrid import _bm25_scores, _lexical_scores, _tokenize

    texts = [fact_text(fact) for fact in pool]
    lexical = _lexical_scores(query, texts)
    bm25 = _bm25_scores(_tokenize(query), [_tokenize(text) for text in texts])
    order = sorted(range(len(pool)), key=lambda index: (-bm25[index], index))
    return [pool[index] for index in order if lexical[index] > 0]


def _matching_facts(
    facts: Iterable[TemporalFact],
    query: str,
    current_only: bool = False,
    dated: str = "any",
) -> List[TemporalFact]:
    """Every fact that matches, filtered and ranked, with NO page slice.

    Shared by :func:`search_facts` and :func:`timeline` so that neither one
    pages the other. ``timeline`` used to call ``search_facts(limit=10_000)``
    and receive at most :data:`FACT_MATCH_CEILING` rows, which cost it twice:
    it reported that clamp as ``total_events`` — a corpus-coverage-shaped
    number that was really an artefact of a page size the caller never asked
    for — and it sorted only the first 100 rows of the match set, so a
    "chronology" over a larger corpus was whatever BM25 ranked highest (or,
    unqueried, whatever the projector emitted first) wearing a time order.

    Raises on an unknown ``dated`` state rather than degrading to ``"any"``,
    which would answer a question nobody asked under the label of one they did.
    """
    if dated not in DATED_FILTERS:
        raise ValueError(
            f"Unknown dated filter: {dated!r} (expected one of {', '.join(DATED_FILTERS)})"
        )
    pool: List[TemporalFact] = []
    for fact in facts:
        if current_only and not fact.current:
            continue
        if dated != "any" and is_dated(fact.valid_from) != (dated == "dated"):
            continue
        pool.append(fact)
    if not query.split():
        # No query is not a zero-relevance ranking, it is "no filter": keep the
        # projection order so an unqueried call reads the corpus as projected.
        return pool
    return _rank_facts(pool, query)


def search_facts(
    facts: Iterable[TemporalFact],
    query: str,
    limit: int = 10,
    current_only: bool = False,
    dated: str = "any",
) -> Dict[str, object]:
    """Rank facts against ``query`` over :func:`fact_text`.

    ``dated`` is one of :data:`DATED_FILTERS` and narrows to facts that do or
    do not carry an orderable ``valid_from``; it is echoed back when it is not
    ``"any"``, so a filtered answer is never mistaken for a thin corpus. An
    unknown state raises rather than degrading to ``"any"``.
    """
    matches = [
        fact.model_dump()
        for fact in _matching_facts(facts, query, current_only=current_only, dated=dated)
    ]
    bounded = max(1, min(limit, FACT_MATCH_CEILING))
    result: Dict[str, object] = {
        "query": query,
        "total_matches": len(matches),
        "facts": matches[:bounded],
    }
    if dated != "any":
        # Report what actually ran, the way `as_of` and search_nodes' `mode` do.
        result["dated"] = dated
    return result


def timeline(
    facts: Iterable[TemporalFact], query: str = "", limit: int = 50, dated: str = "any"
) -> Dict[str, object]:
    """The same matches :func:`search_facts` finds, ordered in time.

    Two counts with deliberately different scopes. ``total_events`` is the
    count of EVERY match, taken before the page slice, so a caller can tell a
    full page from a complete answer; ``undated_events`` counts the undated
    rows IN THE RETURNED PAGE — the same discipline
    ``mcp_server._undated_included`` applies to ``as_of``.

    The sort runs over the full match set, not over a page of it, or the
    earliest events on a corpus larger than one page would simply be missing
    from the chronology that claims to start at the beginning.
    """
    events = [fact.model_dump() for fact in _matching_facts(facts, query, dated=dated)]
    # Sort on the PARSED timestamp, and bucket the undated rows behind the
    # dated ones instead of letting a string comparison place them. Sorting
    # `str(valid_from)` ordered the literal "undated" after every ISO date by
    # the accident that 'u' > '2' — on this project's own graph that silently
    # piled 73% of facts at the end of every timeline — and it mis-ordered
    # dated rows too, since "2026-01-01" and "2026-01-01T05:00:00Z" are the
    # same day but not the same string.
    dated_rows = [event for event in events if is_dated(event.get("valid_from"))]
    undated_rows = [event for event in events if not is_dated(event.get("valid_from"))]
    dated_rows.sort(
        key=lambda item: (
            _parse_iso(item.get("valid_from")),
            str(item.get("subject_name") or ""),
            str(item.get("predicate") or ""),
        )
    )
    undated_rows.sort(
        key=lambda item: (str(item.get("subject_name") or ""), str(item.get("predicate") or ""))
    )
    ordered = (dated_rows + undated_rows)[: max(1, min(limit, TIMELINE_PAGE_CEILING))]
    payload: Dict[str, object] = {
        "query": query,
        "total_events": len(events),
        "events": ordered,
        # Counted over the rows RETURNED, like `undated_included`: a timeline
        # whose tail carries no time at all is a thin answer, and the caller
        # has to be told how much of the page that is.
        "undated_events": sum(1 for event in ordered if not is_dated(event.get("valid_from"))),
    }
    if dated != "any":
        payload["dated"] = dated
    return payload
