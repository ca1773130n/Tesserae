# Layered agent memory — per-agent knowledge graphs

<!-- translations:start -->
<p align="center"><a href="i18n/agent-memory.ko.md">한국어</a> · <a href="i18n/agent-memory.zh.md">中文</a> · <a href="i18n/agent-memory.ja.md">日本語</a> · <a href="i18n/agent-memory.ru.md">Русский</a> · <a href="i18n/agent-memory.es.md">Español</a> · <a href="i18n/agent-memory.fr.md">Français</a> · <a href="i18n/agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

Nobody remembers everything — and no agent's context window fits everything.
Tesserae's answer is a **layered knowledge base**: every agent grows its own
memory from its own sessions, that memory is periodically **distilled**
(organized, compacted, polished, refined — and forgotten, safely), and
managers see only the distilled layer of their reports. The manager's manager
sees a further rollup. Like a real organization, no single reader ever needs
the whole archive.

Everything below is opt-in and additive: projects that never run `tesserae
distill` behave exactly as before.

## The layers

- **L0 — the project graph** (`.tesserae/graph.json`). Unchanged, still
  byte-idempotent. The compile's structural pass now mints one `Agent` node
  per observed agent plus `performed_by` edges from each session — raw
  attribution, zero LLM cost.
- **L1 — one artifact per agent** (`.tesserae/agents/<key>/distilled.graph.json`).
  Written by `tesserae distill`. An ordinary graph file bounded to **one 48k
  read**, so any agent can load its whole distilled memory in a single call.
- **L2' — manager rollups.** Distilling an agent that has reports rolls up the
  reports' L1s: dedup by lineage, group by shared raw evidence, and carry the
  best note **verbatim** — LLM re-summarization depth is capped at 1, so a
  summary is never a paraphrase of a paraphrase. The same pass recurses to any
  org depth.

## Agent identity

Agents are keyed `harness:account:role` — role-grade, so a `reviewer`
subagent and a `planner` subagent grow *different* expertise even on one
machine. Roles come from subagent descriptors in transcripts, then from
declarative registry match rules, then fall back to `default`.

```bash
tesserae agents init         # scan sessions, propose .tesserae/agents/registry.json
tesserae agents list         # observed keys, labels, parents, session counts
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # migrates the artifact dir + registry atomically
```

Zero config works: every observed agent implicitly reports to `org:root`, and
`agent="org"` gives the flat team overview with no registry at all.

## Distilling

```bash
tesserae distill                      # every agent, leaves first, managers last
tesserae distill --agent <key>        # one agent
tesserae distill --dry-run            # estimate LLM calls, write nothing
tesserae distill --max-llm-calls 50   # hard budget; capped runs converge over reruns
tesserae distill --retry-fallbacks    # re-attempt clusters that fell back
tesserae distill --full               # ignore watermarks, re-distill from scratch
```

The pass clusters an agent's findings, summarizes each cluster (citation-
whitelisted and faithfulness-linted), and mints distilled notes whose
identity is a **lineage key** — the hash of the raw L0 evidence underneath,
never the LLM's wording. Caching is aggressive and shared: unchanged inputs
are watermark-skipped, grown clusters fold in incrementally, provider
failures are circuit-broken and produce deterministic structural fallbacks
(flagged, retryable, never cached as success).

Distillation is **opt-in**: set `TESSERAE_AGENT_DISTILL=1` (or
`{"agent_distill": {"enabled": true}}` in `config.json`). When enabled,
`tesserae refresh` also distills automatically — but only agents under
*memory pressure* (their undistilled findings no longer fit half a context
read), the MemGPT-style consolidation trigger.

## Forgetting — never deletion

- **Absorb**: a decayed, low-confidence finding covered by an llm-quality
  distillate is folded into it (`absorbed_refs`) and suppressed in default
  reads — but stays reachable via `include_superseded` and `drill_down`.
- **Demote**: everything else at worst drops from full body to a title+ref
  line in the agent's Index note. Age alone never makes knowledge invisible.
- **Ledger**: every promotion/demotion/absorption is appended to a forget
  ledger and surfaced by `tesserae lint` (`AGENT_FORGET_LEDGER`), along with
  an undistilled-backlog metric per agent (`AGENT_UNDISTILLED_BACKLOG`).

## Reading as an agent — the `agent=` argument

Every graph-reading MCP tool accepts `agent=`:

- **worker key** → own raw experience ∪ own distilled notes, distillate-
  preferred (absorbed raw is auto-suppressed by an overlay derived at load
  time — nothing is ever written back into `graph.json`).
- **manager key** → a federation of the reports' L1 artifacts only. Raw
  findings never leak upward.
- **`org`** → all distilled artifacts, zero config.

Supporting tools: `agent_view_explain` (members + `distilled_through`
staleness watermark — how old each report's expertise is), and `drill_down`
(resolve a distilled note's `member_refs` back to raw L0 evidence with
alive / changed / absorbed / gone status — every call audit-logged).
`compile_context --multi-pool` reserves budget slots for distilled notes and
expertise profiles and labels stale or fallback-quality knowledge in the
output.

## The growth loop

- **Per-agent harness**: `write_harness` agent mode emits a harness dir per
  agent whose MCP config reaches that agent's resolved view, plus a seed-once
  `purpose.md` mission page generated from its expertise profile.
- **Per-agent guidance**: steer one agent's distillation via
  `.tesserae/extraction-guidance-<key>.md`, layered over the project-level
  `.tesserae/distill-guidance.md`. Editing one agent's stream re-distills
  only that agent.
- **Semantic bridges** (opt-in): link *related* distillates across agents
  with `shares_concept_with` edges in manager/org views — edges, never
  merges.
- **Topic maps**: `agent_topics` rolls an agent's distillate set into a
  deterministic `topics.md` — the agent's table of contents.
- **Subagent promotion**: typed subagent runs mint findings under the
  subagent's own key, so delegated work accumulates into the delegate's
  expertise.

## Determinism guarantees

The project graph stays byte-idempotent; distilled artifacts are
deterministic given (graph bytes, registry, cache dir, prior artifact,
options). Time is always the **corpus clock** — the newest instant in the
sessions themselves, recursively the newest child watermark for managers —
never wall-clock. Node identity never depends on LLM prose. A lint probe
rejects timestamp/counter-shaped metadata on agent-layer nodes, because that
exact class of state has broken byte-idempotence before.

Full design rationale: `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
