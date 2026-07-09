# Community-Page Sources Footer (Scoped-Down Source-Map Provenance) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give `COMMUNITY_SUMMARY` wiki pages the ONE piece of per-page source-file provenance the compile pipeline does not already emit: a deterministic `## Sources` footer (plus a matching `sources` frontmatter key) listing the sorted, deduped source files of the community's members. Community pages are the aggregation pages the new agent-entrypoint `index.md` steers agents into, and today they carry `source_path: ""` and nothing else — an agent must hop N member pages to find grounding files it could hand to `raw_source`. Everything else in the original "OpenWiki source-map" candidate is already shipped or deliberately rejected (see "Why scoped down").

**Architecture:** NO new files or modules. One private helper + one render branch in `WikiLayerProjector._page_for_node` (`tesserae/wiki_projector.py`), driven entirely by the `summarizes` edges (`community_summaries.py` mints `ResearchEdge(source=<community_id>, target=<member_id>, type="summarizes")`) and the members' existing `source_path` fields. The body section — not frontmatter alone — is the delivery vehicle, because `WikiPageStore.write_page` keys idempotence on the sha256 of the **body alone** (`wiki_store.py` lines 269–292): a frontmatter-only addition would never propagate to already-written pages. Body markdown renders on the static site automatically (`_detail_page` → `_render_markdown`) and reaches agents through MCP `wiki_page` (which already returns both `body` and `frontmatter`, `mcp_server.py` line ~2623). Zero changes to `site/`, `mcp_server.py`, vault projection, or synthesis.

**Tech Stack:** Python 3 stdlib only, pytest via `.venv/bin/python`. No new deps.

## Why scoped down (verified against the code, 2026-07-09, post-`f1cf98244b`)

The research candidate was "OpenWiki-style Source map + Git evidence sections per page" (finding 5, **medium confidence, 2-1 vote**). Verified state of each sub-piece:

- **Leaf wiki pages already have file provenance.** `_page_for_node` emits `source_path` / `node_id` / `node_type` frontmatter (`wiki_projector.py` ~380); the site chrome renders a "Source provenance" block from it (`site/pages.py` `_provenance_html`, ~1306); MCP `wiki_page` returns the frontmatter dict. A body `Sources` line for leaf pages was **deliberately removed** once already — see the comment at `wiki_projector.py` ~358: body-level source lines double-rendered against the site eyebrow. Do not reintroduce.
- **Synthesis pages already have the full source map.** `SynthesisProjector.project` writes frontmatter `sources:` (sorted, project-relative source files, normalized via `relativize_source_path`) AND `inputs:` (sorted node ids) AND `content_hash` (`synthesis.py` ~530–538), and the body carries per-claim `[node_id]` citations grounded against input ids (landed today in `llm_synthesis`).
- **Vault pages already carry `node_id` + `source_path` frontmatter** (`markdown_projection.render_node_page`), and vault community pages already wikilink their members in the body.
- **Per-page git evidence: REJECTED, deliberately.** Embedding `git_head` (or commit lists) in page bytes makes every commit change every page: it breaks byte-idempotence of recompiles, and it would make today's `output_snapshot` gate report "output changed" on every compile even when knowledge is unchanged — destroying the signal that machinery exists to provide. The git head is already recorded ONCE per build in build history, and staleness is already surfaced as lint findings (`CODE_GRAPH_BEHIND` / `CODE_GRAPH_STALE_FILE`, landed today). OpenWiki itself walked persistent commit lists back in its own prompt ("discouraged unless a specific historical decision is important"). Also: finding 5's suggested target — "code-derived wiki pages" — does not exist in Tesserae; code-graph nodes are private (`CODE_GRAPH_TYPES`, `kind_for_node` → `None`).
- **The only unserved surface is `COMMUNITY_SUMMARY` pages**: `node.source_path` is `None`, member grounding is two hops away (relations list → member page → its `source_path`). That gap is what this plan closes, and nothing more.

## Global Constraints

