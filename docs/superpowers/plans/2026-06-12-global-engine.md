# Global Engine (Fleet Daemon) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `tesserae engine --all` process that keeps every project in `~/.tesserae/registry.json` fresh, with registry hot-reload and a global cap on concurrent compiles.

**Architecture:** A new `FleetDaemon` (`tesserae/engine/fleet.py`) supervises one existing per-project `Daemon` per registered project, each in its own thread. The fleet owns the process-level concerns (global pidfile, signals, registry reconciliation, shared compile semaphore); the `Daemon` gains three small additive seams (`install_signal_handlers`, `request_stop()`, `compile_gate`). Design doc: `docs/superpowers/specs/2026-06-12-global-engine-design.md`.

**Tech Stack:** Python stdlib only (`threading`, `signal`, `os`, `json`, `pathlib`), pytest. No new dependencies.

---

### Task 1: `Daemon.request_stop()` + `install_signal_handlers` seam

**Files:**
- Modify: `tesserae/engine/daemon.py` (`__init__` ~line 62, `run()` ~line 115)
- Test: `tests/test_daemon_core.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_daemon_core.py`:

```python
def test_request_stop_ends_run_loop_from_another_thread(tmp_path):
    """The fleet supervisor stops units via request_stop(); the drain loop
    must notice within ~queue_timeout and run() must return 0."""
    import threading
    import time

    from tesserae.engine.daemon import Daemon

    (tmp_path / ".tesserae").mkdir(parents=True)
    daemon = Daemon(
        tmp_path,
        queue_timeout=0.05,
        enable_watch=False,
        enable_vault=False,
        enable_session_tail=False,
        install_signal_handlers=False,
        run_pipeline=lambda paths: None,
    )
    rc_box = {}
    thread = threading.Thread(target=lambda: rc_box.setdefault("rc", daemon.run()))
    thread.start()
    time.sleep(0.3)  # let the loop start
    daemon.request_stop()
    thread.join(timeout=5)
    assert not thread.is_alive(), "drain loop did not stop after request_stop()"
    assert rc_box["rc"] == 0
    assert not (tmp_path / ".tesserae" / "daemon.pid").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_daemon_core.py::test_request_stop_ends_run_loop_from_another_thread -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'install_signal_handlers'`

- [ ] **Step 3: Implement**

In `tesserae/engine/daemon.py`:

(a) Add the parameter to `__init__` (after `enable_session_tail: bool = True,`):

```python
        install_signal_handlers: bool = True,
```

and in the body (after `self._enable_session_tail = enable_session_tail`):

```python
        self._install_signal_handlers = install_signal_handlers
```

(b) In `run()`, gate the existing signal-handler block:

```python
                if self._install_signal_handlers:
                    for sig in (signal.SIGTERM, signal.SIGINT):
                        try:
                            loop.add_signal_handler(sig, self._handle_signal)
                        except NotImplementedError:
                            # Windows / non-main-thread: signals unavailable here.
                            logger.warning("add_signal_handler unavailable for %s; skipping", sig)
```

(c) Add the public stop method next to `_handle_signal`:

```python
    def request_stop(self) -> None:
        """Thread-safe external stop (fleet supervisor / tests).

        Same effect as a SIGTERM: the drain loop notices the stop event within
        ``queue_timeout`` and exits via the graceful-drain path.
        """
        self._stop_event.set()
```

- [ ] **Step 4: Run the test + the daemon suite**

