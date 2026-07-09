# Wiki Entrypoint (index.md as Agent Quickstart) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the wiki-root `index.md` (written by `KarpathyLayerWriter._write_index`) from a bare per-kind count table into the single deterministic entrypoint an agent reads first: what this knowledge base is, how to query it (compiled context / MCP tools FIRST — page-by-page wiki browsing costs roughly an order of magnitude more tokens than retrieval, per the OpenWiki/Karpathy-pattern evaluation), the kinds table, key pages (`schema.md`, `purpose.md`, the synthetic contradictions page when it exists), and the top communities. Also point the generated agent-harness files at this entrypoint.

**Architecture:** NO new files, kinds, or modules. Rewrite `_render_index` in `tesserae/karpathy_layer.py` (it already receives the post-memory-pass canonicalized graph — `project.py` calls `write_all` at line ~2606, after `project_contradictions` at line ~2600, so link presence and page presence derive from the same graph). Extract the dispute-collection step out of `wiki_projector._contradictions_page` into `_collect_disputes` and add a public `has_contradictions(graph)` so the index link condition can never drift from the page-emission condition. Expose `canonical_slug` publicly from `wiki_store.py` so the index can link community pages with the exact slugs `WikiLayerProjector` writes. Add one artifact line + one instruction bullet to `agent_harness.render_harness_context`.

**Tech Stack:** Python 3 stdlib only, pytest via `.venv/bin/python`. No new deps.

## Why not a new page

Verified before writing this plan: the wiki root already carries `purpose.md` / `schema.md` / `index.md` (`tesserae/karpathy_layer.py`), the vault root already has `README.md` + its own `index.md` (`obsidian_adapter.py`, `markdown_projection.py`), and the site has `render_home`. A new `quickstart.md` would be a fourth root file duplicating `index.md`'s role. OpenWiki's actual lesson is "ONE mandated entrypoint with links" — we already have the file; it just doesn't orient anyone. So: enrich `index.md`, don't mint a sibling.

## Global Constraints

- **Byte-idempotence (broke 4x historically).** Every write this plan touches, and why it is stable:
  - `.tesserae/wiki/index.md` (`_write_index`, unconditional `write_text`) — new body derives ONLY from (a) the canonicalized graph (per-kind counts; community nodes filtered by `kind_for_node(n) == "communities"` and sorted by `(name.lower(), id)`; dispute-edge presence via `has_contradictions`), (b) config-derived `project_name` / `site_title` (already dataclass fields), and (c) static strings. NO timestamps, NO wall-clock, NO dict-iteration order (every loop sorts), NO filesystem probes (`path.exists()` checks are forbidden in the renderer — derive everything from the graph). `index.md` lives inside `wiki_root`, which `tests/test_idempotence.py` already hashes across two compiles — the existing test guards the new content automatically.
  - `.tesserae/wiki/purpose.md` / `schema.md` — content untouched; behavior unchanged. `write_all` writes them BEFORE `index.md`, so the index's links to them never dangle.
  - Harness files (`claude/CLAUDE.md`, `codex/AGENTS.md`, …, via `agent_harness.write_harness`) — one new static artifact line + one static bullet; deterministic per graph, written on demand to a user-chosen out dir, not part of the compile-idempotence surface.
  - Nothing else writes: no `graph.json`, site, or vault changes.
- **No dangling links.** Contradictions link iff `has_contradictions(graph)` — the exact condition `_contradictions_page` emits under (shared `_collect_disputes`). Community links only for nodes where `kind_for_node(n) == "communities"` — the exact filter `WikiLayerProjector.project` writes pages under — with slugs from the same `canonical_slug` the store uses.
- Reuse, don't duplicate: `kind_for_node`, `_contradictions_page` internals, `_canonical_slug`, `_CONTRADICTIONS_KIND`/`_CONTRADICTIONS_SLUG`.
- Run tests with `.venv/bin/python -m pytest` (system python3 is 3.9 and fails collection).
- One commit per task; conventional messages.

---

## File Structure