- **Byte-idempotence (broke 4x historically).** Every write this plan touches, and why it is stable:
  - `.tesserae/wiki/communities/<slug>.md` (via the existing `WikiPageStore.write_page` call in `WikiLayerProjector.project`) — the new `## Sources` section and `sources` frontmatter derive ONLY from the graph: the set of `summarizes` out-edges of the community node and the `source_path` field of each member node. Collected into a `set`, emitted `sorted()`, capped with a count-derived "…and N more" line. NO timestamps, NO wall-clock, NO git state, NO filesystem probes, NO dict-iteration order. Two compiles of an unchanged graph emit identical bytes; `tests/test_idempotence.py` already hashes the wiki dir across two compiles and guards this automatically.
  - **One-time churn, called out:** on the first compile after this change, every community page whose members have ≥1 `source_path` gets a body-hash change → rewrite → `output_snapshot` reports "changed" once. That is a true content change, exactly what the snapshot gate is for. Subsequent compiles are stable.
  - Nothing else writes: no `graph.json`, `code-graph.json`, site, vault, synthesis, or frontmatter-format changes.
- **Frontmatter/body lockstep.** Set the `sources` frontmatter key ONLY when the body section is emitted (non-empty file list). Because `write_page` gates on body hash, a frontmatter key without a body change would silently fail to appear on existing pages — never let the two diverge.
- Reuse, don't duplicate: `_page_for_node`'s existing `adj` / `nodes_by_id` parameters (no new graph walk), the `[:25]` cap convention from `_format_relation_block`, the `_node` test helper in `tests/test_wiki_projector.py`.
- Member paths are used verbatim (`node.source_path` is already project-relative, e.g. `data/research/daily/...`); do NOT link them — they are `raw_source`-ready path strings, matching the synthesis `sources:` convention. Ignore `metadata["source_paths"]` (plural): only defensive readers exist in `site/`; no writer in the compile path mints it.
- Run tests with `.venv/bin/python -m pytest` (system python3 is 3.9 and fails collection).
- One commit; conventional message.

---

## File Structure

- **Modify** `tesserae/wiki_projector.py` — add module constant `_SOURCES_CAP = 25`, helper `_community_source_files(node, adj, nodes_by_id)`, and the footer branch in `_page_for_node`.
- **Modify** `tests/test_wiki_projector.py` — five new tests (fixtures `_node` / graph builders already there).

---

### Task 1: `## Sources` footer on community pages

**Files:** Modify `tesserae/wiki_projector.py`; Test `tests/test_wiki_projector.py`

**Interfaces:**
- Produces: `_community_source_files(node: ResearchNode, adj: _Adjacency, nodes_by_id: Mapping[str, ResearchNode]) -> List[str]` — for a `COMMUNITY_SUMMARY` node, the sorted deduped list of `nodes_by_id[edge.target].source_path` over `adj.out.get(node.id, [])` where `edge.type == "summarizes"`, the target is known, and its `source_path` is truthy. Returns `[]` for every other node type.
- In `_page_for_node`, after the "Connected node types" block and before the final `body = "\n".join(...)`:
  - when the list is non-empty, append `## Sources`, blank line, one `` - `{path}` `` bullet per entry for the first `_SOURCES_CAP` entries, then (only when truncated) `- …and {len(files) - _SOURCES_CAP} more`, then a blank line;
  - and set `frontmatter["sources"] = files` (the FULL sorted list, matching the uncapped synthesis-page `sources:` convention; the cap is a body-readability measure only).
- Non-community pages: byte-identical to today (helper returns `[]`; no section, no key).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_wiki_projector.py`; mirror the file's existing style for building a projector — `WikiPageStore(tmp_path)` + `WikiLayerProjector(store).project(graph)`, then read the written page. Build `COMMUNITY_SUMMARY` nodes the way `tests/test_community_summaries.py` does so they pass `is_public_research_node`.)

```python
def _community_graph(member_paths, extra_members_without_paths=0):
    members = [
        _node(f"Member {i:02d}", ResearchNodeType.CONCEPT, source_path=p)
        for i, p in enumerate(member_paths)
    ]
    members += [
        _node(f"Pathless {i:02d}", ResearchNodeType.CONCEPT)
        for i in range(extra_members_without_paths)
    ]
    community = _node("Cluster Alpha", ResearchNodeType.COMMUNITY_SUMMARY,
                      description="A cluster.")
    edges = [
        ResearchEdge(source=community.id, target=m.id, type="summarizes")
        for m in members
    ]
    return community, ResearchGraph(nodes=[community, *members], edges=edges)


