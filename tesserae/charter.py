"""A chartered institution over the research graph.

Community detection PROPOSES a domain vocabulary; this module's charter OWNS
it between explicit reorgs. That split exists because detection is
deterministic but not stable: identical input reproduces all 1,649 communities
exactly, yet a single 15-node document moves ~29% of members between
communities and drops large communities to Jaccard 0.39-0.60. Anything keyed
on community membership therefore takes a near-total cache miss per ingest,
and this corpus ingests daily.

See docs/superpowers/specs/2026-08-08-charter-expertise-org-design.md.
"""

from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Set

from .agent_distill import ARTIFACT_CHAR_BUDGET, _render_member_block
from .community_summaries import detect_communities
from .hierarchy import undirected_degrees
from .research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType

# ---------------------------------------------------------------------------
# CH-04, decided in advance of the code that will enforce it (roadmap step 6)
# ---------------------------------------------------------------------------
# CH-04 — "a note that names nothing cannot rise" — IS NOT IMPLEMENTED. There is
# no specificity check, no note-rising machinery and no ``render_brief`` in this
# module or anywhere else; only CH-01 is asserted. The charter-structure plan
# says so itself. What follows is therefore a DESIGN COMMITMENT about unwritten
# code, recorded here because the causal layer (``tesserae.session_recovery``)
# is the first producer whose nodes CH-04 would have judged, and because the
# choice was made against measurement rather than taste.
#
# The question step 6 had to settle: when CH-04 exists, are procedural and
# causal notes EXEMPT from it, or must they name a tool and a file as tokens
# that resolve to L0 node names?
#
# NEITHER. Both options are wrong, and the second is the worse of the two
# because it looks stricter while blocking everything. Measured on the live
# 47,132-node graph:
#
#   * File paths do not resolve. There are ZERO code-layer nodes of any kind
#     (the code layer is opt-in and off), and only 19 nodes graph-wide have a
#     name ending in a source-file extension — all EvidenceSpan / SourceDocument
#     / Repository, not files.
#   * Tool names barely resolve, and only by accident. Of a dozen common tool
#     names exactly two are node names — ``Bash`` and ``WebFetch`` — and both
#     are TechnicalTerm nodes that document extraction happened to mint from a
#     CLAUDE.md routing-rules file. A structural rule that passes only because a
#     config file got ingested is not a rule.
#   * Whole-name matching defeats itself from both ends. SessionInsight names
#     run a median of 24 words and Runbook titles a median of 3, so a Runbook
#     can essentially never contain a session finding's name as a token; and a
#     producer Event is named ``Event {turn_id}: {actor} · {action}``, which is
#     turn-scoped and so can never appear in a general note either.
#
# So requiring tool+file as name-resolvable tokens would silently stop every
# procedural and causal note from rising — a worse exemption than the exemption,
# and a silent one.
#
# THE DECISION: keep CH-04's rule exactly as specified — a note that names
# nothing cannot rise — and widen what a token is allowed to resolve AGAINST for
# procedural and causal nodes, from member NAMES alone to
#
#     {names of L0 members}
#   ∪ {metadata["tool"] of L0 Event members}          (session_event.py)
#   ∪ {file paths in L0 member metadata}
#
# This preserves the argument CH-04 rests on — a recovery note that names
# ``exec_command`` and an operand is executable, while "we improved reliability
# at scale" still names nothing and still cannot rise — and it is satisfiable
# today, because ``metadata["tool"]`` is already stamped on every tool Event by
# the session producer and ``metadata["anchor"]`` on every ``recovers`` edge.
# Widen the resolvable set, not the rule.

#: Split threshold, in rendered member-block characters. A LITERAL, not
#: ``CHUNK_CHAR_BUDGET // 2``: deriving it would let an env override of
#: TESSERAE_LLM_CHUNK_CHARS reshape the tree, which is exactly the
#: declared-input leak agent_distill.py:150-155 warns about for
#: ARTIFACT_CHAR_BUDGET.
DOMAIN_MASS_CAP = 24_000

#: Minimum mass for a sub-community to become its own domain. Tuned by
#: measurement, not taste: at 3,000 the live graph yields 92 routers / 796
#: leaves / 9 unsplittable stalls, depth median 3 max 5. At 6,000 stalls jump
#: to 53; at 12,000 to 83.
DOMAIN_MASS_FLOOR = 3_000


def mass(nodes: Sequence[ResearchNode]) -> int:
    """Rendered size of ``nodes``, in the exact text a distill prompt packs.

    Uses ``_render_member_block`` rather than a proxy so the split threshold
    is measured in the same units as the budget it protects. LLM-free, and
    already the memory-pressure proxy at agent_distill.py:2813.
    """
    return sum(len(_render_member_block(node)) for node in nodes)


def sections(graph: ResearchGraph) -> tuple[list[list[str]], list[str]]:
    """Detect sections, and REPORT what detection threw away.

    ``detect_communities`` filters ``len(c) > 1`` (community_summaries.py:106),
    so Louvain singletons are dropped silently and the returned clusters do
    NOT partition the node set. Returning the dropped ids alongside is what
    keeps the partition invariant (CH-01) provable rather than aspirational —
    they become intake members in Task 4.
    """
    clusters = detect_communities(graph)
    covered = {nid for cluster in clusters for nid in cluster}
    dropped = sorted(node.id for node in graph.nodes if node.id not in covered)
    return clusters, dropped


#: Synthetic node id prefix for quotient scope-nodes. Never persisted into
#: graph.json — the quotient graph is an in-memory scratch structure.
_SCOPE_PREFIX = "CharterScope"


