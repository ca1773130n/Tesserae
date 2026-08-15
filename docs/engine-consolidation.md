# Automatic consolidation — the engine's sleep cycle

<!-- translations:start -->
<p align="center"><a href="i18n/engine-consolidation.ko.md">한국어</a> · <a href="i18n/engine-consolidation.zh.md">中文</a> · <a href="i18n/engine-consolidation.ja.md">日本語</a> · <a href="i18n/engine-consolidation.ru.md">Русский</a> · <a href="i18n/engine-consolidation.es.md">Español</a> · <a href="i18n/engine-consolidation.fr.md">Français</a> · <a href="i18n/engine-consolidation.de.md">Deutsch</a></p>
<!-- translations:end -->

Brains consolidate memory during rest. While you sleep, the day's raw
experience is reorganized, compacted, and integrated — the loud, recent stuff
folded into durable structure. Tesserae's engine does the same thing. When a
project goes **idle**, the always-on daemon stops waiting for the next edit and
spends the quiet reorganizing what it already knows: it **compresses and
forgets** loud recent memory, lets knowledge nothing has retrieved **fade by
disuse**, and **discovers new connections** between what survives.

Until now that pass only ran when you asked for it — `tesserae refresh` under
memory pressure, or an explicit `tesserae distill`. The engine recompiled on
every file and session event but never consolidated on its own. The **sleep
cycle** closes that gap: leave `tesserae engine` running and consolidation
happens during rest, with no command to remember.

Like everything in the [layered memory](agent-memory.md) system, it is a
**no-op unless you opt in** — the daemon consolidates on idle, but the
distillation underneath only does work when `TESSERAE_AGENT_DISTILL` is set.

## When it fires

A dedicated consolidation thread wakes on a fixed **check interval** (default
30s) and evaluates two independent triggers against a monotonic activity clock:

- **Idle trigger.** The project has seen no trigger event and no pipeline run
  for at least `--consolidate-idle` seconds (default **300s = 5 min**). This is
  the "consolidate during rest" case — the engine noticed you stopped working
  and used the lull. A **floor** since the last consolidation prevents thrash,
  so a busy project that just went quiet does not consolidate on a hair
  trigger.
- **Ceiling trigger.** At least `--consolidate-every` seconds have elapsed
  since the last consolidation, **regardless of activity** (default **21600s =
  6h**). This guarantees a continuously busy project still consolidates
  periodically instead of never getting a quiet moment. Setting it to `0`
  disables the ceiling — idle is then the only trigger.

Every edit, session turn, or recompile bumps the activity clock, so the idle
window only elapses during genuine rest. Both clocks are **monotonic**, never
wall-clock, and are never persisted into any artifact — consolidation timing
can never perturb the byte-deterministic graph.

## What it runs — five operations

Each fire loads the compiled graph from `.tesserae/graph.json` (if the file is
absent, the pass is skipped) and runs five consolidation operations, in order.
Together they mirror what a resting brain does: compact the loud recent stuff,
let the never-revisited stuff fade, wire up new associations between what
survives, and rehearse — spend a little effort now, while nobody is waiting, on
the descriptions a reader will want next.

### 1. Compress / forget — distillation

Calls the same `maybe_distill_on_refresh` entry point that `tesserae refresh`
uses to reorganize, compact, and safely forget each agent's memory. That
function is **triple-gated** internally and never raises for a per-agent
failure:

1. **Opt-in gate** — `TESSERAE_AGENT_DISTILL=1` (or `{"agent_distill":
   {"enabled": true}}` in `config.json`). Off by default; the whole cycle is a
   safe no-op until you set it.
2. **Per-agent watermark** — an agent whose findings have not changed since its
   last distillation is skipped.
3. **Per-agent memory pressure** — only agents whose undistilled findings no
   longer fit half a context read are consolidated (the MemGPT-style trigger).

So even when consolidation *fires* on a schedule, it only *does work* for the
agents that both opted in and actually accumulated enough new memory to warrant
it. See [Layered agent memory](agent-memory.md) for what distillation produces.

### 2. Forget by disuse — LRU decay on retrieval, not just age

Distillation's decay is no longer driven by creation age alone. Every read
surface records access against the findings it returns — `last_accessed_at` and
an `access_count` — into a **`node_memory` sidecar**, never into `graph.json`.
Before the distillation pass computes decay, it merges that live access state
into its working view, so a finding that has sat unretrieved since it was
minted decays and becomes eligible to be absorbed or demoted, while one that
was read recently is kept fresh regardless of its age. This is **retrieval
recency**, the LRU (least-recently-used) intuition applied to memory:
knowledge you keep actually pulling stays; knowledge nothing ever asks for
fades first. An empty sidecar reproduces the old age-only behavior exactly, so
it is fully backward-compatible.

