"""tesserae doctor — project-health checks over a ``.tesserae`` workspace.

The check registry is data: every check is a :class:`Check` carrying a
read-only ``detect(ctx)`` and — for the explicitly safe subset only — a
``fix(ctx)`` applied exclusively under ``--fix``. Report-only checks never
mutate; a plain ``run_doctor(root)`` must leave the tree byte-identical
(tests checksum it). Every check is individually exception-safe: a crashing
check becomes an ``error`` finding, never an exception out of
:func:`run_doctor`.

Design notes (deviations from the naive reading of existing code, verified
against source):

* ``WikiLinter.run()`` writes ``lint-report.{md,json}`` unconditionally
  (lint.py ``run()`` tail), so the lint *detect* redirects those artifact
  paths into a throwaway temp dir; the real artifact write only happens via
  the ``--fix`` path (``wiki.lint(fix_trivial=True)``).
* ``idempotence_suspect`` is a compile-time result flag
  (``ProjectWiki.compile``), never persisted — ``output-snapshot.json``
  carries hashes only. Doctor recomputes the same signal post-hoc: graph
  layer identical to the recorded snapshot while projections drifted.
* The live compile lock is probed NON-blocking and reported with the holder
  pid and machine only. Doctor never kills or removes it (recorded failure
  mode: SessionEnd compile pile-ups).
* Machine-scoped state is judged per machine. Several hosts can mount the
  same disk and therefore share one ``.tesserae``, so a pidfile or a lock
  record written elsewhere is described and left alone — ``os.kill(pid, 0)``
  answers about the local process table and says nothing about another
  host's pid. ``--fix`` never touches a foreign host's file.

:func:`migrate_code_scope` is the one exception to "checks never mutate", and
it is not a check: it is a one-shot migration the operator invokes by name
(``tesserae doctor migrate-code-scope``), dry-run by default, and it is
deliberately NOT reachable from ``--fix`` — see its own section below.

This module never imports :mod:`tesserae.cli`. Heavy or optional subsystems
(registry, lint, detection, embeddings, session_chunks) are imported lazily
inside individual checks so ``import tesserae.doctor`` stays cheap and works
on partial installs — including while ``tesserae/session_chunks.py`` is
absent.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

__all__ = [
    "Check",
    "CodeScopeMigration",
    "DoctorContext",
    "DoctorReport",
    "Finding",
    "PageSweep",
    "SqliteSweep",
    "CHECKS",
    "code_scope_migration_json",
    "migrate_code_scope",
    "render_code_scope_migration",
    "run_doctor",
    "run_doctor_all",
    "render_markdown",
    "to_json",
    "write_report",
]

OK = "ok"
WARN = "warn"
ERROR = "error"
_SEVERITY_RANK = {OK: 0, WARN: 1, ERROR: 2}

HOOK_LOG_CAP_BYTES = 10 * 1024 * 1024
BUILD_HISTORY_STALE_DAYS = 90
SESSION_CHUNK_STALE_DAYS = 2
#: A ``*.tmp.<pid>.<hex>`` file is only called orphaned once it is this old.
#: See ``_orphan_tmp_files`` — a dead LOCAL pid says nothing about a writer on
#: another host sharing the same ``.tesserae``.
TMP_ORPHAN_MIN_AGE_HOURS = 24
_BUILT_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """One check's verdict. ``suggestion`` is the operator command for
    report-only findings; ``fixable`` marks findings the safe ``--fix`` path
    can address on this run."""

    check_id: str
    category: str
    severity: str  # 'ok' | 'warn' | 'error'
    message: str
    suggestion: Optional[str] = None
    fixable: bool = False

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
            "fixable": self.fixable,
        }


@dataclass
class DoctorContext:
    """Everything a check may consult. ``wiki`` is None when the project is
    uninitialized; ``registry`` is None when the registry subsystem failed to
    import. ``now`` is injectable so tests can pin dates."""

    project_root: Path
    wiki: Optional[object]
    registry: Optional[object]
    now: datetime


@dataclass
class Check:
    id: str
    category: str
    detect: Callable[[DoctorContext], Optional[Finding]]
    fix: Optional[Callable[[DoctorContext], Optional[str]]] = None
    safe: bool = False


@dataclass
class DoctorReport:
    project_root: str
    findings: List[Finding] = field(default_factory=list)
    fixed: List[str] = field(default_factory=list)
    exit_code: int = 0
    checked_at: str = ""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _f(
    check_id: str,
    category: str,
    severity: str,
    message: str,
    *,
    suggestion: Optional[str] = None,
    fixable: bool = False,
) -> Finding:
    return Finding(
        check_id=check_id,
        category=category,
        severity=severity,
        message=message,
        suggestion=suggestion,
        fixable=fixable,
    )


def _load_wiki(project_root: Path):
    """ProjectWiki for an initialized project, else None (never raises)."""
    try:
        from .project import ProjectWiki

        return ProjectWiki.load(project_root)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _load_registry(registry_path: Optional[Path] = None):
    try:
        from .mcp_server import ProjectRegistry

        return ProjectRegistry(registry_path)
    except Exception:
        return None


def _run_git(cwd: Path, *args: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _tesserae_dir(ctx: DoctorContext) -> Path:
    return ctx.project_root / ".tesserae"


def _aware(now: datetime) -> datetime:
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


def _local_host_id() -> str:
    """This machine's stable id, or ``""`` when it cannot be resolved.

    Several servers can mount the same disk and therefore share ONE
    ``.tesserae``. Nothing in a pidfile or a lock record used to say which
    machine wrote it, so every host read the others' records as its own —
    and ``doctor --fix`` unlinked a pidfile belonging to a daemon that was
    running perfectly well on a different server. Imported lazily and
    defensively for the same reason ``locking._host_tag`` is: doctor must
    still run when the session store cannot be imported or ``~/.tesserae``
    is unwritable.
    """
    try:
        from .harness_sessions import local_host_id

        return local_host_id()
    except Exception:  # noqa: BLE001 — identity is a nicety, the checks are not
        return ""


def _daemon_pidfiles(ctx: DoctorContext) -> List[tuple[str, Path]]:
    """``[(host, path)]`` for every ``.tesserae/daemon*.pid``.

    ``host`` is the id embedded in ``daemon.<host>.pid``, and ``""`` for the
    legacy unqualified ``daemon.pid`` written before pidfiles carried an
    owner. Missing ``.tesserae`` yields ``[]`` — ``Path.glob`` on an absent
    directory is empty, not an error.
    """
    out: List[tuple[str, Path]] = []
    for path in sorted(_tesserae_dir(ctx).glob("daemon*.pid")):
        middle = path.name[len("daemon"): -len(".pid")]
        out.append((middle[1:] if middle.startswith(".") else middle, path))
    return out


# ---------------------------------------------------------------------------
# monkeypatchable probes (kept module-level so tests can pin them)
# ---------------------------------------------------------------------------


def _llm_login_status() -> Dict[str, Optional[bool]]:
    """{'claude': True|False|None, 'codex': ...} — CLI *config presence*.

    Reuses ``setup.detection._probe_credentials`` which defers to
    ``llm_json._claude_cli_available`` / ``_codex_cli_available``. Read what
    those actually test before trusting a ``True``: the binary is on PATH and
    some config dir contains one of ``settings.json`` / ``history.jsonl`` /
    ``auth.json``. Those files prove the CLI has been USED at some point.
    None of them expires when the OAuth session does, so this cannot answer
    "is it logged in" and the finding that consumes it must not say it does —
    see :func:`_detect_llm_login`.
    """
    out: Dict[str, Optional[bool]] = {"claude": None, "codex": None}
    try:
        from .setup.detection import _probe_credentials
    except Exception:
        return out
    for name in ("claude", "codex"):
        try:
            out[name] = _probe_credentials(name)
        except Exception:
            out[name] = None
    return out


def _project_claude_config_dirs(ctx: DoctorContext) -> List[str]:
    """The claude config dirs a compile in THIS project would actually try.

    Resolved through ``llm_json.resolve_llm_client_settings`` — the same call
    ``ProjectWiki._build_json_client`` makes — so the check talks about the
    accounts the project configured instead of every ``~/.claude*`` that
    happens to exist on the box. That glob is how doctor came to name two
    healthy-looking CLIs while the compile it was meant to explain had tried
    exactly one config dir and been refused. ``[]`` means there is no
    project-scoped claude list to report — either nothing is configured (the
    client then discovers dirs itself) or claude is not what a compile here
    reaches for first, see below.
    """
    try:
        from .llm_json import resolve_llm_client_settings

        cfg = ctx.wiki.config() if ctx.wiki is not None else {}
        settings = resolve_llm_client_settings(cfg if isinstance(cfg, dict) else {})
    except Exception:  # noqa: BLE001 — an unreadable config must never crash doctor
        return []
    # ``llm_claude_config_dirs`` is resolved for every provider, but
    # ``build_default_json_client`` only puts the claude CLI first when the
    # provider IS claude — under ``llm_provider: codex`` (or the API-key
    # providers) claude is a fallback that a healthy project never reaches.
    # Reporting the claude dirs regardless turned a missing ~/.claude into a
    # WARN and exit 1 on a project whose compiles were succeeding through
    # codex, which is the same untrue-claim defect this check was rewritten to
    # stop making, only pointing the other way.
    if str(settings.get("provider") or "claude") != "claude":
        return []
    return [str(d) for d in (settings.get("claude_config_dirs") or [])]


#: Filesystem types on which flock(2) is not reliably enforced BETWEEN hosts:
#: network mounts, and the host/guest shared folders that behave like them.
#: Some server+client combinations honour it and several silently degrade to
#: a no-op, and nothing observable from one machine tells those two apart.
_NETWORK_FS_TYPES = frozenset({
    "nfs", "nfs3", "nfs4", "cifs", "smbfs", "smb", "smb2", "afpfs",
    "webdav", "davfs", "davfs2", "fuse.sshfs", "fuse.rclone", "fuse.s3fs",
    "9p", "ceph", "glusterfs", "lustre", "afs", "vboxsf", "virtiofs",
})


def _mount_table() -> List[tuple[str, str]]:
    """``[(mountpoint, fstype)]`` for this machine, ``[]`` when unavailable.

    Module-level so tests can pin a filesystem the dev box does not have.
    Linux answers from ``/proc/mounts`` with no subprocess at all; macOS/BSD
    have no ``/proc`` and no stdlib call that names a filesystem type, so
    ``mount`` is parsed. ``df -T`` is deliberately not used: on Linux it
    prints the type, on macOS the same flag *filters* by type instead.
    """
    entries: List[tuple[str, str]] = []
    proc_mounts = Path("/proc/mounts")
    if proc_mounts.exists():
        try:
            text = proc_mounts.read_text(encoding="utf-8")
        except OSError:
            return []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                entries.append((parts[1].replace("\\040", " "), parts[2]))
        return entries
    try:
        completed = subprocess.run(["mount"], capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    for line in completed.stdout.splitlines():
        # BSD/macOS shape: "<device> on <mountpoint> (<fstype>, <opt>, ...)".
        if " on " not in line or "(" not in line:
            continue
        mountpoint, _, tail = line.split(" on ", 1)[1].rpartition(" (")
        fstype = tail.split(",", 1)[0].strip().rstrip(")")
        if mountpoint and fstype:
            entries.append((mountpoint, fstype))
    return entries


def _filesystem_type(path: Path) -> Optional[str]:
    """The fs type of the mount ``path`` sits on, or None if undetermined.

    Longest matching mountpoint wins: ``/`` matches everything, so a shorter
    prefix must never outrank the ``/mnt/shared`` the project is actually on.
    """
    try:
        target = str(Path(path).resolve())
    except OSError:
        return None
    best_len = -1
    best: Optional[str] = None
    for mountpoint, fstype in _mount_table():
        if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
            if len(mountpoint) > best_len:
                best_len, best = len(mountpoint), fstype
    return best


def _flock_probe(directory: Path) -> tuple[bool, str]:
    """``(acquired, detail)`` for a NON-BLOCKING exclusive flock on ``directory``.

    The directory's own fd is locked rather than a file inside it, because
    ``run_doctor`` without ``--fix`` is checksum-verified to leave the tree
    byte-identical — the probe may not create even a temporary file — and a
    temp file under ``/tmp`` would answer for the wrong filesystem, which is
    the one thing this check exists to identify.
    """
    import fcntl

    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError as exc:
        return False, f"cannot open the directory: {exc}"
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Held by someone else — which is itself proof that this kernel is
        # enforcing flock here, so it counts as a successful probe.
        return True, "already held by another process, so flock is being enforced"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True, "acquired and released"
    finally:
        os.close(fd)


def _embedding_probe() -> dict:
    from .retrieval.hybrid import active_embedding_backend, backend_is_semantic

    # Model downloads print tqdm progress bars; keep them out of the report.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        backend = active_embedding_backend()
        semantic = bool(backend_is_semantic(backend))
    return {
        "backend": getattr(backend, "name", type(backend).__name__),
        "semantic": semantic,
    }


def _environment_probe(project_root: Path) -> str:
    from .setup.detection import detect

    report = detect(project_root)
    clis = ", ".join(
        f"{name}={'yes' if getattr(cli, 'available', False) else 'no'}"
        for name, cli in sorted(report.llm_clis.items())
    )
    version = ".".join(str(v) for v in tuple(report.python.version_info)[:3])
    return (
        f"python {version}; CLIs: {clis}; "
        f"recommended extractor: {report.recommended.extractor}"
    )


# ---------------------------------------------------------------------------
# checks — detect
# ---------------------------------------------------------------------------


def _detect_project_initialized(ctx: DoctorContext) -> Optional[Finding]:
    config = _tesserae_dir(ctx) / "config.json"
    if config.is_file():
        return _f("project_initialized", "core", OK, "project is initialized (.tesserae/config.json present)")
    return _f(
        "project_initialized",
        "core",
        ERROR,
        f"project is not initialized: {config} is missing",
        suggestion="tesserae init",
    )


def _detect_graph_parse(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    graph_path = ctx.wiki.paths.graph
    if not graph_path.exists():
        return _f(
            "graph_parse",
            "core",
            WARN,
            "graph.json missing — project has never been compiled",
            suggestion="tesserae compile",
        )
    try:
        payload = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _f(
            "graph_parse",
            "core",
            ERROR,
            f"graph.json is corrupt: {exc}",
            suggestion="tesserae compile",
        )
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("nodes"), list)
        or not isinstance(payload.get("edges"), list)
    ):
        return _f(
            "graph_parse",
            "core",
            ERROR,
            "graph.json parsed but is not a graph payload (nodes/edges lists missing)",
            suggestion="tesserae compile",
        )
    return _f(
        "graph_parse",
        "core",
        OK,
        f"graph.json parses ({len(payload['nodes'])} nodes, {len(payload['edges'])} edges)",
    )


_REQUIRED_CONFIG_KEYS = ("name", "sources", "graph_path")


def _detect_config_valid(ctx: DoctorContext) -> Optional[Finding]:
    config_path = _tesserae_dir(ctx) / "config.json"
    if not config_path.is_file():
        return None  # project_initialized already reported this
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _f(
            "config_valid",
            "core",
            ERROR,
            f"config.json does not parse: {exc}",
            suggestion="fix .tesserae/config.json by hand or re-run tesserae init",
        )
    if not isinstance(payload, dict):
        return _f(
            "config_valid",
            "core",
            ERROR,
            "config.json is not a JSON object",
            suggestion="re-run tesserae init",
        )
    missing = [key for key in _REQUIRED_CONFIG_KEYS if key not in payload]
    if missing:
        return _f(
            "config_valid",
            "core",
            WARN,
            f"config.json is missing expected keys: {', '.join(missing)}",
            suggestion="re-run tesserae init (it merges, not clobbers)",
        )
    return _f("config_valid", "core", OK, "config.json parses and carries the expected keys")


def _detect_registry_consistent(ctx: DoctorContext) -> Optional[Finding]:
    registry = ctx.registry
    if registry is None:
        return _f("registry_consistent", "registry", OK, "registry subsystem unavailable — skipped")
    path = Path(registry.path)
    if not path.exists():
        return _f("registry_consistent", "registry", OK, "no project registry on this machine")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _f(
            "registry_consistent",
            "registry",
            ERROR,
            f"registry is corrupt JSON ({path}): {exc}",
            suggestion="repair or remove the registry file, then re-register projects",
        )
    if not isinstance(raw, dict):
        return _f(
            "registry_consistent",
            "registry",
            ERROR,
            f"registry is not a JSON object ({path})",
            suggestion="repair or remove the registry file, then re-register projects",
        )
    problems: List[str] = []
    fixable = False
    if "active" in raw:
        problems.append("legacy 'active' key present")
        fixable = True
    missing_roots: List[str] = []
    missing_graphs: List[str] = []
    for name, entry in sorted((raw.get("projects") or {}).items()):
        if not isinstance(entry, dict):
            missing_roots.append(str(name))
            continue
        root = entry.get("root")
        if not root or not Path(root).is_dir():
            missing_roots.append(str(name))
            continue
        graph_path = entry.get("graph_path")
        if graph_path and not Path(graph_path).is_file():
            missing_graphs.append(str(name))
    if missing_roots:
        problems.append(f"entries with missing roots: {', '.join(missing_roots)}")
        fixable = True
    if missing_graphs:
        # Root exists, graph gone: a recompile fixes it — report, don't prune.
        problems.append(
            f"entries whose graph.json is missing (recompile them): {', '.join(missing_graphs)}"
        )
    if not problems:
        count = len(raw.get("projects") or {})
        return _f("registry_consistent", "registry", OK, f"registry consistent ({count} projects)")
    return _f(
        "registry_consistent",
        "registry",
        WARN,
        "; ".join(problems),
        suggestion=("tesserae doctor --fix" if fixable else "tesserae compile in the affected project"),
        fixable=fixable,
    )


def _fix_registry_consistent(ctx: DoctorContext) -> Optional[str]:
    registry = ctx.registry
    if registry is None or not Path(registry.path).exists():
        return None
    data = registry.load()  # load() already drops the legacy 'active' key in-memory
    projects = data.get("projects") or {}
    pruned = [
        name
        for name, entry in sorted(projects.items())
        if not isinstance(entry, dict) or not entry.get("root") or not Path(entry["root"]).is_dir()
    ]
    for name in pruned:
        del projects[name]
    registry.save(data)  # atomic tmp+rename; persists the 'active' drop too
    detail = f"pruned {len(pruned)} stale entr{'y' if len(pruned) == 1 else 'ies'}"
    if pruned:
        detail += f" ({', '.join(pruned)})"
    return f"registry: {detail}; legacy 'active' key dropped if present"


def _newest_git_head(build_history: Path) -> Optional[str]:
    recorded: Optional[str] = None
    try:
        for line in build_history.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            git_head = entry.get("git_head")
            if isinstance(git_head, str) and git_head:
                recorded = git_head
    except OSError:
        return None
    return recorded


def _detect_graph_staleness(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    ledger = ctx.wiki.paths.build_history
    if not ledger.exists():
        return _f("graph_staleness", "freshness", OK, "no build-history ledger — staleness unknown")
    recorded = _newest_git_head(ledger)
    if recorded is None:
        return _f("graph_staleness", "freshness", OK, "build history carries no git_head — staleness unknown")
    from .lint import read_git_head

    head = read_git_head(ctx.project_root)
    if head is None or head == recorded:
        return _f("graph_staleness", "freshness", OK, "compiled graph matches git HEAD")
    return _f(
        "graph_staleness",
        "freshness",
        WARN,
        f"graph was compiled at {recorded[:12]} but git HEAD is {head[:12]} — compile is stale",
        suggestion="tesserae refresh",
    )


def _detect_site_stale(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    graph_path = ctx.wiki.paths.graph
    if not graph_path.exists():
        return None
    index_path = ctx.wiki.paths.site / "search-index.json"
    if not index_path.exists():
        return _f(
            "site_search_index",
            "freshness",
            OK,
            "site not built yet (search-index.json absent); tesserae serve builds it on demand",
        )
    if index_path.stat().st_mtime < graph_path.stat().st_mtime:
        return _f(
            "site_search_index",
            "freshness",
            WARN,
            "site/search-index.json is older than graph.json — the static site is stale",
            suggestion="tesserae doctor --fix (rebuilds the site)",
            fixable=True,
        )
    return _f("site_search_index", "freshness", OK, "static site is current with graph.json")


def _fix_site_stale(ctx: DoctorContext) -> Optional[str]:
    if ctx.wiki is None:
        return None
    ctx.wiki.build_site()
    return "site: rebuilt from graph.json"


def _detect_wiki_lint(ctx: DoctorContext) -> Optional[Finding]:
    """Report-based: running the full WikiLinter inline takes minutes on a
    large graph (45k nodes ≈ >2 min), which would make every doctor run hang.
    Detect reads the persisted lint-report.json and its freshness instead;
    only the explicit --fix path pays for a real linter run."""
    if ctx.wiki is None or not ctx.wiki.paths.graph.exists():
        return None
    # Unknown lint status (no/stale/unreadable report) is not illness — a
    # fresh project has no report yet. OK severity, with the refresh hint.
    report_path = _tesserae_dir(ctx) / "lint-report.json"
    if not report_path.exists():
        return _f(
            "wiki_lint",
            "graph",
            OK,
            "lint status unknown (no report yet)",
            suggestion="tesserae lint",
        )
    if report_path.stat().st_mtime < ctx.wiki.paths.graph.stat().st_mtime:
        return _f(
            "wiki_lint",
            "graph",
            OK,
            "lint report predates the current graph.json",
            suggestion="tesserae lint",
        )
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _f("wiki_lint", "graph", OK, "lint report unreadable", suggestion="tesserae lint")
    by_severity = payload.get("by_severity") or {}
    errors = int(by_severity.get("error", 0))
    warnings = int(by_severity.get("warning", 0))
    findings = payload.get("findings") or []
    fixable = any(f.get("auto_fixable") for f in findings if isinstance(f, dict))
    if not findings:
        return _f("wiki_lint", "graph", OK, "lint clean — no findings")
    severity = ERROR if errors else (WARN if warnings else OK)
    return _f(
        "wiki_lint",
        "graph",
        severity,
        f"lint: {len(findings)} findings (errors={errors}, warnings={warnings})",
        suggestion=("tesserae doctor --fix (applies trivial fixes)" if fixable else "tesserae lint"),
        fixable=fixable,
    )


def _fix_wiki_lint(ctx: DoctorContext) -> Optional[str]:
    if ctx.wiki is None:
        return None
    with contextlib.redirect_stderr(io.StringIO()):
        ctx.wiki.lint(fix_trivial=True, severity_floor="info")
    return "lint: applied trivial auto-fixes (wiki.lint(fix_trivial=True))"


def _detect_compile_lock(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    lock_path = _tesserae_dir(ctx) / "compile.lock"
    if not lock_path.exists():
        return _f("compile_lock", "processes", OK, "no live compile lock")
    # Probe with the same primitive ``compile_lock`` holds it with — flock or
    # msvcrt — rather than a second hand-rolled fcntl call that would report
    # "unsupported" on a platform where the lock now works.
    from .locking import (
        describe_holder,
        locking_unavailable,
        read_holder,
        try_lock_exclusive,
        unlock_exclusive,
    )

    if locking_unavailable():  # pragma: no cover — neither fcntl nor msvcrt
        return _f(
            "compile_lock", "processes", OK, "file locking unsupported on this platform — skipped"
        )
    try:
        handle = lock_path.open("r+", encoding="utf-8")  # never create, never truncate
    except OSError as exc:
        return _f("compile_lock", "processes", WARN, f"compile.lock unreadable: {exc}")
    try:
        if not try_lock_exclusive(handle):
            # The holder record is JSON — ``{"pid": …, "host": …}`` — because
            # several machines can share this ``.tesserae`` and pid 4711 here
            # says nothing about pid 4711 there; ``read_holder`` still accepts
            # the bare integer older versions wrote, the same back-compat
            # idiom ``pidlock.parse`` uses. Naming the machine IS the finding:
            # "a compile is running" means something entirely different when
            # it is running on a server the operator is not looking at.
            holder = read_holder(lock_path)
            host = str((holder or {}).get("host") or "")
            elsewhere = " on another machine" if host and host != _local_host_id() else ""
            # NEVER kill or remove a live compile lock (SessionEnd pile-up
            # failure mode). Report the holder and stand down.
            return _f(
                "compile_lock",
                "processes",
                WARN,
                f"a compile/refresh is running{elsewhere}"
                f"{describe_holder(holder)} — doctor will not touch it",
                suggestion=(
                    f"wait for the compile on {host} to finish"
                    if elsewhere
                    else "wait for the running compile to finish"
                ),
            )
        unlock_exclusive(handle)
        return _f("compile_lock", "processes", OK, "compile.lock present but not held")
    finally:
        handle.close()


def _foreign_pidfile_note(foreign: List[str]) -> str:
    """The trailing clause naming pidfiles this host must not judge."""
    if not foreign:
        return ""
    return (
        f"; {len(foreign)} pidfile(s) belong to other hosts ({', '.join(foreign)}) "
        "and are neither judged nor removed from here"
    )


def _detect_daemon_pid(ctx: DoctorContext) -> Optional[Finding]:
    """Liveness of the engine daemon pidfile THIS host owns, and only that.

    ``pidlock.owner_is_alive`` ends in ``os.kill(pid, 0)``, which interrogates
    the LOCAL process table. Against a record written by another machine that
    verdict is not merely unreliable, it is meaningless — and acting on it
    made ``--fix`` unlink the pidfile of a daemon that was running on a
    different server, on the shared disk they both mount. So the pidfiles are
    partitioned by the host id in their name and every foreign one is reported
    as-is, never probed and never fixable.
    """
    if ctx.wiki is None:
        return None
    entries = _daemon_pidfiles(ctx)
    if not entries:
        return _f("daemon_pid", "processes", OK, "no engine daemon pidfile")
    from .engine import pidlock

    local = _local_host_id()
    note = _foreign_pidfile_note(sorted(host for host, _ in entries if host and host != local))
    stale: List[str] = []
    live: List[int] = []
    for host, path in entries:
        if host and host != local:
            continue
        # An unqualified ``daemon.pid`` names no owner, so it can only be
        # judged the way it was judged before hosts had ids: locally. Keeping
        # that is what makes upgrading a single-machine install a no-op, and
        # the ambiguity ends the first time the daemon rewrites the file under
        # its ``daemon.<host>.pid`` name.
        owner = pidlock.read_owner(path)
        if owner is not None and pidlock.owner_is_alive(owner):
            live.append(owner["pid"])
        else:
            stale.append(path.name)
    if stale:
        return _f(
            "daemon_pid",
            "processes",
            WARN,
            f"stale {', '.join(stale)} — recorded owner is not running{note}",
            suggestion="tesserae doctor --fix (removes the stale pidfile)",
            fixable=True,
        )
    if live:
        return _f("daemon_pid", "processes", OK, f"engine daemon running (pid {live[0]}){note}")
    return _f("daemon_pid", "processes", OK, f"no engine daemon pidfile for this host{note}")


def _fix_daemon_pid(ctx: DoctorContext) -> Optional[str]:
    from .engine import pidlock

    local = _local_host_id()
    removed: List[str] = []
    for host, path in _daemon_pidfiles(ctx):
        if host and host != local:
            continue  # another machine's record — never, under any circumstance
        owner = pidlock.read_owner(path)
        if owner is not None and pidlock.owner_is_alive(owner):
            continue  # re-check at fix time: never remove a live daemon's pidfile
        path.unlink(missing_ok=True)
        removed.append(path.name)
    if not removed:
        return None
    return f"{', '.join(removed)}: removed stale pidfile (owner dead)"


def _detect_llm_login(ctx: DoctorContext) -> Optional[Finding]:
    """Report that an LLM CLI is CONFIGURED — never that it is logged in.

    This check used to say "credentialed LLM CLI: claude, codex" on the
    strength of ``~/.claude/history.jsonl`` existing. A file the CLI wrote
    the last time it ran does not expire when the OAuth session does, so the
    check was structurally unable to see a logged-out CLI: ``tesserae
    compile`` printed "Claude CLI not logged in (tried 1 config dir)" and, in
    the same second, doctor printed "[ok] llm_login: credentialed LLM CLI:
    claude, codex".

    Only one thing settles the question, and it is what compile itself does:
    run ``claude -p`` and read the "not logged in" refusal
    (``llm_json.ClaudeCLIJsonClient._run_prompt``). That spends a real model
    call, a network round trip and several seconds on every ``tesserae
    doctor`` — and on every MCP ``doctor_run`` — which a read-only diagnostic
    must not do. So the finding is scoped to the config dirs compile would
    actually try, and states only what it verified: that they exist. The
    green check no longer contradicts the failure the user is standing in,
    because it no longer makes the claim that was being contradicted.
    """
    dirs = _project_claude_config_dirs(ctx)
    if dirs:
        present = [d for d in dirs if Path(d).is_dir()]
        if not present:
            return _f(
                "llm_login",
                "environment",
                WARN,
                "none of the claude config dirs this project is configured to use exist: "
                + ", ".join(dirs),
                suggestion="claude /login, or correct llm_claude_config_dirs in .tesserae/config.json",
            )
        return _f(
            "llm_login",
            "environment",
            OK,
            f"claude config dir(s) this project would use exist ({', '.join(present)}) "
            "— credentials NOT verified: doctor spends no LLM call, so a logged-out CLI "
            "looks exactly like this",
            suggestion="if compile reports `not logged in`, run `claude /login` for that config dir",
        )
    status = _llm_login_status()
    configured = sorted(name for name, value in status.items() if value is True)
    if configured:
        return _f(
            "llm_login",
            "environment",
            OK,
            f"LLM CLI config present: {', '.join(configured)} — credentials NOT verified "
            "(a config dir is not a live token; only a compile proves the CLI is logged in)",
            suggestion="if compile reports `not logged in`, run `claude /login` or `codex login`",
        )
    return _f(
        "llm_login",
        "environment",
        WARN,
        "no LLM CLI config detected — LLM-backed features degrade to no-LLM paths",
        suggestion="claude /login  or  codex login",
    )


def _detect_filesystem_locking(ctx: DoctorContext) -> Optional[Finding]:
    """Name the filesystem under ``.tesserae`` and probe flock(2) on it.

    Every concurrency guarantee in this project — the compile lock, the
    agent-write lock, the daemon pidfile handshake — rests on flock(2) being
    enforced. Over NFS/SMB that is configuration-dependent and silently
    degrades to a no-op on some setups, which would leave ``compile.lock``
    protecting nothing while looking exactly as healthy as it does now.

    Be precise about the reach of this probe. One machine can establish two
    things: which filesystem the project sits on, and whether its own kernel
    honours flock there. It cannot establish that two hosts flocking the same
    file see each other — a local acquisition succeeds identically whether
    the lock is global or private to this host. Only a second machine can
    settle that, so the finding says so instead of implying the guarantee.
    """
    if ctx.wiki is None:
        return None
    try:
        import fcntl  # noqa: F401 — presence is the platform test
    except ImportError:  # pragma: no cover — Windows
        return _f(
            "filesystem_locking",
            "processes",
            OK,
            "flock unsupported on this platform — skipped",
        )
    tdir = _tesserae_dir(ctx)
    if not tdir.is_dir():
        return None
    fstype = _filesystem_type(tdir)
    named = fstype or "an undetermined filesystem type"
    acquired, detail = _flock_probe(tdir)
    if not acquired:
        return _f(
            "filesystem_locking",
            "processes",
            WARN,
            f"flock(2) was refused on {tdir} ({named}): {detail} — the compile lock and the "
            "engine pidfile handshake protect nothing on this filesystem",
            suggestion="put .tesserae on a filesystem that enforces flock, or serialize compiles yourself",
        )
    if fstype in _NETWORK_FS_TYPES:
        return _f(
            "filesystem_locking",
            "processes",
            WARN,
            f"project is on a network filesystem ({fstype}), where flock(2) is enforced across "
            f"hosts only on some configurations and is a silent no-op on others. The local probe "
            f"succeeded ({detail}), which proves only that THIS host honours it — whether another "
            "machine's lock is visible here cannot be determined from one machine",
            suggestion=(
                "if several machines share this .tesserae, verify flock between two of them "
                "before relying on the compile lock"
            ),
        )
    return _f(
        "filesystem_locking",
        "processes",
        OK,
        f"project is on {named}; flock(2) {detail} here — a single-host probe, which cannot "
        "prove enforcement between machines",
    )


def _detect_optional_deps(ctx: DoctorContext) -> Optional[Finding]:
    from . import deps

    rows = deps.status()
    missing = [row["name"] for row in rows if not row.get("installed")]
    if not missing:
        return _f("optional_deps", "environment", OK, f"all {len(rows)} optional dependencies installed")
    # Informational: optional deps are optional, and installs are networked
    # (never folded into --fix).
    return _f(
        "optional_deps",
        "environment",
        OK,
        f"optional dependencies not installed: {', '.join(missing)}",
        suggestion="tesserae setup",
    )


def _detect_embedding_backend(ctx: DoctorContext) -> Optional[Finding]:
    try:
        info = _embedding_probe()
    except Exception as exc:
        return _f(
            "embedding_backend",
            "environment",
            OK,
            f"embedding backend unavailable: {exc}",
            suggestion="pip install 'tesserae[semantic]'",
        )
    if info.get("semantic"):
        return _f("embedding_backend", "environment", OK, f"semantic embedding backend active: {info['backend']}")
    return _f(
        "embedding_backend",
        "environment",
        OK,
        f"hash-only embedding backend ({info['backend']}) — semantic search degraded",
        suggestion="pip install 'tesserae[semantic]'",
    )


def _parse_built_at(entry: dict) -> Optional[datetime]:
    raw = entry.get("built_at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, _BUILT_AT_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ledger_lines(path: Path) -> tuple[List[str], List[Optional[dict]], Optional[int]]:
    """(raw lines, parsed entries, index of the newest git_head carrier).

    The newest line carrying ``git_head`` feeds the code-graph staleness lint
    (it reads the LAST ``git_head`` in the file), so both detect and fix must
    treat it as permanently kept — detect never counts it stale, fix never
    trims it.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    parsed: List[Optional[dict]] = []
    newest_head_index: Optional[int] = None
    for index, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            parsed.append(None)
            continue
        parsed.append(entry if isinstance(entry, dict) else None)
        if isinstance(entry, dict):
            git_head = entry.get("git_head")
            if isinstance(git_head, str) and git_head:
                newest_head_index = index
    return lines, parsed, newest_head_index


