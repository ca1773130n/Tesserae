# Cross-project federated graph mode — design sketch

Date: 2026-06-26 · Status: design (no code). Goal: let `ask` / `compile_context`
reason **across** registered projects' graphs — true cross-referencing — instead
of the current `scope: all-registered` fan-out that returns N independent answers
(`by_project`, `mcp_server.py:2510`).

## 1. Motivating example

> "What did I decide about caching in my **work** project, and does any paper I
> clipped in my **research** project support or contradict it?"

Today: two separate answers, you join them in your head. Federated: one cited
answer where retrieval **traverses** from the work `SessionDecision` to the
research `Paper` through a cross-project link, and the synthesis writer notes the
connection and its provenance.

## 2. Core decision — virtual federation, not a materialized merge

Reject a persisted "super-graph" (union of all projects recompiled on any
change): it's O(corpus) to build, stale the moment one project recompiles,
collision-prone, and it would smear a derived artifact across projects (breaking
per-project byte-idempotence, KB-07).

**Adopt:** keep every project's `graph.json` untouched and separate. Persist only
the **cross-project links** (small, the expensive-to-compute part). At query time,
assemble a **bounded, in-memory `ResearchGraph`** for just the query's
neighborhood across the selected projects, and run the **existing** retrieval
stack on it unchanged.

The leverage: namespacing node ids by project produces a *real* `ResearchGraph`,
so `retrieval/hybrid.py`, `retrieval/ppr.py`, and `context_compiler.compile_context`
all work as-is. New code is only: (a) link computation, (b) graph assembly,
(c) a `scope: federated` entrypoint. **PPR is already weight-aware**
(`personalized_pagerank(edge_type_weights=…)`), so cross-link weighting is free.

## 3. Data model

```
FederationLink:
  a: {project: str, node_id: str}      # project alias from the registry
  b: {project: str, node_id: str}
  kind: "same_as" | "related"
  weight: float                        # 1.0 for identity; cosine for semantic
  basis: "url"|"arxiv"|"doi"|"repo_url"|"symbol_fqn"|"content_hash"
        |"name_jaccard"|"embedding"
  evidence: str

FederationIndex:                       # the persisted cache
  projects: [{name, graph_hash}]       # invalidation key (per-project graph hash)
  embedding_backend: str               # rebuild if the embedder changes
  thresholds: {jaccard, cosine}
  links: [FederationLink, ...]
```

Stored at `~/.tesserae/federation/links-<hash>.json` (NOT in any project's
`graph.json`). Keyed on the multiset of participating `graph_hash`es + embedding
backend + thresholds, so it's deterministic and cache-invalidates per project.

## 4. Linking pipeline (entity resolution) — the hard core

Naive all-pairs is O(N_A·N_B); make it tractable + deterministic (content-keyed,
no `now()`/RNG, deterministic embedder — same discipline as every compile pass).

1. **Block by type.** Only compare union-compatible `ResearchNodeType`s
   (Paper↔Paper, Concept↔Concept, Repository↔Repository, CodeFunction↔CodeFunction…).
2. **Identity join (high precision → MERGE).** Hash-join on strong keys already in
   node metadata: `arxiv_id`, `repo_url`/`github_repo`, DOI, source URL,
   `source_path`, fully-qualified code-symbol name, or identical `content_hash`.
   O(N). These become `same_as` links, weight 1.0.
3. **Name near-duplicate (medium → EDGE).** Reuse `memory/supersede.jaccard` on
   normalized names above a threshold → `related` link.
4. **Semantic (fuzzy → EDGE).** ANN over the existing embedding index
   (`retrieval/hybrid` backend): per node, top-k nearest cross-project neighbors,
   thresholded by cosine → `related` link, weight = cosine·β. Never all-pairs.

**The rule that keeps it safe: identity → merge, similarity → weighted edge.**
False-merges cross-contaminate answers, so only merge on high-precision keys;
everything fuzzier stays a *weighted* `shares_concept_with` edge that PPR can
down-weight. (Edge types already exist — no schema change.)

## 5. Federated graph assembly (query-time, bounded)

Given selected projects + the persisted links:

1. **Namespace** every node id as `"<project>::<node_id>"` so two projects'
   colliding ids stay distinct.
2. **Union-find merge** the `same_as` links → pick a canonical id (smallest,
   for determinism); re-point each project's edges to canonical ids; stamp
   `metadata.federation_sources = [projA, projB, …]` on merged nodes (for
   provenance/citations).
3. **Add cross-edges** for `related` links as `shares_concept_with`, carrying the
   weight in edge metadata.
4. The result is a `ResearchGraph`. Existing retrieval runs on it verbatim.