Run: `.venv/bin/python -m pytest tests/test_daemon_core.py -v`
Expected: all PASS (new test included; existing tests unaffected because the new parameter defaults to today's behavior)

- [ ] **Step 5: Commit**

```bash
git add tesserae/engine/daemon.py tests/test_daemon_core.py
git commit -m "feat(engine): Daemon.request_stop() + install_signal_handlers seam"
```

---

### Task 2: shared compile gate in `Daemon._run_pipeline`

**Files:**
- Modify: `tesserae/engine/daemon.py` (`__init__`, `_run_pipeline`)
- Test: `tests/test_daemon_core.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_daemon_core.py`:

```python
def test_compile_gate_serializes_pipeline_runs(tmp_path):
    """Two daemons sharing one Semaphore(1) must never run pipelines
    concurrently — the fleet uses this to respect shared LLM rate limits."""
    import threading
    import time

    from tesserae.engine.daemon import Daemon

    gate = threading.Semaphore(1)
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def slow_pipeline(paths):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.2)
        with lock:
            state["active"] -= 1

    daemons = []
    for name in ("a", "b"):
        root = tmp_path / name
        (root / ".tesserae").mkdir(parents=True)
        daemons.append(
            Daemon(
                root,
                enable_watch=False,
                enable_vault=False,
                enable_session_tail=False,
                install_signal_handlers=False,
                compile_gate=gate,
                run_pipeline=slow_pipeline,
            )
        )
    threads = [threading.Thread(target=lambda d=d: d.run(once=True)) for d in daemons]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert all(not t.is_alive() for t in threads)
    assert state["max_active"] == 1, f"pipelines overlapped: {state['max_active']}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_daemon_core.py::test_compile_gate_serializes_pipeline_runs -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'compile_gate'`

- [ ] **Step 3: Implement**

In `tesserae/engine/daemon.py`:

(a) `__init__` parameter (after `install_signal_handlers: bool = True,`):

```python
        compile_gate: Optional[threading.Semaphore] = None,
```

body (after `self._install_signal_handlers = ...`):

```python
        self._compile_gate = compile_gate
```

(b) Wrap `_run_pipeline` — the gate must cover the override too (that is what
the test exercises, and the fleet relies on it regardless of pipeline kind).
Replace the method body's first lines:

```python
    def _run_pipeline(self, paths: List[Path]) -> None:
        from contextlib import nullcontext

        gate = self._compile_gate if self._compile_gate is not None else nullcontext()
        with gate:
            if self._run_pipeline_override is not None:
                self._run_pipeline_override(paths)
                return
            from .pipeline import Pipeline
            from ..project import ProjectWiki

            wiki = ProjectWiki.load(self.project_root)
            steps = [
                (
                    "compile",
                    lambda: wiki.compile(
                        changed_only=bool(paths), changed_paths=paths or None
                    ),
                )
            ]
            try:
                results = Pipeline(steps).run()
            except Exception as exc:  # noqa: BLE001 - daemon must survive
                logger.error("pipeline raised outside StepResult (daemon survives): %s", exc)
                return
            for r in results:
                if r.ok:
                    logger.info("step %s: ok", r.name)
                else:
                    logger.error("step %s: FAILED: %s", r.name, r.error)
```

(Keep the existing comments about CMP-04 changed-path forwarding when editing —
only the indentation level and the `with gate:` wrapper are new.)

- [ ] **Step 4: Run the daemon suite**

Run: `.venv/bin/python -m pytest tests/test_daemon_core.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tesserae/engine/daemon.py tests/test_daemon_core.py
git commit -m "feat(engine): optional shared compile_gate semaphore in Daemon"
```

---

### Task 3: `FleetDaemon` — registry → units, `run(once=True)`

**Files:**
- Create: `tesserae/engine/fleet.py`
- Create: `tests/test_engine_fleet.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine_fleet.py`:

```python
"""FleetDaemon: one process supervising a per-project Daemon per registry entry."""
from __future__ import annotations

import json
from pathlib import Path

from tesserae.engine.fleet import FleetDaemon


def _write_registry(path: Path, projects: dict[str, Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "active": None,
                "projects": {
                    name: {"root": str(root), "graph_path": str(root / ".tesserae" / "graph.json")}
                    for name, root in projects.items()
                },
            }
        ),
        encoding="utf-8",
    )


def _make_project(base: Path, name: str) -> Path:
    root = base / name
    (root / ".tesserae").mkdir(parents=True)
    return root


def test_fleet_once_runs_one_pipeline_per_registered_project(tmp_path):
    registry = tmp_path / "registry.json"
    roots = {name: _make_project(tmp_path, name) for name in ("alpha", "beta")}
    _write_registry(registry, roots)

    ran: list[str] = []

    def factory(name, root, fleet):
        from tesserae.engine.daemon import Daemon

        return Daemon(
            root,
            enable_watch=False,
            enable_vault=False,
            enable_session_tail=False,
            install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
            run_pipeline=lambda paths, name=name: ran.append(name),
        )

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=factory,
    )
    rc = fleet.run(once=True)

    assert rc == 0
    assert sorted(ran) == ["alpha", "beta"]
    assert not (tmp_path / "engine.pid").exists()  # released on exit


def test_fleet_skips_registered_projects_missing_on_disk(tmp_path):
    registry = tmp_path / "registry.json"
    alive = _make_project(tmp_path, "alive")
    ghost = tmp_path / "ghost"  # registered but no .tesserae on disk
    _write_registry(registry, {"alive": alive, "ghost": ghost})

    ran: list[str] = []

    def factory(name, root, fleet):
        from tesserae.engine.daemon import Daemon

        return Daemon(
            root,
            enable_watch=False,
            enable_vault=False,
            enable_session_tail=False,
            install_signal_handlers=False,
            run_pipeline=lambda paths, name=name: ran.append(name),
        )

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=factory,
    )
    assert fleet.run(once=True) == 0
    assert ran == ["alive"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_fleet.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesserae.engine.fleet'`

- [ ] **Step 3: Implement `tesserae/engine/fleet.py`**

```python
"""Fleet supervisor: one process keeping every registered project fresh.

Supervises one per-project :class:`~tesserae.engine.daemon.Daemon` per entry in
``~/.tesserae/registry.json``, each in its own thread. The fleet owns the
process-level concerns — global pidfile, SIGTERM/SIGINT, registry
reconciliation, and a shared compile semaphore so concurrent units don't fight
over the same LLM accounts. Design:
docs/superpowers/specs/2026-06-12-global-engine-design.md
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from .daemon import Daemon

logger = logging.getLogger("tesserae.fleet")

DEFAULT_FLEET_PIDFILE = Path.home() / ".tesserae" / "engine.pid"

# (name, project_root, fleet) -> Daemon. The fleet passes itself so factories
# can reach the shared compile_gate; tests substitute recording daemons.
DaemonFactory = Callable[[str, Path, "FleetDaemon"], Daemon]


@dataclass
class _Unit:
    name: str
    root: Path
    daemon: Daemon
    thread: Optional[threading.Thread] = None


class FleetDaemon:
    """One process, every registered project. See module docstring."""

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        *,
        compile_slots: int = 1,
        registry_poll: float = 10.0,
        pidfile: Optional[Path] = None,
        daemon_factory: Optional[DaemonFactory] = None,
    ) -> None:
        from ..mcp_server import ProjectRegistry

        self._registry = ProjectRegistry(registry_path)
        self.compile_gate = threading.Semaphore(max(1, int(compile_slots)))
        self._registry_poll = registry_poll
        self._pidfile = Path(pidfile) if pidfile is not None else DEFAULT_FLEET_PIDFILE
        self._daemon_factory = daemon_factory or self._default_daemon_factory
        self._units: Dict[str, _Unit] = {}
        self._stop = threading.Event()

    # ----- unit construction ------------------------------------------------

    def _default_daemon_factory(self, name: str, root: Path, fleet: "FleetDaemon") -> Daemon:
        return Daemon(
            root,
            install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
        )

    def _desired_projects(self) -> Dict[str, Path]:
        """Registry entries that exist on disk, name -> resolved root."""
        try:
            data = self._registry.load()
        except ValueError as exc:
            # Corrupt registry: keep the current unit set running rather than
            # tearing the fleet down over a transient bad write.
            logger.error("registry unreadable (%s); keeping current units", exc)
            return {u.name: u.root for u in self._units.values()}
        desired: Dict[str, Path] = {}
        for name, entry in (data.get("projects") or {}).items():
            root = Path(str(entry.get("root") or "")).expanduser()
            if (root / ".tesserae").is_dir():
                desired[name] = root.resolve()
            else:
                logger.warning("registered project %s has no .tesserae at %s; skipping", name, root)
        return desired

    # ----- reconciliation ---------------------------------------------------

    def reconcile(self) -> None:
        """Diff registry against running units; start/stop to converge.

        Public + synchronous so tests drive it directly without poll sleeps.
        """
        desired = self._desired_projects()
        for name in list(self._units):
            unit = self._units[name]
            dead = unit.thread is not None and not unit.thread.is_alive()
            if name not in desired or dead:
                if dead and name in desired:
                    logger.warning("unit %s thread died; will restart", name)
                self._stop_unit(name)
        for name, root in sorted(desired.items()):
            if name not in self._units:
                self._start_unit(name, root)

    def _start_unit(self, name: str, root: Path) -> None:
        daemon = self._daemon_factory(name, root, self)

        def _run() -> None:
            try:
                daemon.run()
            except Exception as exc:  # noqa: BLE001 — unit dies loudly, fleet survives
                logger.error("unit %s exited with error: %s", name, exc)

        thread = threading.Thread(target=_run, name=f"tesserae-unit-{name}", daemon=True)
        self._units[name] = _Unit(name=name, root=root, daemon=daemon, thread=thread)
        thread.start()
        logger.info("unit %s started (%s)", name, root)

    def _stop_unit(self, name: str) -> None:
        unit = self._units.pop(name, None)
        if unit is None:
            return
        unit.daemon.request_stop()
        if unit.thread is not None:
            unit.thread.join(timeout=10)
        logger.info("unit %s stopped", name)

    # ----- lifecycle ----------------------------------------------------------

    def request_stop(self) -> None:
        """Thread-safe external stop (signals route here too)."""
        self._stop.set()

    def run(self, *, once: bool = False) -> int:
        self._write_pidfile()
        try:
            if once:
                # Deterministic CI mode: one bounded once-run per project,
                # sequential, no threads, no signals.
                for name, root in sorted(self._desired_projects().items()):
                    daemon = self._daemon_factory(name, root, self)
                    rc = daemon.run(once=True)
                    logger.info("unit %s once-run rc=%d", name, rc)
                return 0
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    signal.signal(sig, lambda *_: self.request_stop())
                except ValueError:
                    # Not the main thread (tests): caller uses request_stop().
                    logger.warning("signal handlers unavailable off the main thread")
                    break
            self.reconcile()
            while not self._stop.is_set():
                self._stop.wait(self._registry_poll)
                if not self._stop.is_set():
                    self.reconcile()
            for name in list(self._units):
                self._stop_unit(name)
            return 0
        finally:
            self._remove_pidfile()

    # ----- pidfile (same stale-detection recipe as Daemon, fleet-scoped) -----

    def _write_pidfile(self) -> None:
        if self._pidfile.exists():
            try:
                old_pid = int(self._pidfile.read_text().strip())
            except (ValueError, OSError):
                old_pid = None
            if old_pid is not None:
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    logger.warning("stale fleet pidfile (pid %d gone); overwriting", old_pid)
                except PermissionError:
                    raise RuntimeError(f"fleet engine already running (pid {old_pid})")
                else:
                    raise RuntimeError(f"fleet engine already running (pid {old_pid})")
        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        self._pidfile.write_text(str(os.getpid()))

    def _remove_pidfile(self) -> None:
        try:
            self._pidfile.unlink()
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_engine_fleet.py -v`
Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add tesserae/engine/fleet.py tests/test_engine_fleet.py
git commit -m "feat(engine): FleetDaemon — registry-driven multi-project supervisor (once mode)"
```

---

### Task 4: long-running mode — reconcile add/remove, stop fan-out

**Files:**
- Modify: `tesserae/engine/fleet.py` (no changes expected — this task *verifies* the long-running paths written in Task 3)
- Test: `tests/test_engine_fleet.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine_fleet.py`:

```python
import threading
import time


def _recording_factory(ran_units: dict):
    """Factory building real Daemons with stub pipelines that record liveness."""

    def factory(name, root, fleet):
        from tesserae.engine.daemon import Daemon

        ran_units[name] = ran_units.get(name, 0) + 1
        return Daemon(
            root,
            queue_timeout=0.05,
            enable_watch=False,
            enable_vault=False,
            enable_session_tail=False,
            install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
            run_pipeline=lambda paths: None,
        )

    return factory


def test_reconcile_starts_new_and_stops_removed_units(tmp_path):
    registry = tmp_path / "registry.json"
    alpha = _make_project(tmp_path, "alpha")
    _write_registry(registry, {"alpha": alpha})

    built: dict = {}
    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=_recording_factory(built),
    )
    fleet.reconcile()
    assert set(fleet._units) == {"alpha"}
    assert fleet._units["alpha"].thread.is_alive()

    # Register beta, drop alpha → reconcile converges.
    beta = _make_project(tmp_path, "beta")
    _write_registry(registry, {"beta": beta})
    fleet.reconcile()
    assert set(fleet._units) == {"beta"}

    # Cleanup: stop everything.
    for name in list(fleet._units):
        fleet._stop_unit(name)
    assert fleet._units == {}


