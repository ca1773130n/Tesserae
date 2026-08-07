"""tesserae doctor — fixture-driven checks over broken/healthy project states.

Conventions honored:
* registry tests build their own registry under ``tmp_path`` and pass
  ``registry_path`` explicitly (the global ``~/.tesserae/registry.json``
  shadowing trap; conftest's autouse isolation covers the default path).
* dates are pinned via ``run_doctor(..., now=PINNED_NOW)``.
* environment-shaped probes (LLM login, embedding backend, detection sweep)
  are pinned by an autouse fixture so results never depend on the dev box.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tesserae import doctor
from tesserae.project import ProjectWiki

PINNED_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)

#: Captured before the autouse fixture pins the module attribute, so one test
#: can exercise the real config-dir resolution.
_REAL_PROJECT_CLAUDE_DIRS = doctor._project_claude_config_dirs


@pytest.fixture(autouse=True)
def _pin_probes(monkeypatch):
    """Pin the machine-environment probes so doctor results are deterministic."""
    monkeypatch.setattr(doctor, "_llm_login_status", lambda: {"claude": True, "codex": None})
    monkeypatch.setattr(doctor, "_project_claude_config_dirs", lambda ctx: [])
    monkeypatch.setattr(doctor, "_embedding_probe", lambda: {"backend": "pinned", "semantic": True})
    monkeypatch.setattr(doctor, "_environment_probe", lambda root: "pinned environment summary")
    # The filesystem under tmp_path differs per box (apfs here, ext4/overlay
    # in CI) and decides the flock finding's severity, so pin a local one.
    monkeypatch.setattr(doctor, "_mount_table", lambda: [("/", "ext4")])
    # This machine's id, which partitions the pidfiles and lock records that
    # several hosts sharing one .tesserae would otherwise read as their own.
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")


def make_project(root: Path) -> ProjectWiki:
    root.mkdir(parents=True, exist_ok=True)
    return ProjectWiki.init(root, name="doctorproj", sources=["README.md"])


def finding(report: doctor.DoctorReport, check_id: str) -> doctor.Finding:
    matches = [f for f in report.findings if f.check_id == check_id]
    assert matches, f"no finding for {check_id!r}: {[f.check_id for f in report.findings]}"
    return matches[0]


def tree_checksums(*roots: Path) -> dict:
    """{relative path: sha256} over every file under the given roots."""
    out = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# healthy / uninitialized
# ---------------------------------------------------------------------------


def test_healthy_project_exits_zero(tmp_path):
    make_project(tmp_path)
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    bad = [f for f in report.findings if f.severity != "ok"]
    assert bad == [], f"unexpected non-ok findings: {[(f.check_id, f.message) for f in bad]}"
    assert report.exit_code == 0
    assert report.fixed == []
    # Every registered check produced exactly one finding.
    assert sorted(f.check_id for f in report.findings) == sorted(c.id for c in doctor.CHECKS)


def test_uninitialized_directory_is_an_error(tmp_path):
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "project_initialized")
    assert f.severity == "error"
    assert f.suggestion == "tesserae init"
    assert report.exit_code == 2
    # Project-scoped checks skip cleanly instead of crashing.
    assert finding(report, "graph_parse").message == "not applicable"


# ---------------------------------------------------------------------------
# graph / config parsing
# ---------------------------------------------------------------------------


def test_corrupt_graph_json_detected(tmp_path):
    wiki = make_project(tmp_path)
    wiki.paths.graph.write_text("{not json", encoding="utf-8")
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "graph_parse")
    assert f.severity == "error"
    assert "corrupt" in f.message
    assert f.suggestion == "tesserae compile"
    assert report.exit_code == 2


def test_missing_graph_json_is_a_warning(tmp_path):
    wiki = make_project(tmp_path)
    wiki.paths.graph.unlink()
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "graph_parse")
    assert f.severity == "warn"
    assert f.suggestion == "tesserae compile"
    assert report.exit_code == 1


def test_config_parse_error_detected(tmp_path):
    wiki = make_project(tmp_path)
    wiki.paths.config.write_text("{{{ definitely not json", encoding="utf-8")
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "config_valid")
    assert f.severity == "error"
    assert "does not parse" in f.message
    assert report.exit_code == 2


def test_config_missing_required_keys_is_a_warning(tmp_path):
    wiki = make_project(tmp_path)
    wiki.paths.config.write_text(json.dumps({"name": "doctorproj"}), encoding="utf-8")
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "config_valid")
    assert f.severity == "warn"
    assert "sources" in f.message and "graph_path" in f.message


# ---------------------------------------------------------------------------
# registry (isolated registry dirs — never the global one)
# ---------------------------------------------------------------------------


def _write_registry(reg_path: Path, projects: dict, *, active: str | None = None) -> None:
    raw = {"version": 1, "projects": projects}
    if active is not None:
        raw["active"] = active
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def test_registry_stale_entry_and_legacy_active_detected_then_pruned(tmp_path):
    project = tmp_path / "proj"
    make_project(project)
    gone_root = tmp_path / "gone"  # never created — a deleted project root
    reg_path = tmp_path / "reg" / "registry.json"
    _write_registry(
        reg_path,
        {
            "gone": {"root": str(gone_root), "graph_path": str(gone_root / ".tesserae/graph.json")},
            "doctorproj": {
                "root": str(project),
                "graph_path": str(project / ".tesserae" / "graph.json"),
            },
        },
        active="gone",
    )

    report = doctor.run_doctor(project, registry_path=reg_path, now=PINNED_NOW)
    f = finding(report, "registry_consistent")
    assert f.severity == "warn"
    assert f.fixable
    assert "legacy 'active' key" in f.message
    assert "gone" in f.message

    fixed = doctor.run_doctor(project, fix=True, registry_path=reg_path, now=PINNED_NOW)
    assert any(entry.startswith("registry_consistent:") for entry in fixed.fixed)
    assert finding(fixed, "registry_consistent").severity == "ok"

    raw = json.loads(reg_path.read_text(encoding="utf-8"))
    assert "active" not in raw
    assert "gone" not in raw["projects"]
    assert "doctorproj" in raw["projects"]

    # Idempotent: a second --fix run has nothing left to do.
    again = doctor.run_doctor(project, fix=True, registry_path=reg_path, now=PINNED_NOW)
    assert again.fixed == []


def test_registry_missing_graph_is_report_only(tmp_path):
    project = tmp_path / "proj"
    wiki = make_project(project)
    other = tmp_path / "other"
    make_project(other)
    (other / ".tesserae" / "graph.json").unlink()
    reg_path = tmp_path / "reg" / "registry.json"
    _write_registry(
        reg_path,
        {"other": {"root": str(other), "graph_path": str(other / ".tesserae" / "graph.json")}},
    )
    report = doctor.run_doctor(project, fix=True, registry_path=reg_path, now=PINNED_NOW)
    f = finding(report, "registry_consistent")
    assert f.severity == "warn"
    assert not f.fixable  # root exists — recompile, don't prune
    raw = json.loads(reg_path.read_text(encoding="utf-8"))
    assert "other" in raw["projects"]  # --fix must NOT prune it


# ---------------------------------------------------------------------------
# site / search-index staleness
# ---------------------------------------------------------------------------


def test_stale_search_index_detected_and_rebuilt(tmp_path):
    wiki = make_project(tmp_path)
    index = wiki.paths.site / "search-index.json"
    index.write_text("{}", encoding="utf-8")
    os.utime(index, (1_500_000_000, 1_500_000_000))
    os.utime(wiki.paths.graph, (1_600_000_000, 1_600_000_000))

    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "site_search_index")
    assert f.severity == "warn"
    assert f.fixable

    fixed = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert any(entry.startswith("site_search_index:") for entry in fixed.fixed)
    assert finding(fixed, "site_search_index").severity == "ok"
    assert index.stat().st_mtime >= wiki.paths.graph.stat().st_mtime

    again = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert again.fixed == []


def test_unbuilt_site_is_ok_not_a_warning(tmp_path):
    make_project(tmp_path)
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    assert finding(report, "site_search_index").severity == "ok"


# ---------------------------------------------------------------------------
# daemon pidfile
# ---------------------------------------------------------------------------


def _dead_pid() -> int:
    proc = subprocess.Popen(["/bin/sleep", "0"])
    proc.wait()
    return proc.pid


def test_dead_daemon_pid_detected_and_removed(tmp_path):
    make_project(tmp_path)
    pidfile = tmp_path / ".tesserae" / "daemon.pid"
    pidfile.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")

    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "daemon_pid")
    assert f.severity == "warn"
    assert f.fixable
    assert pidfile.exists()  # fix=False must not remove it

    fixed = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert not pidfile.exists()
    assert finding(fixed, "daemon_pid").severity == "ok"

    again = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert again.fixed == []


def test_live_daemon_pid_is_ok_and_never_removed(tmp_path):
    make_project(tmp_path)
    pidfile = tmp_path / ".tesserae" / "daemon.pid"
    pidfile.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    report = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    f = finding(report, "daemon_pid")
    assert f.severity == "ok"
    assert pidfile.exists()


def test_foreign_host_pidfile_is_reported_but_never_judged_or_removed(tmp_path):
    """Shared disk: srv-b's daemon is alive on srv-b, and its pid is dead here.

    ``os.kill(pid, 0)`` answers about the LOCAL process table, so --fix used
    to delete a running daemon's pidfile on the other machine.
    """
    make_project(tmp_path)
    foreign = tmp_path / ".tesserae" / "daemon.srv-b.pid"
    foreign.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")

    report = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    f = finding(report, "daemon_pid")
    assert f.severity == "ok"
    assert not f.fixable
    assert "srv-b" in f.message
    assert foreign.exists()
    assert not any(entry.startswith("daemon_pid:") for entry in report.fixed)


def test_this_hosts_stale_pidfile_is_removed_while_a_foreign_one_survives(tmp_path):
    make_project(tmp_path)
    mine = tmp_path / ".tesserae" / "daemon.srv-a.pid"
    foreign = tmp_path / ".tesserae" / "daemon.srv-b.pid"
    mine.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")
    foreign.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")

    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "daemon_pid")
    assert f.severity == "warn"
    assert f.fixable
    assert "daemon.srv-a.pid" in f.message and "srv-b" in f.message

    fixed = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert not mine.exists()
    assert foreign.exists()  # never, under any circumstance
    assert finding(fixed, "daemon_pid").severity == "ok"


# ---------------------------------------------------------------------------
# live compile lock — report the holder, NEVER touch it
# ---------------------------------------------------------------------------


def test_live_compile_lock_reported_and_left_alone(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    make_project(tmp_path)
    lock_path = tmp_path / ".tesserae" / "compile.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write(str(os.getpid()))
        handle.flush()

        report = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
        f = finding(report, "compile_lock")
        assert f.severity == "warn"
        assert str(os.getpid()) in f.message
        assert "not touch" in f.message
        assert not f.fixable
        assert lock_path.exists()
        assert not any(entry.startswith("compile_lock") for entry in report.fixed)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def test_unheld_compile_lock_is_ok(tmp_path):
    make_project(tmp_path)
    (tmp_path / ".tesserae" / "compile.lock").write_text("", encoding="utf-8")
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    assert finding(report, "compile_lock").severity == "ok"


def test_compile_lock_held_by_another_host_says_so(tmp_path):
    """A JSON holder record names the machine; a lock held elsewhere must not
    read as "wait, something here is compiling"."""
    fcntl = pytest.importorskip("fcntl")
    make_project(tmp_path)
    lock_path = tmp_path / ".tesserae" / "compile.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write(json.dumps({"pid": 4711, "host": "srv-b"}))
        handle.flush()

        f = finding(doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW), "compile_lock")
        assert f.severity == "warn"
        assert "another machine" in f.message
        assert "4711" in f.message and "srv-b" in f.message
        assert f.suggestion == "wait for the compile on srv-b to finish"
        assert lock_path.exists()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


# ---------------------------------------------------------------------------
# flock enforcement / filesystem under the project
# ---------------------------------------------------------------------------


def test_local_filesystem_flock_probe_is_ok_and_claims_nothing_cross_host(tmp_path):
    make_project(tmp_path)
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "filesystem_locking")
    assert f.severity == "ok"
    assert "ext4" in f.message
    assert "cannot prove enforcement between machines" in f.message


def test_network_filesystem_warns_that_flock_may_be_a_no_op(tmp_path, monkeypatch):
    make_project(tmp_path)
    monkeypatch.setattr(doctor, "_mount_table", lambda: [("/", "ext4"), (str(tmp_path), "nfs4")])
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "filesystem_locking")
    assert f.severity == "warn"
    assert "nfs4" in f.message
    # Honest about reach: the local probe succeeded, and that proves nothing
    # about a second machine.
    assert "cannot be determined from one machine" in f.message


def test_refused_flock_is_reported_as_a_broken_guarantee(tmp_path, monkeypatch):
    make_project(tmp_path)
    monkeypatch.setattr(doctor, "_flock_probe", lambda d: (False, "OSError: [Errno 45] unsupported"))
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "filesystem_locking")
    assert f.severity == "warn"
    assert "protect nothing" in f.message


def test_filesystem_type_prefers_the_longest_matching_mountpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_mount_table", lambda: [("/", "ext4"), (str(tmp_path), "cifs")])
    assert doctor._filesystem_type(tmp_path / "deep" / "path") == "cifs"


# ---------------------------------------------------------------------------
# llm_login — reports what it verified, never "logged in"
# ---------------------------------------------------------------------------


def test_llm_login_never_claims_credentials_it_did_not_verify(tmp_path):
    """The green check that contradicted a live `not logged in` compile.

    Presence of a config dir is all doctor checks (running `claude -p` would
    spend a model call), so the finding must say only that.
    """
    make_project(tmp_path)
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "llm_login")
    assert f.severity == "ok"
    assert "credentialed" not in f.message
    assert "NOT verified" in f.message


def test_llm_login_is_scoped_to_the_config_dirs_compile_would_try(tmp_path, monkeypatch):
    make_project(tmp_path)
    configured = tmp_path / "claude-acct-1"
    configured.mkdir()
    monkeypatch.setattr(doctor, "_project_claude_config_dirs", lambda ctx: [str(configured)])
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "llm_login")
    assert f.severity == "ok"
    assert str(configured) in f.message
    assert "NOT verified" in f.message


def test_llm_login_warns_when_no_configured_config_dir_exists(tmp_path, monkeypatch):
    make_project(tmp_path)
    gone = tmp_path / "claude-acct-gone"
    monkeypatch.setattr(doctor, "_project_claude_config_dirs", lambda ctx: [str(gone)])
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "llm_login")
    assert f.severity == "warn"
    assert str(gone) in f.message


def test_llm_login_config_dirs_come_from_the_projects_own_config(tmp_path, monkeypatch):
    """Resolution goes through llm_json, the same path _build_json_client uses."""
    wiki = make_project(tmp_path)
    acct = tmp_path / "acct"
    acct.mkdir()
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["llm_claude_config_dirs"] = [str(acct)]
    wiki.paths.config.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.delenv("TESSERAE_CLAUDE_CONFIG_DIRS", raising=False)
    # The provider is pinned because ~/.tesserae/config.json contributes one:
    # a dev box set to codex would otherwise resolve no claude dirs at all and
    # this assertion would pass or fail depending on whose machine ran it.
    monkeypatch.setenv("TESSERAE_LLM_PROVIDER", "claude")
    ctx = doctor.DoctorContext(
        project_root=tmp_path, wiki=ProjectWiki.load(tmp_path), registry=None, now=PINNED_NOW
    )
    # _REAL_PROJECT_CLAUDE_DIRS, not the attribute: the autouse fixture pins
    # that one so every other test stays independent of this box's config.
    assert _REAL_PROJECT_CLAUDE_DIRS(ctx) == [str(acct)]


def test_llm_login_says_nothing_about_claude_dirs_when_the_provider_is_codex(
    tmp_path, monkeypatch
):
    """A codex project must not be warned about a claude account it never uses.

    ``llm_claude_config_dirs`` resolves for every provider, but a compile under
    ``llm_provider: codex`` runs codex and only falls back to claude — so a
    missing claude dir turned a healthy project into `warn` and exit 1.
    """
    wiki = make_project(tmp_path)
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["llm_provider"] = "codex"
    cfg["llm_claude_config_dirs"] = [str(tmp_path / "claude-acct-gone")]
    wiki.paths.config.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.delenv("TESSERAE_CLAUDE_CONFIG_DIRS", raising=False)
    monkeypatch.delenv("TESSERAE_LLM_PROVIDER", raising=False)
    ctx = doctor.DoctorContext(
        project_root=tmp_path, wiki=ProjectWiki.load(tmp_path), registry=None, now=PINNED_NOW
    )
    assert _REAL_PROJECT_CLAUDE_DIRS(ctx) == []

    monkeypatch.setattr(doctor, "_project_claude_config_dirs", _REAL_PROJECT_CLAUDE_DIRS)
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "llm_login")
    assert f.severity == "ok"
    assert "claude-acct-gone" not in f.message


# ---------------------------------------------------------------------------
# hook log bloat
# ---------------------------------------------------------------------------


def _make_oversized_log(path: Path) -> None:
    with path.open("wb") as handle:
        handle.truncate(doctor.HOOK_LOG_CAP_BYTES + 1)


def test_oversized_hook_log_detected_and_rotated(tmp_path):
    make_project(tmp_path)
    log = tmp_path / ".tesserae" / ".session-end-hook.log"
    _make_oversized_log(log)

    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "hook_log_bloat")
    assert f.severity == "warn"
    assert f.fixable
    assert log.exists()  # fix=False leaves it

    fixed = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert not log.exists()
    assert log.with_name(log.name + ".1").exists()
    assert finding(fixed, "hook_log_bloat").severity == "ok"

    again = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert again.fixed == []


# ---------------------------------------------------------------------------
# build-history hygiene (dates pinned)
# ---------------------------------------------------------------------------


def _write_ledger(wiki: ProjectWiki) -> Path:
    ledger = wiki.paths.build_history
    lines = [
        json.dumps({"built_at": "2026-01-01T00:00:00Z", "git_head": "a" * 40}),  # old, newest head
        json.dumps({"built_at": "2025-12-01T00:00:00Z"}),  # old — trimmable
        json.dumps({"built_at": "2026-07-01T00:00:00Z"}),  # recent — kept
    ]
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ledger


def test_build_history_trim_preserves_newest_git_head(tmp_path):
    wiki = make_project(tmp_path)
    ledger = _write_ledger(wiki)

    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "build_history")
    assert f.severity == "warn"
    assert f.fixable
    assert "1 build-history entries" in f.message  # the git_head carrier is never counted

    fixed = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert any(entry.startswith("build_history:") for entry in fixed.fixed)
    kept = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(kept) == 2
    assert any(entry.get("git_head") == "a" * 40 for entry in kept)  # preserved despite age
    assert not any(entry.get("built_at") == "2025-12-01T00:00:00Z" for entry in kept)
    assert finding(fixed, "build_history").severity == "ok"

    again = doctor.run_doctor(tmp_path, fix=True, now=PINNED_NOW)
    assert again.fixed == []


# ---------------------------------------------------------------------------
# idempotence tripwire (recomputed from hashes — the flag is never persisted)
# ---------------------------------------------------------------------------


def test_idempotence_suspect_detected_from_snapshot_state(tmp_path):
    from tesserae.output_snapshot import snapshot_output

    wiki = make_project(tmp_path)
    current = snapshot_output(wiki.root)
    wiki.paths.output_snapshot.write_text(
        json.dumps(
            {
                "changed": True,
                "graph_sha256": current.graph_sha256,  # graph layer identical
                "projections_sha256": "0" * 64,  # ... but projections drifted
                "output_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "idempotence")
    assert f.severity == "warn"
    assert "idempotence suspect" in f.message


# ---------------------------------------------------------------------------
# session-chunk coverage (module is landing in a parallel workstream)
# ---------------------------------------------------------------------------


def _install_fake_session_chunks(monkeypatch, tmp_path: Path, days: list):
    import tesserae as tesserae_pkg

    fake = types.ModuleType("tesserae.session_chunks")
    fake.chunks_db_path = lambda root: Path(root) / ".tesserae" / "session_chunks.db"
    fake.day_label = lambda ts: ts.strftime("%Y-%m-%d")

    class FakeDB:
        def __init__(self, path):
            self.path = path

        def coverage_rows(self):
            return [{"day": d, "harness": "claude-code", "source": "tailer", "updated_at": ""} for d in days]

    fake.SessionChunksDB = FakeDB
    monkeypatch.setitem(sys.modules, "tesserae.session_chunks", fake)
    monkeypatch.setattr(tesserae_pkg, "session_chunks", fake, raising=False)
    return fake


def test_session_chunks_module_absent_is_skipped_ok(tmp_path, monkeypatch):
    import tesserae as tesserae_pkg

    make_project(tmp_path)
    # Simulate a missing module: None in sys.modules makes the import raise,
    # and the package attribute (bound by any earlier import) must go too or
    # ``from . import session_chunks`` would still resolve it.
    monkeypatch.setitem(sys.modules, "tesserae.session_chunks", None)
    monkeypatch.delattr(tesserae_pkg, "session_chunks", raising=False)
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "session_chunks")
    assert f.severity == "ok"
    assert "unavailable" in f.message


def test_session_chunks_db_absent_is_ok_with_backfill_hint(tmp_path):
    make_project(tmp_path)
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    f = finding(report, "session_chunks")
    assert f.severity == "ok"
    assert f.suggestion == "tesserae sessions chunk-backfill"


def test_session_chunks_stale_coverage_warns(tmp_path, monkeypatch):
    make_project(tmp_path)
    _install_fake_session_chunks(monkeypatch, tmp_path, days=["2026-07-01"])
    (tmp_path / ".tesserae" / "session_chunks.db").write_text("", encoding="utf-8")
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)  # threshold day: 2026-07-08
    f = finding(report, "session_chunks")
    assert f.severity == "warn"
    assert "2026-07-01" in f.message
    assert f.suggestion == "tesserae sessions chunk-backfill"


def test_session_chunks_fresh_coverage_is_ok(tmp_path, monkeypatch):
    make_project(tmp_path)
    _install_fake_session_chunks(monkeypatch, tmp_path, days=["2026-07-09", "2026-07-10"])
    (tmp_path / ".tesserae" / "session_chunks.db").write_text("", encoding="utf-8")
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    assert finding(report, "session_chunks").severity == "ok"


# ---------------------------------------------------------------------------
# fix=False mutates nothing (checksum the tree)
# ---------------------------------------------------------------------------


def _broken_fixture(tmp_path: Path):
    project = tmp_path / "proj"
    wiki = make_project(project)
    _write_ledger(wiki)
    pidfile = project / ".tesserae" / "daemon.pid"
    pidfile.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")
    _make_oversized_log(project / ".tesserae" / ".session-end-hook.log")
    index = wiki.paths.site / "search-index.json"
    index.write_text("{}", encoding="utf-8")
    os.utime(index, (1_500_000_000, 1_500_000_000))
    os.utime(wiki.paths.graph, (1_600_000_000, 1_600_000_000))
    reg_path = tmp_path / "reg" / "registry.json"
    _write_registry(
        reg_path,
        {"gone": {"root": str(tmp_path / "nope"), "graph_path": str(tmp_path / "nope" / "g.json")}},
        active="gone",
    )
    return project, reg_path


def test_fix_false_mutates_nothing(tmp_path):
    project, reg_path = _broken_fixture(tmp_path)
    before = tree_checksums(project, reg_path.parent)

    report = doctor.run_doctor(project, fix=False, registry_path=reg_path, now=PINNED_NOW)
    assert report.exit_code >= 1
    assert report.fixed == []

    after = tree_checksums(project, reg_path.parent)
    assert before == after


def test_full_fix_run_is_idempotent(tmp_path):
    project, reg_path = _broken_fixture(tmp_path)

    first = doctor.run_doctor(project, fix=True, registry_path=reg_path, now=PINNED_NOW)
    assert first.fixed  # something was actually repaired

    second = doctor.run_doctor(project, fix=True, registry_path=reg_path, now=PINNED_NOW)
    assert second.fixed == []
    assert not any(f.fixable for f in second.findings)


# ---------------------------------------------------------------------------
# runner semantics: exit codes, crash-safety, custom check registry
# ---------------------------------------------------------------------------


def _static_check(check_id: str, severity: str) -> doctor.Check:
    return doctor.Check(
        check_id,
        "test",
        lambda ctx, s=severity, c=check_id: doctor.Finding(c, "test", s, f"{c} says {s}"),
    )


def test_exit_code_mapping(tmp_path):
    assert doctor.run_doctor(tmp_path, checks=[_static_check("a", "ok")], now=PINNED_NOW).exit_code == 0
    assert doctor.run_doctor(tmp_path, checks=[_static_check("a", "warn")], now=PINNED_NOW).exit_code == 1
    assert (
        doctor.run_doctor(
            tmp_path,
            checks=[_static_check("a", "warn"), _static_check("b", "error")],
            now=PINNED_NOW,
        ).exit_code
        == 2
    )


def test_crashing_check_becomes_error_finding(tmp_path):
    boom = doctor.Check("boom", "test", lambda ctx: 1 / 0)
    report = doctor.run_doctor(tmp_path, checks=[boom], now=PINNED_NOW)
    f = finding(report, "boom")
    assert f.severity == "error"
    assert "check crashed" in f.message
    assert report.exit_code == 2


def test_crashing_fix_becomes_error_finding(tmp_path):
    def _detect(ctx):
        return doctor.Finding("fixboom", "test", "warn", "needs fixing", fixable=True)

    def _fix(ctx):
        raise RuntimeError("fix exploded")

    check = doctor.Check("fixboom", "test", _detect, fix=_fix, safe=True)
    report = doctor.run_doctor(tmp_path, fix=True, checks=[check], now=PINNED_NOW)
    f = finding(report, "fixboom")
    assert f.severity == "error"
    assert "fix crashed" in f.message
    assert report.fixed == []


# ---------------------------------------------------------------------------
# report rendering / artifacts / --all
# ---------------------------------------------------------------------------


def test_render_markdown_and_json(tmp_path):
    make_project(tmp_path)
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    md = doctor.render_markdown(report)
    assert "# tesserae doctor" in md
    assert "project_initialized" in md
    payload = json.loads(doctor.to_json(report))
    assert payload["exit_code"] == 0
    assert payload["checked_at"] == PINNED_NOW.isoformat()
    assert {f["check_id"] for f in payload["findings"]} == {c.id for c in doctor.CHECKS}


def test_write_report_artifacts(tmp_path):
    make_project(tmp_path)
    report = doctor.run_doctor(tmp_path, now=PINNED_NOW)
    paths = doctor.write_report(tmp_path, report)
    md_path = Path(paths["markdown"])
    json_path = Path(paths["json"])
    assert md_path == tmp_path / ".tesserae" / "doctor-report.md"
    assert json_path == tmp_path / ".tesserae" / "doctor-report.json"
    assert "tesserae doctor" in md_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["project_root"] == str(tmp_path.resolve())


def test_run_doctor_all_iterates_registered_projects(tmp_path):
    from tesserae.mcp_server import ProjectRegistry

    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    make_project(alpha)
    make_project(beta)
    reg_path = tmp_path / "reg" / "registry.json"
    registry = ProjectRegistry(reg_path)
    registry.register(alpha, name="alpha")
    registry.register(beta, name="beta")

    reports = doctor.run_doctor_all(registry, now=PINNED_NOW)
    assert sorted(reports) == ["alpha", "beta"]
    assert all(isinstance(r, doctor.DoctorReport) for r in reports.values())
    assert doctor.overall_exit_code(reports) == 0

    # A broken member propagates the worst exit code.
    (beta / ".tesserae" / "graph.json").write_text("{corrupt", encoding="utf-8")
    reports = doctor.run_doctor_all(registry, now=PINNED_NOW)
    assert reports["beta"].exit_code == 2
    assert doctor.overall_exit_code(reports) == 2


def test_run_doctor_never_imports_cli():
    import importlib

    module = importlib.import_module("tesserae.doctor")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "from .cli" not in source
    assert "import tesserae.cli" not in source
    assert "from tesserae.cli" not in source


# ---------------------------------------------------------------------------
# wiki_lint (report-based: detect never runs the full linter — too slow on
# large graphs; it reads the persisted lint-report.json + freshness)
# ---------------------------------------------------------------------------


def _lint_report(wiki: ProjectWiki, findings: list, by_severity: dict) -> Path:
    path = wiki.project_root / ".tesserae" / "lint-report.json"
    path.write_text(
        json.dumps({"findings": findings, "by_code": {}, "by_severity": by_severity}),
        encoding="utf-8",
    )
    return path


def test_wiki_lint_missing_report_is_ok_with_hint(tmp_path):
    make_project(tmp_path)
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "wiki_lint")
    assert f.severity == "ok"
    assert f.suggestion == "tesserae lint"


def test_wiki_lint_stale_report_is_ok_with_hint(tmp_path):
    wiki = make_project(tmp_path)
    path = _lint_report(wiki, [], {"error": 0, "warning": 0, "info": 0})
    graph_mtime = wiki.paths.graph.stat().st_mtime
    os.utime(path, (graph_mtime - 100, graph_mtime - 100))
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "wiki_lint")
    assert f.severity == "ok"
    assert "predates" in f.message


def test_wiki_lint_fresh_report_severity_and_fixable(tmp_path):
    wiki = make_project(tmp_path)
    path = _lint_report(
        wiki,
        [{"severity": "warning", "code": "W1", "message": "m", "auto_fixable": True}],
        {"error": 0, "warning": 1, "info": 0},
    )
    graph_mtime = wiki.paths.graph.stat().st_mtime
    os.utime(path, (graph_mtime + 100, graph_mtime + 100))
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "wiki_lint")
    assert f.severity == "warn"
    assert f.fixable is True
    assert "1 findings" in f.message


def test_wiki_lint_fresh_clean_report_is_ok(tmp_path):
    wiki = make_project(tmp_path)
    path = _lint_report(wiki, [], {"error": 0, "warning": 0, "info": 0})
    graph_mtime = wiki.paths.graph.stat().st_mtime
    os.utime(path, (graph_mtime + 100, graph_mtime + 100))
    f = finding(doctor.run_doctor(tmp_path, now=PINNED_NOW), "wiki_lint")
    assert f.severity == "ok"
    assert "clean" in f.message