- **Modify** `tesserae/wiki_projector.py` — extract `_collect_disputes(graph, nodes_by_id)`; add public `has_contradictions(graph)`; export it.
- **Modify** `tesserae/wiki_store.py` — public `canonical_slug(value)` wrapper over `_canonical_slug`.
- **Modify** `tesserae/karpathy_layer.py` — rewrite `_render_index` (signature unchanged: `(self, graph) -> str`).
- **Modify** `tesserae/agent_harness.py` — artifact line + instruction bullet in `render_harness_context`.
- **Modify** `tests/test_wiki_projector.py` — `has_contradictions` tests (fixtures `_node` / `_contradiction_graph` already there).
- **Create** `tests/test_karpathy_layer.py` — index-renderer tests (no existing test file dedicates to this module; only `test_incremental_parity.py` touches it incidentally).
- **Modify** `tests/test_agent_harness.py` — harness pointer test.

---

### Task 1: `has_contradictions(graph)` — shared dispute predicate

**Files:** Modify `tesserae/wiki_projector.py`; Test `tests/test_wiki_projector.py`

**Interfaces:**
- Produces: `_collect_disputes(graph: ResearchGraph, nodes_by_id: Mapping[str, ResearchNode]) -> tuple[list, list, list]` — the `(open_pairs, resolved, obsoleted)` lists currently built inline in `_contradictions_page` (identical semantics: endpoint-known edges only; `contradicts_claim` pairs suppressed when a `resolved_by` edge covers the same frozenset pair; rationale whitespace-collapsed).
- `has_contradictions(graph: ResearchGraph) -> bool` — `any(_collect_disputes(graph, {n.id: n for n in graph.nodes}))`, i.e. True iff `_contradictions_page` would emit a page. Add to `__all__`.
- `_contradictions_page` is refactored to call `_collect_disputes` (byte-identical output — the four existing contradictions tests must stay green untouched).

- [ ] **Step 1: Write the failing test** (append to `tests/test_wiki_projector.py`)

```python
def test_has_contradictions_true_only_for_dispute_edges_with_known_endpoints():
    from tesserae.wiki_projector import has_contradictions

    # Full dispute graph -> True.
    assert has_contradictions(_contradiction_graph())

    # No dispute edges -> False.
    a = _node("A", ResearchNodeType.PERFORMANCE_CLAIM)
    b = _node("B", ResearchNodeType.PERFORMANCE_CLAIM)
    plain = ResearchGraph(nodes=[a, b], edges=[
        ResearchEdge(source=a.id, target=b.id, type="mentions"),
    ])
    assert not has_contradictions(plain)

    # Dispute edge with a missing endpoint -> False (matches page emission).
    dangling = ResearchGraph(nodes=[a], edges=[
        ResearchEdge(source=a.id, target="missing", type="contradicts_claim"),
    ])
    assert not has_contradictions(dangling)
```

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/python -m pytest tests/test_wiki_projector.py::test_has_contradictions_true_only_for_dispute_edges_with_known_endpoints -v` → FAIL (ImportError).

- [ ] **Step 3: Implement** — in `wiki_projector.py`, lift the edge-scan block of `_contradictions_page` (the `resolved_pairs` set through the three lists, currently lines ~194–216) into:

```python
def _collect_disputes(graph, nodes_by_id):
    resolved_pairs = {
        frozenset((e.source, e.target)) for e in graph.edges if e.type == "resolved_by"
    }
    open_pairs, resolved, obsoleted = [], [], []
    for edge in graph.edges:
        if edge.source not in nodes_by_id or edge.target not in nodes_by_id:
            continue
        if edge.type == "contradicts_claim":
            if frozenset((edge.source, edge.target)) in resolved_pairs:
                continue
            a, b = sorted((edge.source, edge.target))
            open_pairs.append((a, b))
        elif edge.type == "resolved_by":
            resolved.append((edge.source, edge.target, " ".join((edge.evidence or "").split())))
        elif edge.type == "supersedes":
            obsoleted.append((edge.source, edge.target))
    return open_pairs, resolved, obsoleted


