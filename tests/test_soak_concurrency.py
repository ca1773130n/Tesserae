"""Multi-writer soak over the write paths that share ``.tesserae/`` (roadmap R5).

The deployment this guards is N servers on one shared disk, each running an
engine against the same ``.tesserae/``. The soak runs ``WRITERS`` processes x
``ITERS`` iterations against each multi-producer write path at once, with a
reader process parsing the published files the whole time, then SIGKILLs a
writer it spawned mid-write and checks what the next process inherits:

* ``agent_write`` — ``record_agent_write`` (the ``graph_write`` MCP tool):
  lost records and torn JSONL lines.
* ``graph_rmw`` — read-modify-write of ``graph.json`` under ``compile_lock``,
  published with ``_publish_atomically``: lost rows mean the lock let two
  holders in, torn reads mean a reader saw a half-written file.
* ``chunks`` — ``record_live_turns`` into ``session_chunks.db`` with every
  writer re-delivering the same shared turn: duplicate and lost rows.
* ``sessions`` — ``HarnessSessionStore.write_sessions`` (what
  ``sessions discover --import`` calls): torn ``manifest.json`` reads, and a
  final manifest that misses records on disk.
* ``kill9`` — SIGKILL a writer mid-loop; the lock must be free, every file
  must parse, the dead pidfile must read as stale, and any temp debris is
  counted (``doctor --recover`` is expected to clear it).

**Local disk only.** No NFS or SMB mount is available to this suite, so the
numbers say nothing about whether ``flock`` is enforced across hosts — which
is exactly the open question for the shared-disk deployment. Point
``TESSERAE_SOAK_DIR`` at a network mount to measure that.

Gated behind ``TESSERAE_SOAK=1`` because it takes minutes; CI never runs it.
Run it once plain and once with ``TESSERAE_JOURNALED_WRITES=1`` to compare
the opt-in journal against the default atomic rename.
``TESSERAE_SOAK_ITERS`` (default 500) and ``TESSERAE_SOAK_WRITERS`` (default
3) size it; ``TESSERAE_SOAK_REPORT=<path>`` also writes the counters as JSON.
"""

from __future__ import annotations

