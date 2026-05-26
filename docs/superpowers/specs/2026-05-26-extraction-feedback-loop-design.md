# Extraction-Feedback Loop — v1 Design

**Date:** 2026-05-26
**Status:** approved (brainstorm), pre-implementation
**Authors:** brainstormed with codex (CODEX_HOME=~/.codex-personal1), synthesized by Claude

## Problem

Tesserae accretes data (session re-ingestion) and reshuffles its own nodes
(supersedes, decay, community summaries, insight↔symbol linking), but it does
**not learn to extract better**. `lint_report`/`report.py` compute coverage
metrics (supported vs unsupported claims, orphan nodes) that nothing consumes.
The human corrections that ARE captured — vault edits, review accept/reject —
are written to a report and then discarded.

This feature closes that gap: human corrections become distilled guidance that
is injected (opt-in) into the extractor prompts, so the extractor stops
repeating mistakes the user already fixed.

### Why not the obvious "lint hill-climber"

A loop that mutates prompts/schema to drive `lint` up was explicitly rejected:
`lint` is a weak proxy for truth and optimizing it Goodharts (extract fewer/
safer claims, over-link to junk evidence, demote rich claims to dodge the
metric). Training and evaluating on the same corpus overfits the extractor to
yesterday's mistakes. **Human corrections are the gold label; lint is triage
only.**

## Locked decisions (from brainstorm)

1. **v1 scope:** collect feedback events + distill guidance + inject into the
   extractor prompt behind `--use-extraction-feedback` (OFF by default). Full
   gated loop, end-to-end.
2. **Feedback sources (v1):** vault user-edits (the existing
   `VaultOverride` / `VaultUserLinkChange` objects feeding
   `write_diverged_fields_report`) and review accept/reject
   (`ReviewDecision` joined to `ReviewItem` in `ReviewQueue.apply_decisions`).
   DEFER session-corrections and lint-as-reward.
3. **Distillation:** hybrid — deterministically cluster events, then a small
   LLM pass phrases each cluster as ONE guidance bullet, cached by cluster-hash
   so re-runs are stable (mirror `community_summaries.py`).
4. **Routing:** guidance is routed by `node_type` to the extractor that
   produces that type — NOT one shared blob.
5. **Guardrail v1:** minimal — drop any generated bullet that recommends a
   `negative_value` pattern. NO extraction A/B holdout in v1 (deferred to v2;
   needs candidate-graph capture before overlay).

## Data path

```
compile
  └─ ProjectWiki._apply_vault_overlay produces VaultOverride + VaultUserLinkChange
       └─ collect structured feedback events (from the objects, NOT by parsing
          diverged-fields.md) → append to .tesserae/extraction-feedback.jsonl
             (dedup by event_id)
review apply
  └─ ReviewQueue.apply_decisions joins ReviewItem → append review events

tesserae project evolve
  └─ read events → cluster by (extractor, node_type, field, source)
       └─ for each cluster with >= MIN_EVENTS (default 3):
            LLM-phrase one bullet (cached by cluster-hash) → fallback to
            deterministic template if no LLM reachable
       └─ write .tesserae/extraction-guidance.md (human-curatable)

compile --use-extraction-feedback
  └─ slice guidance by (extractor, node_type) for the current run
       └─ inject matching bullets into that extractor's prompt
```

`evolve` is BOTH a standalone command (refresh guidance) and an optional
compile input. Compile never auto-distills.

**Flag boundary (explicit):** feedback-event *collection* is UNCONDITIONAL —
every compile and every review-apply records events (harmless observability,
cheap, append-only). Only guidance *injection* into prompts is gated behind
`--use-extraction-feedback`. So a user gets the corpus building up immediately
and can turn on injection later once `evolve` has produced a guidance file they
trust.

## Feedback-event schema (`.tesserae/extraction-feedback.jsonl`)

One JSON record per correction, append-only:

| field | purpose |
|---|---|
| `schema_version` | int, currently 1 |
| `event_id` | sha256 dedup identity (see below) |
| `recorded_at` | ISO-8601 UTC |
| `source` | `vault_override` \| `vault_link_change` \| `review_decision` |
| `source_artifact` | provenance, e.g. `.tesserae/diverged-fields.md` |
| `target_extractor` | `doc_graph` \| `session_findings` \| `canonicalization` |
| `node_type` | captured AT EVENT TIME (e.g. `Claim`, `SessionDecision`) |
| `field` | e.g. `description`, `body`, `canonical_identity` |
| `action` | `replace` \| `add_link` \| `remove_link` \| `merge` \| `keep_separate` |
| `node_id` | best-effort; NOT used for clustering |
| `source_path` | originating doc/session path |
| `before_value` / `after_value` | the correction |
| `negative_value` | the corrected-away value (holdout hook) |
| `related_node_ids` | for link/merge events |
| `cluster_key` | `{extractor, node_type, field, source}` |

**Correctness rule (the biggest quiet-failure guard):** store `node_type`,
`field`, `source_path`, and values at event time and cluster on THOSE, never on
`node_id`. Nodes rename/merge/disappear after projection; clustering on node_id
produces precise-looking clusters that teach the wrong extractor.

**Dedup keys (event_id):**
- override: `sha256(schema_version + source + node_id + field + action + normalized(before) + normalized(after))`
- link change: `sha256(source + source_node_id + action + target_slug + target_node_id)`
- review: `sha256(source + item_id + action + canonical_node_id)`

## Guidance file format (`.tesserae/extraction-guidance.md`)

```md
# Tesserae Extraction Guidance
<!-- tesserae-guidance-schema: 1 -->

## Extractor: doc_graph
### Node Type: Claim
<!-- cluster: sha256:abc source=vault_override field=description events=7 -->
- Prefer concise claim descriptions that preserve the measured result and omit broad background framing.

## Extractor: session_findings
### Node Type: SessionDecision
<!-- cluster: sha256:def source=vault_override field=body events=4 -->
- Phrase decisions as explicit choices the user accepted, not speculative next steps.
```

Parse `## Extractor:` / `### Node Type:` headings to slice. Users may delete
bullets; the `<!-- cluster: -->` comments preserve cache identity without
hurting readability.

## Components (7 units)

| Module | Purpose |
|---|---|
| `tesserae/extraction_feedback.py` | event dataclasses, JSONL append/read, dedup by event_id, cluster-hash generation |
| `tesserae/extraction_guidance.py` | deterministic clustering, LLM bullet phrasing + deterministic fallback, cache read/write mirroring `community_summaries.py` |
| `tesserae/guidance_markdown.py` | render / parse / slice `.tesserae/extraction-guidance.md` by extractor + node_type |
| `tesserae/project.py` | new paths (`extraction_feedback`, `extraction_guidance`, `extraction_guidance_cache`); collect events in `_apply_vault_overlay` and on review-decision apply |
| `tesserae/cli.py` | `tesserae project evolve` (distill); `tesserae project compile --use-extraction-feedback` (consume) |
| `tesserae/llm_extractor.py` | inject doc-graph guidance in `build_research_extraction_prompt` |
| `tesserae/session_graph_llm.py` | inject session guidance into the system/user prompt |

## Tunables / additions over codex's draft

- **`MIN_EVENTS` threshold** (default 3): a cluster earns an LLM bullet only
  with >= MIN_EVENTS events — prevents sparse noise from becoming "guidance."
- **Graceful no-LLM degradation:** if `evolve` can't reach the LLM (e.g. Claude
  CLI not logged in), emit deterministic-templated bullets as a fallback rather
  than erroring. (The auth-discovery fix from v0.3.1 makes this rarer but it
  must still degrade cleanly.)

## Out of scope (v2+)

- Extraction A/B holdout guardrail (needs candidate-graph capture before vault
  overlay).
- Session-correction mining ("no, that's wrong" in later transcripts).
- Auto-inject (no flag) — only after the holdout guardrail exists.
- Lint metrics as anything beyond example-ranking for review.

## Testing

- `extraction_feedback`: dedup (same vault edit across two compiles → one
  event), cluster-key stability across node rename, schema round-trip.
- `extraction_guidance`: clustering groups by cluster_key; MIN_EVENTS gate;
  cache hit skips LLM on unchanged cluster membership; no-LLM fallback path;
  negative_value bullet-filter drops offending bullets.
- `guidance_markdown`: render → parse round-trip; slice returns only matching
  extractor+node_type bullets; user-deleted bullet stays deleted.
- Injection: `--use-extraction-feedback` on → prompt contains the sliced
  guidance; off → prompt unchanged (byte-for-byte vs current).
- No regressions in the full suite; the two extractor prompt sites keep their
  existing contracts when the flag is off.