def has_contradictions(graph: ResearchGraph) -> bool:
    """True iff the synthetic contradictions page would be emitted for ``graph``.

    Single source of truth with ``_contradictions_page`` (via ``_collect_disputes``)
    so the wiki index link can never dangle.
    """
    nodes_by_id = {node.id: node for node in graph.nodes}
    return any(_collect_disputes(graph, nodes_by_id))
```

`_contradictions_page` becomes: `open_pairs, resolved, obsoleted = _collect_disputes(graph, nodes_by_id)` then the existing `if not (...)` and rendering, unchanged. Add `"has_contradictions"` to `__all__`.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_wiki_projector.py -q` → PASS (all 5, including the 4 pre-existing contradictions tests, untouched).

- [ ] **Step 5: Commit** — `git add tesserae/wiki_projector.py tests/test_wiki_projector.py && git commit -m "refactor(wiki): extract _collect_disputes + public has_contradictions"`

### Task 2: public `canonical_slug` in wiki_store

**Files:** Modify `tesserae/wiki_store.py`; Test `tests/test_wiki_store.py`

**Interfaces:**
- Produces: `canonical_slug(value: str) -> str` — public one-line wrapper delegating to `_canonical_slug` (the same function `WikiPageStore.slug_for` uses), so non-store consumers can compute page slugs without instantiating a store.

- [ ] **Step 1: Write the failing test** (append to `tests/test_wiki_store.py`)

```python
def test_canonical_slug_matches_store_slug_for(tmp_path):
    from tesserae.wiki_store import WikiPageStore, canonical_slug

    store = WikiPageStore(tmp_path)
    for name in ("Gaussian Splatting", "C++ / CUDA kernels", "  weird   spacing  "):
        assert canonical_slug(name) == store.slug_for(name)
```

- [ ] **Step 2: Run to verify it fails** — ImportError.

- [ ] **Step 3: Implement** — in `wiki_store.py`, below `_canonical_slug`:

```python
def canonical_slug(value: str) -> str:
    """Public alias for the store's slug rule (``WikiPageStore.slug_for``)."""
    return _canonical_slug(value)
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_wiki_store.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add tesserae/wiki_store.py tests/test_wiki_store.py && git commit -m "feat(wiki-store): public canonical_slug helper"`

### Task 3: rewrite `_render_index` as the agent entrypoint

**Files:** Modify `tesserae/karpathy_layer.py`; Test `tests/test_karpathy_layer.py` (new)

**Interfaces:**
- Consumes: `has_contradictions` (Task 1), `canonical_slug` (Task 2), existing `kind_for_node`, existing `_CONTRADICTIONS_KIND`/`_CONTRADICTIONS_SLUG` (import from `wiki_projector` — do NOT hardcode `"questions"`/`"contradictions"`).
- Produces: `_render_index(self, graph) -> str` emitting, in order:
  1. `# Index` H1 (unchanged) + one orientation paragraph naming `self.project_name`: this is the typed knowledge base compiled by Tesserae; markdown is a projection, `graph.json` is authoritative.
  2. `## How to query` — static text steering agents: **first** the compiled context / MCP tools (`compile_context`, `search_nodes`, `node_context`, `search_facts`, `wiki_page`, `query_decisions`, `lint_report`); **second** wiki browsing via the links below, with one static sentence: browsing pages one-by-one costs roughly an order of magnitude more tokens than a retrieval query — use links for orientation and deep dives, not routine lookups.
  3. `## Kinds` — the existing count table with one added relative-path column: `| Kind | Count | Wiki dir | Site route |` → `| {kind} | {n} | `{kind}/` | `<site>/{kind}/index.html` |`, kinds sorted; keep the `_(empty)_` row when no public nodes.
  4. `## Key pages` — always `[schema.md](schema.md)` and `[purpose.md](purpose.md)` bullets (both written earlier in the same `write_all` call); PLUS `[Contradictions](questions/contradictions.md)` built from `f"{_CONTRADICTIONS_KIND}/{_CONTRADICTIONS_SLUG}.md"` **only when** `has_contradictions(graph)`.
  5. `## Communities` — only when at least one node has `kind_for_node(n) == "communities"`: up to 10 bullets `- [{name}](communities/{canonical_slug(name)}.md)`, sorted by `(name.lower(), id)`, capped at 10 (`[:10]`).