def _detect_build_history(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    path = ctx.wiki.paths.build_history
    if not path.exists():
        return _f("build_history", "hygiene", OK, "no build-history ledger")
    cutoff = _aware(ctx.now) - timedelta(days=BUILD_HISTORY_STALE_DAYS)
    try:
        lines, parsed, newest_head_index = _ledger_lines(path)
    except OSError as exc:
        return _f("build_history", "hygiene", WARN, f"build-history unreadable: {exc}")
    stale = 0
    for index, entry in enumerate(parsed):
        if entry is None or index == newest_head_index:
            continue
        built_at = _parse_built_at(entry)
        if built_at is not None and built_at < cutoff:
            stale += 1
    if stale:
        return _f(
            "build_history",
            "hygiene",
            WARN,
            f"{stale} build-history entries older than {BUILD_HISTORY_STALE_DAYS} days",
            suggestion="tesserae doctor --fix (trims, preserving the newest git_head)",
            fixable=True,
        )
    return _f("build_history", "hygiene", OK, "build history within bounds")


def _fix_build_history(ctx: DoctorContext) -> Optional[str]:
    if ctx.wiki is None:
        return None
    path = ctx.wiki.paths.build_history
    if not path.exists():
        return None
    cutoff = _aware(ctx.now) - timedelta(days=BUILD_HISTORY_STALE_DAYS)
    lines, parsed, newest_head_index = _ledger_lines(path)
    kept: List[str] = []
    trimmed = 0
    for index, line in enumerate(lines):
        entry = parsed[index]
        if entry is None:
            kept.append(line)  # keep unparseable lines — trimming them isn't safe
            continue
        built_at = _parse_built_at(entry)
        if built_at is not None and built_at < cutoff and index != newest_head_index:
            trimmed += 1
            continue
        kept.append(line)
    if not trimmed:
        return None
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return f"build-history: trimmed {trimmed} stale entries (newest git_head preserved)"


def _detect_backend_artifacts(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    stale: List[str] = []
    present = False
    try:
        from .raganything_refresh import RAGA_ROOT, _artifact_is_current as _raga_current

        raga_dir = ctx.project_root / RAGA_ROOT
        if raga_dir.exists():
            present = True
            if not _raga_current(ctx.project_root):
                stale.append("raganything")
    except Exception:
        pass
    if not present:
        return _f("backend_artifacts", "freshness", OK, "no external backend artifacts")
    if stale:
        # Report-only: refreshing is LLM/network heavy.
        return _f(
            "backend_artifacts",
            "freshness",
            WARN,
            f"stale backend artifacts: {', '.join(stale)}",
            suggestion="tesserae integrations refresh " + " / ".join(stale),
        )
    return _f("backend_artifacts", "freshness", OK, "backend artifacts current with git HEAD")


def _detect_idempotence(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    state_path = ctx.wiki.paths.output_snapshot
    if not state_path.exists():
        return _f("idempotence", "hygiene", OK, "no output-snapshot state (snapshot gate has not run)")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _f("idempotence", "hygiene", WARN, f"output-snapshot state unreadable: {exc}")
    from .output_snapshot import snapshot_output

    current = snapshot_output(ctx.wiki.root)
    graph_same = state.get("graph_sha256") == current.graph_sha256
    projections_same = state.get("projections_sha256") == current.projections_sha256
    if graph_same and not projections_same:
        # `idempotence_suspect` itself is compile-time-only (never persisted);
        # this recomputes the same tripwire post-hoc.
        return _f(
            "idempotence",
            "hygiene",
            WARN,
            "graph layer unchanged since last compile but projections drifted — "
            "idempotence suspect (compile determinism bug or manual edits)",
            suggestion="tesserae compile, then diff .tesserae/wiki and .tesserae/site",
        )
    return _f("idempotence", "hygiene", OK, "output snapshot consistent with on-disk artifacts")


def _detect_orphan_worktrees(ctx: DoctorContext) -> Optional[Finding]:
    if not (ctx.project_root / ".git").exists():
        return _f("orphan_worktrees", "hygiene", OK, "not a git repository — worktree check skipped")
    out = _run_git(ctx.project_root, "worktree", "list", "--porcelain")
    if out is None:
        return _f("orphan_worktrees", "hygiene", OK, "git unavailable — worktree check skipped")
    missing: List[str] = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            wt = line[len("worktree "):].strip()
            if wt and not Path(wt).exists():
                missing.append(wt)
    if missing:
        return _f(
            "orphan_worktrees",
            "hygiene",
            WARN,
            f"{len(missing)} orphan git worktrees (paths gone): {', '.join(missing)}",
            suggestion="tesserae doctor --fix (git worktree prune); directory deletion stays manual",
            fixable=True,
        )
    return _f("orphan_worktrees", "hygiene", OK, "no orphan git worktrees")


def _fix_orphan_worktrees(ctx: DoctorContext) -> Optional[str]:
    if _run_git(ctx.project_root, "worktree", "prune") is None:
        return None
    return "git: pruned orphan worktree records (git worktree prune)"


def _oversized_hook_logs(ctx: DoctorContext) -> List[Path]:
    tdir = _tesserae_dir(ctx)
    if not tdir.is_dir():
        return []
    out: List[Path] = []
    for path in sorted(tdir.glob(".*-hook.log")):
        try:
            if path.stat().st_size > HOOK_LOG_CAP_BYTES:
                out.append(path)
        except OSError:
            continue
    return out


def _detect_hook_logs(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    big = _oversized_hook_logs(ctx)
    if not big:
        return _f("hook_log_bloat", "hygiene", OK, "hook logs within bounds")
    described = ", ".join(f"{p.name} ({p.stat().st_size // (1024 * 1024)} MB)" for p in big)
    return _f(
        "hook_log_bloat",
        "hygiene",
        WARN,
        f"hook logs exceed 10 MB: {described}",
        suggestion="tesserae doctor --fix (rotates to <name>.1)",
        fixable=True,
    )


def _fix_hook_logs(ctx: DoctorContext) -> Optional[str]:
    big = _oversized_hook_logs(ctx)
    if not big:
        return None
    for path in big:
        os.replace(path, path.with_name(path.name + ".1"))
    return f"hook logs: rotated {len(big)} oversized log(s) to <name>.1"


def _orphan_tmp_files(ctx: DoctorContext) -> List[Path]:
    """``*.tmp.<pid>.<hex>`` halves of an atomic write nobody is finishing.

    Two guards, and both are load-bearing. The pid must be gone, because a
    LIVE writer is between ``write_text`` and ``replace`` and unlinking its tmp
    file corrupts the write this check exists to clean up after. And the file
    must be older than a day, because ``os.kill(pid, 0)`` answers about the
    local process table only: several hosts can mount one ``.tesserae``, a
    foreign pid can collide with a dead local one, and age is the only signal
    available here that does not depend on which machine is asking.
    """
    from .engine import pidlock
    from .sidecars import tmp_owner_pid

    tdir = _tesserae_dir(ctx)
    if not tdir.is_dir():
        return []
    cutoff = _aware(ctx.now) - timedelta(hours=TMP_ORPHAN_MIN_AGE_HOURS)
    out: List[Path] = []
    for path in sorted(tdir.glob("*.tmp.*")):
        pid = tmp_owner_pid(path.name)
        if pid is None or pid <= 0 or not path.is_file():
            continue
        # Liveness goes through pidlock, the module that already owns this
        # question — including its conservatism rule, which answers "alive"
        # whenever it cannot tell. A second hand-rolled os.kill probe here
        # would be a second place to fix when the answer changes.
        if pidlock.owner_is_alive({"pid": pid}):
            continue  # writer still running: never touch its tmp file
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            out.append(path)
    return out


def _detect_sidecars(ctx: DoctorContext) -> Optional[Finding]:
    """Ownership hygiene over ``.tesserae/`` against the sidecar registry.

    Reports three populations kept separate on purpose: orphaned tmp halves
    (Tesserae's, safe to remove once dead), hand-made ``graph.json.bak-*``
    copies (Tesserae's by name only — no code path writes them, so they are
    described and left), and entries no registry entry claims (someone else's,
    or a new sidecar that skipped registration). Nothing here removes a file
    that carries state: ``safe_to_delete`` is what says which those are, and
    only the tmp orphans have it.
    """
    if ctx.wiki is None:
        return None
    from .sidecars import unclassified_entries

    tdir = _tesserae_dir(ctx)
    orphans = _orphan_tmp_files(ctx)
    backups = sorted(p.name for p in tdir.glob("graph.json.bak-*")) if tdir.is_dir() else []
    unknown = unclassified_entries(tdir)
    parts: List[str] = []
    if orphans:
        parts.append(f"{len(orphans)} orphaned tmp file(s) ({', '.join(p.name for p in orphans[:3])})")
    if backups:
        parts.append(f"{len(backups)} manual graph.json backup(s) ({', '.join(backups[:3])})")
    if unknown:
        parts.append(f"{len(unknown)} unclassified entr{'y' if len(unknown) == 1 else 'ies'} ({', '.join(unknown[:3])})")
    if not parts:
        return _f("sidecars", "hygiene", OK, "every .tesserae entry is registered and no debris")
    suggestion = (
        "tesserae doctor --fix (removes orphaned tmp files only)"
        if orphans
        else "review by hand — doctor never removes state it did not write"
    )
    return _f("sidecars", "hygiene", WARN, "; ".join(parts), suggestion=suggestion, fixable=bool(orphans))


def _fix_sidecars(ctx: DoctorContext) -> Optional[str]:
    # Re-detected at fix time, not reused from detect: a compile can have
    # started in between, and its tmp file must survive this pass.
    orphans = _orphan_tmp_files(ctx)
    if not orphans:
        return None
    for path in orphans:
        path.unlink(missing_ok=True)
    return f"sidecars: removed {len(orphans)} orphaned tmp file(s) (writer pid gone)"


def _detect_vault(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    vault = Path(ctx.wiki.effective_obsidian_vault())
    if vault.is_dir():
        return _f("vault_configured", "core", OK, f"vault directory exists: {vault}")
    try:
        inside = vault.resolve().is_relative_to(ctx.project_root.resolve())
    except (OSError, ValueError):
        inside = False
    if inside:
        # Default in-project vault simply hasn't been created yet — normal
        # pre-first-sync state, but --fix can mkdir it.
        return _f(
            "vault_configured",
            "core",
            OK,
            f"vault directory not created yet: {vault}",
            suggestion="tesserae doctor --fix (mkdir) or tesserae vault sync",
            fixable=True,
        )
    return _f(
        "vault_configured",
        "core",
        WARN,
        f"configured vault does not exist: {vault}",
        suggestion="create the directory, or update obsidian.vault_path in .tesserae/config.json",
    )


def _fix_vault(ctx: DoctorContext) -> Optional[str]:
    if ctx.wiki is None:
        return None
    vault = Path(ctx.wiki.effective_obsidian_vault())
    if vault.is_dir():
        return None
    try:
        inside = vault.resolve().is_relative_to(ctx.project_root.resolve())
    except (OSError, ValueError):
        inside = False
    if not inside:
        return None  # never mkdir outside the project
    vault.mkdir(parents=True, exist_ok=True)
    return f"vault: created {vault}"


def _detect_session_chunks(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    # Lazy import: tesserae/session_chunks.py may not exist yet (it lands in
    # a parallel workstream). Doctor must work with or without it.
    try:
        from . import session_chunks as sc
    except Exception:
        return _f("session_chunks", "freshness", OK, "session-chunk store unavailable (module absent) — skipped")
    try:
        db_path = sc.chunks_db_path(ctx.project_root)
    except Exception:
        db_path = _tesserae_dir(ctx) / "session_chunks.db"
    if not db_path.exists():
        return _f(
            "session_chunks",
            "freshness",
            OK,
            "no session-chunk db yet (engine daemon not running / backfill never run)",
            suggestion="tesserae sessions chunk-backfill",
        )
    try:
        db = sc.SessionChunksDB(db_path)
        days = sorted({str(row["day"]) for row in db.coverage_rows()})
    except Exception as exc:
        return _f(
            "session_chunks",
            "freshness",
            WARN,
            f"session-chunk db unreadable: {exc}",
            suggestion="tesserae sessions chunk-backfill",
        )
    if not days:
        return _f(
            "session_chunks",
            "freshness",
            WARN,
            "session-chunk db has no day coverage — summaries take the slow raw-scan path",
            suggestion="tesserae sessions chunk-backfill",
        )
    try:
        threshold = sc.day_label(_aware(ctx.now) - timedelta(days=SESSION_CHUNK_STALE_DAYS))
    except Exception:
        threshold = None
    if threshold is not None and days[-1] < threshold:
        return _f(
            "session_chunks",
            "freshness",
            WARN,
            f"session-chunk coverage stale — last covered day is {days[-1]}",
            suggestion="tesserae sessions chunk-backfill",
        )
    return _f(
        "session_chunks",
        "freshness",
        OK,
        f"session chunks cover {len(days)} day(s) (through {days[-1]})",
    )


def _detect_environment(ctx: DoctorContext) -> Optional[Finding]:
    try:
        summary = _environment_probe(ctx.project_root)
    except Exception as exc:
        return _f("environment", "environment", OK, f"environment probe unavailable: {exc}")
    return _f("environment", "environment", OK, summary)


# ---------------------------------------------------------------------------
# migrate-code-scope — one-shot cleanup of an already-compiled workspace
# ---------------------------------------------------------------------------
#
# Source code left Tesserae's scope. New compiles no longer mint the code
# layer, but a workspace compiled by an earlier release still carries it:
# measured on this repository, 218,796 of 220,005 markdown_projection pages,
# 1,370 vault pages, a 35 MB ``code-graph.json``, a 291 MB
# ``code-graph-cache.json``, and the bulk of a 1.19 GB ``sqlite.db``.
#
# What is NOT here, because it heals itself on the next compile: the sqlite
# ``nodes`` / ``edges`` tables (``write_graph(replace=True)`` deletes all rows
# first), the provenance sidecars (``reconcile_provenance`` /
# ``prune_provenance_to_graph`` run every compile), the static site (the
# builder rmtrees its output, taking 8.36 GB of code raw pages with it), and
# synthesis ``sources`` frontmatter (computed from a graph that no longer has
# code nodes). Deleting rows does not shrink a SQLite file though, so the
# space those passes free is only RECLAIMED here, by the VACUUM.

# The retired vocabulary as the strings a page's ``type:`` frontmatter
# carries. Derived from ``CODE_GRAPH_TYPES`` and never from a name pattern:
# ``Repository`` and ``Project`` are DOCUMENT types, and a regex over
# "Code|Source|Dependency|Repository|Project" would take 271 vault Repository
# pages — anchors of 1,663 Repository->Session edges — with the code layer.
def _retired_type_values() -> frozenset:
    from .research_graph import CODE_GRAPH_TYPES

    return frozenset(item.value for item in CODE_GRAPH_TYPES)


# Artifacts of the retired layer. ``code-graph.json`` is also unlinked by
# every compile (``ProjectWiki._write_artifacts``); it is repeated here so an
# operator who migrates before recompiling still gets a clean workspace.
_RETIRED_ARTIFACTS = ("code-graph.json", "code-graph-cache.json")

# Frontmatter is a few dozen lines; a page BODY can be megabytes (the largest
# projected page measured 2.5 MB). Read only far enough to close the block.
_FRONTMATTER_LINE_CAP = 200

# How many matched paths a dry run prints. Enough to eyeball what the
# predicate caught, few enough that 218,796 matches stay readable.
_SAMPLE_CAP = 5


def _declared_node_type(path: Path) -> Optional[str]:
    """The node type this page's OWN frontmatter declares, or None.

    None means "this file does not tell us what it is" — no frontmatter, an
    unterminated block, or no ``type:`` key. The migration always KEEPS those.
    Gating on each file's own frontmatter is the whole safety argument: the
    projection's ``concepts/`` directory is 99.45% code-derived and 0.43%
    genuine Concept pages, so a predicate that guessed from the directory,
    the filename or a leftover node id would destroy the survivors and the
    deletion count would look exactly the same either way.
    """
    from .vault_pull import parse_frontmatter

    lines: List[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line.rstrip("\n"))
                # The closing fence — everything the parser needs is in hand.
                if len(lines) > 1 and lines[-1].rstrip() == "---":
                    break
                if len(lines) >= _FRONTMATTER_LINE_CAP:
                    return None
    except OSError:
        return None
    # ``parse_frontmatter`` is the project's one definition of a well-formed
    # block (opens on ``---``, closes on ``---``); reusing it keeps this
    # predicate from drifting away from the renderer that wrote the pages.
    if not parse_frontmatter("\n".join(lines)):
        return None
    # ...but read the type off the FIRST unindented ``type:`` rather than the
    # parsed dict. ``render_node_page`` emits ``type:`` third and then appends
    # the node's metadata keys at the same indent level, so a node carrying a
    # metadata key literally named ``type`` would shadow the real one in a
    # last-write-wins dict — and a survivor shadowed into a code type is a
    # deleted page. No node in the measured corpus does this; the ordering
    # guarantee is free, so take it rather than rely on that staying true.
    for line in lines[1:]:
        if line.rstrip() == "---":
            break
        if line.startswith(("\t", " ")) or not line.startswith("type:"):
            continue
        return line.partition(":")[2].strip()
    return None


@dataclass(frozen=True)
class PageSweep:
    """One directory's worth of the frontmatter sweep.

    ``survivors`` is the number to read first. A predicate bug is invisible
    in ``retired`` — 218,796 deletions and 220,005 deletions look alike — but
    it is glaring in ``survivors``, which must stay at the count of genuine
    non-code pages the directory held before the sweep.
    """

    directory: str
    scanned: int
    retired: int
    survivors: int
    unclassified: int
    kept_with_user_notes: int
    by_type: Dict[str, int]
    sample: List[str]

    def to_dict(self) -> dict:
        return {
            "directory": self.directory,
            "scanned": self.scanned,
            "retired": self.retired,
            "survivors": self.survivors,
            "unclassified": self.unclassified,
            "kept_with_user_notes": self.kept_with_user_notes,
            "by_type": dict(self.by_type),
            "sample": list(self.sample),
        }


@dataclass(frozen=True)
class SqliteSweep:
    """Sidecar rows dropped from ``sqlite.db``, and whether it was VACUUMed."""

    path: str
    bytes_before: int
    bytes_after: int
    deleted_rows: Dict[str, int]
    code_nodes_remaining: int
    vacuumed: bool
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "deleted_rows": dict(self.deleted_rows),
            "code_nodes_remaining": self.code_nodes_remaining,
            "vacuumed": self.vacuumed,
            "note": self.note,
        }


@dataclass(frozen=True)
class CodeScopeMigration:
    """Everything ``migrate_code_scope`` found, or removed under ``apply``."""

    project_root: str
    applied: bool
    sweeps: List[PageSweep] = field(default_factory=list)
    sqlite: Optional[SqliteSweep] = None
    artifacts: List[dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_root": self.project_root,
            "applied": self.applied,
            "sweeps": [sweep.to_dict() for sweep in self.sweeps],
            "sqlite": self.sqlite.to_dict() if self.sqlite else None,
            "artifacts": list(self.artifacts),
            "notes": list(self.notes),
        }


def _sweep_pages(
    directory: Path,
    retired: frozenset,
    *,
    apply: bool,
    respect_user_notes: bool,
) -> PageSweep:
    """Classify every ``*.md`` under ``directory``; delete the code-typed ones.

    ``respect_user_notes`` keeps a code-typed page whose ``<!-- user-notes -->``
    block has content, mirroring ``vault_pull.prune_orphan_pages``. It is on
    for the Obsidian vault, where a human may have written in the append zone
    and no future compile will ever regenerate the page to carry it forward.
    It is off for ``.tesserae/markdown_projection``, which is compile output
    nobody is invited to edit — reading every one of 220k pages in full to
    look for notes that cannot be there costs 383 MB of I/O for nothing.
    """
    scanned = retired_count = survivors = unclassified = kept_notes = 0
    by_type: Dict[str, int] = {}
    sample: List[str] = []
    if not directory.is_dir():
        return PageSweep(str(directory), 0, 0, 0, 0, 0, {}, [])

    for page in sorted(directory.rglob("*.md")):
        rel = page.relative_to(directory)
        # Dot-directories are the user's tooling (``.obsidian/``), never ours.
        if any(part.startswith(".") for part in rel.parts):
            continue
        scanned += 1
        declared = _declared_node_type(page)
        if declared is None:
            unclassified += 1
            continue
        if declared not in retired:
            survivors += 1
            continue
        if respect_user_notes and _has_user_notes(page):
            kept_notes += 1
            continue
        by_type[declared] = by_type.get(declared, 0) + 1
        if len(sample) < _SAMPLE_CAP:
            sample.append(str(rel))
        retired_count += 1
        if apply:
            try:
                page.unlink()
            except OSError:
                continue

    if apply:
        _remove_empty_dirs(directory)
    return PageSweep(
        directory=str(directory),
        scanned=scanned,
        retired=retired_count,
        survivors=survivors,
        unclassified=unclassified,
        kept_with_user_notes=kept_notes,
        by_type=by_type,
        sample=sample,
    )


def _has_user_notes(page: Path) -> bool:
    from .markdown_projection import extract_user_notes

    return bool(extract_user_notes(page))


def _remove_empty_dirs(root: Path) -> None:
    """Bottom-up sweep of directories the deletion emptied."""
    for path in sorted(root.rglob("*"), reverse=True):
        if not path.is_dir():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            continue


# Sidecars keyed on a node id. Both outlive the node: nothing prunes
# ``node_memory`` at all, and ``node_provenance`` is only reconciled by a
# compile, so an operator who migrates first would otherwise VACUUM around
# rows that are already garbage.
_NODE_ID_SIDECARS = ("node_provenance", "node_memory")


def _readonly_sqlite_uri(db_path: Path) -> str:
    """``file:`` URI that opens ``db_path`` read-only.

    The path is percent-encoded: SQLite parses a ``file:`` URI, so a project
    directory containing ``#`` or ``?`` would otherwise truncate the filename
    at that character and open (or fail on) the wrong database.
    """
    from urllib.parse import quote

    return f"file:{quote(str(db_path))}?mode=ro"


def _sqlite_tables(con) -> set:
    return {
        row[0]
        for row in con.execute("select name from sqlite_master where type='table'")
    }


def _sweep_sqlite(db_path: Path, retired: frozenset, *, apply: bool) -> Optional[SqliteSweep]:
    """Drop orphaned sidecar rows, then reclaim the freed pages.

    "Orphaned" is referential: a sidecar row whose node id is absent from
    ``nodes``, or whose (source, type, target) triple is absent from
    ``edges``. Those two tables are the authority precisely because a compile
    rewrites them wholesale — so the intended order is compile, THEN migrate,
    and ``code_nodes_remaining`` reports when that has not happened yet.
    """
    import sqlite3

    if not db_path.exists():
        return None
    bytes_before = db_path.stat().st_size
    deleted: Dict[str, int] = {}
    remaining = 0
    # A dry run opens the store READ-ONLY. Counting rows needs no write
    # handle, and the database being surveyed is the operator's live one —
    # "it only ran SELECTs" is not the same promise as never having asked for
    # a writable connection to a 1.19 GB file.
    connection = (
        sqlite3.connect(db_path)
        if apply
        else sqlite3.connect(_readonly_sqlite_uri(db_path), uri=True)
    )
    with contextlib.closing(connection) as con:
        tables = _sqlite_tables(con)
        if "nodes" in tables:
            placeholders = ",".join("?" * len(retired))
            remaining = int(
                con.execute(
                    f"select count(*) from nodes where type in ({placeholders})",
                    tuple(sorted(retired)),
                ).fetchone()[0]
            )
            for table in _NODE_ID_SIDECARS:
                if table not in tables:
                    continue
                where = "where node_id not in (select id from nodes)"
                count = int(
                    con.execute(f"select count(*) from {table} {where}").fetchone()[0]
                )
                deleted[table] = count
                if apply and count:
                    con.execute(f"delete from {table} {where}")
        if "edges" in tables and "edge_provenance" in tables:
            where = (
                "where not exists (select 1 from edges e"
                " where e.source = edge_provenance.source"
                " and e.type = edge_provenance.type"
                " and e.target = edge_provenance.target)"
            )
            count = int(
                con.execute(
                    f"select count(*) from edge_provenance {where}"
                ).fetchone()[0]
            )
            deleted["edge_provenance"] = count
            if apply and count:
                con.execute(f"delete from edge_provenance {where}")
        if apply:
            con.commit()

    note: Optional[str] = None
    vacuumed = False
    if apply:
        # VACUUM rebuilds the database into a temporary copy, so it needs free
        # space on the order of the file itself and takes an exclusive lock for
        # the duration. That is why it lives behind an explicit command and is
        # never run from a compile — a 1.19 GB rebuild inside the compile path
        # would block every reader and could fail halfway on a full disk.
        import shutil as _shutil

        free = _shutil.disk_usage(db_path.parent).free
        if free < bytes_before:
            note = (
                f"skipped VACUUM: needs ~{bytes_before} bytes free to rebuild,"
                f" {free} available"
            )
        else:
            con = sqlite3.connect(db_path, isolation_level=None)
            try:
                con.execute("vacuum")
                vacuumed = True
            except sqlite3.Error as exc:
                note = f"VACUUM failed: {exc}"
            finally:
                con.close()
    if remaining and note is None:
        note = (
            f"{remaining} code-typed rows still in `nodes` — run `tesserae compile`"
            " first, then re-run this to reclaim what it frees"
        )
    return SqliteSweep(
        path=str(db_path),
        bytes_before=bytes_before,
        bytes_after=db_path.stat().st_size,
        deleted_rows=deleted,
        code_nodes_remaining=remaining,
        vacuumed=vacuumed,
        note=note,
    )


def migrate_code_scope(
    project_root: str | Path, *, apply: bool = False
) -> CodeScopeMigration:
    """Remove the retired code layer from an already-compiled workspace.

    Reports only, unless ``apply`` is set. The default is a dry run because
    every step is a mass delete against directories where the code-derived
    pages outnumber the real ones two hundred to one; an operator gets to
    read the survivor counts before anything is unlinked.
    """
    root = Path(project_root).resolve()
    tdir = root / ".tesserae"
    if not tdir.is_dir():
        raise FileNotFoundError(f"not a Tesserae project: {root}")

    from .project import ProjectWiki

    wiki = ProjectWiki.load(root)
    retired = _retired_type_values()
    notes: List[str] = []

    sweeps = [
        _sweep_pages(
            wiki.paths.markdown_projection,
            retired,
            apply=apply,
            respect_user_notes=False,
        )
    ]
    # Both vault locations, not just the configured one. A project that later
    # pointed `obsidian.vault_path` at a real Obsidian vault leaves the
    # in-project default behind, still full of what the last sync wrote — and
    # that is exactly where this repository's 1,370 code-typed vault pages
    # were found, while the configured vault had none. Deduped on the resolved
    # path so the usual case (they are the same directory) sweeps once.
    seen: set = set()
    for vault in (Path(wiki.effective_obsidian_vault()), wiki.paths.obsidian_vault):
        resolved = vault.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        sweeps.append(
            _sweep_pages(vault, retired, apply=apply, respect_user_notes=True)
        )

    artifacts: List[dict] = []
    for name in _RETIRED_ARTIFACTS:
        artifact = tdir / name
        if not artifact.exists():
            continue
        artifacts.append({"path": str(artifact), "bytes": artifact.stat().st_size})
        if apply:
            try:
                artifact.unlink()
            except OSError as exc:
                notes.append(f"could not remove {artifact}: {exc}")

    sqlite_sweep = _sweep_sqlite(wiki.paths.sqlite, retired, apply=apply)
    if not apply:
        notes.append("dry run — nothing was removed; re-run with --apply")
    return CodeScopeMigration(
        project_root=str(root),
        applied=apply,
        sweeps=sweeps,
        sqlite=sqlite_sweep,
        artifacts=artifacts,
        notes=notes,
    )


def render_code_scope_migration(result: CodeScopeMigration) -> str:
    """Operator-readable summary. Survivor counts lead, per the PageSweep docstring."""
    verb = "removed" if result.applied else "would remove"
    lines = [f"# tesserae doctor migrate-code-scope — {result.project_root}", ""]
    lines.append("mode: apply" if result.applied else "mode: dry run (default)")
    lines.append("")
    for sweep in result.sweeps:
        lines.append(f"## {sweep.directory}")
        lines.append("")
        if not sweep.scanned:
            lines.append("- no pages found")
            lines.append("")
            continue
        lines.append(f"- {sweep.survivors} non-code pages survive (the check)")
        lines.append(f"- {verb} {sweep.retired} code-typed pages of {sweep.scanned} scanned")
        if sweep.by_type:
            detail = ", ".join(
                f"{name} {count}" for name, count in sorted(sweep.by_type.items())
            )
            lines.append(f"  - by type: {detail}")
        for path in sweep.sample:
            lines.append(f"  - e.g. {path}")
        if sweep.kept_with_user_notes:
            lines.append(
                f"- kept {sweep.kept_with_user_notes} code-typed pages carrying"
                " user notes (delete by hand if you want them gone)"
            )
        if sweep.unclassified:
            lines.append(
                f"- left {sweep.unclassified} pages alone: no `type:` frontmatter"
            )
        lines.append("")
    if result.artifacts:
        lines.append("## Artifacts")
        lines.append("")
        for entry in result.artifacts:
            lines.append(f"- {verb} {entry['path']} ({entry['bytes']} bytes)")
        lines.append("")
    if result.sqlite:
        sweep = result.sqlite
        lines.append("## sqlite.db")
        lines.append("")
        for table, count in sorted(sweep.deleted_rows.items()):
            lines.append(f"- {verb} {count} orphaned rows from {table}")
        if sweep.vacuumed:
            reclaimed = sweep.bytes_before - sweep.bytes_after
            lines.append(f"- VACUUM reclaimed {reclaimed} bytes")
        elif not result.applied:
            lines.append(f"- would VACUUM ({sweep.bytes_before} bytes today)")
        if sweep.note:
            lines.append(f"- note: {sweep.note}")
        lines.append("")
    for note in result.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines).rstrip() + "\n"


def code_scope_migration_json(result: CodeScopeMigration) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"


def _detect_code_scope_leftovers(ctx: DoctorContext) -> Optional[Finding]:
    """Surface a pre-drop workspace, and name the command that clears it.

    Deliberately cheap: two ``stat`` calls and one indexed count. Doctor runs
    often, and the sweep this points at walks 225k files.

    Report-only, never ``fixable``. ``--fix`` is documented as safe repairs
    only, and this deletes hundreds of thousands of pages and rebuilds a
    multi-gigabyte database — it has to be asked for by name.
    """
    if ctx.wiki is None:
        return None
    import sqlite3

    leftovers: List[str] = []
    tdir = _tesserae_dir(ctx)
    for name in _RETIRED_ARTIFACTS:
        artifact = tdir / name
        if artifact.exists():
            leftovers.append(f"{name} ({artifact.stat().st_size // 1_000_000} MB)")
    db_path = Path(ctx.wiki.paths.sqlite)
    if db_path.exists():
        retired = _retired_type_values()
        placeholders = ",".join("?" * len(retired))
        try:
            # Read-only URI: a plain connect() would CREATE the file, and a
            # doctor run without --fix must leave the tree byte-identical.
            with sqlite3.connect(_readonly_sqlite_uri(db_path), uri=True) as con:
                row = con.execute(
                    f"select 1 from nodes where type in ({placeholders}) limit 1",
                    tuple(sorted(retired)),
                ).fetchone()
            if row is not None:
                leftovers.append("code-typed rows in sqlite.db")
        except sqlite3.Error:
            pass
    if not leftovers:
        return _f(
            "code_scope_leftovers", "hygiene", OK, "no retired code-layer artifacts"
        )
    return _f(
        "code_scope_leftovers",
        "hygiene",
        WARN,
        "workspace still carries the retired code layer: " + ", ".join(leftovers),
        suggestion="tesserae doctor migrate-code-scope   (dry run; add --apply)",
    )


# ---------------------------------------------------------------------------
# registry of checks (data)
# ---------------------------------------------------------------------------

CHECKS: List[Check] = [
    Check("project_initialized", "core", _detect_project_initialized),
    Check("graph_parse", "core", _detect_graph_parse),
    Check("config_valid", "core", _detect_config_valid),
    Check("registry_consistent", "registry", _detect_registry_consistent, fix=_fix_registry_consistent, safe=True),
    Check("graph_staleness", "freshness", _detect_graph_staleness),
    Check("site_search_index", "freshness", _detect_site_stale, fix=_fix_site_stale, safe=True),
    Check("wiki_lint", "graph", _detect_wiki_lint, fix=_fix_wiki_lint, safe=True),
    Check("compile_lock", "processes", _detect_compile_lock),  # report-only, NEVER kill
    Check("filesystem_locking", "processes", _detect_filesystem_locking),  # read-only probe
    Check("daemon_pid", "processes", _detect_daemon_pid, fix=_fix_daemon_pid, safe=True),
    Check("llm_login", "environment", _detect_llm_login),
    Check("optional_deps", "environment", _detect_optional_deps),
    Check("embedding_backend", "environment", _detect_embedding_backend),
    Check("build_history", "hygiene", _detect_build_history, fix=_fix_build_history, safe=True),
    Check("backend_artifacts", "freshness", _detect_backend_artifacts),
    Check("code_scope_leftovers", "hygiene", _detect_code_scope_leftovers),
    Check("idempotence", "hygiene", _detect_idempotence),
    Check("orphan_worktrees", "hygiene", _detect_orphan_worktrees, fix=_fix_orphan_worktrees, safe=True),
    Check("hook_log_bloat", "hygiene", _detect_hook_logs, fix=_fix_hook_logs, safe=True),
    Check("sidecars", "hygiene", _detect_sidecars, fix=_fix_sidecars, safe=True),
    Check("vault_configured", "core", _detect_vault, fix=_fix_vault, safe=True),
    Check("session_chunks", "freshness", _detect_session_chunks),
    Check("environment", "environment", _detect_environment),
]


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _safe_detect(check: Check, ctx: DoctorContext) -> Finding:
    try:
        finding = check.detect(ctx)
    except Exception as exc:  # noqa: BLE001 — a crashing check is a finding, never an exception
        return _f(check.id, check.category, ERROR, f"check crashed: {exc!r}")
    if finding is None:
        return _f(check.id, check.category, OK, "not applicable")
    return finding


def run_doctor(
    project_root: str | Path,
    fix: bool = False,
    *,
    registry_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    checks: Optional[List[Check]] = None,
) -> DoctorReport:
    """Run every check against ``project_root`` and return a DoctorReport.

    ``fix=False`` (the default) is guaranteed read-only. ``fix=True`` applies
    only the fixes on checks marked ``safe=True``, then re-detects so the
    report reflects the post-fix state. Exit codes: 0 healthy / 1 warnings /
    2 errors.
    """
    root = Path(project_root).resolve()
    ctx = DoctorContext(
        project_root=root,
        wiki=_load_wiki(root),
        registry=_load_registry(registry_path),
        now=_aware(now if now is not None else datetime.now(tz=timezone.utc)),
    )
    report = DoctorReport(project_root=str(root), checked_at=ctx.now.isoformat())
    for check in checks if checks is not None else CHECKS:
        finding = _safe_detect(check, ctx)
        if fix and check.safe and check.fix is not None and finding.fixable:
            try:
                applied = check.fix(ctx)
            except Exception as exc:  # noqa: BLE001 — a crashing fix is a finding too
                report.findings.append(
                    _f(check.id, check.category, ERROR, f"fix crashed: {exc!r}")
                )
                continue
            if applied:
                report.fixed.append(f"{check.id}: {applied}")
                finding = _safe_detect(check, ctx)  # re-detect: report the post-fix state
        report.findings.append(finding)
    worst = max((_SEVERITY_RANK.get(f.severity, 2) for f in report.findings), default=0)
    report.exit_code = worst
    return report


def run_doctor_all(
    registry,
    fix: bool = False,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, DoctorReport]:
    """Doctor every registered project. ``registry`` is a ProjectRegistry (or
    a registry path). Returns ``{alias: DoctorReport}``; overall exit code is
    ``max(report.exit_code)`` (0 for an empty registry)."""
    if registry is None or isinstance(registry, (str, Path)):
        registry = _load_registry(Path(registry) if registry is not None else None)
    reports: Dict[str, DoctorReport] = {}
    if registry is None:
        return reports
    try:
        pairs = list(registry.iter_registered_projects())
    except Exception:
        return reports
    for alias, project_root in pairs:
        reports[alias] = run_doctor(
            project_root, fix, registry_path=Path(registry.path), now=now
        )
    return reports


def overall_exit_code(reports: Dict[str, DoctorReport]) -> int:
    return max((report.exit_code for report in reports.values()), default=0)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

_GLYPHS = {OK: "✓", WARN: "!", ERROR: "✗"}


def render_markdown(report: DoctorReport) -> str:
    lines: List[str] = []
    lines.append(f"# tesserae doctor — {report.project_root}")
    lines.append("")
    lines.append(f"checked at: {report.checked_at}")
    counts = {OK: 0, WARN: 0, ERROR: 0}
    for finding in report.findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    lines.append(
        f"result: {counts[OK]} ok, {counts[WARN]} warnings, {counts[ERROR]} errors "
        f"(exit {report.exit_code})"
    )
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    for finding in report.findings:
        glyph = _GLYPHS.get(finding.severity, "?")
        line = f"- [{glyph}] **{finding.check_id}** ({finding.category}): {finding.message}"
        lines.append(line)
        if finding.suggestion:
            lines.append(f"  - suggested: `{finding.suggestion}`")
    if report.fixed:
        lines.append("")
        lines.append("## Fixed this run")
        lines.append("")
        for entry in report.fixed:
            lines.append(f"- {entry}")
    lines.append("")
    return "\n".join(lines)


def to_json(report: DoctorReport) -> str:
    payload = {
        "project_root": report.project_root,
        "checked_at": report.checked_at,
        "exit_code": report.exit_code,
        "fixed": list(report.fixed),
        "findings": [finding.to_dict() for finding in report.findings],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_report(project_root: str | Path, report: DoctorReport) -> Dict[str, str]:
    """Write ``.tesserae/doctor-report.md`` + ``.json``; returns their paths.

    An explicit, separate step (the CLI calls it) so :func:`run_doctor` with
    ``fix=False`` stays byte-level read-only.
    """
    tdir = Path(project_root) / ".tesserae"
    tdir.mkdir(parents=True, exist_ok=True)
    md_path = tdir / "doctor-report.md"
    json_path = tdir / "doctor-report.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(to_json(report), encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}
