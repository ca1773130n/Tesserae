"""Temporal fact projection and competitive analysis helpers.

This module absorbs the strongest open-source memory/KG patterns we evaluated:
Graphiti-style temporal facts with provenance, MegaMem-style project/vault
artifacts, and MCP-friendly fact search surfaces — while keeping Tesserae's
controlled ontology and no-API-key local workflow.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .research_graph import ResearchGraph, ResearchNode, ResearchNodeType, stable_id


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
INVALIDATING_PREDICATES = {"contradicts_claim", "supersedes", "invalidates"}

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
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    # Which edge kind closed the interval: "supersedes" | "invalidates" |
    # "contradicts_claim" | None. Non-null exactly when ``valid_to`` is.
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
        # ended node id -> (timestamp, basis predicate, superseder node id)
        ended_by: Dict[str, Tuple[str, str, str]] = {}
        for fact in facts:
            if fact.predicate not in INVALIDATING_PREDICATES:
                continue
            invalidators.setdefault(fact.object_id, []).append(fact.id)
            # The superseded node ends when its EARLIEST superseder was
            # observed. An undated superseder cannot close the interval:
            # ``current`` still flips (we know it ended) but ``valid_to``
            # stays None (we do not know when) — never a guessed boundary.
            ts = _source_ts(nodes.get(fact.subject_id), roots)
            if ts is None:
                continue
            entry = (ts, fact.predicate, fact.subject_id)
            prior = ended_by.get(fact.object_id)
            if prior is None or _end_sort_key(entry) < _end_sort_key(prior):
                ended_by[fact.object_id] = entry

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
            if fact.predicate in INVALIDATING_PREDICATES:
                endpoints = [fact.subject_id]
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
    empty on a real graph, so every boundary rides on ``supersedes`` edges),
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


def search_facts(facts: Iterable[TemporalFact], query: str, limit: int = 10, current_only: bool = False) -> Dict[str, object]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    scored = []
    for index, fact in enumerate(facts):
        if current_only and not fact.current:
            continue
        text = json.dumps(fact.model_dump(), ensure_ascii=False).casefold()
        score = sum(1 for term in terms if term in text)
        if not terms or score > 0:
            scored.append((score, index, fact))
    scored.sort(key=lambda item: (-item[0], item[1]))
    matches = [fact.model_dump() for score, _index, fact in scored if score > 0 or not terms]
    bounded = max(1, min(limit, 100))
    return {"query": query, "total_matches": len(matches), "facts": matches[:bounded]}


def timeline(facts: Iterable[TemporalFact], query: str = "", limit: int = 50) -> Dict[str, object]:
    result = search_facts(facts, query=query, limit=10_000)
    events = list(result["facts"])
    events.sort(key=lambda item: (str(item.get("valid_from") or ""), str(item.get("subject_name") or ""), str(item.get("predicate") or "")))
    return {"query": query, "total_events": len(events), "events": events[: max(1, min(limit, 200))]}


def render_competitive_report() -> str:
    return """# Tesserae Competitive Hardening Report

## Open-source advantages absorbed

| System | Advantage | Tesserae absorption |
|---|---|---|
| MegaMem | Obsidian/project-local graph artifacts plus MCP exposure | `.tesserae/` project workspaces, compile, SQLite, markdown projection, MCP config |
| MegaMem | Sync state and analytics | content-hash manifest, processed/skipped counts, durable report output |
| Graphiti/Zep | temporal facts with validity and provenance | `temporal_facts.jsonl` projects every validated edge into temporal facts with `valid_from`, `current`, `invalidated_by`, confidence, evidence, and source provenance |
| Graphiti/Zep | custom entity/edge types | controlled research ontology and edge whitelist, rejecting schema drift instead of generic `Entity` sprawl |
| Graphiti MCP | fact/entity MCP tools | dependency-light stdio MCP `search_facts`, `timeline`, `search_nodes`, `node_context`, and schema tools |
| Agentic RAG/Qdrant-style systems | semantic retrieval substrate | local Qwen/Ollama embedding path, no API key required |

## Tesserae differentiators retained

- controlled ontology rather than auto-discovered schema drift
- claim/evidence-first graph model for research intelligence
- project-local and no API key by default
- markdown is a projection, not the graph source of truth
- MCP server works without requiring Neo4j, FalkorDB, Qdrant, or Python MCP SDK

## Remaining next advantages to consider

- optional HTTP/SSE MCP transport with scoped tokens
- richer sync analytics dashboard
- graph diff/review UX for temporal invalidation decisions
- optional hybrid lexical+dense reranking over `temporal_facts.jsonl`
"""
