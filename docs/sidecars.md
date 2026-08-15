# `.tesserae/` — what is in it, and what deleting it costs

<!-- translations:start -->
<p align="center"><a href="i18n/sidecars.ko.md">한국어</a> · <a href="i18n/sidecars.zh.md">中文</a> · <a href="i18n/sidecars.ja.md">日本語</a> · <a href="i18n/sidecars.ru.md">Русский</a> · <a href="i18n/sidecars.es.md">Español</a> · <a href="i18n/sidecars.fr.md">Français</a> · <a href="i18n/sidecars.de.md">Deutsch</a></p>
<!-- translations:end -->
A mature project accumulates around sixty entries under `.tesserae/`, and a
directory listing tells you nothing about which of them a compile rebuilds for
free, which cost an LLM pass to rebuild, and which hold work that nothing can
reconstruct. `compile.lock` and a zero-byte orphaned tmp file look exactly like
`candidate-same-as.json`, which carries human verdicts.

This page is that answer, ordered by consequence. The classification itself
lives in `tesserae/sidecars.py` — one registry entry per file, each recording
its owner, its kind, and what is lost by deleting it. The registry is the
source of truth; this page is its readable projection, and `tesserae doctor`
prints the live one.

Every entry carries two independent fields:

| Kind | Where the bytes come from |
|---|---|
| `derived` | republished by a compile from the sources |
| `accumulated` | appended to over time; no compile re-derives it |
| `cache` | a stored answer to a question that can be asked again |
| `scratch` | process bookkeeping: locks, pidfiles, tmp debris |

Kind says where the bytes come from. It does **not** say whether removal is
safe — `safe_to_delete` is a separate field, and it disagrees with kind often
enough to matter: a `cache` whose answer came from a model is not safe to
delete, and a `derived` file can carry human approvals. The sections below are
ordered by that second field, because it is the one you actually want.

## Safe to reclaim — a compile rebuilds these

Delete any of these and the next compile puts them back, byte for byte, with no
model call:

<!-- sidecars:safe-list -->
`agent_harness` · `code-graph.json` · `combined-graph.json` ·
`diverged-fields.md` · `doctor-report.json` ·
`doctor-report.md` · `graph.json` · `graph.kuzu` · `graphiti_episodes.jsonl` ·
`hierarchy.json` · `lint-report.json` · `lint-report.md` · `log.md` ·
`markdown_projection` · `merge-ledger.json` · `okf` ·
`okf-imported.graph.json` · `output-snapshot.json` · `report.md` · `research` ·
`schema-drift.md` · `site` · `summaries` · `temporal_facts.jsonl` · `wiki` ·
`.watch-cache.json` · `arxiv-cache.json` · `code-graph-cache.json` · `external`

`graph.json` is on that list on purpose. The compiled graph is a pure function
of the sources plus the accumulated sidecars below — which is precisely why
*those* are the ones to protect, and why a "let me just delete `.tesserae/` and
recompile" reflex is wrong even though its most visible file is disposable.

## Costs a model pass — and changes `graph.json` bytes

These are stored answers from an LLM. Rebuilding them costs a pass, and the
model does not return the same words twice, so anything downstream of them
changes bytes too.

| Entry | Kind | What a rebuild costs |
|---|---|---|
| `session_findings` | `cache` | the sharpest case: these findings become graph **nodes**, so dropping the cache re-runs a non-deterministic extractor and the next `graph.json` differs in bytes — the byte-idempotence break this repo has taken four times |
| `community_summaries` | `cache` | LLM-written community summaries keyed on the member hash |
| `distill_cache` | `cache` | agent distillation results |
| `distillation_cache` | `cache` | distillation results |
| `extraction_guidance_cache` | `cache` | one LLM-phrased bullet per feedback cluster |
| `schema_drift_cache` | `cache` | LLM sub-type proposals per host type |
| `supersede_cache` | `cache` | LLM supersede arbitration |
| `schema-drift-proposals.json` | `derived` | derived bytes, non-derivable content: the record holds the human `approved` gate and an editable `proposed_type`, so a rebuild costs a pass **and** discards the approvals |

## Unrecoverable — nothing rebuilds these

No compile re-derives anything here. Deleting one is data loss, not a delay.

