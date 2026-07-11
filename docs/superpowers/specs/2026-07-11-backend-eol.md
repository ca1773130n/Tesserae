# Backend end-of-life: understand-anything (now) and cognee (0.19)

Date: 2026-07-11 · Status: understand-anything executed; cognee scheduled for 0.19

## Context

Post-v0.18.0, the default KG update and retrieval paths are fully in-house:
the typed ResearchGraph pipeline writes the graph (native code-graph extractor
included), and `ask` retrieves via the LLM planner over KG primitives. The
three bolted-on backends no longer participate in the default loop:

- **understand-anything** merged its external `knowledge-graph.json` into the
  ResearchGraph at compile — code-structure nodes now redundantly covered by
  `code_graph_extractor.py` (356 files re-extracted natively in the v0.18.0
  release smoke). External remote-install tool, chronically stale artifacts.
- **cognee** duplicated the graph *outward* into its own store (cognify pass,
  bundle export) and contributed nothing back; default-install behavior was
  actively harmful pre-0.18.0 (log spam + OperationalError inside `ask`).
- **rag-anything** is KEPT as an optional extra: multimodal document RAG is a
  capability the KG genuinely lacks; isolated behind `enabled: false` +
  `[raganything]` extras with real test coverage.

## Stage 1 — understand-anything removal (this change)

Remove: `understand_anything_adapter.py`, `understand_anything_refresh.py`,
the compile-time `merge_understand_anything_graph` call, UA branches in
`integrations refresh`, `deps.py` detection, `project_setup.py` external-tool
entries, the doctor `backend_artifacts` UA arm, `docs/integrations/
understand-anything.md` (+ its 7 i18n copies), and every wizard/help mention.

Stubs (exit 2, one line, per the no-silent-aliases convention):
- `tesserae integrations refresh understand-anything` →
  `removed — code-structure nodes are extracted natively; see tesserae code ingest`

Preserved: UA-minted nodes already in compiled graphs are ordinary nodes and
remain valid; nothing rewrites history. `external_tools` config entries for UA
are ignored with a one-line stderr note (not an error) so old configs load.

## Stage 2 — cognee removal (execute in 0.19)

Remove: `cognee_adapter.py`, `cognee_direct.py`, `cognee_query.py`, the
cognify pass + bundle export in `project.py`, `query --backend cognee`
(+ `--cognee-search-type`, `--cognee-dataset`), the `[cognee]` extra, wizard
mentions, and `memory_backends.cognee` handling (ignored-with-note on load).

Stubs:
- `tesserae query --backend cognee` →
  `removed in 0.19 — cognee was demoted in 0.18 and never fed the graph; use plain query or ask`

Grace rationale: `query --backend cognee` shipped in 0.18.0; one release of
stub-grace before deletion. If anything depends on it in the wild, the 0.18
cycle is when we'd hear.

## Non-goals

rag-anything stays. If it also goes unused, extract it into a plugin rather
than deleting — it is the only backend with a capability the KG lacks.
