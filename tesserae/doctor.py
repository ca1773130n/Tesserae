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
  pid only. Doctor never kills or removes it (recorded failure mode:
  SessionEnd compile pile-ups).

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
    "DoctorContext",
    "DoctorReport",
    "Finding",
    "CHECKS",
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


# ---------------------------------------------------------------------------
# monkeypatchable probes (kept module-level so tests can pin them)
# ---------------------------------------------------------------------------


def _llm_login_status() -> Dict[str, Optional[bool]]:
    """{'claude': True|False|None, 'codex': ...} — credentialed-CLI probe.

    Reuses ``setup.detection._probe_credentials`` which defers to
    ``llm_json._claude_cli_available`` / ``_codex_cli_available``.
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
    try:
        import fcntl
    except ImportError:  # pragma: no cover — Windows
        return _f("compile_lock", "processes", OK, "flock unsupported on this platform — skipped")
    try:
        handle = lock_path.open("r+", encoding="utf-8")  # never create, never truncate
    except OSError as exc:
        return _f("compile_lock", "processes", WARN, f"compile.lock unreadable: {exc}")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            holder = ""
            try:
                handle.seek(0)
                pid = handle.read().strip()
                if pid:
                    holder = f" by pid {pid}"
            except OSError:
                pass
            # NEVER kill or remove a live compile lock (SessionEnd pile-up
            # failure mode). Report the holder and stand down.
            return _f(
                "compile_lock",
                "processes",
                WARN,
                f"a compile/refresh is running (lock held{holder}) — doctor will not touch it",
                suggestion="wait for the running compile to finish",
            )
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return _f("compile_lock", "processes", OK, "compile.lock present but not held")
    finally:
        handle.close()


def _detect_daemon_pid(ctx: DoctorContext) -> Optional[Finding]:
    if ctx.wiki is None:
        return None
    pidfile = _tesserae_dir(ctx) / "daemon.pid"
    if not pidfile.exists():
        return _f("daemon_pid", "processes", OK, "no engine daemon pidfile")
    from .engine import pidlock

    owner = pidlock.read_owner(pidfile)
    if owner is not None and pidlock.owner_is_alive(owner):
        return _f("daemon_pid", "processes", OK, f"engine daemon running (pid {owner['pid']})")
    return _f(
        "daemon_pid",
        "processes",
        WARN,
        "stale daemon.pid — recorded owner is not running",
        suggestion="tesserae doctor --fix (removes the stale pidfile)",
        fixable=True,
    )


def _fix_daemon_pid(ctx: DoctorContext) -> Optional[str]:
    pidfile = _tesserae_dir(ctx) / "daemon.pid"
    if not pidfile.exists():
        return None
    from .engine import pidlock

    owner = pidlock.read_owner(pidfile)
    if owner is not None and pidlock.owner_is_alive(owner):
        return None  # re-check at fix time: never remove a live daemon's pidfile
    pidfile.unlink(missing_ok=True)
    return "daemon.pid: removed stale pidfile (owner dead)"


def _detect_llm_login(ctx: DoctorContext) -> Optional[Finding]:
    status = _llm_login_status()
    logged_in = sorted(name for name, value in status.items() if value is True)
    if logged_in:
        return _f("llm_login", "environment", OK, f"credentialed LLM CLI: {', '.join(logged_in)}")
    return _f(
        "llm_login",
        "environment",
        WARN,
        "no credentialed LLM CLI detected — LLM-backed features degrade to no-LLM paths",
        suggestion="claude /login  or  codex login",
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
    Check("daemon_pid", "processes", _detect_daemon_pid, fix=_fix_daemon_pid, safe=True),
    Check("llm_login", "environment", _detect_llm_login),
    Check("optional_deps", "environment", _detect_optional_deps),
    Check("embedding_backend", "environment", _detect_embedding_backend),
    Check("build_history", "hygiene", _detect_build_history, fix=_fix_build_history, safe=True),
    Check("backend_artifacts", "freshness", _detect_backend_artifacts),
    Check("idempotence", "hygiene", _detect_idempotence),
    Check("orphan_worktrees", "hygiene", _detect_orphan_worktrees, fix=_fix_orphan_worktrees, safe=True),
    Check("hook_log_bloat", "hygiene", _detect_hook_logs, fix=_fix_hook_logs, safe=True),
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
