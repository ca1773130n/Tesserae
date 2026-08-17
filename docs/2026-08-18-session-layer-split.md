# Keeping process memory out of the charter

**Date:** 2026-08-18
**Status:** design, ready to implement
**Touches:** `tesserae/charter.py`, `tesserae/project.py` (one call site), `tesserae/wiki_projector.py` (docstring only)

---

## Verdict up front

The asymmetry that was flagged is real: `partition_graph` splits a code layer out of
the research layer so code cannot pollute research surfaces, and nothing does the same
for the record of how work happened. But the fix as flagged — a third return value from
`partition_graph`, keyed on `SESSION_FINDING_TYPES` — is wrong twice over, and both
failures are measured, not argued.

It is wrong about **where**: `partition_graph`'s research half is written straight to
`.tesserae/graph.json`, the only graph the twenty MCP read paths load. Splitting there
deletes the session-memory product.

It is wrong about **what**: `SESSION_FINDING_TYPES` is the wrong set. Applied to this
project's own graph it makes the institution *worse* — live divisions go from 4 readable
names to 11, five of which anchor on `Event` nodes named after tool calls. `Event` is
17.8% of this graph and the canonical set does not contain it.

The right fix is a scoping flag inside `build_charter`, over
`SESSION_FINDING_TYPES ∪ {SESSION, EVENT, AGENT}`, with the founding gate moved onto the
same scoped universe. On this project's graph that takes demoted anchors from 296/1,415
to 1/812 and leaves six readable divisions. On HypePaper it declines to found a charter
at all, which is the correct answer for a research corpus of 10,094 characters.

---

## 1. Is the fix viable?

Viable, but not in the shape it was flagged, and the reason is not the one the earlier
measurement suggested.

### The 48,000 bound is a red herring — it was an artifact of filtering upstream

The prior measurement found that dropping session nodes takes HypePaper from 460,998
mass to 11,008, below `ARTIFACT_CHAR_BUDGET` (48,000), so `worth_chartering` returns
False and no charter is founded. True — but only because that measurement filtered
*before* the gate.

`worth_chartering(research_layer)` is called at `project.py:2191` on the layer handed in.
If the scoping lives inside `build_charter` (where `exclude_synthesis` already lives),
gate 1 still sees 460,998 and passes. Measured, all four variants:

| HypePaper variant | scoped nodes | scoped mass | gate 1 sees | domains | real | gate 2 (≥2 real) |
|---|---|---|---|---|---|---|
| baseline (today) | 2,277 | 460,752 | 460,998 → pass | 46 | 45 | pass |
| findings only | 396 | 54,644 | 460,998 → pass | 9 | 8 | pass |
| findings ∪ SESSION | 110 | 10,762 | 460,998 → pass | 2 | 1 | **fail** |
| findings ∪ SESSION ∪ EVENT ∪ AGENT | 103 | 10,094 | 460,998 → pass | 2 | 1 | **fail** |

So the mass gate stops binding and the ≥2-real-domains gate at `project.py:2200` becomes
the constraint instead. HypePaper ends up with no charter either way — but by accident,
after a wasted build, rather than for a stated reason.

### That decline is the correct outcome, and the gate should say so

`worth_chartering`'s own docstring gives the doctrine: *"a research layer that fits
`ARTIFACT_CHAR_BUDGET` can be handed to an agent whole, so dividing it into divisions and
departments adds a routing decision to a corpus that never needed one."*

HypePaper's research layer is 10,094 characters. It fits inside the one-read bound almost
five times. It has a charter today only because session memory inflates its mass 46×.
The defect is not merely that session nodes *name* the divisions — it is that session
memory *buys an institution the knowledge has not earned*. Once that is the framing, the
fix follows: evaluate the founding gate on the same universe the charter is built over.
HypePaper then declines at gate 1, legibly, and the 46-domain charter anchored on
`codex-codex-nomcp-default`, `fid` and `map` never exists.

So: do **not** make this configurable, and do **not** preserve HypePaper's charter by
exempting `SESSION` to keep the arithmetic above the bound. The earlier "Variant A"
cleared 48,000 only because `Session` nodes carried 79.9% of the surviving mass — keeping
the purest how-work-happened type in order to fund an institution about what is true is
the same category error the fix exists to correct.

### The type set is where the flagged fix actually breaks