import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ITERS = int(os.environ.get("TESSERAE_SOAK_ITERS", "500") or 500)
WRITERS = int(os.environ.get("TESSERAE_SOAK_WRITERS", "3") or 3)
KILLS = int(os.environ.get("TESSERAE_SOAK_KILLS", "10") or 10)
#: write_sessions rebuilds the manifest from every record on disk, so its cost
#: is quadratic in the record count; a fifth of ITERS keeps the run in minutes.
SESSION_ITERS = max(1, ITERS // 5)
#: Big enough that a publish spans many write() calls, so a reader or a
#: SIGKILL has a real window to land inside one.
PAD = "x" * 200_000
SOAK_HOST = "soak-host"

pytestmark = pytest.mark.skipif(
    os.environ.get("TESSERAE_SOAK", "") not in ("1", "true", "yes"),
    reason="multi-minute soak; set TESSERAE_SOAK=1 to run",
)


# --------------------------------------------------------------------------- #
# worker side — run as `python tests/test_soak_concurrency.py <role> ...`       #
# --------------------------------------------------------------------------- #


def _agent_payload(name: str) -> dict:
    return {
        "nodes": [{"name": name, "type": "Concept", "description": "soak"}],
        "provenance": {"agent": "soak", "commit": "0000000"},
    }


def _w_agent_write(tdir: Path, writer: int, iters: int) -> None:
    from tesserae.agent_write import record_agent_write

    for i in range(iters):
        record_agent_write(tdir / "agent-writes.jsonl", _agent_payload(f"soak-{writer}-{i}"), "soak")


def _w_graph_rmw(tdir: Path, writer: int, iters: int) -> None:
    from tesserae.locking import compile_lock
    from tesserae.project import _publish_atomically

    target = tdir / "graph.json"
    for i in range(iters):
        with compile_lock(tdir, wait_seconds=120.0):
            rows = json.loads(target.read_text(encoding="utf-8"))["rows"] if target.exists() else []
            rows.append(f"{writer}:{i}")
            _publish_atomically(target, json.dumps({"rows": rows, "pad": PAD}))


def _turn(ts: str, text: str) -> dict:
    return {"timestamp": ts, "role": "user", "text": text}


def _w_chunks(tdir: Path, writer: int, iters: int) -> None:
    from tesserae.session_chunks import SessionChunksDB, record_live_turns

    db = SessionChunksDB(tdir / "session_chunks.db")
    for i in range(iters):
        ts = f"2026-01-01T00:{(i // 60) % 60:02d}:{i % 60:02d}Z"
        # The shared turn is re-delivered by every writer (tailer restarts,
        # backfill over tailed rows); the unique one is this writer's alone.
        turns = [_turn(ts, f"shared-{i}"), _turn(ts, f"unique-{writer}-{i}")]
        record_live_turns(db, "claude-code", "/soak/s.jsonl", "s", turns)


def _session(writer: int, i: int, project_root: Path):
    from tesserae.harness_sessions import HarnessSession

    return HarnessSession(
        id=f"claude-code:soak-{writer}-{i}",
        slug=f"soak-{writer}-{i}",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="soak",
        project_root=str(project_root),
        started_at=f"2026-01-01T00:00:{i % 60:02d}Z",
        title=f"soak {writer} {i}",
    )


def _w_sessions(tdir: Path, writer: int, iters: int) -> None:
    from tesserae.harness_sessions import PRODUCER_DISCOVERY, HarnessSessionStore

    store = HarnessSessionStore(tdir / "harness_sessions")
    for i in range(iters):
        store.write_sessions(
            [_session(writer, i, tdir)], replace=False,
            producer=PRODUCER_DISCOVERY, host=f"soak-host-{writer}",
        )


def _w_reader(tdir: Path, stop: Path, out: Path) -> None:
    """Parse every published file in a loop; count reads that do not parse."""
    from tesserae.agent_write import _read_records

    counts = {"graph_reads": 0, "graph_torn": 0, "manifest_reads": 0,
              "manifest_torn": 0, "overlay_reads": 0, "overlay_torn_lines": 0}
    targets = (("graph", tdir / "graph.json"), ("manifest", tdir / "harness_sessions" / "manifest.json"))
    while not stop.exists():
        for key, path in targets:
            try:
                raw = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            counts[f"{key}_reads"] += 1
            try:
                json.loads(raw)
            except ValueError:
                counts[f"{key}_torn"] += 1
        overlay = tdir / "agent-writes.jsonl"
        if overlay.exists():
            skips: list = []
            _read_records(overlay, skips)
            counts["overlay_reads"] += 1
            counts["overlay_torn_lines"] += len(skips)
    out.write_text(json.dumps(counts))


def _w_killee(tdir: Path, progress: Path) -> None:
    """Loop every write path forever; the parent SIGKILLs this mid-loop."""
    from tesserae.engine import pidlock
    from tesserae.locking import compile_lock
    from tesserae.project import _publish_atomically

    (tdir / "daemon.soak.pid").write_text(pidlock.serialize())
    i = 0
    while True:
        with compile_lock(tdir, wait_seconds=30.0):
            _publish_atomically(tdir / "graph.json", json.dumps({"rows": [i], "pad": PAD * 5}))
        _w_agent_write(tdir, 900 + i, 1)
        i += 1
        progress.write_text(str(i))


def _worker_main(argv: list) -> None:
    role, tdir = argv[0], Path(argv[1])
    if role == "reader":
        _w_reader(tdir, Path(argv[2]), Path(argv[3]))
    elif role == "killee":
        _w_killee(tdir, Path(argv[2]))
    else:
        fn = {"agent_write": _w_agent_write, "graph_rmw": _w_graph_rmw,
              "chunks": _w_chunks, "sessions": _w_sessions}[role]
        fn(tdir, int(argv[2]), int(argv[3]))


# --------------------------------------------------------------------------- #
# driver side                                                                  #
# --------------------------------------------------------------------------- #


def _spawn(*args: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "TESSERAE_HOST_ID": SOAK_HOST}
    return subprocess.Popen([sys.executable, __file__, *args], env=env, cwd=str(REPO_ROOT))


def _run_writers(role: str, tdir: Path, iters: int) -> dict:
    stop, out = tdir / ".stop", tdir / f".reader-{role}.json"
    reader = _spawn("reader", str(tdir), str(stop), str(out))
    started = time.monotonic()
    writers = [_spawn(role, str(tdir), str(w), str(iters)) for w in range(WRITERS)]
    try:
        rcs = [p.wait(timeout=1800) for p in writers]
    finally:
        for p in writers:
            if p.poll() is None:
                p.kill()
                p.wait()
        stop.write_text("1")
        reader.wait(timeout=120)
    stop.unlink()
    counts = json.loads(out.read_text())
    out.unlink()
    counts["writer_exit_codes"] = rcs
    counts["seconds"] = round(time.monotonic() - started, 1)
    return counts


def _measure_agent_write(tdir: Path) -> dict:
    from tesserae.agent_write import _read_records

    counts = _run_writers("agent_write", tdir, ITERS)
    skips: list = []
    records = _read_records(tdir / "agent-writes.jsonl", skips)
    names = {n["name"] for r in records.values() for n in r.get("nodes", [])}
    expected = {f"soak-{w}-{i}" for w in range(WRITERS) for i in range(ITERS)}
    counts.update(expected=len(expected), lost=len(expected - names), final_torn_lines=len(skips))
    return counts


def _measure_graph_rmw(tdir: Path) -> dict:
    counts = _run_writers("graph_rmw", tdir, ITERS)
    rows = json.loads((tdir / "graph.json").read_text())["rows"]
    expected = {f"{w}:{i}" for w in range(WRITERS) for i in range(ITERS)}
    counts.update(expected=len(expected), lost=len(expected - set(rows)),
                  duplicated=len(rows) - len(set(rows)))
    return counts


def _measure_chunks(tdir: Path) -> dict:
    import sqlite3

    counts = _run_writers("chunks", tdir, ITERS)
    con = sqlite3.connect(tdir / "session_chunks.db")
    texts = [t for (t,) in con.execute("select text from turns")]
    con.close()
    expected = {f"shared-{i}" for i in range(ITERS)} | {
        f"unique-{w}-{i}" for w in range(WRITERS) for i in range(ITERS)}
    counts.update(expected=len(expected), rows=len(texts), lost=len(expected - set(texts)),
                  duplicate_chunks=len(texts) - len(set(texts)))
    return counts


def _measure_sessions(tdir: Path) -> dict:
    counts = _run_writers("sessions", tdir, SESSION_ITERS)
    root = tdir / "harness_sessions"
    on_disk = _slugs(root)
    manifest = json.loads((root / "manifest.json").read_text())
    listed = {str(e.get("id")) for e in manifest.get("sessions", [])}
    expected = {f"claude-code:soak-{w}-{i}" for w in range(WRITERS) for i in range(SESSION_ITERS)}
    # ``manifest_missing`` is staleness, not loss: the next write rebuilds the
    # manifest from disk. It is reported, not failed on.
    counts.update(expected=len(expected), lost_records=len(expected - on_disk),
                  manifest_missing=len(on_disk - listed), iters_per_writer=SESSION_ITERS)
    return counts


def _slugs(root: Path) -> set:
    out = set()
    for path in root.glob("*/*.json"):
        try:
            out.add(str(json.loads(path.read_text()).get("id")))
        except ValueError:
            out.add(f"<torn:{path.name}>")
    return out


def _orphans(tdir: Path) -> list:
    return sorted(p.name for p in tdir.iterdir() if ".tmp." in p.name)


def _measure_kill9(tdir: Path) -> dict:
    from tesserae.agent_write import _read_records
    from tesserae.engine import pidlock
    from tesserae.locking import CompileLockHeldError, compile_lock

    result = {"kills": 0, "lock_stuck": 0, "graph_unparseable": 0, "overlay_torn_lines": 0,
              "pidfile_judged_alive": 0, "orphan_tmp_files": 0, "recovered_by_doctor": None}
    rng = random.Random(1729)
    for _ in range(KILLS):
        progress = tdir / ".progress"
        progress.unlink(missing_ok=True)
        child = _spawn("killee", str(tdir), str(progress))
        deadline = time.monotonic() + 60
        while not progress.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(rng.uniform(0.0, 0.3))
        os.kill(child.pid, signal.SIGKILL)  # our own child, by exact PID
        child.wait()
        result["kills"] += 1
        try:
            with compile_lock(tdir, wait_seconds=0):
                pass
        except CompileLockHeldError:
            result["lock_stuck"] += 1
        try:
            json.loads((tdir / "graph.json").read_text())
        except ValueError:
            result["graph_unparseable"] += 1
        skips: list = []
        _read_records(tdir / "agent-writes.jsonl", skips)
        result["overlay_torn_lines"] += len(skips)
        if pidlock.owner_is_alive(pidlock.read_owner(tdir / "daemon.soak.pid")):
            result["pidfile_judged_alive"] += 1
    result["orphan_tmp_files"] = len(_orphans(tdir))
    result["orphan_names_sample"] = _orphans(tdir)[:5]
    # Informational: from ANOTHER host the dead writer's pid means nothing, so
    # a lease-aware reader waits for the lease to expire (True) where a
    # pid-only reader probes its own process table (False, by luck).
    os.environ["TESSERAE_HOST_ID"] = "soak-other-host"
    try:
        result["other_host_sees_live_lease"] = pidlock.owner_is_alive(
            pidlock.read_owner(tdir / "daemon.soak.pid"))
    finally:
        os.environ["TESSERAE_HOST_ID"] = SOAK_HOST
    try:
        from tesserae.locking import recover_journal  # absent before R5
    except ImportError:
        return result
    result["recovery_actions"] = len(recover_journal(tdir))
    result["recovered_by_doctor"] = {"orphans_left": len(_orphans(tdir)),
                                     "journal_left": len(list((tdir / "journal").glob("*.wal")))
                                     if (tdir / "journal").is_dir() else 0}
    return result


def test_soak_shared_tesserae_writers(monkeypatch):
    # The driver judges the writers' pidfiles and journal from THEIR host; a
    # different host id would (correctly) wait out their leases instead.
    monkeypatch.setenv("TESSERAE_HOST_ID", SOAK_HOST)
    base = os.environ.get("TESSERAE_SOAK_DIR") or None
    report: dict = {"iters": ITERS, "writers": WRITERS, "filesystem": base or "local tmp",
                    "journaled_writes": os.environ.get("TESSERAE_JOURNALED_WRITES", "")}
    with tempfile.TemporaryDirectory(prefix="tesserae-soak-", dir=base) as raw:
        root = Path(raw)
        for name, measure in (("agent_write", _measure_agent_write), ("graph_rmw", _measure_graph_rmw),
                              ("chunks", _measure_chunks), ("sessions", _measure_sessions),
                              ("kill9", _measure_kill9)):
            tdir = root / name
            tdir.mkdir()
            report[name] = measure(tdir)
            print(f"soak {name}: {json.dumps(report[name], sort_keys=True)}", file=sys.stderr)
    out = os.environ.get("TESSERAE_SOAK_REPORT")
    if out:
        Path(out).write_text(json.dumps(report, indent=2, sort_keys=True))
    failures = {
        "agent_write.lost": report["agent_write"]["lost"],
        "agent_write.torn": report["agent_write"]["overlay_torn_lines"] + report["agent_write"]["final_torn_lines"],
        "graph.lost": report["graph_rmw"]["lost"],
        "graph.torn": report["graph_rmw"]["graph_torn"],
        "chunks.dup": report["chunks"]["duplicate_chunks"],
        "chunks.lost": report["chunks"]["lost"],
        "sessions.torn": report["sessions"]["manifest_torn"],
        "sessions.lost": report["sessions"]["lost_records"],
        "kill9.lock_stuck": report["kill9"]["lock_stuck"],
        "kill9.graph": report["kill9"]["graph_unparseable"],
        "kill9.pidfile": report["kill9"]["pidfile_judged_alive"],
        "kill9.journal_left": (report["kill9"]["recovered_by_doctor"] or {}).get("journal_left", 0),
    }
    assert {k: v for k, v in failures.items() if v} == {}


if __name__ == "__main__":
    _worker_main(sys.argv[1:])
