"""Run one per-project operation across every registered project.

``compile``/``refresh`` operate on a single project resolved from ``--project``,
which is fine on a laptop and useless on a machine that hosts several projects:
keeping them fresh meant a shell loop that stopped at the first failure and told
you nothing about the rest. This module is the shared spine for ``--all``.

Three properties make a batch trustworthy, and they are the whole reason this
exists rather than a ``for`` loop at each call site:

* **Per-project failure isolation.** One project raising must not abort the
  others. The same defect existed in the fleet daemon's once-mode.
* **A distinguishable "someone else is compiling" outcome.** A project whose
  compile lock is held by a background engine has not failed — the batch just
  did not get to it. Reporting that as an error trains people to ignore the
  exit code.
* **Concurrency that defaults to one.** A compile is LLM-heavy; running seven at
  once burns quota in parallel and, on a shared disk, has seven hosts' worth of
  contention to lose to as well.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

#: A project that finished its work.
OK = "ok"
#: A project whose compile lock was held by someone else — not a failure.
LOCKED = "locked"
#: A project that raised.
FAILED = "failed"


def resolve_projects(project_names: Optional[Sequence[str]] = None) -> List[Tuple[str, Path]]:
    """Resolve the projects to operate on: all registered, or the named subset.

    Default scope is every registered project
    (``ProjectRegistry.iter_registered_projects()``). ``project_names`` opts into
    a subset (order preserved as registered). Unknown names raise ``ValueError``
    — a typo must error, not silently mean "no projects". ``mcp_server`` is
    imported lazily so this module stays importable from ``mcp_server`` without
    a cycle.
    """
    from tesserae.mcp_server import ProjectRegistry

    registered = list(ProjectRegistry().iter_registered_projects())
    if not project_names:
        return registered
    wanted = set(project_names)
    known = {name for name, _root in registered}
    unknown = [n for n in project_names if n not in known]
    if unknown:
        available = ", ".join(sorted(known)) or "(none registered)"
        raise ValueError(
            f"unknown project name(s): {', '.join(unknown)}. "
            f"Available: {available} — see `tesserae projects list`."
        )
    return [(name, root) for name, root in registered if name in wanted]


@dataclass
class ProjectOutcome:
    """What happened to one project in a batch."""

    name: str
    root: Path
    status: str
    detail: str = ""
    output: str = ""
    #: Whatever ``work`` returned, for callers that want to aggregate numbers.
    result: object = field(default=None)

    @property
    def ok(self) -> bool:
        return self.status == OK


def run_across_projects(
    targets: Sequence[Tuple[str, Path]],
    work: Callable[[str, Path], object],
    jobs: int = 1,
) -> List[ProjectOutcome]:
    """Run ``work(name, root)`` for every target, isolating failures.

    ``work`` signals failure by raising. A :class:`~tesserae.locking.CompileLockHeldError`
    is recorded as :data:`LOCKED` rather than :data:`FAILED`, because another
    process holding the lock is the system working as designed.

    With ``jobs == 1`` the work runs sequentially and its output streams live,
    which is what a human watching a terminal wants. Above that, each project's
    stdout is buffered and replayed whole when it finishes, because interleaving
    seven compiles' progress lines produces something nobody can read. Buffering
    only above 1 keeps the common path free of the capture entirely.
    """
    from .locking import CompileLockHeldError

    ordered = list(targets)
    if not ordered:
        return []

    def _run_one(name: str, root: Path, capture: bool) -> ProjectOutcome:
        buffer = io.StringIO()
        try:
            if capture:
                with redirect_stdout(buffer):
                    result = work(name, root)
            else:
                result = work(name, root)
        except CompileLockHeldError as exc:
            return ProjectOutcome(name, root, LOCKED, str(exc), buffer.getvalue())
        except Exception as exc:  # noqa: BLE001 — isolation is the point
            return ProjectOutcome(name, root, FAILED, f"{type(exc).__name__}: {exc}", buffer.getvalue())
        return ProjectOutcome(name, root, OK, "", buffer.getvalue(), result)

    if jobs <= 1:
        outcomes = []
        for name, root in ordered:
            print(f"\n=== {name} ({root}) ===")
            outcomes.append(_run_one(name, root, capture=False))
        return outcomes

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(_run_one, name, root, True) for name, root in ordered]
        outcomes = [f.result() for f in futures]
    for outcome in outcomes:
        print(f"\n=== {outcome.name} ({outcome.root}) ===")
        if outcome.output:
            sys.stdout.write(outcome.output)
    return outcomes


def render_outcomes(outcomes: Sequence[ProjectOutcome]) -> str:
    """A compact end-of-run table. The reason a batch is worth running."""
    if not outcomes:
        return "No projects registered — nothing to do. Register one with: tesserae projects register <path>"
    width = max(len(o.name) for o in outcomes)
    lines = ["", f"{'project'.ljust(width)}  status   detail"]
    for outcome in outcomes:
        detail = outcome.detail
        if len(detail) > 96:
            detail = detail[:93] + "..."
        lines.append(f"{outcome.name.ljust(width)}  {outcome.status.ljust(7)}  {detail}")
    counts = {
        OK: sum(1 for o in outcomes if o.status == OK),
        LOCKED: sum(1 for o in outcomes if o.status == LOCKED),
        FAILED: sum(1 for o in outcomes if o.status == FAILED),
    }
    lines.append(
        f"\n{counts[OK]} ok, {counts[LOCKED]} locked, {counts[FAILED]} failed "
        f"({len(outcomes)} project(s))"
    )
    return "\n".join(lines)


def exit_code_for(outcomes: Sequence[ProjectOutcome]) -> int:
    """0 = everything ran, 1 = something was locked, 2 = something failed.

    Mirrors the ``doctor``/``lint`` mapping already in this CLI (errors → 2,
    warnings → 1) so a caller does not have to learn a second convention. A
    locked project is a "come back later", not a defect, so it must not share an
    exit code with a traceback.
    """
    if any(o.status == FAILED for o in outcomes):
        return 2
    if any(o.status == LOCKED for o in outcomes):
        return 1
    return 0