def test_community_page_lists_sorted_deduped_member_source_files(tmp_path):
    community, graph = _community_graph(
        ["data/b.md", "data/a.md", "data/b.md"]  # unsorted + duplicate
    )
    store = WikiPageStore(tmp_path)
    WikiLayerProjector(store).project(graph)
    page = store.read_page(store.path_for("communities", store.slug_for(community.name)))
    assert "## Sources" in page.body
    assert page.body.index("- `data/a.md`") < page.body.index("- `data/b.md`")
    assert page.body.count("- `data/b.md`") == 1
    assert page.frontmatter["sources"] == ["data/a.md", "data/b.md"]


def test_community_sources_capped_with_deterministic_more_line(tmp_path):
    community, graph = _community_graph([f"data/{i:03d}.md" for i in range(30)])
    store = WikiPageStore(tmp_path)
    WikiLayerProjector(store).project(graph)
    page = store.read_page(store.path_for("communities", store.slug_for(community.name)))
    assert "- `data/024.md`" in page.body and "- `data/025.md`" not in page.body
    assert "…and 5 more" in page.body
    assert len(page.frontmatter["sources"]) == 30  # frontmatter uncapped


def test_community_page_omits_sources_when_members_lack_source_path(tmp_path):
    community, graph = _community_graph([], extra_members_without_paths=3)
    store = WikiPageStore(tmp_path)
    WikiLayerProjector(store).project(graph)
    page = store.read_page(store.path_for("communities", store.slug_for(community.name)))
    assert "## Sources" not in page.body
    assert "sources" not in page.frontmatter


def test_non_community_pages_gain_no_sources_section(tmp_path):
    concept = _node("Plain Concept", ResearchNodeType.CONCEPT,
                    source_path="data/x.md")
    store = WikiPageStore(tmp_path)
    WikiLayerProjector(store).project(ResearchGraph(nodes=[concept], edges=[]))
    page = store.read_page(store.path_for("concepts", store.slug_for(concept.name)))
    assert "## Sources" not in page.body
    assert "sources" not in page.frontmatter  # source_path frontmatter is enough


def test_community_sources_deterministic_across_node_and_edge_order(tmp_path):
    community, graph = _community_graph(["data/c.md", "data/a.md", "data/b.md"])
    reordered = ResearchGraph(
        nodes=list(reversed(graph.nodes)), edges=list(reversed(graph.edges))
    )
    store_a = WikiPageStore(tmp_path / "a")
    store_b = WikiPageStore(tmp_path / "b")
    WikiLayerProjector(store_a).project(graph)
    WikiLayerProjector(store_b).project(reordered)
    slug = store_a.slug_for(community.name)
    text_a = store_a.path_for("communities", slug).read_text(encoding="utf-8")
    text_b = store_b.path_for("communities", slug).read_text(encoding="utf-8")
    assert text_a == text_b
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_wiki_projector.py -k "sources" -v` → FAIL (no `## Sources` emitted; missing frontmatter key).

- [ ] **Step 3: Implement** — in `tesserae/wiki_projector.py`:

```python
_SOURCES_CAP = 25


def _community_source_files(
    node: ResearchNode,
    adj: _Adjacency,
    nodes_by_id: Mapping[str, ResearchNode],
) -> List[str]:
    """Sorted deduped member source files for a COMMUNITY_SUMMARY node.

    Pure function of the graph (``summarizes`` out-edges + member
    ``source_path`` fields) so the page footer is byte-stable across
    recompiles. Empty for every other node type.
    """
    if node.type is not ResearchNodeType.COMMUNITY_SUMMARY:
        return []
    return sorted({
        nodes_by_id[edge.target].source_path
        for edge in adj.out.get(node.id, [])
        if edge.type == "summarizes"
        and edge.target in nodes_by_id
        and nodes_by_id[edge.target].source_path
    })
```

