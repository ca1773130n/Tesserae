# CHARTER Plan 1 — Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive a stable, chartered division→department→team structure over the research graph and persist it as `.tesserae/charter/charter.json`, ending with `tesserae domains status` printing the real tree.

**Architecture:** Community detection *proposes* a domain vocabulary; a versioned charter *owns* it between explicit reorgs. Divisions come from a quotient graph (sections as nodes, cross-section edges as edges) fed back through the same `detect_communities`. Oversized domains split recursively by sub-community. Identity survives ingest churn via anchor-then-cell succession.

**Tech Stack:** Python 3.10+, stdlib only for new code, `networkx` (already a dependency, used lazily inside `detect_community_levels`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-charter-expertise-org-design.md`

**Scope:** This plan builds structure only. Briefs/altitude (Plan 2) and routing/attach (Plan 3) follow.

## Global Constraints

- Run tests with `uv run pytest` from the repo root. Bare `pytest` walks into gitignored `evals/` clones and fails collection.
- **Never construct `ResearchNode` positionally.** Field order is `(id, name, type, aliases, description, source_path, metadata)` — `aliases` precedes `description`, so `ResearchNode(cid, title, type, desc)` silently puts the description into `aliases`. Keyword arguments only.
- `ResearchEdge.__post_init__` raises `ValueError` for any `type` not in `ALLOWED_EDGE_TYPES` (`research_graph.py:286-393`). `"part_of"` is valid; `"quotient_of"`, `"related_to"`, `"member_of"` are **not**.
- `detect_communities` returns `List[List[str]]`: inner lists sorted, **outer list NOT sorted** (Louvain emission order, deliberately preserved — `community_summaries.py:88-91`).
- `detect_communities` **does not partition the node set**. The `len(c) > 1` filter at `community_summaries.py:106` drops every singleton. Never assume `set(chain(*clusters)) == {n.id for n in graph.nodes}`.
- `_undirected_projection` (`community_summaries.py:111`) silently drops self-loops and edges whose endpoints are not in `graph.nodes`. Synthetic quotient edges are invisible to Louvain unless the synthetic nodes are also in `graph.nodes`.
- `ARTIFACT_CHAR_BUDGET = 48_000` at `agent_distill.py:155`. It is a constant, not an env knob.
- `DOMAIN_MASS_CAP = 24_000` and `DOMAIN_MASS_FLOOR = 3_000` are literal module constants in `charter.py`. Do **not** derive `DOMAIN_MASS_CAP` from `CHUNK_CHAR_BUDGET`; an env override of `TESSERAE_LLM_CHUNK_CHARS` must never reshape the tree.
- No new `ResearchNodeType`. The charter is a separate artifact; `graph.json` bytes do not change.
- All writes go through `_publish_atomically` (`project.py`) — tmp file with pid+token suffix, then `os.replace`.
- `charter.json` has sorted keys and no timestamps. `reorg_seq` is an integer.

---

### Task 1: Module skeleton, constants, and mass()

**Files:**
- Create: `tesserae/charter.py`
- Test: `tests/test_charter.py`

**Interfaces:**
- Consumes: `_render_member_block` from `tesserae.agent_distill`
- Produces: `DOMAIN_MASS_CAP: int`, `DOMAIN_MASS_FLOOR: int`, `mass(nodes: Sequence[ResearchNode]) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_charter.py
from __future__ import annotations

from tesserae.charter import DOMAIN_MASS_CAP, DOMAIN_MASS_FLOOR, mass
from tesserae.research_graph import ResearchNode, ResearchNodeType


def _node(nid: str, name: str, description: str = "") -> ResearchNode:
    return ResearchNode(
        id=nid, name=name, type=ResearchNodeType.CONCEPT, description=description
    )


def test_mass_counts_the_bytes_the_distill_prompt_would_consume():
    nodes = [_node("Concept:a", "Alpha", "first"), _node("Concept:b", "Beta", "second")]
    # mass is the sum of rendered member blocks — the same text the prompt packs.
    assert mass(nodes) > 0
    assert mass(nodes) == mass(list(reversed(nodes))), "mass must be order-free"
    assert mass([]) == 0


def test_mass_constants_are_literals_not_derived_from_chunk_budget():
    # Deriving the cap from CHUNK_CHAR_BUDGET would let TESSERAE_LLM_CHUNK_CHARS
    # reshape the tree — the leak class agent_distill.py:150-155 warns about.
    assert DOMAIN_MASS_CAP == 24_000
    assert DOMAIN_MASS_FLOOR == 3_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesserae.charter'`

- [ ] **Step 3: Write minimal implementation**

```python
# tesserae/charter.py
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

from typing import Sequence

from .agent_distill import _render_member_block
from .research_graph import ResearchNode

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/charter.py tests/test_charter.py
git commit -m "feat(charter): module skeleton with mass() and the split constants"
```

---

### Task 2: Sections — the first detection pass

**Files:**
- Modify: `tesserae/charter.py`
- Test: `tests/test_charter.py`

**Interfaces:**
- Consumes: `detect_communities` from `tesserae.community_summaries`
- Produces: `sections(graph: ResearchGraph) -> tuple[list[list[str]], list[str]]` returning `(clusters, dropped_singleton_ids)`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_charter.py
from tesserae.charter import sections
from tesserae.research_graph import ResearchEdge, ResearchGraph


def _two_triangles_plus_orphan() -> ResearchGraph:
    """Two triangles bridged once, plus one node no edge touches."""
    nodes = [
        ResearchNode(id=f"Concept:a{i}", name=f"A{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [
        ResearchNode(id=f"Concept:b{i}", name=f"B{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [ResearchNode(id="Concept:lonely", name="Lonely", type=ResearchNodeType.CONCEPT)]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(ResearchEdge(source=f"Concept:a{i}", target=f"Concept:a{j}", type="shares_concept_with"))
            edges.append(ResearchEdge(source=f"Concept:b{i}", target=f"Concept:b{j}", type="shares_concept_with"))
    edges.append(ResearchEdge(source="Concept:a0", target="Concept:b0", type="shares_concept_with"))
    return ResearchGraph(nodes=nodes, edges=edges)


def test_sections_returns_clusters_and_reports_dropped_singletons():
    graph = _two_triangles_plus_orphan()
    clusters, dropped = sections(graph)

    assert len(clusters) == 2
    assert all(c == sorted(c) for c in clusters), "members must be sorted"
    # THE TRAP: detect_communities drops singletons at community_summaries.py:106,
    # so clusters do NOT partition the node set. sections() must report the
    # remainder explicitly or those nodes vanish from the institution.
    assert dropped == ["Concept:lonely"]
    covered = {nid for c in clusters for nid in c}
    assert covered | set(dropped) == {n.id for n in graph.nodes}


def test_sections_is_deterministic():
    graph = _two_triangles_plus_orphan()
    assert sections(graph) == sections(graph)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter.py::test_sections_returns_clusters_and_reports_dropped_singletons -v`
Expected: FAIL with `ImportError: cannot import name 'sections'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/charter.py imports
from .community_summaries import detect_communities
from .research_graph import ResearchGraph

# add to tesserae/charter.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/charter.py tests/test_charter.py
git commit -m "feat(charter): sections() reports the singletons detection drops"
```

---

### Task 3: The quotient graph — divisions from cross-section edges

**Files:**
- Modify: `tesserae/charter.py`
- Test: `tests/test_charter.py`

**Interfaces:**
- Consumes: `sections`, `detect_communities`
- Produces: `quotient_graph(graph, clusters) -> ResearchGraph`, `divisions(graph, clusters) -> list[list[int]]` (lists of indices into `clusters`)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_charter.py
from tesserae.charter import divisions, quotient_graph


def test_quotient_graph_nodes_and_edges_are_both_present():
    """_undirected_projection drops edges whose endpoints are not in
    graph.nodes (community_summaries.py:131-132), so a quotient graph that
    carries edges but not their synthetic nodes is INVISIBLE to Louvain."""
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    q = quotient_graph(graph, clusters)

    assert len(q.nodes) == len(clusters)
    node_ids = {n.id for n in q.nodes}
    for edge in q.edges:
        assert edge.source in node_ids and edge.target in node_ids
    # The single a0-b0 bridge becomes exactly one cross-section edge.
    assert len(q.edges) == 1
    assert all(e.type == "part_of" for e in q.edges)


def test_quotient_edge_type_is_allowed():
    """ResearchEdge.__post_init__ raises ValueError for a type outside
    ALLOWED_EDGE_TYPES. 'part_of' is valid; 'quotient_of' is not."""
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    quotient_graph(graph, clusters)  # must not raise


def test_divisions_group_sections_that_share_edges():
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    groups = divisions(graph, clusters)
    # Both sections are bridged, so they land in one division.
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1]


def test_divisions_is_deterministic():
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    assert divisions(graph, clusters) == divisions(graph, clusters)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter.py -k quotient -v`
Expected: FAIL with `ImportError: cannot import name 'quotient_graph'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/charter.py imports
from .research_graph import ResearchEdge, ResearchNode, ResearchNodeType

# add to tesserae/charter.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/charter.py tests/test_charter.py
git commit -m "feat(charter): quotient graph collapses 1,820 sections into balanced divisions"
```

---

### Task 4: Intake — everything structure cannot route

**Files:**
- Modify: `tesserae/charter.py`
- Test: `tests/test_charter.py`

**Interfaces:**
- Consumes: `sections`, `divisions`
- Produces: `intake_members(graph, clusters, groups) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_charter.py
from tesserae.charter import intake_members


def test_intake_collects_singletons_and_edge_isolated_sections():
    """Two disjoint triangles with NO bridge: both sections are quotient
    singletons, so neither joins a division and both fall to intake, along
    with the orphan node detection dropped entirely."""
    nodes = [
        ResearchNode(id=f"Concept:a{i}", name=f"A{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [
        ResearchNode(id=f"Concept:b{i}", name=f"B{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [ResearchNode(id="Concept:lonely", name="Lonely", type=ResearchNodeType.CONCEPT)]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(ResearchEdge(source=f"Concept:a{i}", target=f"Concept:a{j}", type="shares_concept_with"))
            edges.append(ResearchEdge(source=f"Concept:b{i}", target=f"Concept:b{j}", type="shares_concept_with"))
    graph = ResearchGraph(nodes=nodes, edges=edges)

    clusters, dropped = sections(graph)
    groups = divisions(graph, clusters)
    members = intake_members(graph, clusters, groups)

    assert groups == [], "no cross-section edge means no division"
    assert "Concept:lonely" in members
    assert set(members) == {n.id for n in graph.nodes}
    assert members == sorted(members), "intake membership must be sorted"


def test_intake_is_empty_when_every_section_is_routed():
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    groups = divisions(graph, clusters)
    members = intake_members(graph, clusters, groups)
    # Only the true orphan is unroutable.
    assert members == ["Concept:lonely"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter.py -k intake -v`
Expected: FAIL with `ImportError: cannot import name 'intake_members'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/charter.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/charter.py tests/test_charter.py
git commit -m "feat(charter): intake collects what structure cannot route"
```

---

### Task 5: Recursive split by the one-read bound

**Files:**
- Modify: `tesserae/charter.py`
- Test: `tests/test_charter.py`

**Interfaces:**
- Consumes: `mass`, `DOMAIN_MASS_CAP`, `DOMAIN_MASS_FLOOR`, `detect_communities`
- Produces: `induced_subgraph(graph, member_ids) -> ResearchGraph`, `split(graph, member_ids) -> SplitResult` where `SplitResult` is a frozen dataclass `(children: tuple[tuple[str, ...], ...], direct: tuple[str, ...], stalled: bool)`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_charter.py
import pytest

from tesserae.charter import SplitResult, induced_subgraph, split


def _fat_node(nid: str, filler: int) -> ResearchNode:
    return ResearchNode(
        id=nid, name=nid, type=ResearchNodeType.CONCEPT, description="x" * filler
    )


def _two_fat_triangles() -> ResearchGraph:
    """Two triangles, each heavy enough that the pair exceeds DOMAIN_MASS_CAP
    and each side clears DOMAIN_MASS_FLOOR."""
    nodes = [_fat_node(f"Concept:a{i}", 5_000) for i in range(3)]
    nodes += [_fat_node(f"Concept:b{i}", 5_000) for i in range(3)]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(ResearchEdge(source=f"Concept:a{i}", target=f"Concept:a{j}", type="shares_concept_with"))
            edges.append(ResearchEdge(source=f"Concept:b{i}", target=f"Concept:b{j}", type="shares_concept_with"))
    edges.append(ResearchEdge(source="Concept:a0", target="Concept:b0", type="shares_concept_with"))
    return ResearchGraph(nodes=nodes, edges=edges)


def test_induced_subgraph_keeps_only_internal_edges():
    graph = _two_fat_triangles()
    sub = induced_subgraph(graph, ["Concept:a0", "Concept:a1", "Concept:a2"])
    assert {n.id for n in sub.nodes} == {"Concept:a0", "Concept:a1", "Concept:a2"}
    for edge in sub.edges:
        assert edge.source in {n.id for n in sub.nodes}
        assert edge.target in {n.id for n in sub.nodes}
    assert len(sub.edges) == 3  # the a0-b0 bridge is excluded


def test_split_divides_an_oversized_domain_by_sub_community():
    graph = _two_fat_triangles()
    members = [n.id for n in graph.nodes]
    assert mass(graph.nodes) > DOMAIN_MASS_CAP

    result = split(graph, members)
    assert isinstance(result, SplitResult)
    assert not result.stalled
    assert len(result.children) == 2
    # Children are sorted by (-mass, first id) so the result is stable.
    assert result.children[0] == ("Concept:a0", "Concept:a1", "Concept:a2") or \
           result.children[0] == ("Concept:b0", "Concept:b1", "Concept:b2")
    # CH-01: children plus direct exactly reconstruct the input.
    covered = {mid for child in result.children for mid in child} | set(result.direct)
    assert covered == set(members)


def test_split_stalls_loudly_rather_than_raising_when_it_cannot_divide():
    """One node too big to split has no sub-community. It must be flagged
    unsplittable and degrade, not raise — the artifact layer already has a
    counted-remainder path for this."""
    graph = ResearchGraph(nodes=[_fat_node("Concept:huge", 30_000)], edges=[])
    result = split(graph, ["Concept:huge"])
    assert result.stalled is True
    assert result.children == ()
    assert result.direct == ("Concept:huge",)


def test_split_leaves_a_small_domain_alone():
    graph = _two_triangles_plus_orphan()
    members = [n.id for n in graph.nodes]
    result = split(graph, members)
    assert result.children == ()
    assert set(result.direct) == set(members)
    assert result.stalled is False


def test_split_is_deterministic():
    graph = _two_fat_triangles()
    members = [n.id for n in graph.nodes]
    assert split(graph, members) == split(graph, members)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter.py -k split -v`
Expected: FAIL with `ImportError: cannot import name 'SplitResult'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/charter.py imports
from dataclasses import dataclass

# add to tesserae/charter.py
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
    return SplitResult(children=children, direct=direct, stalled=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/charter.py tests/test_charter.py
git commit -m "feat(charter): recursive split by sub-community, stalling loudly rather than raising"
```

---

### Task 6: Anchors and slugs — stable identity

**Files:**
- Modify: `tesserae/charter.py`
- Test: `tests/test_charter.py`

**Interfaces:**
- Consumes: `undirected_degrees` from `tesserae.hierarchy`
- Produces: `assign_anchors(graph, member_sets) -> list[str]`, `slug_for(name: str, taken: Set[str]) -> str`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_charter.py
from tesserae.charter import assign_anchors, slug_for


def test_anchors_are_top_degree_and_never_shared_between_siblings():
    graph = _two_triangles_plus_orphan()
    a = ["Concept:a0", "Concept:a1", "Concept:a2"]
    b = ["Concept:b0", "Concept:b1", "Concept:b2"]
    anchors = assign_anchors(graph, [a, b])
    assert len(anchors) == 2
    assert len(set(anchors)) == 2, "siblings must not claim the same anchor"
    assert anchors[0] in a and anchors[1] in b


def test_anchor_assignment_is_deterministic():
    graph = _two_triangles_plus_orphan()
    sets = [["Concept:a0", "Concept:a1", "Concept:a2"], ["Concept:b0", "Concept:b1", "Concept:b2"]]
    assert assign_anchors(graph, sets) == assign_anchors(graph, sets)


def test_slug_is_stable_and_deduped():
    taken: set[str] = set()
    first = slug_for("3D Gaussian Splatting", taken)
    taken.add(first)
    assert first == "3d-gaussian-splatting"
    second = slug_for("3D Gaussian Splatting", taken)
    assert second == "3d-gaussian-splatting-2", "a collision must not overwrite"


def test_slug_handles_non_ascii_without_collapsing_to_empty():
    taken: set[str] = set()
    assert slug_for("한 줄 요약", taken) != ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter.py -k anchor -v`
Expected: FAIL with `ImportError: cannot import name 'assign_anchors'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/charter.py imports
import re
import unicodedata
from typing import Set

from .hierarchy import undirected_degrees

# add to tesserae/charter.py
def assign_anchors(
    graph: ResearchGraph, member_sets: Sequence[Sequence[str]]
) -> list[str]:
    """Pick each domain's top-degree member, greedily, no two the same.

    The anchor is the identity substrate: a hub does not move when 15 nodes
    arrive. Measured preservation under a one-document perturbation is 97.0%
    at fine level and 81.0% at coarse, against member-set Jaccard which fails
    for ~72% of large scopes. Assignment is greedy in ``(-degree, id)`` order
    ACROSS siblings so two domains can never claim the same anchor.
    """
    degrees = undirected_degrees(graph)
    ranked: list[tuple[int, int, str]] = []
    for index, members in enumerate(member_sets):
        for member_id in members:
            ranked.append((-degrees.get(member_id, 0), index, member_id))
    ranked.sort(key=lambda row: (row[0], row[2]))

    anchors: dict[int, str] = {}
    claimed: set[str] = set()
    for _degree, index, member_id in ranked:
        if index in anchors or member_id in claimed:
            continue
        anchors[index] = member_id
        claimed.add(member_id)
    return [anchors.get(i, sorted(m)[0] if m else "") for i, m in enumerate(member_sets)]


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
        # Non-Latin names (e.g. "한 줄 요약") strip to nothing. A content hash
        # keeps the slug stable and unique rather than falling back to a
        # counter that would move when siblings are reordered.
        import hashlib

        base = "domain-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/charter.py tests/test_charter.py
git commit -m "feat(charter): anchor-based identity and stable human-facing slugs"
```

---

### Task 7: Build the charter and persist it

**Files:**
- Modify: `tesserae/charter.py`
- Test: `tests/test_charter.py`

**Interfaces:**
- Consumes: everything above
- Produces: `build_charter(graph, *, exclude_synthesis=True) -> dict`, `charter_path(project_root) -> Path`, `write_charter(project_root, charter) -> Path`, `read_charter(project_root) -> dict | None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_charter.py
import json
from pathlib import Path

from tesserae.charter import build_charter, charter_path, read_charter, write_charter


def test_build_charter_partitions_every_node_exactly_once():
    """CH-01, the invariant the whole structure rests on."""
    graph = _two_triangles_plus_orphan()
    charter = build_charter(graph)

    seen: list[str] = []
    for entry in charter["domains"].values():
        seen.extend(entry["direct_member_ids"])
    assert sorted(seen) == sorted(n.id for n in graph.nodes)
    assert len(seen) == len(set(seen)), "a node may belong to exactly one domain"


def test_build_charter_excludes_synthesis_nodes_by_default():
    """Measured on the live graph, leaving these in makes roughly half the
    institution an org chart of Tesserae's own output: division anchors came
    out as 'Project Pulse' and '한 줄 요약'."""
    graph = _two_triangles_plus_orphan()
    graph.nodes.append(
        ResearchNode(id="Synthesis:pulse", name="Project Pulse", type=ResearchNodeType.SYNTHESIS)
    )
    charter = build_charter(graph)
    everyone = {
        mid for e in charter["domains"].values() for mid in e["direct_member_ids"]
    }
    assert "Synthesis:pulse" not in everyone

    kept = build_charter(graph, exclude_synthesis=False)
    everyone_kept = {
        mid for e in kept["domains"].values() for mid in e["direct_member_ids"]
    }
    assert "Synthesis:pulse" in everyone_kept


def test_charter_round_trips_and_is_byte_stable(tmp_path: Path):
    graph = _two_triangles_plus_orphan()
    charter = build_charter(graph)
    path = write_charter(tmp_path, charter)
    assert path == charter_path(tmp_path)
    assert read_charter(tmp_path) == charter

    first = path.read_bytes()
    write_charter(tmp_path, build_charter(graph))
    assert path.read_bytes() == first, "same input must produce identical bytes"


def test_charter_has_no_timestamps():
    """A wall-clock field would break byte-idempotence on every rebuild."""
    charter = build_charter(_two_triangles_plus_orphan())
    blob = json.dumps(charter)
    assert "timestamp" not in blob and "generated_at" not in blob
    assert charter["reorg_seq"] == 0


def test_read_charter_returns_none_when_absent(tmp_path: Path):
    assert read_charter(tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter.py -k charter -v`
Expected: FAIL with `ImportError: cannot import name 'build_charter'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/charter.py imports
import json
from pathlib import Path
from typing import Optional

# add to tesserae/charter.py
#: Node types that are Tesserae's OWN output rather than knowledge it ingested.
#: Excluded by default: measured on the live graph, leaving them in made
#: division 3's anchor "Project Pulse", division 2's "한 줄 요약", and four of
#: the 3DGS division's top eight departments "Daily Digest — <date>" pages —
#: roughly half the institution describing the tool instead of the subject.
_SYNTHESIS_TYPES = frozenset(
    {ResearchNodeType.SYNTHESIS, ResearchNodeType.COMMUNITY_SUMMARY}
)

_INTAKE_SLUG = "intake"


def charter_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".tesserae" / "charter" / "charter.json"


def read_charter(project_root: Path | str) -> Optional[dict]:
    path = charter_path(project_root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_charter(project_root: Path | str, charter: dict) -> Path:
    """Persist with sorted keys and no timestamps, through the atomic publish."""
    from .project import _publish_atomically

    path = charter_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _publish_atomically(
        path, json.dumps(charter, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return path


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
    anchors = assign_anchors(scoped, division_members)
    by_id = {n.id: n for n in scoped.nodes}

    domains: dict[str, dict] = {}
    taken: set[str] = set()
    member_index: dict[str, str] = {}

    def _emit(members: Sequence[str], anchor_id: str, tier: int, parent: Optional[str]) -> str:
        anchor_name = by_id[anchor_id].name if anchor_id in by_id else anchor_id
        slug = slug_for(anchor_name, taken)
        taken.add(slug)
        result = split(scoped, members)
        child_slugs: list[str] = []
        if result.children:
            child_anchors = assign_anchors(scoped, [list(c) for c in result.children])
            for child_members, child_anchor in zip(result.children, child_anchors):
                child_slugs.append(_emit(list(child_members), child_anchor, tier + 1, slug))
        domains[slug] = {
            "tier": tier,
            "own_altitude": _altitude_for(tier, len(members)),
            "parent_slug": parent,
            "child_slugs": sorted(child_slugs),
            "anchor_id": anchor_id,
            "direct_member_ids": sorted(result.direct),
            "member_count": len(members),
            "reorg_seq": 0,
            "status": "live",
            "transition": "founded",
            "unsplittable": result.stalled,
        }
        for mid in result.direct:
            member_index[mid] = slug
        return slug

    for members, anchor in zip(division_members, anchors):
        _emit(members, anchor, 1, None)

    if intake:
        domains[_INTAKE_SLUG] = {
            "tier": 1,
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

    return {
        "version": 1,
        "reorg_seq": 0,
        "domains": domains,
        "member_index": member_index,
    }


def _altitude_for(tier: int, member_count: int) -> str:
    """Depth maps to altitude, clamped by size so a label means the same thing
    across branches — without the clamp a depth-2 domain of 60 members and one
    of 700 would both read as 'department'."""
    if tier == 1:
        return "division"
    if tier == 2 and member_count >= 100:
        return "department"
    return "team"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/charter.py tests/test_charter.py
git commit -m "feat(charter): build and persist the charter, synthesis nodes excluded"
```

---

### Task 8: Succession — surviving a reorg

**Files:**
- Modify: `tesserae/charter.py`
- Test: `tests/test_charter.py`

**Interfaces:**
- Consumes: `read_charter`, `build_charter`
- Produces: `succeed(prior: dict, fresh: dict) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_charter.py
from tesserae.charter import succeed


def _charter_with(slug: str, anchor: str, members: list[str], seq: int = 0) -> dict:
    return {
        "version": 1,
        "reorg_seq": seq,
        "domains": {
            slug: {
                "tier": 1, "own_altitude": "division", "parent_slug": None,
                "child_slugs": [], "anchor_id": anchor,
                "direct_member_ids": sorted(members), "member_count": len(members),
                "reorg_seq": seq, "status": "live", "transition": "founded",
                "unsplittable": False,
            }
        },
        "member_index": {m: slug for m in members},
    }


def test_a_domain_keeps_its_slug_when_its_anchor_survives():
    """The whole point: one 15-node document moves ~29% of members, so
    membership cannot key identity. A hub does not move."""
    prior = _charter_with("alpha", "Concept:hub", ["Concept:hub", "Concept:x"])
    fresh = _charter_with("beta", "Concept:hub", ["Concept:hub", "Concept:y", "Concept:z"])
    merged = succeed(prior, fresh)

    assert "alpha" in merged["domains"], "slug must survive on anchor match"
    assert "beta" not in merged["domains"]
    assert merged["domains"]["alpha"]["transition"] == "stable"
    assert merged["domains"]["alpha"]["direct_member_ids"] == ["Concept:hub", "Concept:y", "Concept:z"]
    assert merged["reorg_seq"] == 1


def test_a_domain_whose_anchor_moved_gets_a_new_slug_and_the_old_is_tombstoned():
    prior = _charter_with("alpha", "Concept:gone", ["Concept:gone", "Concept:x"])
    fresh = _charter_with("beta", "Concept:new", ["Concept:new", "Concept:q"])
    merged = succeed(prior, fresh)

    assert merged["domains"]["beta"]["transition"] == "founded"
    assert merged["domains"]["alpha"]["status"] == "retired"
    assert merged["domains"]["alpha"]["superseded_by"] is None
    # A tombstone stays readable so an old citation degrades to a message
    # rather than a missing file.
    assert "alpha" in merged["domains"]


def test_succession_is_deterministic():
    prior = _charter_with("alpha", "Concept:hub", ["Concept:hub"])
    fresh = _charter_with("beta", "Concept:hub", ["Concept:hub", "Concept:y"])
    assert succeed(prior, fresh) == succeed(prior, fresh)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter.py -k succe -v`
Expected: FAIL with `ImportError: cannot import name 'succeed'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/charter.py
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
    """
    anchor_to_prior = {
        entry["anchor_id"]: slug
        for slug, entry in sorted(prior.get("domains", {}).items())
        if entry.get("status") == "live" and entry.get("anchor_id")
    }
    next_seq = int(prior.get("reorg_seq", 0)) + 1

    domains: dict[str, dict] = {}
    rename: dict[str, str] = {}
    for slug, entry in sorted(fresh.get("domains", {}).items()):
        inherited = anchor_to_prior.get(entry.get("anchor_id") or "")
        target = inherited or slug
        rename[slug] = target
        carried = dict(entry)
        carried["reorg_seq"] = next_seq
        carried["transition"] = "stable" if inherited else "founded"
        domains[target] = carried

    for slug, entry in sorted(fresh.get("domains", {}).items()):
        target = rename[slug]
        domains[target]["parent_slug"] = (
            rename.get(entry["parent_slug"]) if entry.get("parent_slug") else None
        )
        domains[target]["child_slugs"] = sorted(
            rename.get(child, child) for child in entry.get("child_slugs", [])
        )

    survivors = set(domains)
    for slug, entry in sorted(prior.get("domains", {}).items()):
        if slug in survivors:
            continue
        tombstone = dict(entry)
        tombstone["status"] = "retired"
        tombstone["transition"] = "retired"
        tombstone.setdefault("superseded_by", None)
        domains[slug] = tombstone

    member_index = {
        mid: rename.get(slug, slug) for mid, slug in sorted(fresh.get("member_index", {}).items())
    }
    return {
        "version": 1,
        "reorg_seq": next_seq,
        "domains": domains,
        "member_index": member_index,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter.py -v`
Expected: PASS (27 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/charter.py tests/test_charter.py
git commit -m "feat(charter): anchor-based succession keeps slugs stable across a reorg"
```

---

### Task 9: `tesserae domains status`

**Files:**
- Modify: `tesserae/cli.py`
- Test: `tests/test_charter_cli.py`

**Interfaces:**
- Consumes: `read_charter`, `build_charter`, `write_charter`
- Produces: CLI verb `tesserae domains status [--project PATH] [--json]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_charter_cli.py
from __future__ import annotations

import json
from pathlib import Path

from tesserae.charter import build_charter, write_charter
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def _graph() -> ResearchGraph:
    nodes = [
        ResearchNode(id=f"Concept:a{i}", name=f"A{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [
        ResearchNode(id=f"Concept:b{i}", name=f"B{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(ResearchEdge(source=f"Concept:a{i}", target=f"Concept:a{j}", type="shares_concept_with"))
            edges.append(ResearchEdge(source=f"Concept:b{i}", target=f"Concept:b{j}", type="shares_concept_with"))
    edges.append(ResearchEdge(source="Concept:a0", target="Concept:b0", type="shares_concept_with"))
    return ResearchGraph(nodes=nodes, edges=edges)


def test_domains_status_prints_the_tree(tmp_path: Path, capsys):
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    write_charter(tmp_path, build_charter(_graph()))

    rc = main(["domains", "status", "--project", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "division" in out
    assert "members" in out


def test_domains_status_json_is_machine_readable(tmp_path: Path, capsys):
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    write_charter(tmp_path, build_charter(_graph()))

    rc = main(["domains", "status", "--project", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["reorg_seq"] == 0
    assert payload["domains"]


def test_domains_status_says_so_when_there_is_no_charter(tmp_path: Path, capsys):
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    rc = main(["domains", "status", "--project", str(tmp_path)])
    out = capsys.readouterr().out + capsys.readouterr().err

    assert rc == 0
    assert "no charter" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charter_cli.py -v`
Expected: FAIL — `domains` is not a known command (exit 2)

- [ ] **Step 3: Write minimal implementation**

Add the handler near the other group handlers in `tesserae/cli.py`:

```python
def _build_domains_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae domains",
        description="Inspect the chartered domain structure (divisions, departments, teams).",
    )
    sub = parser.add_subparsers(dest="domains_command", required=True)
    p_status = sub.add_parser("status", help="Print the chartered domain tree.")
    p_status.add_argument("--project", default=".", help="Project root directory; defaults to the current directory")
    p_status.add_argument("--json", dest="as_json", action="store_true", help="Emit the charter payload as JSON.")
    p_status.set_defaults(_handler="_handle_domains_status")
    return parser


def _handle_domains_status(args: argparse.Namespace) -> int:
    from .charter import read_charter

    charter = read_charter(args.project)
    if charter is None:
        print(
            "no charter yet — the project is below the one-read bound, or has "
            "not been compiled since the charter pass landed."
        )
        return 0
    if getattr(args, "as_json", False):
        print(json.dumps(charter, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    domains = charter["domains"]
    roots = sorted(
        slug for slug, e in domains.items()
        if e["parent_slug"] is None and e["status"] == "live"
    )

    def _render(slug: str, depth: int) -> None:
        entry = domains[slug]
        flag = "  [unsplittable]" if entry.get("unsplittable") else ""
        print(
            f"{'  ' * depth}{slug}  ({entry['own_altitude']}, "
            f"{entry['member_count']} members){flag}"
        )
        for child in entry["child_slugs"]:
            _render(child, depth + 1)

    for slug in roots:
        _render(slug, 0)
    retired = sum(1 for e in domains.values() if e["status"] == "retired")
    print(f"\nreorg_seq={charter['reorg_seq']}  live={len(roots)} root(s)  retired={retired}")
    return 0
```

Register `domains` in the route table beside the other groups (find `"agents"` in the route dispatch and add `"domains"` alongside it, pointing at `_build_domains_parser`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_charter_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: all pass, count = previous baseline + 30

```bash
git add tesserae/cli.py tests/test_charter_cli.py
git commit -m "feat(cli): tesserae domains status prints the chartered tree"
```

---

## Self-review

**Spec coverage.** Structure section: sections (T2), quotient divisions (T3), intake (T4), recursive split (T5), tier labels (T7 `_altitude_for`), synthesis exclusion (T7), identity succession (T8), charter.json shape (T7). CH-01 partition lint is asserted in T7. The remaining five CH lints, `render_brief`, altitude, the distill extraction, routing, MCP and harness wiring are **Plans 2 and 3** — deliberately out of scope here.

**Not yet wired into compile.** `build_charter` is not called from `ProjectWiki.compile` in this plan. That hook lands in Plan 2, once briefs exist to make it worth running. Until then the charter is produced on demand, which keeps this plan's blast radius to new files plus one CLI verb.

**Type consistency.** `SplitResult(children, direct, stalled)` is used identically in T5 and T7. `slug_for(name, taken)` takes a mutable set the caller owns in both T6 and T7. `assign_anchors(graph, member_sets) -> list[str]` returns one anchor per input set, positionally aligned, in T6 and T7.

**Known gap carried forward.** `_altitude_for` clamps at `member_count >= 100` for tier 2, which is a guess; the spec records that quotas and clamps should be re-set once a real division brief exists to read.