**Localized, not whole-union (scale).** Don't assemble all projects entirely.
Federated query = seed-driven:
  seed hybrid search in each selected project → take top seeds → expand a few hops
  *within* each project → pull only the cross-links whose endpoints are on that
  expanded frontier → assemble the stitched bounded subgraph → PPR + compile_context.
Cost scales with the **query neighborhood**, not the total corpus, so 10s of
projects / 100k-node graphs stay tractable.

## 6. Federated retrieval

- **Recall (seeds):** run each project's hybrid (lexical+embedding) search,
  merge-rank globally with project provenance. Identity-merged nodes dedup.
- **Inference (traversal):** `personalized_pagerank` over the stitched federated
  adjacency, with `edge_type_weights` set so cross-`shares_concept_with` edges
  propagate **less** mass than identity structure (same_as already merged) — so a
  semantic bridge nudges, an identity match fully connects. *This is the
  cross-project "inference": probability mass flows A→B through resolved links.*
- **Synthesis:** `compile_context` over the federated subgraph; citations carry
  `project::node`; the synthesis writer is told which nodes are cross-project and
  asked to surface the connection (and any contradiction — reuse the contradiction
  pass cross-project as a v2 bonus).

## 7. Invariants & guarantees

- **Per-project `graph.json` untouched.** Federation only reads them; the link
  index and the assembled graph are separate derived artifacts → KB-07 preserved.
- **Deterministic.** Links are content-keyed (graph hashes + deterministic
  embedder); two runs over unchanged projects → identical links/answers.
- **Local-only / private.** No network; federation is opt-in and **scoped** —
  union only the projects you name, so personal/work stay separate by default.
- **Honest degradation.** No real embedding backend (hash-bucket stub) ⇒ skip
  semantic links, use identity+name only, and **say so** (don't emit noise).

## 8. Surfaces & artifacts

- `tesserae ask "Q" --scope federated [--projects A,B,C]`
- MCP `ask` / `compile_context`: `scope: "federated"`, `scope_aliases: [...]`.
- `tesserae federation build` (precompute the link index),
  `federation status` (counts: merges, related edges, per-project),
  `federation explain <project::node>` (its cross-project links — inspectability).
- New module `tesserae/federation/`: `link.py` (resolution), `assemble.py`
  (namespacing+merge+edges), `retrieve.py` (localized federated query).
- Reuses: `ProjectRegistry`, `ResearchGraph`, `retrieval/ppr.py`,
  `retrieval/hybrid.py`, `context_compiler.compile_context`,
  `memory/supersede.jaccard`.

## 9. Cost / scale / failure modes

| Risk | Mitigation |
|------|------------|
| All-pairs linking blowup | type-blocking + identity hash-join + ANN (no all-pairs) |
| Whole-union memory at query | localized seed→expand→stitch (bounded subgraph) |
| False `same_as` merge | merge only on high-precision keys; fuzzy → weighted edge |
| No embeddings | degrade to identity+name links, announce it |
| Disjoint ontologies (code vs papers) | links only on shared types → federation honestly adds little; fine |
| Stale links after a recompile | invalidate only links touching the changed project (graph-hash keyed) |
| PPR mass dominated by cross-edges | tune `edge_type_weights[shares_concept_with]` < within-project defaults |

## 10. Staged plan

- **MVP (no embeddings, low risk):** identity-merge links only
  (arxiv/repo/url/doi/symbol/content_hash) + federated **hybrid recall** with
  dedup + cited synthesis over the merged top-k. No PPR yet. Delivers cross-project
  recall + identity dedup + one cited answer — ~70% of the value, deterministic,
  no embedding dependence. New: `link.py` (identity only), `assemble.py`,
  `scope: federated` in ask.
- **v2 — true inference:** add semantic `related` edges (ANN) + **federated PPR**
  (localized) + cross-project contradiction surfacing. The "traverse A→B" payoff.
- **v3 — ergonomics/scale:** persisted incremental link cache + `federation
  status/explain` + scope aliases/privacy controls + bounded-neighborhood tuning.

## 11. Open questions

1. **Default merge precision** — which identity keys are safe to auto-merge vs.
   require confirmation? (arxiv/doi/repo_url: safe; bare URL: usually; name: never.)
2. **Where the link index lives** — global (`~/.tesserae/federation/`) vs. a named
   "federation" pseudo-project in the registry (lets you `ask` it like a project).
3. **Edge-weight calibration** — needs a small eval (does cross-project PPR surface
   genuinely relevant other-project nodes without flooding?). Gate v2 on it.
4. **Contradiction across projects** — high-value ("your decision contradicts a
   clipped paper") but needs the merge to be trustworthy first.