In `_page_for_node`, after the `if type_mix:` block (before `body = ...`):

```python
        source_files = _community_source_files(node, adj, nodes_by_id)
        if source_files:
            body_lines.append("## Sources")
            body_lines.append("")
            for path_str in source_files[:_SOURCES_CAP]:
                body_lines.append(f"- `{path_str}`")
            remaining = len(source_files) - _SOURCES_CAP
            if remaining > 0:
                body_lines.append(f"- …and {remaining} more")
            body_lines.append("")
```

and after the existing `frontmatter` dict is built:

```python
        if source_files:
            frontmatter["sources"] = source_files
```

Do NOT touch `_format_relation_block`, the leaf-page frontmatter keys, `write_page`, or anything in `site/` / `mcp_server.py` — the section reaches the site through markdown rendering and reaches MCP through the existing `body` + `frontmatter` fields.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_wiki_projector.py tests/test_idempotence.py -q` → PASS (all pre-existing wiki-projector tests untouched; idempotence re-proves the new bytes are stable across two full compiles).

- [ ] **Step 5: Commit** — `git add tesserae/wiki_projector.py tests/test_wiki_projector.py && git commit -m "feat(wiki): deterministic Sources footer on community pages"`

---

## Final verification

- [ ] `.venv/bin/python -m pytest tests/test_wiki_projector.py tests/test_idempotence.py tests/test_byte_idempotence_phase5.py tests/test_community_summaries.py tests/test_lint.py -q` — all green.
- [ ] Real drive: compile a registered project with community summaries enabled; open a `.tesserae/wiki/communities/*.md` page — frontmatter `sources:` list present, `## Sources` footer lists project-relative paths; pick one path and confirm MCP `raw_source` accepts it verbatim.
- [ ] Compile twice; second compile must report output **unchanged** (`output_snapshot`) and rewrite zero wiki files.
- [ ] Site spot-check: rebuild the site, open one community detail page, confirm the Sources section renders as a plain list (body markdown → HTML; no `site/` change was needed).

## Self-review notes

- **Coverage:** sorted+deduped emission and frontmatter parity (test 1), cap + deterministic overflow line + uncapped frontmatter (test 2), empty-case omission in both body and frontmatter (test 3), leaf-page non-regression (test 4), order-independence (test 5), byte-idempotence (existing `test_idempotence.py`, re-run in Step 4/final).
- **Determinism audit is in Global Constraints** — the only compile-path write with new content is the community page body/frontmatter, fully graph-derived, inside the already-guarded wiki dir; the one-time snapshot "changed" report on upgrade is expected and documented.
- **Deliberately out of scope (kill on sight if an implementer adds them):** per-page `git_head` or commit lists anywhere (idempotence-hostile; staleness lives in lint + build history as of today); a body `Sources` line on leaf pages (double-renders against the site provenance chrome — was removed once already); synthesis-page changes (already have `sources` + `inputs` frontmatter); vault projection changes (community vault pages already wikilink members); reading `metadata["source_paths"]` (no compile-path writer exists); any `site/` or `mcp_server.py` change; hashing frontmatter into `write_page`'s idempotence key (would churn every page once for no reader benefit).
- **Confirm during execution:** exact projector/store invocation style at the top of `tests/test_wiki_projector.py` (mirror `test_contradictions_page_*`); that `COMMUNITY_SUMMARY` test nodes pass `is_public_research_node` (if a validity gate filters them, copy the metadata shape `tests/test_community_summaries.py` uses — `member_ids`, `member_count`, `tags`, `extractor`); that `read_page` parses the inline YAML list back to a Python list for the frontmatter assertions (if it returns a string, assert on the rendered text instead).