def quotient_graph(graph: ResearchGraph, clusters: Sequence[Sequence[str]]) -> ResearchGraph:
    """One node per section, one ``part_of`` edge per cross-section L0 edge.

    This is what turns an unusable top level into a readable one. The existing
    dendrogram's coarsest level has 1,820 communities of median size 2, so
    ``graph_map`` at root emits 1,820 size-ranked cards behind a cursor — an
    agent picks by rank and page number rather than by meaning. Feeding the
    quotient back through the SAME detector collapses that to a handful of
    balanced divisions.

    Both the synthetic nodes AND the edges are returned, because
    ``_undirected_projection`` (community_summaries.py:131-132) drops any edge
    whose endpoints are absent from ``graph.nodes`` — a quotient carrying only
    edges would be silently invisible to Louvain.

    ``part_of`` is used because ``ResearchEdge.__post_init__`` validates
    against ALLOWED_EDGE_TYPES and raises ValueError otherwise; "quotient_of"
    does not exist.
    """
    section_of: dict[str, int] = {}
    for index, cluster in enumerate(clusters):
        for node_id in cluster:
            section_of[node_id] = index

    nodes = [
        ResearchNode(
            id=f"{_SCOPE_PREFIX}:{index}",
            name=f"section-{index}",
            type=ResearchNodeType.CONCEPT,
            metadata={"member_count": len(cluster)},
        )
        for index, cluster in enumerate(clusters)
    ]

    pairs: set[tuple[int, int]] = set()
    for edge in graph.edges:
        left = section_of.get(edge.source)
        right = section_of.get(edge.target)
        if left is None or right is None or left == right:
            continue
        pairs.add((min(left, right), max(left, right)))

    edges = [
        ResearchEdge(
            source=f"{_SCOPE_PREFIX}:{left}",
            target=f"{_SCOPE_PREFIX}:{right}",
            type="part_of",
        )
        for left, right in sorted(pairs)
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def divisions(graph: ResearchGraph, clusters: Sequence[Sequence[str]]) -> list[list[int]]:
    """Group section indices into divisions by running detection on the quotient.

    Returns lists of INDICES into ``clusters``, sorted within each group and
    with groups ordered by ``(-size, first index)`` so the result is stable
    even though ``detect_communities`` deliberately does not sort its outer
    list (community_summaries.py:88-91).

    A section with no cross-section edge is a Louvain singleton in the
    quotient and is therefore dropped by the ``len(c) > 1`` filter. Those are
    NOT returned here; Task 4 routes them to intake.
    """
    groups: list[list[int]] = []
    for cluster in detect_communities(quotient_graph(graph, clusters)):
        indices = sorted(int(nid.split(":", 1)[1]) for nid in cluster)
        groups.append(indices)
    groups.sort(key=lambda g: (-sum(len(clusters[i]) for i in g), g[0]))
    return groups


def intake_members(
    graph: ResearchGraph,
    clusters: Sequence[Sequence[str]],
    groups: Sequence[Sequence[int]],
) -> list[str]:
    """Every node no division holds: dropped singletons + edge-isolated sections.

    Measured on the live graph this is 5,643 nodes (12.0%) — 1,508 sections
    with no cross-section edge (3,806 members, max size 14) plus 1,837 Louvain
    singletons. It is genuinely unroutable BY STRUCTURE: lexical clustering as
    a fallback splitter was measured and produces 5,322 clusters of median
    size 1 with zero clusters above 3,000 chars, i.e. it is a near-duplicate
    clusterer, not a topical one.

    Intake is therefore the one domain whose brief is honestly a census. The
    fix is better linking at extraction time, which is a different project;
    until then this is a standing extraction-quality lint.
    """
    routed_sections = {index for group in groups for index in group}
    routed_members = {
        node_id
        for index, cluster in enumerate(clusters)
        if index in routed_sections
        for node_id in cluster
    }
    return sorted(node.id for node in graph.nodes if node.id not in routed_members)


@dataclass(frozen=True)
class SplitResult:
    """One level of division. ``children`` + ``direct`` always reconstruct the
    input member set exactly — that is lint CH-01, true by construction here
    so it can be asserted rather than hoped for."""

    children: tuple[tuple[str, ...], ...]
    direct: tuple[str, ...]
    stalled: bool


def induced_subgraph(graph: ResearchGraph, member_ids: Sequence[str]) -> ResearchGraph:
    """The subgraph over ``member_ids``, keeping only edges internal to it."""
    keep = set(member_ids)
    nodes = [node for node in graph.nodes if node.id in keep]
    edges = [
        edge for edge in graph.edges if edge.source in keep and edge.target in keep
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def split(graph: ResearchGraph, member_ids: Sequence[str]) -> SplitResult:
    """Divide an oversized domain by SUB-COMMUNITY, never by size.

    Numbered size shards were rejected: an agent cannot tell what is in shard 2
    versus shard 3 without reading both, which reintroduces exactly the
    multi-read cost the one-read bound exists to prevent. Splitting by meaning
    lets it choose a branch from named subjects.

    A domain that cannot be divided STALLS rather than raising: it stays an
    oversized leaf flagged ``stalled``, and degrades through the artifact
    layer's existing counted-remainder path. Measured on the live graph at
    DOMAIN_MASS_FLOOR=3,000 this happens 9 times out of 888 domains.
    """
    members = sorted(set(member_ids))
    by_id = {node.id: node for node in graph.nodes}
    scoped = [by_id[mid] for mid in members if mid in by_id]

    if mass(scoped) <= DOMAIN_MASS_CAP:
        return SplitResult(children=(), direct=tuple(members), stalled=False)

    sub = induced_subgraph(graph, members)
    candidates = [
        cluster
        for cluster in detect_communities(sub)
        if mass([by_id[mid] for mid in cluster if mid in by_id]) >= DOMAIN_MASS_FLOOR
    ]
    if not candidates:
        return SplitResult(children=(), direct=tuple(members), stalled=True)

    # Sort by (-mass, first id): detect_communities deliberately does not sort
    # its outer list, so an explicit key is what makes the charter stable.
    candidates.sort(
        key=lambda c: (-mass([by_id[mid] for mid in c if mid in by_id]), c[0])
    )
    children = tuple(tuple(sorted(cluster)) for cluster in candidates)
    claimed = {mid for child in children for mid in child}
    direct = tuple(mid for mid in members if mid not in claimed)

    # CRITICAL fix (Task 7 review): a dense/clique-like oversized domain has no
    # internal substructure for Louvain to exploit, so its coarsest partition
    # can be a SINGLE community spanning every input member — reproduced
    # upstream by nx.community.louvain_partitions(nx.complete_graph(20))
    # having exactly one community at its coarsest level. Handing that back as
    # a normal one-child result does not shrink the member set at all, so a
    # caller that recurses on the children (as _emit does) recomputes the
    # identical result and recurses again, without bound, until RecursionError.
    # A single candidate that covers every member is not a STRICT
    # sub-partition of the input, so it is "cannot be divided" in exactly the
    # sense the stall path already exists for.
    if len(children) == 1 and not direct:
        return SplitResult(children=(), direct=tuple(members), stalled=True)

    return SplitResult(children=children, direct=direct, stalled=False)


#: Node types that must not NAME a domain while any other member could.
#:
#: Not a quality ranking of the ontology — a demotion list, so a type nobody
#: has measured stays eligible by default and a new producer gets a fair shot
#: at naming its own subject. Every entry is here because it was MEASURED
#: heading a live domain with a name that cannot serve as a slug. Censused on
#: the 47,132-node graph, 205 of 779 anchored domains (26%) were headed by one
#: of these, including two of the six divisions:
#:
#:   * SOURCE_DOCUMENT (121 domains) — a container for text, not a subject.
#:     Its name is whatever heading the parser found: the 7,955-member
#:     division anchored on ``한 줄 요약`` ("one-line summary"), which strips to
#:     nothing under NFKD+ASCII and hashes to ``domain-bfd88123``. Both of the
#:     charter's two unreadable hash slugs come from this type.
#:   * EVIDENCE_SPAN (44) — a quoted span; names are extractor boilerplate
#:     ("Repository metadata evidence").
#:   * SESSION (24) — a transcript envelope named ``<date> — <first line of
#:     the prompt>``, which is where nearly all 155 slugs over 50 characters
#:     come from (``2026-06-09-why-we-don-t-have-ingest-commnad-sometimes-``…,
#:     typo included and pinned forever).
#:   * EVENT (9) — named ``Event {turn_id}: {actor} · {action}``: turn-scoped,
#:     so the slug names one keystroke of one session.
#:   * AGENT (5) — named ``harness:account:role``, i.e. it puts an operator's
#:     email address in a permanent, shareable attach path.
#:   * TECHNICAL_TERM (2) — extraction's catch-all for jargon; it gave the
#:     6,283-member division the slug ``python``.
#:   * The SESSION_* finding types (3) — this module's own CH-04 note measures
#:     their names at a median of 24 words.
#:   * STUB (0 today) — a tombstone for a wikilink that resolves to nothing,
#:     so it names a node that does not exist.
#:
#: Demoting does NOT change what a domain HOLDS — the anchor is an identity
#: and a name, not a member filter — and it does not weaken the identity
#: substrate the 97.0%/81.0% preservation measurement is about. It arguably
#: strengthens it: every demoted type is a per-ingest artifact (a document a
#: re-parse renames, a span an extractor re-mints, one transcript, one turn),
#: while the types left eligible are producer-owned knowledge that outlives
#: the file it arrived in.
_ANCHOR_DEMOTED_TYPES = frozenset(
    {
        ResearchNodeType.SOURCE_DOCUMENT,
        ResearchNodeType.EVIDENCE_SPAN,
        ResearchNodeType.TECHNICAL_TERM,
        ResearchNodeType.STUB,
        ResearchNodeType.SESSION,
        ResearchNodeType.SESSION_INSIGHT,
        ResearchNodeType.SESSION_DECISION,
        ResearchNodeType.SESSION_QUESTION,
        ResearchNodeType.SESSION_TODO,
        ResearchNodeType.SESSION_HYPOTHESIS,
        ResearchNodeType.SESSION_TAKEAWAY,
        ResearchNodeType.SESSION_FAILURE,
        ResearchNodeType.EVENT,
        ResearchNodeType.AGENT,
    }
)


def assign_anchors(
    graph: ResearchGraph,
    member_sets: Sequence[Sequence[str]],
    *,
    claimed: Optional[set[str]] = None,
) -> list[str]:
    """Pick each domain's top-degree NAMEABLE member, greedily, no two the same.

    The anchor is the identity substrate: a hub does not move when 15 nodes
    arrive. Measured preservation under a one-document perturbation is 97.0%
    at fine level and 81.0% at coarse, against member-set Jaccard which fails
    for ~72% of large scopes. Assignment is greedy in ``(-degree, id)`` order
    ACROSS siblings so two domains can never claim the same anchor — a
    collision would make two domains indistinguishable to succession.

    ``claimed`` is that no-two-the-same guarantee widened from ONE CALL to the
    WHOLE CHARTER, and it is not optional polish. Scoring uses GLOBAL
    ``undirected_degrees`` rather than degree within the member set, so a
    child domain re-picks whichever node is the highest-degree in the entire
    graph — which is, by construction, the node its PARENT already anchored
    on. ``build_charter`` calls this once for divisions and again per split
    for that division's children, and per-call deduping cannot see across
    those calls, so ancestor and descendant came out sharing one anchor on the
    plan's own simplest split fixture: division ``a0`` and department ``a0-2``
    both anchored ``Concept:a0``.

    That collision is what made succession do the exact opposite of its job.
    ``succeed`` matches a fresh domain to a prior one BY ANCHOR, so with two
    domains behind one anchor only one could inherit: measured on an
    UNCHANGED graph, every reorg retired a live division and renamed a live
    department, compounding without bound
    (``a0`` -> ``a0-2`` -> ``a0-2-2`` -> ...). An operator who pinned an agent
    to a slug lost that attach path on the next ingest.

    Pass the SAME set object through every call for one charter. It is read
    (a member already claimed by an ancestor or a sibling is skipped, so the
    domain falls to its next-highest-degree unclaimed member) and mutated in
    place (every anchor handed out is recorded for later calls).

    ``_ANCHOR_DEMOTED_TYPES`` outranks degree rather than merely breaking ties
    on it, and that ordering is the whole fix. The roadmap proposed type as a
    MIDDLE key — ``(-degree, type, id)`` — which measurement shows is a no-op:
    the two unusable divisions are not degree ties. ``한 줄 요약`` heads its
    division at degree 116 against 112 for the next candidate, and ``Python``
    at 112 against 108, so a tie-break never runs and both slugs survive
    unchanged. Ranking by type FIRST and by degree within the type is what
    actually moves them, and it is why this lands before the first charter is
    written: the slug is an operator-pinned attach path, so a selector fixed
    afterwards is a refound of every domain it touches.
    """
    degrees = undirected_degrees(graph)
    # Restricted to the ids actually being ranked, not every node of the
    # graph. ``build_charter`` calls this once per router — 89 times on the
    # live graph — always against the WHOLE graph (degree is global by
    # design, see above), so an unrestricted set comprehension rebuilt a
    # ~20k-id set on each call and cost 2.8s of the 10.7s build.
    wanted = {member_id for members in member_sets for member_id in members}
    demoted = {
        node.id
        for node in graph.nodes
        if node.id in wanted and node.type in _ANCHOR_DEMOTED_TYPES
    }
    ranked: list[tuple[int, int, int, str]] = []
    for index, members in enumerate(member_sets):
        for member_id in members:
            ranked.append(
                (
                    1 if member_id in demoted else 0,
                    -degrees.get(member_id, 0),
                    index,
                    member_id,
                )
            )
    ranked.sort(key=lambda row: (row[0], row[1], row[3]))

    anchors: dict[int, str] = {}
    if claimed is None:
        claimed = set()
    for _rank, _degree, index, member_id in ranked:
        if index in anchors or member_id in claimed:
            continue
        anchors[index] = member_id
        claimed.add(member_id)

    result: list[str] = []
    for index, members in enumerate(member_sets):
        if index in anchors:
            result.append(anchors[index])
            continue
        # A set can reach here only if every one of its members was already
        # claimed — by a sibling that won a tie in the greedy pass above, or
        # by an ANCESTOR in an earlier call sharing this ``claimed`` set — or
        # the set is empty. ``sorted(members)[0]`` alone is NOT safe here:
        # that member is, by construction, already in ``claimed`` by whoever
        # took it, so returning it unconditionally would hand two domains the
        # same anchor id — the exact identity collision this function exists
        # to rule out, since two domains sharing an anchor become
        # indistinguishable to succession. Search for a still-unclaimed
        # member instead; if none exists, an empty string is a visible,
        # checkable degradation, not a silent one. Deciding what to DO about
        # it belongs to the caller, which has the tree context this function
        # does not: ``_emit`` folds such a set into its parent's direct block
        # rather than emitting a domain that could never succeed itself.
        candidate = next((mid for mid in sorted(members) if mid not in claimed), "")
        if candidate:
            claimed.add(candidate)
        result.append(candidate)
    return result


def slug_for(name: str, taken: Set[str]) -> str:
    """A stable, human-facing slug. Minted once from the anchor name and pinned.

    Human-facing because it goes in a config file an operator pins an agent to,
    and it must survive a reorg. A collision gets a numeric suffix rather than
    overwriting, because two domains silently sharing a path would corrupt both.
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    if not base:
        # Non-Latin names (e.g. "한 줄 요약") strip to nothing under NFKD +
        # ASCII-encode. A content hash keeps the slug stable and unique
        # rather than falling back to a counter that would move when
        # siblings are reordered — and the live graph has such names as
        # real division anchors.
        base = "domain-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


#: Node types that are Tesserae's OWN output rather than knowledge it ingested.
#: Excluded by default: measured on the live graph, leaving them in made
#: division 3's anchor "Project Pulse", division 2's "한 줄 요약", and four of
#: the 3DGS division's top eight departments "Daily Digest — <date>" pages —
#: roughly half the institution describing the tool instead of the subject.
_SYNTHESIS_TYPES = frozenset(
    {ResearchNodeType.SYNTHESIS, ResearchNodeType.COMMUNITY_SUMMARY}
)

_INTAKE_SLUG = "intake"


class CharterUnreadable(RuntimeError):
    """A charter file exists but could not be parsed.

    Distinct from "no charter", and that distinction is the whole reason this
    class exists — see ``read_charter``.
    """


def charter_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".tesserae" / "charter" / "charter.json"


def read_charter(project_root: Path | str) -> Optional[dict]:
    """The charter, or None if this project genuinely has none.

    ABSENT and UNREADABLE are different conditions and must not collapse into
    the same return value. Every caller reads None as "this project has no
    charter yet" and proceeds to found one, which is correct for a project
    below the one-read bound — ``.tesserae/charter/`` is never created for it.
    Applied to a TRUNCATED or hand-mangled charter.json, the same None would
    make the engine silently RE-FOUND the entire institution: every pinned
    attach path broken, zero tombstones to explain where the old slugs went,
    and no error anywhere to say a file needed fixing. Swallowing
    JSONDecodeError here would therefore destroy exactly the stability this
    module exists to provide, in the one situation where an operator most
    needs to be told.
    """
    path = charter_path(project_root)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CharterUnreadable(
            f"charter at {path} exists but could not be read: {exc}. "
            "Fix the file's permissions or delete it and recompile to re-found "
            "the charter (which will mint new slugs, so prefer restoring it)."
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CharterUnreadable(
            f"charter at {path} is not valid JSON: {exc}. "
            "Restore it from version control if you can — deleting it and "
            "recompiling re-founds the charter from scratch, which mints new "
            "slugs and breaks every pinned attach path."
        ) from exc


def write_charter(project_root: Path | str, charter: dict) -> Path:
    """Persist with sorted keys and no timestamps, through the atomic publish."""
    from .project import _publish_atomically

    path = charter_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _publish_atomically(
        path, json.dumps(charter, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return path


def worth_chartering(graph: ResearchGraph) -> bool:
    """Is this graph too big to be one read? Only then does an institution exist.

    The one-read bound, applied as the FOUNDING test: a research layer that
    fits ``ARTIFACT_CHAR_BUDGET`` can be handed to an agent whole, so dividing
    it into divisions and departments adds a routing decision to a corpus that
    never needed one. Below the bound ``.tesserae/charter/`` is never created
    and every downstream reader stays byte-identical to its no-charter output.

    Measured in the same ``mass`` units the split threshold uses, so "the
    charter exists" and "a domain must split" are answers to the same
    question at two scales.

    Deliberately NOT a maintenance test. ``_write_charter_sidecar`` consults
    this only when there is no charter yet: once an institution exists its
    slugs are pinned attach paths, and a corpus oscillating around 48,000
    characters must not found and abandon them on alternating compiles.
    """
    return mass(graph.nodes) >= ARTIFACT_CHAR_BUDGET


def build_charter(graph: ResearchGraph, *, exclude_synthesis: bool = True) -> dict:
    """Derive the full institution from the research graph.

    Founding pass only: ``reorg_seq`` is 0 and every domain is ``founded``.
    Succession against a prior charter is Task 8.
    """
    scoped = ResearchGraph(
        nodes=[
            n for n in graph.nodes
            if not (exclude_synthesis and n.type in _SYNTHESIS_TYPES)
        ],
        edges=list(graph.edges),
    )
    keep = {n.id for n in scoped.nodes}
    scoped.edges = [e for e in scoped.edges if e.source in keep and e.target in keep]

    clusters, _dropped = sections(scoped)
    groups = divisions(scoped, clusters)
    intake = intake_members(scoped, clusters, groups)

    division_members = [
        sorted({mid for index in group for mid in clusters[index]}) for group in groups
    ]
    # ONE claimed-anchor set for the WHOLE charter, threaded through every
    # assign_anchors call including the recursive ones inside _emit. Deduping
    # per call is not enough: assign_anchors scores on global degree, so a
    # child re-picks the node its parent already anchored on, and two domains
    # behind one anchor are indistinguishable to succeed(). See the
    # assign_anchors docstring for the measured consequence — a reorg on an
    # UNCHANGED graph retiring a live division and renaming a live department,
    # every time, forever.
    claimed_anchors: set[str] = set()
    anchors = assign_anchors(scoped, division_members, claimed=claimed_anchors)
    by_id = {n.id: n for n in scoped.nodes}

    domains: dict[str, dict] = {}
    # ``intake`` is reserved BEFORE any domain slug is minted, so no division
    # can ever take it. Without this, a division whose anchor node is NAMED
    # "Intake" minted the base slug ``intake`` for itself and the intake write
    # below then erased that division outright: measured on a seven-node
    # fixture, six nodes ended up in zero domains while all six member_index
    # entries still named ``intake``, a domain that did not hold them, and any
    # children of the erased division survived orphaned on a parent_slug
    # nothing claimed. That is CH-01 — the partition every other invariant
    # rests on — silently void.
    #
    # Reserved unconditionally, not only when ``intake`` is non-empty: a
    # conditional reservation would hand the slug to a division on a pass
    # whose intake set happened to be empty, and the next ingest that produced
    # a single unroutable node would collide all over again. The slug space
    # has to be stable across reorgs, which is the whole point of this module.
    taken: set[str] = {_INTAKE_SLUG}
    member_index: dict[str, str] = {}

    def _emit(members: Sequence[str], anchor_id: str, tier: int, parent: Optional[str]) -> str:
        anchor_name = by_id[anchor_id].name if anchor_id in by_id else anchor_id
        slug = slug_for(anchor_name, taken)
        taken.add(slug)
        result = split(scoped, members)
        direct = list(result.direct)
        child_slugs: list[str] = []
        if result.children:
            # Same claimed set as the divisions above, so a child cannot
            # re-take its own parent's anchor (nor any ancestor's, nor any
            # already-emitted domain's).
            child_anchors = assign_anchors(
                scoped, [list(c) for c in result.children], claimed=claimed_anchors
            )
            for child_members, child_anchor in zip(result.children, child_anchors):
                if not child_anchor:
                    # A cluster with no anchor left to take. It is NOT emitted
                    # as a domain: its members fold into this domain's direct
                    # block instead.
                    #
                    # Emitting it was the defect. ``slug_for("")`` finds no
                    # ASCII base, so it hashes the EMPTY string and every
                    # anchorless domain charter-wide minted the same base slug
                    # ``domain-e3b0c442``; and ``succeed`` builds
                    # ``anchor_to_prior`` only from non-empty anchors, so such
                    # a domain could never inherit its own prior identity. It
                    # re-founded on every reorg while its tombstone was
                    # relocated by ``_claim``, so an UNCHANGED graph piled up
                    # ``domain-e3b0c442-2``, ``-2-2``, ``-2-2-2`` … one new
                    # tombstone per reorg, without bound — the same unbounded
                    # relocation the intake and ancestor-collision fixes
                    # closed, arriving through a third door. Measured: 11 of
                    # 400 random graphs, and depth 5 on the live graph.
                    #
                    # Folding is safe precisely BECAUSE such a cluster is
                    # tiny. Member sets partition the graph, so a node claimed
                    # by a sibling or by any unrelated domain is not in this
                    # cluster at all; the only way every member is already
                    # claimed is that every member anchors one of this
                    # cluster's own ANCESTORS, which bounds it by tree depth
                    # (~5 nodes on the live graph). A handful of nodes added
                    # to a router's direct block cannot blow the one-read
                    # bound, and such a cluster is far too small to have
                    # meaningful children of its own.
                    #
                    # CH-01 survives: the members move into the PARENT's
                    # direct set rather than into a child's, so each is still
                    # held exactly once.
                    direct.extend(child_members)
                    continue
                child_slugs.append(_emit(list(child_members), child_anchor, tier + 1, slug))
        domains[slug] = {
            "tier": tier,
            "own_altitude": _altitude_for(tier, len(members)),
            "parent_slug": parent,
            "child_slugs": sorted(child_slugs),
            "anchor_id": anchor_id,
            "direct_member_ids": sorted(direct),
            "member_count": len(members),
            "reorg_seq": 0,
            "status": "live",
            "transition": "founded",
            "unsplittable": result.stalled,
        }
        for mid in direct:
            member_index[mid] = slug
        return slug

    for members, anchor in zip(division_members, anchors):
        if not anchor:
            # Unreachable, and stated rather than left implicit for the same
            # reason as the intake raise below: a domain emitted with an empty
            # anchor is the defect the fold inside _emit exists to prevent,
            # and a division is the one place there is no parent to fold into.
            # It cannot happen because divisions are disjoint and non-empty and
            # ``claimed_anchors`` is empty on this first call, so the greedy
            # pass in assign_anchors gives every division a member of its own.
            raise RuntimeError(
                f"division with members {members[:3]}... was assigned no anchor; "
                "a top-level domain has no parent to fold into, so this would "
                "mint the anchorless slug that can never succeed itself"
            )
        _emit(members, anchor, 1, None)

    if intake:
        if _INTAKE_SLUG in domains:
            # Unreachable while ``taken`` is seeded with _INTAKE_SLUG above,
            # and stated as a raise rather than left implicit because the
            # unguarded form of this write is the defect itself: it silently
            # replaced a live division and voided the partition, and nothing
            # downstream noticed. If the reservation ever regresses, this
            # fails at the point of corruption instead of shipping a charter
            # that lints clean and routes six nodes into nowhere.
            raise RuntimeError(
                f"charter slug {_INTAKE_SLUG!r} was minted for a real domain "
                f"(anchor {domains[_INTAKE_SLUG].get('anchor_id')!r}); the "
                "reservation in build_charter must run before any domain slug "
                "is minted"
            )
        domains[_INTAKE_SLUG] = {
            "tier": 1,
            # Deliberately NOT routed through _altitude_for, which would
            # return "division" for tier 1. Altitude is a render parameter,
            # not a synonym for depth: it sets carry_quota and support_floor
            # (spec section "Altitude controls exactly two knobs"), and a
            # division's quota of 18 carried notes would TRUNCATE a domain
            # whose brief is honestly a census of everything structure could
            # not route. "team" is the unbounded-carry altitude, which is the
            # only one that can render intake truthfully.
            "own_altitude": "team",
            "parent_slug": None,
            "child_slugs": [],
            "anchor_id": "",
            "direct_member_ids": sorted(intake),
            "member_count": len(intake),
            "reorg_seq": 0,
            "status": "live",
            "transition": "founded",
            "unsplittable": False,
        }
        for mid in intake:
            member_index[mid] = _INTAKE_SLUG

    _verify_partition(scoped, domains, member_index)
    return {
        "version": 1,
        "reorg_seq": 0,
        "domains": domains,
        "member_index": member_index,
    }


def _verify_partition(
    graph: ResearchGraph, domains: dict[str, dict], member_index: dict[str, str]
) -> None:
    """CH-01, checked on the charter this call actually produced.

    CH-01 — every member held by exactly one domain — is true by construction
    here, and was true by construction the three previous times it was voided:
    the intake-slug overwrite, the ancestor/descendant anchor collision and
    anchorless-child emission each left ``member_index`` naming a domain that
    did not hold those members, and each shipped a charter that read as
    healthy. What caught all three was a test on a fixture. Now that a compile
    derives this on every real corpus, the invariant has to be checked on the
    charter being written rather than on the ones a suite happens to build —
    a partition that is void is worse than no charter, because every reader
    downstream trusts it to say where a node lives.

    Raising rather than degrading, matching the two guards above: a caller
    given a silently void partition routes agents into domains that hold
    nothing, and the failure surfaces far from its cause. This is deliberately
    NOT the ordering-window check — whether these ids survive into
    ``graph.json`` is decided by three passes that run after this one, and
    belongs to lint, not here.
    """
    held: dict[str, str] = {}
    duplicated: set[str] = set()
    for slug, entry in domains.items():
        for member_id in entry["direct_member_ids"]:
            if member_id in held:
                duplicated.add(member_id)
            held[member_id] = slug

    universe = {node.id for node in graph.nodes}
    missing = sorted(universe - held.keys())
    unknown = sorted(held.keys() - universe)
    if duplicated or missing or unknown or held != member_index:
        raise RuntimeError(
            "CH-01 violated by the charter just built: "
            f"{len(duplicated)} member(s) held by more than one domain "
            f"(e.g. {sorted(duplicated)[:3]}), {len(missing)} held by none "
            f"(e.g. {missing[:3]}), {len(unknown)} not in the graph "
            f"(e.g. {unknown[:3]}), member_index "
            f"{'disagrees with' if held != member_index else 'agrees with'} "
            "the direct blocks. Refusing to return a charter whose "
            "member_index names domains that do not hold those members."
        )


def succeed(prior: dict, fresh: dict) -> dict:
    """Carry stable slugs across a reorg by matching on ANCHOR.

    Member-set matching was measured and rejected: Jaccard >= 0.5 fails for
    roughly 72% of large scopes on a single 15-node document, because large
    communities land at J=0.39-0.60. The anchor is preserved 97.0% of the time
    at fine level and 81.0% at coarse, so it is the mechanism that keeps a
    pinned attach path working.

    A prior domain whose anchor no longer heads any fresh domain is TOMBSTONED
    rather than deleted: ``status: retired`` keeps a months-old citation
    resolvable to "this subject was reorganised" instead of a missing file.

    Slug TEXT can collide even when identity does not: ``slug_for`` derives a
    slug from an anchor's display name and dedupes only within one
    ``build_charter`` call, so two unrelated anchors minted in different eras
    (e.g. two Concept nodes both named "python") can land on the same base
    string. Review of the first cut of this function found that resolving
    that collision implicitly let it silently drop a LIVE domain two ways:
    (a) a founded fresh domain reusing a dead prior domain's exact slug text
    made ``slug in survivors`` true, which wrongly shielded that prior domain
    from ever being tombstoned; (b) a founded fresh domain whose own slug
    equalled ANOTHER fresh domain's inherited target let the unconditional
    ``domains[target] = carried`` write in pass 1 silently overwrite one live
    domain with the other, while ``member_index`` — remapped independently
    through ``rename[]`` — kept pointing the overwritten domain's members at
    a slug that no longer held them. ``_claim`` below fixes both: every
    domain states its desired slug and only gets a different one, via
    ``slug_for``, when that slug is already taken. Priority is strict rather
    than arbitrary, because only a live domain's slug is load-bearing for a
    pinned attach path: a real succession (rank 0) outranks a founded
    domain's coincidental slug (rank 1), which outranks a tombstone
    (rank 2) — a tombstone exists purely to be found by an old citation, so
    it is the one that moves when its text is contested.
    """
    prior_domains = prior.get("domains", {})
    fresh_domains = fresh.get("domains", {})

    # Only a LIVE prior domain can donate its slug — a domain already retired
    # by an earlier reorg must not be resurrected just because some fresh
    # domain's anchor happens to match its old one.
    #
    # Written as a loop rather than a dict comprehension because a
    # comprehension is silently LAST-WINS on a duplicate key, and a duplicate
    # anchor among live prior domains is a CORRUPT charter, not a detail to
    # resolve by accident. build_charter can no longer produce one (anchors
    # are now unique charter-wide), but charter.json is a file on disk that a
    # bad hand-merge, or a charter written by the buggy build this fix
    # replaces, can leave in exactly that state. Under last-wins, which of the
    # two domains kept its slug and which was tombstoned depended on nothing
    # but sort order.
    #
    # FIRST-WINS on sorted slug instead: deterministic, and the loser is not
    # quietly ignored — it falls through to the tombstone pass below like any
    # other prior domain no fresh domain inherited, so it is retired visibly
    # with a readable slug rather than disappearing.
    anchor_to_prior: dict[str, str] = {}
    for slug, entry in sorted(prior_domains.items()):
        anchor = entry.get("anchor_id")
        if entry.get("status") != "live" or not anchor:
            continue
        anchor_to_prior.setdefault(anchor, slug)
    next_seq = int(prior.get("reorg_seq", 0)) + 1

    # Whether a fresh domain inherits is decided by ANCHOR MATCH ALONE, up
    # front, before any slug-text collision is resolved below. Deciding it
    # from which slug a domain ends up holding in ``domains`` instead — as
    # the pre-review version did — would let a coincidental string collision
    # rewrite whether a real succession happened; the anchor match is ground
    # truth, the slug is just its label.
    inherited_target: dict[str, Optional[str]] = {
        slug: anchor_to_prior.get(entry.get("anchor_id") or "")
        for slug, entry in sorted(fresh_domains.items())
    }

    # Intake is the ONE domain whose identity is not its anchor: it has none
    # (``anchor_id: ""``), because it is a census of everything structure
    # could not route rather than a subject with a hub. Matching it by anchor
    # like every other domain matched it against nothing, so a reorg
    # tombstoned the prior intake and re-founded the fresh one every single
    # time — and since the live fresh domain already held the reserved slug,
    # each tombstone was relocated by ``_claim``, so an UNCHANGED graph piled
    # up ``intake-2``, ``intake-2-2``, ``intake-2-2-2`` … without bound. That
    # is the same no-op-reorg churn this function exists to prevent, merely
    # displaced into the tombstone space.
    #
    # Its identity is the RESERVED SLUG instead, which build_charter
    # guarantees is unique and permanent (no domain may mint it). Matching on
    # it is exact, not heuristic. This is deliberately conditional on the
    # fresh charter still HAVING an intake domain: if a reorg leaves nothing
    # unroutable, intake has genuinely gone away and must tombstone like
    # anything else, so self-succession must not become immortality.
    if (
        _INTAKE_SLUG in fresh_domains
        and prior_domains.get(_INTAKE_SLUG, {}).get("status") == "live"
    ):
        inherited_target[_INTAKE_SLUG] = _INTAKE_SLUG

    inherited_prior_slugs = {target for target in inherited_target.values() if target}

    domains: dict[str, dict] = {}
    rename: dict[str, str] = {}
    taken: set[str] = set()

    def _claim(desired: str, payload: dict, fresh_slug: Optional[str]) -> None:
        """Write ``payload`` at ``desired``, or the next free ``slug_for``
        variant if something already claimed it this call. Never overwrites
        — an unconditional overwrite is exactly the defect review found: it
        could erase a live domain's entry while ``member_index`` still
        pointed members at it."""
        target = desired if desired not in domains else slug_for(desired, taken)
        taken.add(target)
        domains[target] = payload
        if fresh_slug is not None:
            rename[fresh_slug] = target

    # Rank 0: real successions. A fresh domain whose anchor matches a live
    # prior domain has the strongest claim on that prior domain's slug —
    # dropping it would break the exact pinned attach path succession exists
    # to protect.
    for slug, entry in sorted(fresh_domains.items()):
        target = inherited_target[slug]
        if not target:
            continue
        carried = dict(entry)
        carried["reorg_seq"] = next_seq
        carried["transition"] = "stable"
        _claim(target, carried, slug)

    # Rank 1: founded fresh domains. No inheritance claim, so a collision
    # against a rank-0 slug (review finding (b): this domain's own base slug
    # happens to equal ANOTHER fresh domain's inherited target) must yield a
    # fresh slug rather than overwrite — overwriting here is what let a live
    # domain vanish from ``domains`` while ``member_index`` still named it.
    for slug, entry in sorted(fresh_domains.items()):
        if inherited_target[slug]:
            continue
        carried = dict(entry)
        carried["reorg_seq"] = next_seq
        carried["transition"] = "founded"
        _claim(slug, carried, slug)

    # Pass 2: remap parent_slug / child_slugs through the same rename map.
    # This has to run only after every fresh domain has a FINAL slug
    # (including any collision relocation above), because a child renamed —
    # whether by inheritance or by losing a slug collision — would otherwise
    # leave its parent, or a sibling's child_slugs, pointing at an abandoned
    # string.
    for slug, entry in sorted(fresh_domains.items()):
        target = rename[slug]
        parent = entry.get("parent_slug")
        if parent:
            # NOT rename.get(): a miss returns None, and None in this schema
            # means "I am a root division". So an unmapped parent silently
            # PROMOTED a child to the top of the institution — changing its
            # altitude, and changing what an agent routing from root is shown
            # — on input that was already corrupt. Every fresh slug is in
            # ``rename`` by construction (both claim passes above register
            # one), so a miss can only mean the fresh charter names a parent
            # no domain in the same charter defines.
            if parent not in rename:
                raise ValueError(
                    f"fresh charter domain {slug!r} names parent_slug "
                    f"{parent!r}, which no domain in that charter defines; "
                    "refusing to silently promote it to a root division"
                )
            domains[target]["parent_slug"] = rename[parent]
        else:
            domains[target]["parent_slug"] = None
        # child_slugs deliberately keeps ``rename.get(child, child)``: an
        # unmapped child is preserved VERBATIM rather than raising, because
        # unlike a dangling parent it restructures nothing, and the renderer
        # marks it (cli.py _render) so an operator sees the corruption instead
        # of succession quietly dropping the evidence of it.
        domains[target]["child_slugs"] = sorted(
            rename.get(child, child) for child in entry.get("child_slugs", [])
        )

    # Rank 2: tombstones. A live prior domain no fresh domain inherited is
    # retired in place, not dropped — deleting it would turn a stable
    # citation into a 404 the moment a reorg moved its anchor, defeating the
    # point of a stable slug. Membership in ``inherited_prior_slugs`` (built
    # from anchor matches above) is what decides this, NOT presence in
    # ``domains`` — review finding (a): a founded fresh domain occupying the
    # same slug TEXT as an unrelated dead prior domain must not shield that
    # prior domain from being tombstoned. Tombstones claim last: if a
    # retired slug's text collides with a still-live domain, the live domain
    # keeps the readable slug and the tombstone is what moves.
    for slug, entry in sorted(prior_domains.items()):
        if slug in inherited_prior_slugs:
            continue
        tombstone = dict(entry)
        tombstone["status"] = "retired"
        tombstone["transition"] = "retired"
        tombstone.setdefault("superseded_by", None)
        # reorg_seq is intentionally left at whatever it already was: a
        # tombstone is a frozen snapshot of the domain as it last was live,
        # not a record this reorg touched.
        _claim(slug, tombstone, None)

    member_index = {
        mid: rename.get(slug, slug) for mid, slug in sorted(fresh.get("member_index", {}).items())
    }
    return {
        "version": 1,
        "reorg_seq": next_seq,
        "domains": domains,
        "member_index": member_index,
    }


#: The two keys ``succeed`` rewrites on EVERY call regardless of whether the
#: institution moved. Excluded from the no-op comparison below, and from
#: nothing else.
_REORG_BOOKKEEPING = frozenset({"reorg_seq", "transition"})


def is_noop_reorg(prior: dict, merged: dict) -> bool:
    """Did ``succeed`` change the institution, or only its bookkeeping?

    ``succeed`` is unconditional by construction: ``next_seq`` is
    ``prior.reorg_seq + 1`` and every carried domain is re-stamped ``stable``
    at that seq, so calling it on an UNCHANGED graph still returns a different
    payload from the one on disk. Writing that payload every compile would
    make ``charter.json`` the one compile output that can never be
    byte-idempotent — churning a file whose entire purpose is to be the stable
    thing everything else is keyed on — and would redefine ``reorg_seq`` from
    "how many times this institution has been reorganised" into "how many
    times this project has been compiled", which is not a fact anyone can use.

    So the seq advances when the institution advances. Comparing everything
    EXCEPT the two bookkeeping keys is what decides that: same slugs, same
    parents, same children, same anchors, same members, same statuses means no
    reorg happened and the bytes on disk are already correct.

    A domain therefore keeps the ``transition`` and ``reorg_seq`` of the reorg
    that last MOVED it, which is the same rule ``succeed`` already applies to
    tombstones ("a frozen snapshot of the domain as it last was live, not a
    record this reorg touched") — now applied to live domains too.
    """

    def identity(charter: dict) -> str:
        return json.dumps(
            {
                "version": charter.get("version"),
                "member_index": charter.get("member_index", {}),
                "domains": {
                    slug: {
                        key: value
                        for key, value in entry.items()
                        if key not in _REORG_BOOKKEEPING
                    }
                    for slug, entry in charter.get("domains", {}).items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    return identity(prior) == identity(merged)


def _altitude_for(tier: int, member_count: int) -> str:
    """Depth maps to altitude, clamped by size so a label means the same thing
    across branches — without the clamp a depth-2 domain of 60 members and one
    of 700 would both read as 'department'."""
    if tier == 1:
        return "division"
    if tier == 2 and member_count >= 100:
        return "department"
    return "team"
