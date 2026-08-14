"""Tests for tesserae.sidecars — the ``.tesserae/`` ownership registry.

The classification is the deliverable of roadmap step 10, so these tests pin
it: an unregistered sidecar fails, a misclassified durable file fails, and the
predicate's refusal to claim somebody else's file is asserted rather than
assumed. Same posture as ``tests/test_views.py`` pins the edge partition.

No compile runs here and no LLM is touched — the registry is data.
"""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path

import pytest

from tesserae import doctor
from tesserae.project import ProjectPaths, ProjectWiki
from tesserae.sidecars import (
    KIND_ACCUMULATED,
    KIND_CACHE,
    KIND_DERIVED,
    KIND_SCRATCH,
    KINDS,
    SCOPE_PROJECT,
    SCOPE_USER,
    SCOPES,
    SIDECARS,
    classify,
    is_tesserae_sidecar,
    of_kind,
    summary,
    tmp_owner_pid,
    unclassified_entries,
)

PACKAGE = Path(__file__).resolve().parents[1] / "tesserae"


# ---------------------------------------------------------------------------
# registry shape
# ---------------------------------------------------------------------------


def test_every_entry_declares_a_known_kind_scope_and_reason() -> None:
    """A ``why`` is not decoration: it is the record of what deletion costs."""
    for sidecar in SIDECARS:
        assert sidecar.kind in KINDS, sidecar
        assert sidecar.scope in SCOPES, sidecar
        assert sidecar.owner, sidecar
        assert len(sidecar.why) > 20, f"{sidecar.name}: 'why' must say what is lost"


def test_no_duplicate_entries_within_a_scope() -> None:
    """Two rows for one name means one of them is silently unreachable."""
    for scope in sorted(SCOPES):
        names = [s.name for s in SIDECARS if s.scope == scope]
        assert len(names) == len(set(names)), f"duplicate names in {scope} scope: {names}"


def test_accumulated_state_is_never_marked_safe_to_delete() -> None:
    """The invariant the registry exists to hold.

    ``accumulated`` means no compile can re-derive it — human verdicts, the
    agent overlay, the transaction-time ledger. Flipping one of these to
    ``safe_to_delete`` is how a future reset command would quietly destroy the
    one thing in the pipeline nothing can reconstruct.
    """
    for scope in sorted(SCOPES):
        for sidecar in of_kind(KIND_ACCUMULATED, scope=scope):
            assert not sidecar.safe_to_delete, sidecar.name


def test_the_human_verdict_ledger_is_accumulated_and_protected() -> None:
    """``candidate-same-as.json`` is the file this whole step is about.

    A compile that cannot find it does not error — it re-asks a question a
    human already answered, and a rejected pair comes back un-rejected. It is
    named explicitly so a bulk reclassification cannot sweep it up.
    """
    entry = classify("candidate-same-as.json")
    assert entry is not None
    assert entry.kind == KIND_ACCUMULATED
    assert entry.safe_to_delete is False


def test_sqlite_db_is_protected_because_of_the_tables_inside_it() -> None:
    """Mixed contents, classified by the most valuable table.

    ``node_vectors`` is a droppable cache, but ``node_memory``,
    ``fact_observed`` and ``read_audit`` share the file, and transaction time
    only ever moves forward — dropping the database to reclaim the vector
    cache resets every fact's 'when we learned it' to now.
    """
    entry = classify("sqlite.db")
    assert entry is not None
    assert entry.kind == KIND_ACCUMULATED
    assert entry.safe_to_delete is False
    for table in ("node_memory", "fact_observed", "read_audit"):
        assert table in entry.why


def test_llm_backed_caches_are_not_safe_to_delete() -> None:
    """A cache rebuilt by a model is not a cache you may silently drop.

    ``session_findings`` holds LLM-minted findings that become NODES, so
    dropping it re-runs a non-deterministic extractor and the next graph.json
    differs in bytes — the byte-idempotence break this repo has taken four
    times. Same for every other model-backed cache.
    """
    for name in (
        "session_findings",
        "community_summaries",
        "extraction_guidance_cache",
        "schema_drift_cache",
        "supersede_cache",
        "distill_cache",
        "distillation_cache",
    ):
        entry = classify(name)
        assert entry is not None, name
        assert entry.kind == KIND_CACHE, name
        assert entry.safe_to_delete is False, f"{name}: rebuilt by a model, so not silently droppable"


def test_live_locks_are_scratch_but_never_safe_to_delete() -> None:
    """Removing a HELD lock loses mutual exclusion, not data.

    ``kind`` and ``safe_to_delete`` answer different questions, and these are
    the entries that prove it: pure bookkeeping, and still untouchable.
    """
    for name in ("compile.lock", "session_chunks.lock", ".recompile.lock.d", "daemon.pid"):
        entry = classify(name)
        assert entry is not None, name
        assert entry.kind == KIND_SCRATCH, name
        assert entry.safe_to_delete is False, name