This is the finding that changes the design. Measured on this project's own graph
(`.tesserae/graph.json`, 2026-08-17 14:19, 62,366 nodes / 136,297 edges, on-disk charter
of 1,411 live domains at `reorg_seq: 0`):

| exclusion set | domains | demoted anchors | live divisions |
|---|---|---|---|
| none (today) | 1,415 | 296 (20.9%) | 4 — `3d-gaussian-splatting`, `cognee`, `intake`, `tesserae` |
| `SESSION_FINDING_TYPES ∪ {SESSION}` — **as flagged** | 1,018 | 131 (12.9%) | 11 — incl. `event-362-tool-bash`, `event-770-tool-edit`, `event-491-tool-toolsearch`, `event-718-tool-agent`, `event-340-assistant-verifying-with-dry-run-…` |
| `… ∪ {EVENT}` | 790 | 1 (0.1%) | 7 — `3d-gaussian-splatting`, `3d-gaussian-splatting-3`, `3d-reconstruction`, `droid-slam`, `intake`, `tesserae`, `vggt-3` |
| `… ∪ {EVENT, AGENT}` — **recommended** | 812 | 1 (0.1%) | 6 — `3d-gaussian-splatting`, `3d-gaussian-splatting-3`, `intake`, `psnr`, `tesserae`, `vggt` |
| `{EVENT}` alone | 891 | 11 (1.2%) | 7 — `3d-gaussian-splatting-3`, `cognee`, `intake`, `psnr`, `tesserae`, `vggt`, `vista4d` |

The flagged set makes the top of the institution worse. Removing session findings without
removing `Event` fragments the graph — 739 slugs lost, 342 new, only 676 kept — and
Louvain re-clusters around the 11,082 `Event` nodes that remain, promoting tool-call
records into division anchors. `Event` is the second most common type in this graph and
`SESSION_FINDING_TYPES` does not contain it.

Add `Event` and demoted anchors collapse from 296 to 1. The single survivor is `intake`,
whose `anchor_id` is `""` by construction.

`Agent` is in the set for the same reason at smaller scale: an `Agent` node is an OAuth
account name, and it is exactly what HypePaper's largest division is currently called.
It costs nothing on this graph (790 → 812 domains, demoted anchors unchanged at 1) and it
closes the flagship symptom by construction.

`SOURCE_DOCUMENT` is deliberately **not** in the set. It is a public wiki kind routed to
`sources` (`wiki_projector.py:101`) and it is ingested content, not process memory.
Sweeping in its 2,012 nodes here would remove a research surface from the charter.

---

## 2. Where the split belongs

**Inside `build_charter`, as a second keyword flag beside `exclude_synthesis`, with the
scoping extracted into a `charter_scope()` helper so the founding gate can share it.**

Not a third return value from `partition_graph`. Not a filter at the three
charter/summary/hierarchy call sites.

### Why not `partition_graph`

Its research half is serialized to `.tesserae/graph.json` at `project.py:3828-3838`, and
that file is what every MCP read path loads (`mcp_server.py:5684 → 5721 → 5754`, 20 call
sites). A session layer there does not rename domains — it empties `find_session_findings`,
`fresh_insights` and `list_sessions` outright, takes `search_nodes`' corpus from 2,279
candidates to 112 across every lane, drops 3,691 cross-layer provenance edges
(`summarizes` 2,167, `references` 917, `performed_by` 319, `discussed_in` 288) because a
research edge survives only when both endpoints are research, and empties `ask`'s planner
lanes. **Recall through `ask` / `query` / `search_nodes` must not regress, and this design
does not touch it: `partition_graph` is unchanged, so `graph.json` is byte-identical.**

### Why not a filter at the call site

`project.py:2190` looks like the obvious seam and is a trap. `build_charter` takes
`roots = graph_project_roots(graph)` from the graph *as handed in*, on purpose, and that
function reads `metadata.project_root` off `SESSION` nodes only (`temporal.py:205`).
Measured on HypePaper:

```
roots(unscoped research layer) = ('/Users/neo/Developer/Projects/HypePaper',)
roots(scoped)                  = ()
```