### 3. Associate — discover new connections

The final operation looks for *new* relationships between what survived. It
embeds distilled notes and links pairs whose meanings are close —
**embedding-gated**, so it only runs when a real embedding backend is
configured (the hash stub is skipped, never producing noise links). Discovery
runs intra-project and **cross-agent**, and the connections it finds are minted
as `shares_concept_with` edges carrying a `federation_semantic` marker.

Crucially, these discovered edges are written to a **sidecar overlay** under
`.tesserae`, *never* into `graph.json`. The overlay **accumulates across
cycles** — each association pass dedups against and extends what earlier passes
found. At read time (query, PPR expansion, federation views) the overlay is
merged into the graph **in memory only**, exactly like the per-agent view
overlay — so the byte-deterministic `graph.json` is never touched. The whole
operation is wrapped and never raises into the daemon loop.

### 4. Summarize — pre-warm the community caches agents descend into

`graph_map` serves a card per scope. A scope whose summary cache is cold gets a
deterministic *structural* card — a member count and a list of top members —
and the first agent to visit it pays a synchronous LLM call to get prose. This
operation moves that cost off the read path: within a per-tick budget
(`--summarize-budget`, default 25; `0` disables it) it materializes summaries
for the scopes most likely to be visited next, so the visit finds a warm cache.

Candidates are ranked by **demand** — the scope's own access bumps from
`graph_map` traversal plus the access counts of its members — then by size,
degree and level, in a total order, so two ticks over identical state pick the
same scopes. A cache that is already warm and still digest-valid costs no
budget; only a cold materialization does. Without an LLM client the whole
operation is a no-op.

### 5. Brief — pre-warm the charter's domain briefs

The same shape, one axis over: the candidates are the live domains of
[the charter](../README.md) rather than the dendrogram's communities. A cold
domain renders as a structural card everywhere it appears — in `graph_map`, in
`charter_route`'s scoring corpus, and in lint's `CHARTER_FALLBACK` census — so
this pass is what gives the chartered institution prose at all.

The budget is its own knob (`--brief-budget`, default 8; `0` disables it),
deliberately separate from `--summarize-budget` so neither op can starve the
other, and deliberately smaller: the charter's **divisions** are what
`graph_map` serves as its root card set, and there are only a handful of them,
so 8 warms the entry point in the first idle tick and deeper tiers follow at 8
per tick behind it.

The order is **breadth-first**, not a demand rank. A domain's member set
contains its whole subtree, so a parent's demand always dominates its
children's and no domain is warmed before its ancestors. That is deliberate:
agents descend from the root, so the coarse card is the one read first and the
one worth having prose for. Access counts order domains where neither contains
the other, and the live **divisions** — domains with no live parent, the same
rule `graph_map`'s root uses, not `tier == 1` — sort ahead of everything else.

Some domains never cost a budget slot, because a slot is meant to be an LLM
call: retired domains, the `intake` census (which has no subject, so a brief
written from 25 of its thousands of members would be a confident description
of a fraction of a percent), a domain whose members have left the graph, and
anything already warm. And a domain whose materialization **fails** — most
often because its prose cited none of its children and was rejected — is held
off for a doubling number of ticks rather than retried at the same rank
forever, so a permanently unwarmable domain cannot hold a slot that a warmable
one could use.

### What this costs per hour

Both budgets are per **tick**, and a tick fires at most once per
`--consolidate-idle` window. At the defaults:

| | per tick | ticks/hour at `--consolidate-idle 300` | ceiling |
|---|---|---|---|
| Summarize | 25 | 12 | 300 LLM calls/hour |
| Brief | 8 | 12 | 96 LLM calls/hour |
| **Total** | **33** | **12** | **396 LLM calls/hour** |

That is a **ceiling reached only while caches are cold**, and it decays to
**zero**: a warm, digest-valid cache costs no call and no slot, so once a
project's scopes and domains are summarized the sleep cycle spends nothing
until the graph changes. Set either budget to `0` to switch its op off, or
raise `--consolidate-idle` to make ticks rarer.

**A budget is a ceiling, not a quota.** Both budgets are spent *sequentially*
inside one tick, and the tick holds the compile gate for the whole pass — so at
the defaults a tick could occupy the gate across 33 consecutive LLM calls. A
file save landing mid-tick had to wait out every remaining call before its
pipeline run could start, which with a CLI provider is minutes. Both pre-warm
loops now check, at the top of each iteration, whether a pipeline run is
blocked on the gate, and **abandon their remaining budget** if one is:

- the check happens *between* calls, never mid-call, so the run already in
  flight always finishes and the pipeline waits out at most that one call;
- stopping early is lossless. Warming is idempotent, so a scope or domain the
  tick never reached is simply still cold on the next one, at the same rank —
  nothing is lost, corrupted, or paid for twice;
