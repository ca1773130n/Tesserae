# Karpathy alignment — plan

**Date:** 2026-05-01
**Goal:** complete the Karpathy three-layer model: raw → wiki → schema, with `ingest` / `query` / `lint` as first-class operations.

## Current gap

| Karpathy op | Our equivalent | Status |
|---|---|---|
| ingest | `project ingest` / `project compile` | ✅ shipped |
| query | (none — MCP search exists for agents) | ⚠ missing |
| lint | (none) | ⚠ missing |
| schema | implicit in `research_graph.py` enums | ⚠ implicit |
| purpose | (none) | ⚠ missing |

## Plan

### Phase 1 — `project lint` (Subagent Z1)

`llm_wiki/lint.py` defines `WikiLinter(project_root)`. Checks:

- **Orphan papers** — Paper with no edges, or only `mentioned_in`.
- **Missing `implemented_in`** — Paper with `arxiv_id` that has a sibling Repository (matching arxiv_id metadata) but no `implemented_in` edge.
- **Stale citations** — markdown links to `papers/<id>.md` whose target file does not exist.
- **Dangling wiki links** — `<a href>` in any `wiki/*/*.md` body whose target slug isn't a known wiki page.
- **Drift between graph and wiki/** — wiki page exists with no matching public graph node, or vice versa.
- **Contradicting claims** — two `PerformanceClaim`s from different sources reporting opposite signs of the same metric on the same benchmark for the same model.
- **Low title quality** — Paper with `title_quality in {arxiv_only, invalid}` flagged for manual review.
- **Synthesis ghost inputs** — synthesis page whose `inputs` reference graph node ids that no longer exist.
- **Suggested merges** — two Repository nodes pointing at the same `github_repo` URL but stored under different ids; two Person nodes with identical canonical name from same affiliation.
- **Stale build-history** — entries in `.build-history.jsonl` more than 90 days old (informational).

Output: `.llm-wiki/lint-report.md` (categorized) plus colored stderr summary. Exit code: 0 = clean, 1 = warnings, 2 = errors.

CLI: `project lint [--strict] [--severity warning|error] [--fix-trivial]`. `--fix-trivial` only fixes safe ones (e.g., add missing `implemented_in` edges, prune ghost inputs).

### Phase 2 — `project query` (Subagent Z2)

`llm_wiki/query.py` defines `WikiQuery(project_root)` over the existing `search-index.json` plus per-page `.txt` AI siblings.

Two modes:

- **search-only (default)** — BM25 over the index, prints top N results with paths and a 2-line excerpt each.
- **`--llm`** — uses anthropic SDK with prompt caching: system block is the wiki overview + ontology; cached. Per-query block is the question + the top K relevant page bodies. Falls back to search-only if no `ANTHROPIC_API_KEY` or SDK missing.

CLI:
- `project query "<question>"` — one-shot.
- `project query --interactive` — REPL with arrow-key history (use `readline` from stdlib).
- `--top-k N`, `--kind <kind>`, `--llm`, `--model claude-sonnet-4-6`, `--no-llm` (force off even if env enables it).

Output for one-shot: prints to stdout, JSON if `--json`. REPL: rich-text-ish (just stdout, no curses).

### Phase 3 — schema layer (inline, after Z1+Z2 land)

Write into `.llm-wiki/wiki/`:

- `purpose.md` — auto-seeded from project config + a clearly-marked editable user section. States "what this wiki is for" so future ingest can read it.
- `schema.md` — auto-generated from `ResearchNodeType` + `ALLOWED_EDGE_TYPES`; explains every node and edge type in plain English, with examples drawn from the live graph.
- `index.md` — already implicit in synthesis pulse; expose explicitly.
- `log.md` — chronological compile log, sourced from `.build-history.jsonl`.

These four files are the "schema" layer Karpathy specifies. Surface them on the static site About route.

## Sequencing

- Z1 + Z2 in parallel — both add new modules + new CLI subcommand; minimal cross-file conflict (both touch `cli.py` parsers in different blocks).
- Phase 3 inline after both land.

## Verification

- `project lint` over the live `data/` corpus produces a non-empty report identifying real issues; running it again after auto-fix lowers the count.
- `project query "What is Gaussian Splatting?"` returns at least one Concept page; with `--llm` (dry-run mode if no key) it returns a synthesized answer with citations.
- `purpose.md` + `schema.md` exist after compile; About page renders both.
