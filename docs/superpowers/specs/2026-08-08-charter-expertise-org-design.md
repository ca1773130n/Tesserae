# CHARTER — a chartered institution over the knowledge graph

**Status:** design approved 2026-08-08, not yet implemented.
**Supersedes nothing.** Extends `2026-07-19-layered-agent-kg.md`, which keyed
expertise to historical agent identities; this design replaces that axis (see
[Rejected](#rejected)).

## The problem

Tesserae's agent-expertise layer keys memory to *who ran the session* —
`claude-code:<account>:<role>`. That is the wrong subject. Those identities are
interchangeable general-purpose runtimes, not experts.

What is wanted instead: **specialized expertise KGs that any general-purpose
agent can attach to in order to become an expert**, organized like an
institution — divisions, departments, teams — with two orthogonal axes:

* **domain routing** (horizontal): which subject area,
* **abstraction layering** (vertical): how much detail, where every level is a
  complete answer at its own altitude rather than a table of contents.

The existing layer also fails outright at scale. Two of seven agents cannot
produce an artifact at all: 118,360 and 71,624 chars against the 48,000-char
one-read bound. The current degradation drops the *oldest index entries* 32 at a
time and never splits by subject, so a large expert has no path to a usable
artifact.

## The measurement that determines the design

Everything here follows from one property of community detection under
incremental ingest, measured on the live graph:

| | |
|---|---|
| Identical input, re-run | 1,649 / 1,649 communities reproduced exactly |
| After **one 15-node document** | ~29% of members change community |
| Large communities, member-set Jaccard | **0.39 – 0.60** |
| Level-0 cell mass retained | 97.4% |
| Coarse-level mass retained | 71.7% |
| Anchor (top-degree member) preserved | 97.0% fine / 81.0% coarse |

So detection is deterministic but **not stable**. Any design that keys identity
on community membership takes a near-total cache miss on every ingest, and this
corpus ingests daily.

**Therefore: detection proposes, a versioned charter disposes.**

## Structure

### Definitions

A **domain** is a chartered subject area with a stable slug. Every domain has
both *direct members* (L0 nodes it holds itself) and *child domains* — leaf and
router are the same object with an empty child list, which makes the
router/leaf duality free and the partition lint true by construction.

**Tier** is depth. **Altitude** is a label in `{division, department, team}`
carried in the charter and used as a *render parameter*.

### Derivation

Run over the research layer only (`partition_graph`, `wiki_projector.py:539`),
so the in-flight code-layer removal needs no coordination.

**Step 1 — sections.** `detect_communities(research_graph)`
(`community_summaries.py:59-76`). Measured: 1,649 sections, sizes
`[8879, 3297, 2801, 1900, 1421, 981, …]`, median 2, in 2.4s. 1,837 Louvain
singletons fall out at the `len(c) > 1` filter.

**Step 2 — divisions by quotient.** Build a scope-graph: one `ResearchNode` per
section, one `part_of` edge per `(section_a, section_b)` pair that any L0 edge
crosses. Hand it to the *same* `detect_communities`.

This is the key trick. The existing dendrogram's top level has 1,820
communities of median size 2 — `graph_map` at root emits 1,820 size-ranked
cards, budget-paginated behind a `+N more, cursor=K` line, so an agent picks by
rank and page number rather than by meaning. The quotient collapses 1,649
sections / 1,039 cross-section edges into **4 balanced divisions**
(13,768 / 13,656 / 6,944 / 6,913 members).

**Step 3 — intake.** The 1,508 edge-isolated sections (3,806 members, max size
14) plus 1,837 singletons = 5,643 nodes (12.0%) become one `intake` domain with
a deterministic census brief and `quality: unrouted`. This is the one place a
table-of-contents body is honest, because there is no structure to route by.

Lexical fallback clustering was tested and rejected: `_cluster_scope_findings`
over the intake set yields 5,322 clusters of median size 1 and zero clusters
above 3,000 chars. It is a near-dup clusterer, not a topical one.

**Step 4 — recursive split by the one-read bound.**

```
sub      = [c for c in detect_communities(_induced_subgraph(rg, members)) if len(c) > 1]
children = [c for c in sub if mass(c) >= DOMAIN_MASS_FLOOR]
direct   = members - union(children)
if mass(direct) > DOMAIN_MASS_CAP:  direct becomes child "<slug>-general", recurse
if not children:                    STALL — oversized leaf flagged `unsplittable`
```

`mass()` is `sum(len(_render_member_block(n)))` — the exact text the distill
prompt consumes, LLM-free, already the memory-pressure proxy at
`agent_distill.py:2813`.

`DOMAIN_MASS_CAP = 24_000` as a **literal module constant**, deliberately *not*
derived from `CHUNK_CHAR_BUDGET // 2`: deriving it would let an env override of
`TESSERAE_LLM_CHUNK_CHARS` reshape the tree, which is the leak class
`agent_distill.py:150-155` already warns about for `ARTIFACT_CHAR_BUDGET`.

`DOMAIN_MASS_FLOOR = 3_000`, tuned by measurement: at 3,000 → 92 routers / 796
leaves / 9 stalls / depth median 3 max 5 / router fanout median 6, build 2.4s.
At 6,000 stalls jump to 53; at 12,000 to 83.

Deterministic and order-free at every depth because `_undirected_projection`
(`community_summaries.py:111-135`) sorts nodes and edge pairs before networkx
sees them.

**Tier labels.** Depth 1 = division, 2 = department, 3+ = team, clamped by leaf
member count so a label means the same across branches — a depth-2 domain of 60
members is a `team`, not a `department`.

**No new `ResearchNodeType`.** Briefs are separate artifacts, following the
agent-distillate precedent. Adding a node type would touch every projection
that switches on type plus seven-language docs, for nothing.

### Settled: synthesis nodes are excluded

Measured on the unfiltered graph, division 3's anchor is *Project Pulse*,
division 2's is *한 줄 요약*, and four of the 3DGS division's top eight
departments are *Daily Digest — \<date\>* pages. Roughly half the institution
would be an org chart of Tesserae's own output rather than of the knowledge it
ingested — the same shape as the self-capture loop fixed in v0.29.0.

Synthesis-family types are filtered out of the charter graph. Every division,
slug and measured number above is re-derived under that filter.

## Layering

The two axes are produced by **different mechanisms** and are independently
addressable:

* **horizontal (domain)** — *which* members. From community structure. Changes
  only at reorg.
* **vertical (altitude)** — *how* those members render. A parameter of
  `render_brief(domain, altitude)`. Changes on request, at zero LLM cost.

`.tesserae/charter/<slug>/brief.department.graph.json` and
`brief.division.graph.json` are the same member set at two altitudes, different
bytes, cached separately — the same shape as
`level_cache_path(cache_dir, level, cid)`, which already treats `(level, cid)`
as the cache key.

Any domain can be read at a *coarser* altitude ("show me this department the
way a division head sees it"). Reading at a *finer* altitude than its own is
not available in one read, by construction — that is its children's briefs.

**Altitude controls exactly two knobs:**

| altitude | carry_quota | support_floor |
|---|---|---|
| team | unbounded to budget | 1 |
| department | 40 | 4 |
| division | 18 | 12 |

**The rank that makes abstraction real:**

```
key = (-support_size, -distinct_child_count, -member_count, id)
```

where `support_size = len(_merged_refs([note]))` — the transitive raw-L0 root
count, already computed at `agent_distill.py:2391`. A note supported by 400
source documents is a broader claim than one supported by 3, so selection under
a tight quota promotes breadth deterministically.

This is why the rank is not primarily "how many child domains contributed":
that degenerates to a no-op on a siloed corpus where every group has exactly one
contributor. Support size does not degenerate. Cross-cutting count survives as
the secondary key — it helps where it works and can never be the single point
of failure.

**Degeneration is measured, not assumed.** At each router,
`altitude_lift = median(support_size of carried) / median(support_size of children's carried)`.
Lift ≈ 1.0 means the parent is a sample, not an abstraction. Written into the
brief header and surfaced through `lint_report`.

### Zero LLM calls per altitude or tier — stated as law

`_distill_manager` dedups child notes by `lineage_key`, groups by raw-root
overlap at Jaccard ≥ 0.5 (never by LLM-authored titles), and carries the
representative's title/body/kind **verbatim** (`agent_distill.py:2621-2647`,
comment at :2622: *"No paraphrase-of-paraphrase (LLM depth stays 1)"*). The only
prose minted above a leaf is contradiction arbitration.

A division brief is therefore never a paraphrase of a paraphrase, and adding a
tier or rendering a new altitude costs nothing. **This is what turns a reorg
from a bomb into a CPU pass.**

## Artifacts

**Below the bound — zero new files.** If the research layer fits
`ARTIFACT_CHAR_BUDGET`, or detection yields fewer than 2 sections,
`.tesserae/charter/` is never created and `TESSERAE.md` is byte-identical to
today. No migration.

**`.tesserae/charter/charter.json`** — registry and stable-identity substrate.
Sorted keys, no timestamps, `reorg_seq` an integer. Per slug:
`{own_altitude, tier, parent_slug, child_slugs[], anchor_id, cell_ids[],
direct_member_ids[], member_count, support_total, reorg_seq,
status: live|retired, superseded_by, slug_aliases[], derived_from[]}`, plus a
top-level `member_index` (node_id → slug).

A **declared determinism input** — the same hazard class `_prior_verbatim`
already carries. Rebuild *with* the charter is byte-identical; rebuild *without*
it refounds slugs, and `--refound` makes that explicit.

**`.tesserae/charter/<slug>/brief.<altitude>.graph.json`** — the one-read
artifact, byte-for-byte the same *shape* as
`.tesserae/agents/<key>/distilled.graph.json`, so `graph_map`,
`resolve_agent_view` and `drill_down` read it with no changes. Flat namespace
keyed by slug, deliberately not nested by tier: a domain promoted from team to
department keeps its path.

**Brief anatomy** (order is load-bearing — most abstract first, so any prefix is
a valid shorter answer):

```
H  header       slug, tier, altitude, anchor name, member count, parent, distilled_through, reorg_seq
N  carried notes  verbatim DistilledNote bodies selected by the altitude rank
A  arbitration    the only prose minted above a leaf
R  refs           one deduped map keyed by lineage_key
C  router footer  one line per child: slug, altitude, member count, anchor name, top 5 tags
I  index note     the counted remainder
```

`fit_to_budget` is **not modified**. The design catches the existing
`DistillSizeError` as the split trigger, adding a third outcome — split by
sub-community — on top of the two that exist.

**Retired domains are tombstoned, not deleted**: `status: retired` +
`superseded_by`, last brief kept readable, so a months-old citation degrades to
"this subject was reorganised into X" rather than a missing file.

## Routing

**Attached — 0 reads at task time.** The agent's config already contains
`.tesserae/agent_harness/domains/<slug>/TESSERAE.md`. It starts expert. Primary
mode, costs nothing.

**Routed — 1 call.** `charter_route(task, altitude="auto")`: `hybrid_search`
over ~900 charter rows (not 46,924 L0 nodes), greedy beam-1 descent, ties on
`(-score, slug)`. Returns `{path, brief, parent, siblings}`.

**Descent — read `TESSERAE.md`, then briefs**, following the router footer.
Every choice is between named subjects with anchor names and tags. No cursor,
no page number, no size rank. Worst case = 1 + depth = 6; median depth 3.

**Honesty split:** artifacts are byte-idempotent; the *route* is best-effort and
may vary with the embedding backend. Stated in the tool description, not buried.

## Evolution

**Fast clock — every compile.** Community detection never runs into the tree.
New nodes attach to the domain their existing neighbours hold by majority vote
over the sorted projection, ties by lowest slug. Structure does not move, so the
~29% reshuffle never touches an artifact path. The dirty set is free: the
incremental compile already computes touched ids (`project.py:1209-1245`); map
them through `member_index`. Measured: one document → 1–3 dirty leaves + ≤5
ancestors → ≤8 brief evaluations, most `skipped-watermark`.

**Slow clock — reorg**, versioned by `reorg_seq`. **Manual by default**
(settled): drift metrics recommend, they never fire it. A reorg is the one act
that re-keys slugs and breaks pinned attach paths.

**Identity — the fix for the churn measurement.** Member-set Jaccard ≥ 0.5 fails
for ~72% of large scopes on a single document. Three mechanisms, in order:

1. **Anchor** — top-degree member under `undirected_degrees`, assigned greedily
   in `(-degree, id)` order so no two siblings claim the same one.
   Preservation: 97.0% fine / 81.0% coarse.
2. **Cell set** — where the anchor moved, match over level-0 cell sets at
   Jaccard ≥ 0.5. Mass retention 97.4% vs 71.7% for member sets.
3. Neither matches → new slug.

**Transitions**, each appending a ledger row to a `SCOPE_REORG_LEDGER` namespace
in the existing `agent_distill_state` table (no schema change — `agent_key` is
free text): `founded / stable / promoted / demoted / split / merged / retired`.
Split: larger side keeps the slug. Merge: larger contributor keeps it, loser
tombstoned. *"Why did my domain change"* is answerable from the ledger,
including the Jaccard that decided it.

**Watermarks**, cheapest test first: `_node_content_hash` per member →
`_slice_input_hash` for a leaf's direct members → router input hash over sorted
`(child_slug, note.id, lineage_key, content_hash)` lines. A child re-distill
that changes no constituent bytes reproduces the hash, so the parent does zero
work.

**Corpus clock.** Leaf: `_domain_clock(members, as_of)` = `max(first_seen_at)`,
hard-failing if no member carries one — the same never-invent-a-timestamp
posture as `_corpus_clock`. Router: recursive `max(children.distilled_through)`.
Never wall-clock, so two runs any wall-time apart are byte-identical.

**Cost posture — settled: lazy.** Structure eager (seconds, no LLM). Leaf briefs
materialized on first attach or first route, with the never-blocking posture of
`materialize_community_summary`: any failure returns the deterministic floor and
is labelled `quality: structural`, never an error.

## Auditability

Every carried note is verbatim from the tier below, so a claim at any altitude
resolves downward by content hash to L0. `drill_down` works unchanged because
briefs share the distillate shape.

**Six deterministic lints**, no LLM in any, following `_cites_child_communities`'
posture — a rejected result is never cached, the structural floor renders
instead:

| lint | invariant |
|---|---|
| **CH-01 partition** | `union(child members) ∪ direct == own members`. True by construction; asserted as a test. Closes the singleton hole permanently. |
| **CH-02 coverage** | Every child appears in the router footer and either contributes ≥1 carried note or records `why_empty ∈ {below_support_floor, no_distillate, quota}`. |
| **CH-03 verbatim** | Every carried note's `content_hash` exists at the tier below. Turns "no paraphrase" from a promise into a hash chain. |
| **CH-04 specificity** | A note may rise only if its title+body contains a token that is the `name` of an L0 node in its own domain. *"We improved reliability and scalability at scale"* names nothing and cannot rise. |
| **CH-05 conflict** | Every pair `_conflicting_note_pairs` finds across children must appear as an arbitration note. |
| **CH-06 fallback census** | Count domains at `quality: structural`. Exit non-zero above a threshold. Without it, one LLM outage during a build over ~800 domains freezes a large slice of the institution at type-histogram quality forever, because no later healthy compile retries it. |

CH-04's known limits are carried forward rather than hidden: an
EvidenceSpan-only domain can starve it, and a domain whose members are named
"Python" passes it trivially.

## New code

1. **`tesserae/charter.py` (~380 lines)** — the only new module. Sections →
   quotient divisions → recursive split; intake; anchor-then-cell succession;
   fast-clock attachment; drift metrics; `render_brief`; the six CH lints;
   `charter.json` IO. Justified: no existing module owns *identity across
   compiles* — `community_summaries` owns detection, `hierarchy` owns dendrogram
   reads, `agent_distill` owns artifacts.
2. **Behaviour-preserving extraction in `agent_distill.py`** — lift
   `_distill_scope(...)` out of `distill_agent` and `_rollup(...)` out of
   `_distill_manager`, leaving both as thin agent-keyed callers. **Required, not
   cosmetic:** `distill_agent` returns early at `if not sessions`
   (`:1941-1942`), so document-only domains cannot use it. The existing
   byte-for-byte determinism tests are the regression oracle.
3. **`_domain_clock(members, as_of)` (~15 lines)** — `max(first_seen_at)`,
   raising `DistillError` when no member carries one.
4. **Altitude selection in `_rollup` (~30 lines)** — the carry rank, the
   quota/floor table, the deduped refs block with per-lineage capping, and
   `altitude_lift`.
5. **Wiring (~70 lines)** — a `domain:<slug>` branch in `_mcp_graph_map` and
   `compile_context`'s scope resolver; a `charter_route` tool; `scope=` and
   `strategy=` added to `compile_context`'s inputSchema and dispatcher (pure
   wiring for already-tested code); a `## Divisions` block in
   `agent_harness.py`; `charter_drift` / `charter_fallback` on `lint_report`; a
   `tesserae domains {status,reorg,attach}` CLI.

## Settled decisions

| # | decision |
|---|---|
| 1 | Domain vocabulary comes from the project's own KG communities, not a hand-written list |
| 2 | Single entry point that fits one read; becomes a router on overflow; below the bound nothing changes |
| 3 | Overflow splits by sub-community recursively, never by numbered size chunks |
| 4 | Synthesis-family nodes excluded from the charter graph |
| 5 | Leaf briefs built lazily on first attach/route |
| 6 | Reorg is manual; drift metrics recommend but never fire it |
| 7 | Intake (12%) accepted as a standing extraction-quality lint |
| 8 | `DOMAIN_MASS_CAP` is a literal `24_000`, not derived from `CHUNK_CHAR_BUDGET` |

## Rejected

* **Keying expertise to historical agent identities**
  (`claude-code:<account>:<role>`). Those are general-purpose runtimes, not
  experts. This is what `2026-07-19-layered-agent-kg.md` assumed.
* **Reading precomputed dendrogram levels as the tier structure.** Measured
  degenerate: the coarsest level has 1,820 communities of median size 2, and
  descent on the head yields 1 child, then 1, then 3009/28. Recursion on the
  induced subgraph instead yields 34 balanced sub-communities.
* **Numbered size shards (`domain-1..N`).** An agent cannot tell what is in
  shard 2 vs 3 without reading both.
* **Member-set Jaccard as the identity mechanism.** Measured failing for ~72% of
  large scopes on a single document.
* **Ranking carried notes primarily by cross-cutting contributor count.**
  Degenerates to a no-op on a siloed corpus.
* **LLM inference of structure at read time.** Breaks watermarks, ownership and
  byte-idempotence.

## Open

Altitude quotas (division 18/12, department 40/4, team unbounded/1) are set from
budget arithmetic. They are a product judgement about how much a division head
should read, and should be re-set from what a real division brief reads like
once one exists.