- an abandoned domain takes **no back-off strike**. Strikes are for a domain
  whose warm attempt burned a call and failed; an abandoned one was never
  tried, so charging it would push a warmable domain down the queue because an
  unrelated file was saved;
- it is reported, not silent. The tick's summary dict gains `abandoned` and
  `unspent` (how many budget slots went unused), so the daemon log distinguishes
  "stood down for a pipeline" from "there was nothing to warm".

**Why here and not in compile.** A brief costs one LLM call. Minting them
during compile would put one call per domain on every compile, and compile is
the path this project keeps deterministic and cheap. Minting them lazily on
read would mean a `graph_map` call could block on a model. The idle sleep cycle
is the only place left that can spend a call nobody is waiting on.

## Safety and determinism

- **Runs under the compile gate, for the whole pass.** Consolidation acquires
  the same lock a recompile does, so it serializes with compiles and **never
  overlaps one**. A pending compile waits for an in-flight consolidation and
  vice versa — the graph is never read mid-write. The gate is deliberately
  **not** released between LLM calls: every op in a tick reads the one
  `graph.json` the tick loaded, so handing the gate back mid-pass would let a
  compile rewrite the graph underneath and leave the briefs written early in a
  pass describing a different graph than the ones written late. That is why a
  tick waiting on a pipeline **abandons its remaining budget** rather than
  yielding the gate — it trades speculative warming for latency, never
  consistency for latency.
- **Never raises into the daemon loop.** The whole pass is wrapped; any error
  is logged and the thread keeps looping. A failed consolidation never takes
  the engine down.
- **No-op when the gate is off.** With `TESSERAE_AGENT_DISTILL` unset the pass
  loads nothing expensive and returns immediately, so leaving the sleep cycle
  on costs effectively nothing.
- **Deterministic artifacts, unchanged.** Distilled artifacts remain
  deterministic given their inputs; the sleep cycle only changes *when*
  distillation runs, never *what* it produces. Idle timing never leaks into
  `graph.json` or any distilled layer.
- **`graph.json` stays byte-idempotent.** No operation here writes it. Access
  state lives in the `node_memory` sidecar, discovered connections in an
  accumulating overlay, and both summaries and domain briefs in the
  `community_summaries` cache — all under `.tesserae`, all merged in memory
  only at read time. The authoritative graph bytes are untouched by retrieval
  history, discovered links or pre-warmed prose. Summaries and briefs are
  **caches, not knowledge**: deleting the cache directory costs the next reader
  a structural card and nothing else.
- **Clean shutdown.** The consolidation thread observes the daemon's stop
  event and exits promptly on `Ctrl-C` / shutdown. It is a long-running-mode
  feature only: `tesserae engine ... --once` never starts it.

## CLI flags

| Flag | Default | Effect |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Enable or disable the sleep cycle entirely. On by default (a no-op unless the distill gate is set). |
| `--consolidate-idle SECONDS` | `300` | Rest window: consolidate after this many seconds with no activity. |
| `--consolidate-every SECONDS` | `21600` | Ceiling: consolidate at least this often regardless of activity. `0` disables the ceiling. |
| `--consolidate-check SECONDS` | `30` | How often the consolidation thread wakes to re-evaluate the triggers. |
| `--summarize-budget N` | `25` | Max LLM calls per tick spent pre-warming community summaries. `0` disables the SUMMARIZE op. |
| `--brief-budget N` | `8` | Max LLM calls per tick spent pre-warming charter domain briefs. `0` disables the BRIEF op. |

## Fleet behavior (`--all`)

`tesserae engine --all` keeps every registered project fresh in one process.
Each project's unit gets its own consolidation thread with the same knobs, and
all units share one fleet-wide compile gate — so a consolidation in one project
serializes against compiles across the whole fleet, never overlapping any of
them.

## Worked example

Turn distillation on, then run the engine with a snappier sleep cycle for a
demo — consolidate after 60s idle, and at least every 30 min no matter what:

```bash
export TESSERAE_AGENT_DISTILL=1
tesserae engine \
  --consolidate-idle 60 \
  --consolidate-every 1800 \
  --consolidate-check 15
```

Work in your editor and agents as usual; the engine watches, debounces, and
recompiles on every change. Stop for a minute and the idle trigger fires: the
consolidation thread acquires the compile gate and distills any agent under
memory pressure — reorganizing, compacting, and safely forgetting — then goes
back to sleep. Keep working past the half-hour mark without ever pausing and
the ceiling fires anyway, so a relentless project still consolidates.

To keep the engine running but leave consolidation to manual `tesserae distill`
runs, pass `--no-consolidate`. To let it run on idle but never on a fixed
schedule, pass `--consolidate-every 0`.
