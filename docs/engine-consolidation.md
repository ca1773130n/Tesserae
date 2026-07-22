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

## What it runs — three operations

Each fire loads the compiled graph from `.tesserae/graph.json` (if the file is
absent, the pass is skipped) and runs three consolidation operations, in order.
Together they mirror what a resting brain does: compact the loud recent stuff,
let the never-revisited stuff fade, and wire up new associations between what
survives.

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

## Safety and determinism

- **Runs under the compile gate.** Consolidation acquires the same lock a
  recompile does, so it serializes with compiles and **never overlaps one**. A
  pending compile waits for an in-flight consolidation and vice versa — the
  graph is never read mid-write.
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
- **`graph.json` stays byte-idempotent.** Neither new operation writes it.
  Access state lives in the `node_memory` sidecar and discovered connections
  in an accumulating overlay — both under `.tesserae`, both merged in memory
  only at read time. The authoritative graph bytes are untouched by
  retrieval history or discovered links.
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
