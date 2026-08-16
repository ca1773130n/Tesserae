"""Runs the Neo4j agent-memory TCK against :class:`TesseraeAdapter`.

    # everything the kit collects, against Tesserae
    uv run python -m evals.tck.run_tck

    # one tier
    uv run python -m evals.tck.run_tck --tier bronze

    # keep the assembled tree to inspect what actually ran
    uv run python -m evals.tck.run_tck --tier bronze --keep-tree

Prerequisites, in the order they are checked, each a SKIP with the command that
satisfies it rather than a traceback — the ``evals/qa/run_qa_eval.py`` posture,
for the same reason: a harness that fails loudly on a missing optional input
gets wired into CI by someone trying to make the build green.

1. **CI.** ``CI`` set in the environment prints SKIP and exits 0. This one is
   cheap, offline and model-free, so the reason is not cost — it is that the
   kit is a gitignored clone pinned to a commit, and a green CI job that
   silently skipped is worse than no job.
2. **The clone.** ``neo4j-agent-memory-tck`` is not on PyPI. See
   :mod:`evals.tck.vendor_tck`.

**What this runner does not do.** It reports pytest's own pass/fail/skip counts
and stops there. It does not call the kit's ``compliance_report`` tier
calculator, which scores a tier as achieved when ``total - skipped == 0`` — an
implementation that skips every scenario is scored Gold by it. A number this
repo would publish cannot come from a calculator with that property.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from .vendor_tck import (
    MissingPrerequisite,
    REPO_ROOT,
    TCK_CLONE,
    TCK_PINNED_COMMIT,
    require_tck,
)

TIERS = ("bronze", "silver", "gold", "platinum")

#: Written into the assembled tree. The kit sets these in its own
#: ``pyproject.toml``; the tree lives outside it, so without this file pytest
#: finds no async mode and every scenario errors on an un-awaited coroutine.
PYTEST_INI = """\
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = session
markers =
    bronze: Bronze tier - schema and short-term memory
    silver: Silver tier - all three memory types
    gold: Gold tier - SHOULD clauses and cross-memory integration
    platinum: Platinum tier - Volume 5 hosted-service operations
"""


def build_run_tree(destination: Path) -> Path:
    """Assemble a runnable copy of the kit's tests with our adapter wired in.

    The kit's ``tck/tests/`` is copied rather than run in place, and our
    ``conftest.py`` is written over ``tests/v1/conftest.py`` — the path whose
    upstream contents wire ``ReferenceAdapter``, and the nearest conftest to the
    scenarios, so it is the one that wins. The clone stays pristine.
    """
    source = TCK_CLONE / "tck" / "tests"
    if not source.is_dir():
        raise MissingPrerequisite(
            f"the kit's test tree is not at {source}",
            "re-clone: see evals/tck/README.md",
        )
    tests = destination / "tests"
    if tests.exists():
        shutil.rmtree(tests)
    shutil.copytree(source, tests, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(Path(__file__).resolve().parent / "conftest.py", tests / "v1" / "conftest.py")
    (destination / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
    return destination


def pytest_argv(tree: Path, tier: Optional[str], extra: List[str]) -> List[str]:
    argv = [sys.executable, "-m", "pytest", str(tree / "tests"), "-p", "no:cacheprovider"]
    if tier:
        argv += ["-m", tier]
    return argv + extra


def clone_commit() -> str:
    """The clone's HEAD, so a recorded result names the code that produced it."""
    try:
        out = subprocess.run(
            ["git", "-C", str(TCK_CLONE), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier",
        choices=TIERS,
        default=None,
        help="run one tier's scenarios; omit to run everything the kit collects",
    )
    parser.add_argument(
        "--keep-tree",
        action="store_true",
        help="leave the assembled tree on disk and print its path",
    )
    parser.add_argument(
        "--tree",
        default=None,
        help="assemble into this directory instead of a fresh temporary one",
    )
    args, extra = parser.parse_known_args(argv)

    if os.environ.get("CI"):
        print(
            "SKIP: CI is set — the agent-memory TCK never runs in CI\n"
            "      it needs a gitignored clone pinned to a commit; a job that "
            "silently skipped is worse than no job"
        )
        return 0

    try:
        require_tck()
    except MissingPrerequisite as missing:
        print(missing.skip_line())
        return 0

    head = clone_commit()
    if head != TCK_PINNED_COMMIT:
        print(
            f"NOTE: the clone is at {head[:12]}, not the pinned "
            f"{TCK_PINNED_COMMIT[:12]} this adapter was measured against. "
            "Scenario counts and assertions may differ."
        )

    destination = Path(args.tree) if args.tree else Path(tempfile.mkdtemp(prefix="tesserae-tck-run-"))
    destination.mkdir(parents=True, exist_ok=True)
    tree = build_run_tree(destination)

    env = dict(os.environ)
    # The copied conftest imports evals.tck.adapter, so the repo root has to be
    # importable from a tree that lives outside it.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    print(f"TCK clone   : {TCK_CLONE} @ {head[:12]}")
    print(f"run tree    : {tree}")
    print(f"tier        : {args.tier or 'all collected'}")
    completed = subprocess.run(pytest_argv(tree, args.tier, extra), env=env)

    if args.keep_tree:
        print(f"\nassembled tree kept at {tree}")
    elif not args.tree:
        shutil.rmtree(destination, ignore_errors=True)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