# ---------------------------------------------------------------------------
# the predicate
# ---------------------------------------------------------------------------


def test_exact_names_win_over_patterns() -> None:
    """``graph.json`` must not resolve through ``graph.json.bak-*``.

    A wildcard that swallows the artifact it backs up would classify the
    compiled graph as a manual backup — and any pass acting on the class would
    act on the wrong file.
    """
    assert classify("graph.json").kind == KIND_DERIVED
    assert classify("graph.json.bak-8367").kind == KIND_SCRATCH
    assert classify("manifest.tmp.25422.c5bb0996").kind == KIND_SCRATCH
    assert classify("daemon.mycrusher.pid").kind == KIND_SCRATCH


def test_the_predicate_refuses_to_claim_someone_elses_file() -> None:
    """The agent-memory borrowing: act on our objects, never on a user's.

    ``compile-restore5.log`` and the vendored ``cognee_bundle/`` really do sit
    in this project's ``.tesserae/`` and no Tesserae code path writes either.
    """
    for name in ("compile-restore5.log", "engine.log", "cognee_bundle", "notes.md"):
        assert not is_tesserae_sidecar(name), name


def test_scope_separates_two_directories_with_the_same_name() -> None:
    """``~/.tesserae/registry.json`` is not a project entry, and vice versa."""
    assert is_tesserae_sidecar("registry.json", scope=SCOPE_USER)
    assert not is_tesserae_sidecar("registry.json", scope=SCOPE_PROJECT)
    assert is_tesserae_sidecar("graph.json", scope=SCOPE_PROJECT)
    assert not is_tesserae_sidecar("graph.json", scope=SCOPE_USER)
    # ``config.json`` is real in both and means something different in each.
    assert classify("config.json", scope=SCOPE_PROJECT).owner == "tesserae.project"
    assert classify("config.json", scope=SCOPE_USER).owner == "tesserae.llm_json"


def test_unknown_kind_or_scope_fails_loud() -> None:
    with pytest.raises(ValueError):
        of_kind("temporary")
    with pytest.raises(ValueError):
        classify("graph.json", scope="global")


def test_tmp_owner_pid_reads_the_atomic_write_name() -> None:
    assert tmp_owner_pid("manifest.tmp.25422.c5bb0996") == 25422
    assert tmp_owner_pid("code-graph.json.tmp.1.deadbeef") == 1
    assert tmp_owner_pid("graph.json") is None
    assert tmp_owner_pid("notes.tmp.txt") is None


def test_unclassified_entries_names_the_strangers_only(tmp_path: Path) -> None:
    root = tmp_path / ".tesserae"
    root.mkdir()
    (root / "graph.json").write_text("{}", encoding="utf-8")
    (root / "candidate-same-as.json").write_text("{}", encoding="utf-8")
    (root / "compile-restore5.log").write_text("", encoding="utf-8")
    (root / "cognee_bundle").mkdir()
    assert unclassified_entries(root) == ["cognee_bundle", "compile-restore5.log"]


def test_summary_counts_every_project_entry_exactly_once() -> None:
    counts = summary()
    assert sum(counts.values()) == len([s for s in SIDECARS if s.scope == SCOPE_PROJECT])


# ---------------------------------------------------------------------------
# completeness — a new sidecar must be registered or CI fails
# ---------------------------------------------------------------------------


def test_every_project_path_is_registered(tmp_path: Path) -> None:
    """``ProjectPaths`` is the compile's own list of what it writes.

    This is the enforcement the roadmap asked for, in the form that cannot be
    argued with: add a field to ``ProjectPaths`` without a registry entry and
    this fails, rather than the file quietly joining the unclassified pile.
    """
    paths = ProjectWiki(tmp_path).paths
    unregistered = []
    for field in dataclasses.fields(ProjectPaths):
        if field.name == "root":
            continue  # the directory itself, not an entry in it
        name = getattr(paths, field.name).name
        if not is_tesserae_sidecar(name):
            unregistered.append(f"{field.name} -> {name}")
    assert not unregistered, f"ProjectPaths entries missing from tesserae.sidecars: {unregistered}"


def test_every_dot_tesserae_literal_in_the_package_is_registered() -> None:
    """Catch the writers that never went through ``ProjectPaths``.

    ``discovered_links.json``, ``candidate-same-as.json``, ``arxiv-cache.json``
    and the schema-drift cache are all written by modules that build their own
    path, which is exactly how a sidecar becomes invisible. The scan is over
    literal ``.tesserae/<name>`` occurrences in the package source, docstrings
    included, because a path named in a docstring is a path someone writes.
    """
    # Globs are captured whole (``daemon*.pid``) so a docstring naming a
    # generated family matches the registry's pattern entry rather than
    # decomposing into a name nobody wrote. A trailing ``.`` is prose.
    literal = re.compile(r"\.tesserae/([A-Za-z0-9_\-*?][A-Za-z0-9_.\-*?]*)")
    unregistered: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        # ``~/.tesserae`` and ``Path.home() / ".tesserae"`` are the USER scope;
        # a line mentioning home is judged against that registry instead.
        for line in text.splitlines():
            scope = SCOPE_USER if ("~/" in line or "home()" in line) else SCOPE_PROJECT
            for raw in literal.findall(line):
                name = raw.rstrip(".")
                if not name:
                    continue
                # ``doctor-report.{md,json}`` truncates at the brace: a capture
                # that is the stem of a registered name is that name's prose
                # form, not an unregistered sidecar.
                if any(
                    s.scope == scope and s.name.startswith(name + ".") for s in SIDECARS
                ):
                    continue
                if not is_tesserae_sidecar(name, scope=scope):
                    unregistered.setdefault(f"{path.name}:{scope}", set()).add(name)
    assert not unregistered, f"unregistered .tesserae entries: {unregistered}"