def test_run_loop_stops_all_units_on_request_stop(tmp_path):
    registry = tmp_path / "registry.json"
    alpha = _make_project(tmp_path, "alpha")
    _write_registry(registry, {"alpha": alpha})

    built: dict = {}
    fleet = FleetDaemon(
        registry_path=registry,
        registry_poll=0.1,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=_recording_factory(built),
    )
    rc_box = {}
    runner = threading.Thread(target=lambda: rc_box.setdefault("rc", fleet.run()))
    runner.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "alpha" not in fleet._units:
        time.sleep(0.05)
    assert "alpha" in fleet._units

    fleet.request_stop()
    runner.join(timeout=10)
    assert not runner.is_alive()
    assert rc_box["rc"] == 0
    assert fleet._units == {}
    assert not (tmp_path / "engine.pid").exists()
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/test_engine_fleet.py -v`
Expected: all PASS if Task 3's implementation is complete. If `test_run_loop_stops_all_units_on_request_stop` fails on the signal block, the `except ValueError` guard (non-main thread) in `run()` is missing — add it as written in Task 3.

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine_fleet.py
git commit -m "test(engine): fleet reconcile add/remove + stop fan-out coverage"
```

---

### Task 5: CLI — `tesserae engine --all [--compile-slots N]`

**Files:**
- Modify: `tesserae/cli.py` (`_build_engine_parser`, `_handle_engine` ~line 1661)
- Test: `tests/test_cli_engine.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_engine.py` (mirror the existing single-project CLI
test style in that file — it already exercises `--once` with a temp project):