- Determinism: no timestamps, no `Path.exists()`, no unsorted iteration; body ends `.rstrip() + "\n"` as today.

- [ ] **Step 1: Write the failing tests** (create `tests/test_karpathy_layer.py`)

```python
"""Tests for the wiki-root index.md agent entrypoint (KarpathyLayerWriter)."""

from tesserae.karpathy_layer import KarpathyLayerWriter
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)


def _node(name, node_type, **kwargs):
    return ResearchNode(
        id=kwargs.pop("id", stable_id(node_type.value, name)),
        name=name,
        type=node_type,
        aliases=kwargs.pop("aliases", []),
        source_path=kwargs.pop("source_path", None),
        metadata=kwargs.pop("metadata", {}),
        description=kwargs.pop("description", ""),
    )


def _writer(tmp_path):
    return KarpathyLayerWriter(wiki_root=tmp_path, project_name="demo-project")


def _graph(nodes=(), edges=()):
    return ResearchGraph(nodes=list(nodes), edges=list(edges))


def test_index_is_agent_entrypoint_with_query_guidance(tmp_path):
    concept = _node("Gaussian Splatting", ResearchNodeType.CONCEPT)
    body = _writer(tmp_path)._render_index(_graph([concept]))
    assert body.startswith("# Index")
    assert "demo-project" in body
    assert "## How to query" in body
    # MCP-first steering, wiki-browsing second.
    assert "compile_context" in body and "search_nodes" in body
    assert "## Kinds" in body and "| concepts | 1 |" in body
    assert "`concepts/`" in body  # relative wiki-dir column
    assert "[schema.md](schema.md)" in body
    assert "[purpose.md](purpose.md)" in body


def test_index_links_contradictions_page_only_when_disputes_exist(tmp_path):
    a = _node("X beats Y", ResearchNodeType.PERFORMANCE_CLAIM)
    b = _node("Y beats X", ResearchNodeType.PERFORMANCE_CLAIM)
    w = _writer(tmp_path)
    without = w._render_index(_graph([a, b]))
    assert "contradictions.md" not in without
    with_disputes = w._render_index(_graph(
        [a, b],
        [ResearchEdge(source=a.id, target=b.id, type="contradicts_claim")],
    ))
    assert "(questions/contradictions.md)" in with_disputes


def test_index_lists_top_communities_sorted_and_capped(tmp_path):
    comms = [
        _node(f"Community {i:02d}", ResearchNodeType.COMMUNITY_SUMMARY)
        for i in range(12)
    ]
    body = _writer(tmp_path)._render_index(_graph(comms))
    assert "## Communities" in body
    assert "(communities/community-00.md)" in body
    assert "community-09" in body and "community-10" not in body  # capped at 10
    # No section at all when there are no communities.
    assert "## Communities" not in _writer(tmp_path)._render_index(_graph([]))


def test_index_render_is_deterministic_across_node_order(tmp_path):
    nodes = [
        _node("Zeta", ResearchNodeType.CONCEPT),
        _node("Alpha", ResearchNodeType.COMMUNITY_SUMMARY),
        _node("Mid", ResearchNodeType.PAPER),
    ]
    w = _writer(tmp_path)
    assert w._render_index(_graph(nodes)) == w._render_index(_graph(list(reversed(nodes))))
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_karpathy_layer.py -v` → FAIL (guidance/sections missing from current count-table body).

- [ ] **Step 3: Implement** — rewrite `_render_index` per the interface above. Imports at module top: `from .wiki_projector import _CONTRADICTIONS_KIND, _CONTRADICTIONS_SLUG, has_contradictions, kind_for_node` (extend the existing `wiki_projector` import) and `from .wiki_store import canonical_slug`. Community selection:

```python
communities = sorted(
    (n for n in graph.nodes if kind_for_node(n) == "communities"),
    key=lambda n: (n.name.lower(), n.id),
)[:10]
```

Do not touch `_write_index`, `write_all`, or the other renderers. Update the module docstring's `index.md` line ("wiki-layer table of contents" → "agent entrypoint: query guidance + table of contents").

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_karpathy_layer.py tests/test_wiki_projector.py tests/test_idempotence.py -q` → PASS. `test_idempotence.py` re-proves the new index bytes are stable across two full compiles of the wiki corpus fixture.

- [ ] **Step 5: Commit** — `git add tesserae/karpathy_layer.py tests/test_karpathy_layer.py && git commit -m "feat(wiki): index.md becomes the agent entrypoint (query guidance, key pages, communities)"`

### Task 4: point the agent harness at the entrypoint

**Files:** Modify `tesserae/agent_harness.py`; Test `tests/test_agent_harness.py`

**Interfaces:**
- Produces: in `render_harness_context`, (a) one artifact bullet after the graph.json line: `` "- `.tesserae/wiki/index.md` — wiki entrypoint: query guidance + table of contents" ``; (b) one instruction bullet after "Prefer MCP graph queries...": `` "- When you do browse the wiki, start at `.tesserae/wiki/index.md` and follow its links; do not crawl pages blindly." ``. Static strings only — determinism unaffected.

- [ ] **Step 1: Write the failing test** (append to `tests/test_agent_harness.py`, reusing that file's existing fixture/helper style for building a graph + calling `render_harness_context` — read the file's existing tests first and mirror them)

```python
def test_harness_context_points_agents_at_wiki_entrypoint(...):
    text = render_harness_context(...)  # same invocation as the existing render tests
    assert ".tesserae/wiki/index.md" in text
    assert "start at `.tesserae/wiki/index.md`" in text
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** the two bullets.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_agent_harness.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add tesserae/agent_harness.py tests/test_agent_harness.py && git commit -m "feat(harness): point generated agent files at the wiki entrypoint"`

---

## Final verification

- [ ] `.venv/bin/python -m pytest tests/test_karpathy_layer.py tests/test_wiki_projector.py tests/test_wiki_store.py tests/test_agent_harness.py tests/test_idempotence.py tests/test_incremental_parity.py tests/test_byte_idempotence_phase5.py -q` — all green.
- [ ] Real drive: compile a project (`python3 -m tesserae compile` in a registered project), open `.tesserae/wiki/index.md`, confirm: How-to-query section present; kinds table has the wiki-dir column; contradictions link present iff `questions/contradictions.md` exists on disk; every communities link resolves to a file under `communities/`.
- [ ] Compile the same project twice; `git status` / hash the wiki dir — `index.md` byte-identical.

## Self-review notes

- **Coverage:** shared dispute predicate + no-drift guarantee (T1), slug parity (T2), entrypoint content, conditional link, communities cap, order-independence (T3), harness pointer (T4), byte-idempotence (existing `test_idempotence.py`, re-run in T3/final).
- **Determinism audit is in Global Constraints** — the only compile-path write with new content is `index.md`, fully graph+config+static-derived, inside the already-guarded wiki dir.
- **Deliberately out of scope (kill on sight if an implementer adds them):** a separate `quickstart.md` file; rendering the wiki-root files on the static site; vault README changes (it already links its own `[[index]]`); per-community member counts (would need metadata not guaranteed on `COMMUNITY_SUMMARY` nodes); any LLM call.
- **Confirm during execution:** exact fixture/helper style in `tests/test_agent_harness.py` before writing T4's test; `ResearchNode` constructor kwargs match `tests/test_wiki_projector.py::_node`; `COMMUNITY_SUMMARY` passes `is_public_research_node` in the test graphs (if a validity gate filters it, set whatever metadata the gate needs — mirror how `tests/test_community_summaries.py` builds these nodes).