| Entry | Kind | What you lose |
|---|---|---|
| `candidate-same-as.json` | `accumulated` | human same-as verdicts. A compile that cannot find it does not fail — it silently re-asks a question a human already answered, and a rejected pair comes back un-rejected |
| `sqlite.db` | `accumulated` | mixed; see below |
| `agent-writes.jsonl` | `accumulated` | the agent-authored overlay, replayed as a fifth producer on every compile; deleting it erases every agent write |
| `vault_snapshot.json` | `accumulated` | the baseline `vault_pull` diffs against. Delete it mid-edit and the next compile cannot tell your edit from its own prior projection — the vault's whole override mechanism |
| `obsidian_vault` | `accumulated` | bidirectional and user-owned: your edits here are pulled back into the graph, so it is not a projection that can be redrawn |
| `config.json` | `accumulated` | project configuration, including `obsidian.vault_path` — user input, never regenerated |
| `charter` | `derived` | every compile derives it from `graph.json`, yet no rebuild reproduces it: slugs are minted from whichever anchors a rebuild happens to pick, so deleting it re-founds every domain under a new name, breaks every pinned attach path, and discards the tombstones that were the only record of where the old names went |
| `agents` | `accumulated` | per-agent `registry.json` and the hand-written `purpose.md` |
| `discovered_links.json` | `accumulated` | the association overlay accumulates scored links across runs; one run does not reconstruct it |
| `extraction-feedback.jsonl` | `accumulated` | human corrections captured during vault overlay and review-apply |
| `extraction-guidance.md` | `accumulated` | hand-edited guidance that an evolve pass merges into |
| `harness_sessions` | `accumulated` | imported session state |
| `harness_sessions.db` | `accumulated` | imported agent sessions, whose upstream transcripts rotate away — a re-import does not reconstruct them |
| `session_chunks.db` | `accumulated` | normalised turns written live by the daemon's tailer, from transcripts that do not stay available |
| `manifest.json` | `accumulated` | per-source ingest state; without it the next batch re-ingests everything and re-runs extraction over sources it already read |
| `.build-history.jsonl` | `accumulated` | one line per build with the `git_head` it compiled at; deleting it makes graph staleness permanently unknown |

### `sqlite.db` is mixed, and classified by its most valuable table

The graph mirror in it is derived and `node_vectors` is a droppable vector
cache — but the same file holds `node_memory` (decay, access counts, reinforced
confidence), `fact_observed` (transaction time, a real wall clock that only
moves forward) and `read_audit`, none of which can be recovered. Dropping the
file to reclaim the vector cache resets every fact's "when we learned it" to
now. Reclaim space with `tesserae doctor --fix`, which vacuums, rather than by
deleting the database.

## Locks, pidfiles and debris

| Entry | Kind | Before removing |
|---|---|---|
| `compile.lock` | `scratch` | the compile mutex. **Never** removed by any automated path — the recorded failure mode is SessionEnd compile pile-ups, and doctor's `compile_lock` check is report-only for the same reason |
| `.recompile.lock.d` | `scratch` | mkdir-based hook mutex; removing a held one lets two recompiles race |
| `session_chunks.lock` | `scratch` | backfill's skip-if-held flock; removing a held one lets two backfills write the same day |
| `daemon*.pid` | `scratch` | engine pidfile, host-scoped as `daemon.<host>.pid`. Doctor removes one only after confirming the recorded owner is dead **on this machine** |
| `graph.json.bak-*` | `scratch` | no Tesserae code path writes these. They are hand-made copies from a restore session — reported, never removed, because a human made them |
| `*.tmp*` | `scratch` | the orphaned half of a tmp+replace write, named `<target>.tmp.<pid>.<hex>`. Removable only once the owning pid is gone: a live writer is mid-rename |
| `.*-hook.log*` | `scratch` | shell-hook diagnostics; doctor rotates the oversized ones |

## `~/.tesserae/` — machine-wide, same directory name

The user-scope directory shares a name with the project one and means something
different. `config.json` exists in both: in the project it is project
configuration, here it is LLM configuration for every project on the machine.

| Entry | Kind | What you lose |
|---|---|---|
| `registry.json` | `accumulated` | the project registry. Deleting it unregisters every project on this machine |
| `config.json` | `accumulated` | machine-wide LLM configuration; user input |
| `host_id` | `accumulated` | this machine's identity. Regenerating it makes every host-scoped pidfile and session record on shared storage look foreign |
| `harness_sessions` | `accumulated` | machine-wide session import state |
| `llm_cache` | `cache` | cached LLM responses; a rebuild calls models and does not reproduce them |
| `federation` | `cache` | cross-project link and vector caches — safe to drop |
| `wiki` | `derived` | machine-scoped serve scratch — safe to drop |
| `engine.pid` | `scratch` | fleet pidfile; a stale one once held a six-day-dead pid, which is why pidlock validates rather than trusts |
| `engine.pid.lock` | `scratch` | fleet pidfile mutex; removing a held one lets two fleets start |
| `*.bak*` | `scratch` | pre-migration copies of `registry.json` and `config.json`. No code path writes them, so they exist because somebody wanted them kept |

## Seeing the live classification

```bash
tesserae doctor          # the `sidecars` check, under hygiene
tesserae doctor --fix    # removes orphaned tmp halves, nothing else
```

The `sidecars` check reads your actual `.tesserae/` against the registry and
reports three populations separately: orphaned tmp halves, hand-made
`graph.json.bak-*` copies, and entries no registry entry claims. `--fix`
removes only the first, and only when the writer pid is dead and the file is
over 24 hours old — because a live writer sits between `write_text` and
`replace`, and `os.kill(pid, 0)` answers about the local process table only
when several hosts can mount one `.tesserae/`.

**Unclassified entries are reported and never touched.** An entry the registry
does not claim is more likely to be somebody else's file — your notes, another
tool's cache — than a Tesserae bug, so the answer to finding one is to name it,
not to remove it. It is also how a new Tesserae sidecar that skipped
registration becomes visible.

Tesserae ships no bulk `reset` verb. The classification is what would make one
possible; writing the classification down and shipping a destructive command
against it in the same breath is the wrong order.