def test_the_kinds_partition_the_registry() -> None:
    """Every entry lands in exactly one kind bucket, in both scopes."""
    for scope in sorted(SCOPES):
        buckets = [set(s.name for s in of_kind(k, scope=scope)) for k in sorted(KINDS)]
        total = sum(len(b) for b in buckets)
        union = set().union(*buckets)
        assert total == len(union) == len([s for s in SIDECARS if s.scope == scope])


# ---------------------------------------------------------------------------
# the doctor check
# ---------------------------------------------------------------------------


def _ctx(root: Path) -> doctor.DoctorContext:
    from datetime import datetime, timezone

    return doctor.DoctorContext(
        project_root=root,
        wiki=ProjectWiki(root),
        registry=None,
        now=datetime.now(timezone.utc),
    )


def _age(path: Path, hours: float) -> None:
    import time

    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))


def test_doctor_reports_debris_and_strangers_separately(tmp_path: Path) -> None:
    root = tmp_path / ".tesserae"
    root.mkdir()
    orphan = root / "manifest.tmp.999999.abc12345"
    orphan.write_text("", encoding="utf-8")
    _age(orphan, 48)
    (root / "graph.json.bak-8367").write_text("{}", encoding="utf-8")
    (root / "compile-restore5.log").write_text("", encoding="utf-8")

    finding = doctor._detect_sidecars(_ctx(tmp_path))
    assert finding.severity == doctor.WARN
    assert "orphaned tmp file" in finding.message
    assert "manual graph.json backup" in finding.message
    assert "unclassified" in finding.message
    assert finding.fixable is True


def test_doctor_fix_removes_orphans_and_nothing_else(tmp_path: Path) -> None:
    """The blast radius of ``--fix``, pinned.

    A manual backup is a human's file and an unclassified entry is somebody
    else's; removing either would make this check more dangerous than the
    debris it cleans.
    """
    root = tmp_path / ".tesserae"
    root.mkdir()
    orphan = root / "manifest.tmp.999999.abc12345"
    orphan.write_text("", encoding="utf-8")
    _age(orphan, 48)
    backup = root / "graph.json.bak-8367"
    backup.write_text("{}", encoding="utf-8")
    stranger = root / "compile-restore5.log"
    stranger.write_text("", encoding="utf-8")

    message = doctor._fix_sidecars(_ctx(tmp_path))
    assert message and "1 orphaned tmp file" in message
    assert not orphan.exists()
    assert backup.exists()
    assert stranger.exists()


def test_doctor_leaves_a_live_writers_tmp_file_alone(tmp_path: Path) -> None:
    """A live pid means a write is mid-``replace``; unlinking corrupts it."""
    root = tmp_path / ".tesserae"
    root.mkdir()
    live = root / f"manifest.tmp.{os.getpid()}.abc12345"
    live.write_text("", encoding="utf-8")
    _age(live, 48)

    assert doctor._orphan_tmp_files(_ctx(tmp_path)) == []
    assert doctor._fix_sidecars(_ctx(tmp_path)) is None
    assert live.exists()


def test_doctor_leaves_a_recent_tmp_file_alone(tmp_path: Path) -> None:
    """Age is the only cross-host signal available.

    ``os.kill(pid, 0)`` answers about the LOCAL process table, and several
    hosts can mount one ``.tesserae``, so a foreign writer's pid can look dead
    here. A fresh file is never assumed orphaned.
    """
    root = tmp_path / ".tesserae"
    root.mkdir()
    fresh = root / "manifest.tmp.999999.abc12345"
    fresh.write_text("", encoding="utf-8")

    assert doctor._orphan_tmp_files(_ctx(tmp_path)) == []
    assert fresh.exists()


def test_doctor_is_ok_on_a_clean_workspace(tmp_path: Path) -> None:
    root = tmp_path / ".tesserae"
    root.mkdir()
    (root / "graph.json").write_text("{}", encoding="utf-8")
    (root / "config.json").write_text("{}", encoding="utf-8")

    finding = doctor._detect_sidecars(_ctx(tmp_path))
    assert finding.severity == doctor.OK
    assert finding.fixable is False