```python
def test_engine_all_once_runs_fleet_over_registry(tmp_path, monkeypatch, capsys):
    import json

    from tesserae.cli import main
    from tesserae.project import ProjectWiki

    # Two real (empty) projects + a registry pointing at them.
    roots = {}
    for name in ("alpha", "beta"):
        root = tmp_path / name
        root.mkdir()
        ProjectWiki.init(root, name=name)
        roots[name] = root
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "active": None,
                "projects": {
                    n: {"root": str(r), "graph_path": str(r / ".tesserae" / "graph.json")}
                    for n, r in roots.items()
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TESSERAE_REGISTRY", str(registry))
    monkeypatch.setenv("TESSERAE_FLEET_PIDFILE", str(tmp_path / "engine.pid"))

    rc = main(["engine", "--all", "--once"])

    assert rc == 0
    # Once-mode compiled each project: both graphs exist afterwards.
    for root in roots.values():
        assert (root / ".tesserae" / "graph.json").exists()


def test_engine_all_and_project_are_mutually_exclusive(capsys):
    import pytest

    from tesserae.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["engine", "--all", "--project", "/tmp/x"])
    assert exc.value.code == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_engine.py -v`
Expected: the two new tests FAIL (`--all` unrecognized); existing tests PASS.

- [ ] **Step 3: Implement**