Filtering upstream of `build_charter` therefore silently disables `_source_ts`'s path
rung and collapses ladder coverage from 81.9% to 7.6% with nothing failing to say so —
the exact failure the existing comment at `charter.py:849-854` was written to prevent.
That comment currently says session nodes "are not synthesis types today so the two agree
exactly." This change is precisely the event it anticipated, and the comment must be
updated to say so.

### Why inside `build_charter` is right

The mechanism already exists there and is one flag wide: `exclude_synthesis=True` builds
a `scoped` graph, prunes edges to the kept ids, and every subsequent step —
`sections`, `divisions`, `intake_members`, `assign_anchors`, `_verify_partition` —
runs on `scoped`. CH-01 is verified against `scoped` at `charter.py:1045`, so the partition
invariant stays true by construction rather than being voided. Roots are already taken
before scoping. The new flag inherits all of it.

---

## 3. Call sites

| Site | Function | Change? |
|---|---|---|
| `project.py:2095` | `_write_hierarchy_sidecar` → `detect_community_levels` | **No.** Its coarsest-level cids must stay byte-equal to the summary pass or `prune_stale_summary_caches` deletes every cache just written. |
| `project.py:2190` | `_write_charter_sidecar` | **Yes** — the only one. Gate 1 gets the scoped layer; `build_charter` still gets the unscoped layer, for roots. |
| `project.py:2317` | `_merge_community_summaries` | **No.** Matched pair with 2095. Community cards legitimately cover session memory. |
| `project.py:3828` | `_write_artifacts` → `graph.json` | **No, never.** This is the session-memory product and the MCP read surface. |
| `wiki_projector.py:514` | `partition_graph` | **No code change.** Docstring paragraph only, so this does not get re-flagged. |

---

## 4. The diff, file by file

### `tesserae/research_graph.py` — no change

`SESSION_FINDING_TYPES` (`:302`) stays as it is. It is derived from
`SESSION_FINDING_KIND_TO_TYPE` and is correct for what it names. The charter needs a
strictly larger set, which is a charter concern and belongs in `charter.py`.

### `tesserae/charter.py`

Extend the import at `:36`:

```python
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    SESSION_FINDING_TYPES,
)
```

Add beside `_SYNTHESIS_TYPES` (after `:503`):

```python
#: Node types that record HOW WORK HAPPENED rather than what is true. The
#: charter is an institution over knowledge; a division named after an OAuth
#: account, a tool call or a pasted prompt is the graph describing its own
#: operator instead of its subject.
#:
#: DERIVED from ``SESSION_FINDING_TYPES`` rather than re-listed, following
#: session_graph.py:192, so a new finding kind joins automatically and cannot
#: be forgotten here.
#:
#: EVENT is the member that matters most and the one the canonical set misses.
#: Measured on this project's own 62,366-node graph: excluding
#: ``SESSION_FINDING_TYPES | {SESSION}`` WITHOUT Event makes the institution
#: WORSE, not better — live divisions go 4 -> 11 and five of them anchor on
#: Event nodes named after tool calls (``event-362-tool-bash``,
#: ``event-770-tool-edit``, ``event-491-tool-toolsearch``,
#: ``event-718-tool-agent``), because Event is 17.8% of this graph and Louvain
#: re-clusters around whatever hubs are left. Adding Event takes demoted
#: anchors from 296/1,415 to 1/790, the survivor being ``intake`` itself.
#:
#: AGENT is here for the same reason at smaller scale: it is an OAuth account
#: name. It costs 22 domains on this graph and changes no anchor, and it is
#: what HypePaper's largest division is currently CALLED
#: (``codex-codex-nomcp-default``).
#:
#: SOURCE_DOCUMENT is deliberately NOT here. It is a public wiki kind
#: (wiki_projector.py:101, route ``sources``) and ingested content, not process
#: memory; sweeping in its 2,012 nodes would delete a research surface from the
#: charter rather than remove noise from it.
PROCESS_MEMORY_TYPES = frozenset(
    SESSION_FINDING_TYPES
    | {
        ResearchNodeType.SESSION,
        ResearchNodeType.EVENT,
        ResearchNodeType.AGENT,
    }
)
```

Add the helper immediately before `build_charter`:

