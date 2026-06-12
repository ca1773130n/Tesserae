# Global Engine (Fleet Daemon) — Design

**Status:** proposed · **Date:** 2026-06-12 · **Author:** session w/ Claude
**Implements mission pillar 2** (autonomous, proactive knowledge ingestion) at
machine scope instead of project scope.

## Problem

`tesserae engine` today is **one process per project**: the `Daemon`
(`tesserae/engine/daemon.py`) owns one asyncio loop, a per-project pidfile
(`<project>/.tesserae/daemon.pid`), and poller threads (source `WatchLoop`,
`VaultWatcher`, `SessionTailer`) that are all constructed around a single
`project_root`. A user with N registered projects must run — and remember to
run — N processes. Costs:

- **Operational**: N processes to start/stop/monitor; forgetting one means that
  project's knowledge base silently goes stale.
- **I/O**: each `SessionTailer` separately enumerates and polls the harness
  transcript roots (`~/.claude*`, `~/.codex*`). The work is slug-scoped and
  bounded per project, but it is repeated N times for the same directories.
- **LLM contention**: two compiles extracting session findings concurrently
  share the same CLI-agent accounts and rate limits. Independent processes
  cannot coordinate; we have observed timeout cascades when compiles overlap
  (see v0.7.2 release notes — the wedge that motivated the per-project compile
  lock).

## Goal

One `tesserae engine --all` process that keeps **every project registered in
`~/.tesserae/registry.json`** fresh, with a global cap on concurrent compiles.
Registering/unregistering a project takes effect without restarting the engine.

## Non-goals (this iteration)

- **Shared transcript watcher.** A single watcher routing new session lines to
  projects by cwd would deduplicate the polling I/O entirely, but it means
  rewriting `SessionTailer`'s project-scoped discovery, offsets store, and
  negative-match re-peek logic for multi-tenancy. The per-project tailer is
  already bounded (slug-scoped enumeration, mtime-floored Codex discovery), so
  N tailer threads in one process is acceptable. Revisit when N or transcript
  volume makes it measurable. (Future work §below.)
- Cross-project scheduling priorities, web UI, remote control. YAGNI.
- Windows support beyond what `Daemon` already has.

## Relationship to the MCP server

Unchanged and complementary: the **engine is the write side** (keeps graphs
fresh on its own schedule), the **MCP server is the read side** (serves
compiled artifacts on request). Both now consume the same registry
(`ProjectRegistry`, currently defined in `tesserae/mcp_server.py`):
the MCP server to *resolve* projects, the fleet to *enumerate* them.

## Design

### Architecture: composition, not rewrite

A new `FleetDaemon` (`tesserae/engine/fleet.py`) supervises one existing
`Daemon` **per registered project, each in its own thread**. Every `Daemon`
already creates its own event loop inside `run()` and tolerates running in a
non-main thread (its `add_signal_handler` falls back gracefully); the fleet
owns process-level concerns:

```
FleetDaemon (main thread)
├── global pidfile  ~/.tesserae/engine.pid     (stale-detect, same recipe as Daemon)
├── SIGTERM/SIGINT  → request_stop() fan-out
├── registry poll   ~/.tesserae/registry.json  (reconcile every registry_poll s)
├── compile gate    threading.Semaphore(compile_slots)   shared by all units
└── units: { name → (Daemon thread) }
      ├── project-A Daemon  (own loop, own pollers, own per-project pidfile)
      ├── project-B Daemon
      └── ...
```

Why composition wins over a single-loop rewrite:

- The `Daemon` drain loop's coalescing/debounce/shutdown invariants are subtle
  and battle-tested (`test_daemon_core.py`). Sharding its queue by project
  would re-open all of them for marginal benefit.
- Per-unit failure isolation falls out naturally: a unit thread that dies logs
  loudly and is restarted on the next reconcile tick; the fleet survives.
- The per-project pidfile keeps protecting against double ownership: if a
  standalone `tesserae engine --project X` is already running, the fleet's
  unit for X fails its pidfile check (`RuntimeError: Daemon already running`),
  logs, and stands down — exactly the right behavior.

### Daemon changes (small, additive)

1. `install_signal_handlers: bool = True` — the fleet passes `False`; unit
   loops must not race the fleet for process signals (and non-main threads
   can't install them anyway — this silences the warning path).
2. `request_stop()` — public, thread-safe stop (sets the existing
   `_stop_event`; the drain loop notices within `queue_timeout`). The fleet
   fans this out on shutdown and joins unit threads.
3. `compile_gate: Optional[threading.Semaphore] = None` — when set,
   `_run_pipeline` runs inside `with gate:`. With `compile_slots=1` (default)
   fleet-wide compiles serialize, which both respects shared LLM account rate
   limits and composes with the per-project `compile_lock` from v0.7.2 (the
   flock guards against *external* compiles; the semaphore schedules
   *internal* ones without burning lock-contention errors).

### Reconciliation (registry hot-reload)

`reconcile()` diffs desired state (registry entries whose `root/.tesserae`
exists) against running units:

- new entry → construct unit `Daemon(root, install_signal_handlers=False,
  compile_gate=gate)` and start its thread
- removed entry → `request_stop()` + join
- dead thread (unit crashed) → drop it; next tick restarts it (simple,
  self-healing; no backoff in v1 — a crash-looping unit logs every
  `registry_poll` seconds, which is visible and cheap)

The fleet polls the registry every `registry_poll` seconds (default 10).
Polling, not file-watching: the registry changes rarely and a 10 s lag is
imperceptible; mtime-watching saves nothing measurable.

### CLI

`tesserae engine --all [--compile-slots N] [--once]` — `--all` and `--project`
are mutually exclusive; `--project` keeps the existing single-project behavior
byte-for-byte. `--once` in fleet mode runs each unit's `run(once=True)`
sequentially (deterministic, CI-friendly, same contract as today).

### `once` / test seams

- `daemon_factory(name, root) -> Daemon` injection lets tests substitute units
  whose `run_pipeline` is a recording stub — no real project, no compile.
- `reconcile()` is public and synchronous — tests drive it directly instead of
  sleeping through poll ticks.

## Failure modes considered

| Failure | Behavior |
|---|---|
| Unit raises in its thread | Logged; thread exits; next reconcile restarts it |
| Project deleted on disk but still registered | Skipped by the `.tesserae` existence check at reconcile time |
| Standalone engine already owns a project | Unit pidfile check raises; unit stands down; fleet continues |
| Fleet killed (SIGKILL) | Global + unit pidfiles go stale; next start detects via `os.kill(pid, 0)` and overwrites |
| Corrupt registry JSON | `ProjectRegistry.load` raises `ValueError`; reconcile logs and keeps the previous unit set (engine keeps running) |
| Two compiles contending for LLM accounts | Serialized by the shared semaphore (`--compile-slots`, default 1) |

## Future work

- **Shared transcript watcher** (stage 2): one poller over harness roots
  routing new lines by cwd → per-project queues; removes the last N×
  duplication. Requires multi-tenant offsets in `HarnessSessionsDB`.
- Move `ProjectRegistry` out of `mcp_server.py` into a neutral module
  (`tesserae/registry.py`) once a third consumer appears.
- `tesserae engine status` showing per-unit health from the fleet pidfile +
  unit pidfiles.

## Implementation plan

See `docs/superpowers/plans/2026-06-12-global-engine.md`.