In `tesserae/cli.py`:

(a) In `_build_engine_parser()` add (next to the existing `--project` argument —
keep `--project`'s default so plain `tesserae engine` still means "this
project"):

```python
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fleet mode: run every project in ~/.tesserae/registry.json from one process.",
    )
    parser.add_argument(
        "--compile-slots",
        type=int,
        default=1,
        help="Fleet mode: max concurrent compiles across all projects (default 1).",
    )
```

(b) In `_handle_engine` (line ~1661), branch before constructing the
single-project `Daemon`:

```python
def _handle_engine(args: argparse.Namespace) -> int:
    """Start the supervised refresh daemon (alias: ``daemon``).

    ``--all`` switches to fleet mode: one process supervising every project in
    the registry (see docs/superpowers/specs/2026-06-12-global-engine-design.md).
    """
    import logging
    import os

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    if getattr(args, "all", False):
        if args.project != ".":  # user passed an explicit --project alongside --all
            print("tesserae engine: --all and --project are mutually exclusive", file=sys.stderr)
            raise SystemExit(2)
        from .engine.fleet import FleetDaemon

        registry_env = os.environ.get("TESSERAE_REGISTRY")
        pidfile_env = os.environ.get("TESSERAE_FLEET_PIDFILE")
        fleet = FleetDaemon(
            registry_path=Path(registry_env) if registry_env else None,
            compile_slots=args.compile_slots,
            pidfile=Path(pidfile_env) if pidfile_env else None,
        )
        return fleet.run(once=args.once)
    from .engine.daemon import Daemon

    daemon = Daemon(
        Path(args.project).resolve(),
        debounce=args.debounce,
        watch_interval=args.interval,
    )
    return daemon.run(once=args.once)
```

NOTE (verified 2026-06-12): `_build_engine_parser` declares
`parser.add_argument("--project", default=".")`, so the `args.project != "."`
mutual-exclusion check above is correct against the current parser.

(c) The `TESSERAE_REGISTRY` / `TESSERAE_FLEET_PIDFILE` env overrides exist for
test hermeticity (the CLI test above) and ops flexibility; document them in the
parser epilog.

- [ ] **Step 4: Run the CLI engine tests**

Run: `.venv/bin/python -m pytest tests/test_cli_engine.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full engine-adjacent suites**

Run: `.venv/bin/python -m pytest tests/test_cli_engine.py tests/test_daemon_core.py tests/test_daemon_sources.py tests/test_engine_fleet.py -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add tesserae/cli.py tests/test_cli_engine.py
git commit -m "feat(cli): tesserae engine --all — fleet mode over the project registry"
```

---

### Task 6: full-suite gate + docs touch-up

**Files:**
- Modify: `docs/superpowers/specs/2026-06-12-global-engine-design.md` (flip Status to implemented)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: 0 failures (skips OK)

- [ ] **Step 2: Manual smoke (optional but recommended)**

```bash
.venv/bin/tesserae engine --all --once
```
Expected: one `unit <name> once-run rc=0` log line per registered project that
exists on disk; exit 0. (Projects with LLM extraction configured may take a
while — this is a real compile.)

- [ ] **Step 3: Flip design-doc status + commit**

Change `**Status:** proposed` to `**Status:** implemented (vNEXT)` in
`docs/superpowers/specs/2026-06-12-global-engine-design.md`.

```bash
git add docs/superpowers/specs/2026-06-12-global-engine-design.md
git commit -m "docs(engine): mark global-engine design implemented"
```

---

## Self-review notes

- **Spec coverage:** single process over registry (Tasks 3–5), hot-reload
  (Task 3 `reconcile` + Task 4 tests), compile cap (Task 2 + factory wiring in
  Task 3), graceful stop (Tasks 1, 4), global pidfile (Task 3), CLI (Task 5).
  Shared transcript watcher is explicitly future work in the design doc — no
  task, by design.
- **Known judgment call:** the fleet pidfile duplicates ~20 lines of Daemon's
  pidfile recipe rather than extracting a shared helper; extraction is noted
  as future work in the design doc (do it when a third copy appears).
- **Type consistency:** `DaemonFactory = (name, root, fleet) -> Daemon`
  everywhere (Tasks 3, 4, 5); `compile_gate` is a `threading.Semaphore` in
  both Daemon and FleetDaemon.