```python
def charter_scope(
    graph: ResearchGraph,
    *,
    exclude_synthesis: bool = True,
    exclude_process: bool = True,
) -> ResearchGraph:
    """The universe the charter is built over: knowledge, not process.

    Extracted from ``build_charter`` so the FOUNDING GATE and the BUILD can see
    the same universe. They did not. ``worth_chartering`` was handed the whole
    research layer while ``build_charter`` scoped internally, so a corpus whose
    mass is 97.8% process memory bought an institution its knowledge had not
    earned. Measured on HypePaper: 460,998 total mass against 10,094 of actual
    research — a charter founded nine times over the one-read bound by a corpus
    that fits inside it almost five times, and named accordingly
    (``codex-codex-nomcp-default``, ``fid``, ``map``).

    Edges are pruned to both-endpoints-kept. ``ResearchGraph`` validates edge
    TYPES only (research_graph.py:731) and ``_undirected_projection`` silently
    SKIPS dangling edges (community_summaries.py:129), so a filter that forgets
    this produces an invalid graph that Louvain swallows rather than raises on,
    and the damage is invisible in every downstream artifact. Measured on
    HypePaper: 4,679 of 16,636 edges (28.1%) would dangle if left unpruned.
    """
    drop: Set[ResearchNodeType] = set()
    if exclude_synthesis:
        drop |= _SYNTHESIS_TYPES
    if exclude_process:
        drop |= PROCESS_MEMORY_TYPES
    nodes = [node for node in graph.nodes if node.type not in drop]
    keep = {node.id for node in nodes}
    edges = [
        edge for edge in graph.edges if edge.source in keep and edge.target in keep
    ]
    return ResearchGraph(nodes=nodes, edges=edges)
```

Replace `build_charter`'s signature and its inline `scoped` block (`:834-855`):

```python
def build_charter(
    graph: ResearchGraph,
    *,
    exclude_synthesis: bool = True,
    exclude_process: bool = True,
) -> dict:
    """Derive the full institution from the research graph.

    Founding pass only: ``reorg_seq`` is 0 and every domain is ``founded``.
    Succession against a prior charter is Task 8.
    """
    scoped = charter_scope(
        graph,
        exclude_synthesis=exclude_synthesis,
        exclude_process=exclude_process,
    )

    # Roots come from the graph as HANDED IN, not from ``scoped``. This was
    # already load-bearing for the synthesis filter; ``exclude_process`` makes
    # it CRITICAL and no longer hypothetical. ``graph_project_roots`` reads
    # ``metadata.project_root`` off SESSION nodes ONLY (temporal.py:205), and
    # SESSION is exactly what ``scoped`` now drops. Measured on HypePaper:
    # roots resolve to ('/Users/neo/Developer/Projects/HypePaper',) from the
    # layer as handed in and to () from ``scoped``. Scoping upstream of this
    # call — at project.py:2190, say — therefore collapses ladder coverage
    # from 81.9% to 7.6% (temporal.py:129) with nothing failing to say so.
    # This is why the scope lives in here and not at the call site.
    roots = graph_project_roots(graph)
```

Everything below is untouched: `sections(scoped)`, `divisions(scoped, …)`,
`intake_members(scoped, …)`, `assign_anchors(scoped, …)`, `_verify_partition(scoped, …)`.

### `tesserae/project.py`

At `:2163`, add `charter_scope` to the import. At `:2190`:

```python
        research_layer, _code_layer = partition_graph(graph)
        # The founding gate is evaluated on the SAME universe build_charter
        # builds over. Handing it the unscoped layer let process memory fund an
        # institution the knowledge had not earned: HypePaper cleared the bound
        # at 460,998 while its research corpus was 10,094 characters — a fifth
        # of the one-read bound — and got 46 divisions named after OAuth
        # accounts and pasted prompts for it.
        if prior is None and not worth_chartering(charter_scope(research_layer)):
            # Below the one-read bound: never create .tesserae/charter/. The
            # bound is a FOUNDING test only — an existing institution keeps
            # being maintained even if the corpus later shrinks, because its
            # slugs are pinned paths and must not be abandoned by a corpus
            # oscillating around the budget.
            return None

        # NOTE: the UNSCOPED layer, deliberately. build_charter re-scopes
        # internally and must see SESSION nodes first to resolve project roots.
        # See the roots comment in build_charter before "simplifying" this.
        fresh = build_charter(research_layer)
```

### `tesserae/wiki_projector.py`

No code change. Append to `partition_graph`'s docstring:

```
    There is deliberately NO session/process layer here, and adding one is a
    mistake this docstring exists to stop. This function's research half is
    written straight to ``.tesserae/graph.json`` (project.py:3828-3838), which
    is the only graph every MCP read path loads (mcp_server.py:5684 -> 5721 ->
    5754, 20 call sites). Splitting session memory out here does not clean up a
    surface — it empties ``find_session_findings``, ``fresh_insights`` and
    ``list_sessions``, takes ``search_nodes``' candidate corpus from 2,279 to
    112, and drops 3,691 cross-layer provenance edges. The charter is the only
    surface where process memory must not compete to name things, and it scopes
    itself: see ``charter.charter_scope`` and ``PROCESS_MEMORY_TYPES``.
```

---

## 5. Tests

### New, in `tests/test_charter.py`

1. **`test_process_memory_types_is_derived_and_covers_event_and_agent`** — asserts
   `SESSION_FINDING_TYPES <= PROCESS_MEMORY_TYPES`, that `SESSION`, `EVENT` and `AGENT`
   are members, and that `SOURCE_DOCUMENT` is **not**. Guards the derivation and the one
   type that must stay out.
2. **`test_charter_scope_prunes_edges_to_kept_nodes`** — research triangle plus session
   nodes plus cross-layer edges; asserts every surviving edge has both endpoints in the
   node set. This is the invariant nothing else enforces, since `ResearchGraph` validates
   edge types only and Louvain silently skips danglers.
3. **`test_a_process_node_cannot_anchor_a_domain_even_with_no_alternative`** — the
   flagship case, and the one the existing demotion test at `:1219` does not cover: it
   always leaves `Concept:runner` eligible. Build a cluster whose *only* members are an
   `Agent` hub and session findings, assert no domain is anchored on it. Measured on
   HypePaper, all 35 bad anchors were this shape — no eligible member existed — which is
   why demotion alone never fixed it.
4. **`test_the_founding_gate_sees_the_scoped_universe`** — a graph whose mass clears
   `ARTIFACT_CHAR_BUDGET` only by virtue of session nodes; assert `worth_chartering(g)`
   is True and `worth_chartering(charter_scope(g))` is False. The HypePaper shape as a
   fixture.
5. **`test_project_roots_survive_process_scoping`** — a `SESSION` node carrying
   `metadata.project_root` plus dated research nodes; assert the built charter's domains
   are dated rather than `undated`. This is the regression guard for the temporal rung
   and it fails if anyone moves the scope to the call site.
6. **`test_exclude_process_false_restores_the_prior_universe`** — mirrors the
   `exclude_synthesis=False` test at `:482`, so the flag is provably a scope and not a
   rewrite.
7. **`test_process_members_are_absent_from_member_index`** — pins the routing decision
   below as a decision rather than an accident.

### New, in `tests/test_charter_compile.py`

8. **`test_a_session_heavy_project_is_not_chartered`** — drive `_write_charter_sidecar`
   end to end on a graph whose unscoped mass clears the bound and whose scoped mass does
   not; assert `.tesserae/charter/` is never created. This is HypePaper, and it is the
   test that would have caught the defect.

### Existing tests

**Needing no change** — verified, not assumed:

- `tests/test_artifact_split.py:159` `test_partition_graph_separates_layers`, and the
  seven `partition_graph` tests in `tests/test_hierarchy_sidecar_layer.py`.
  `partition_graph` keeps its arity and its behaviour. This is the main argument for the
  seam: the blast-radius tests do not move.
- `tests/test_charter.py:1219-1250`, the anchor-demotion parametrize. It already covers
  `SESSION`, `EVENT`, `AGENT` and calls `assign_anchors` directly, below the scope.
- `tests/test_charter.py:1348-1363`, the `worth_chartering` bound tests. The function is
  unchanged; only what it is handed changes.
- `tests/test_charter_compile.py`, `tests/test_charter_briefs.py`,
  `tests/test_charter_cli.py`. Audited: no fixture in any of the three constructs a
  `Session`, `Event` or `Agent` node, so no fixture loses mass or members.
- Every session/MCP test — `tests/test_mcp_sessions.py`,
  `tests/test_project_compile_sessions_structural.py`,
  `tests/test_byte_idempotence_phase5.py`, `tests/test_session_event.py`,
  `tests/test_session_home_path_redaction.py`. `graph.json` is byte-identical.

**Needing update:** the comment at `charter.py:849-854` asserts that session nodes are not
synthesis types "so the two agree exactly." That stops being true with this change and
the comment must be rewritten, not deleted — its measurement is the reason the roots line
sits where it does.

---

## 6. What this does not fix

**HypePaper's charter does not get better — it stops existing.** That is the correct
outcome, and it should be said plainly rather than sold as a fix. HypePaper's research
layer is 103 nodes and 10,094 characters, with 24 anchor-eligible nodes in the whole
graph. There is no arrangement of those nodes that yields an institution. The 46 domains
it has today are not a partition failure; they are session memory wearing the institution
as a costume.

**The upstream cause is untouched and is the real defect.** 2,182 of HypePaper's 2,279
nodes have no `source_path` — they arrived from the harness-session import, not from the
configured `sources`. HypePaper's Tesserae project has never meaningfully ingested its own
research corpus. Suppressing process memory from the charter makes that visible; it does
not fix it. Anyone who wants HypePaper chartered should be pointed at ingestion, not here.

**Process memory becomes unrouted by the charter.** On this project's graph, `member_index`
goes from 62,086 entries to 48,886 — 13,200 session and event nodes belong to no domain,
so `graph_map` / `drill_down` on such an id resolves to nothing. This is deliberate. The
alternative, sweeping them into `intake`, would make `intake` the largest division by a
wide margin and reimport the exact problem one level down. CH-01 stays exact because
`_verify_partition` runs against `scoped`, and lint stays clean because its CH-01 probe is
one-directional (`lint.py:1594`). Recommended follow-up: a lint probe that *reports* the
unrouted count, so it is a visible number rather than a silent absence. Session memory
keeps its own routes — `find_session_findings`, `fresh_insights`, `list_sessions`, the
`/sessions/` pages — and none of them change.

**Migration is a refound, not a succession, for any project that already has a charter.**
This project does: 1,411 live domains at `reorg_seq: 0`. Rescoping gives 603 slugs kept,
812 retired, 209 founded. Slugs are operator-pinned attach paths, so this must be run as
a deliberate one-time refound with the old charter kept as a tombstone — not slipped in
under a routine compile. Sequence it as its own step, after the code lands and before
anything is built on `charter_route`.

**`Event` nodes still pollute every other surface.** There are 11,082 of them in
`graph.json`, and they remain in `search_nodes`' corpus, in `hierarchy.json` and in the
community summaries. This design deliberately confines itself to the charter, because
those surfaces are where session memory is supposed to be reachable. If `Event` proves to
be noise in retrieval too, that is a separate measurement and a separate change.

**The ≥2-real-domains gate is still too weak to catch a degenerate institution.**
`project.py:2200` counts domains at every tier, so HypePaper's findings-only variant
passed it with 8 real domains while having exactly one real tier-1 division holding 380 of
398 nodes — the shape `project.py:2202-2204`'s own comment calls "a rename of the graph,
not a structure an agent can route through." The gate should count live divisions, not
domains. Out of scope here, worth its own issue.

---

## Appendix: how the numbers were produced

Measured 2026-08-18 against two live graphs, by monkeypatching `charter._SYNTHESIS_TYPES`
to the candidate exclusion set — which reproduces the proposed in-`build_charter` seam
exactly, since `scoped` is built from that constant and `roots` is taken before it.

- **This project:** `/Users/neo/Developer/Projects/Tesserae/.tesserae/graph.json`,
  mtime 2026-08-17 14:19, 62,366 nodes / 136,297 edges, 0 code-layer nodes, full research
  mass 11,383,599. On-disk charter 1,411 live domains, `reorg_seq` 0, `member_index`
  62,086.
- **HypePaper:** `/Users/neo/Developer/Projects/HypePaper/.tesserae/graph.json`,
  mtime 2026-07-31 21:52, 2,279 nodes / 16,636 edges, 0 code-layer nodes, full research
  mass 460,998, no `.tesserae/charter/`. A compile held `compile.lock` throughout and was
  not touched; the file's mtime was identical before and after, so every figure is against
  that baseline. Re-measure if that compile rewrites the file.

Build times, for anyone budgeting: 16.7s baseline and 8.2s scoped on the 62k-node graph;
0.3s and 0.0s on HypePaper.
