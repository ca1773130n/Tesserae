"""Locates the Neo4j agent-memory TCK, which is not on PyPI.

``neo4j-agent-memory-tck`` declares ``name`` and ``version`` in its
``pyproject.toml`` but has never been published to any index — ``uv pip install
neo4j-agent-memory-tck`` fails with "not found in the package registry". A git
clone is the only install path, so this module treats the clone the same way
``evals/qa/vendor_base.py`` treats the vendored cognee checkout: an optional,
gitignored prerequisite whose absence is a SKIP with the command that fixes it,
never an import error at collection time.

Nothing here imports the TCK. It only reports whether the TCK *can* be
imported, so :mod:`evals.tck.adapter` can define its Tesserae-backed core
without the kit installed and ``tests/test_tck_adapter.py`` can exercise that
core on a checkout that has never cloned anything.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

#: Repo root — evals/tck/vendor_tck.py → evals/tck → evals → root.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where the runner expects the clone. Gitignored by the ``evals/*`` rule that
#: already covers the cognee and MegaMem checkouts.
TCK_CLONE = REPO_ROOT / "evals" / "agent-memory-tck"

TCK_REPO_URL = "https://github.com/neo4j-labs/agent-memory-tck.git"

#: The commit this adapter was written and measured against. The TCK publishes
#: no tags, so a commit is the only pin available; a later commit may move
#: scenario counts and invalidate the recorded result.
TCK_PINNED_COMMIT = "4603b91f4fc831f19901b4f68d96f8dc039e9a38"

TCK_VERSION = "1.0.0"

CLONE_COMMAND = f"git clone {TCK_REPO_URL} {TCK_CLONE.relative_to(REPO_ROOT)}"
INSTALL_COMMAND = f"uv pip install -e {TCK_CLONE.relative_to(REPO_ROOT)}"


class MissingPrerequisite(RuntimeError):
    """A prerequisite for RUNNING the TCK is absent.

    Carries the exact command that satisfies it, so a runner's SKIP line can
    say what to do rather than only what failed. Same contract as
    ``evals.qa.vendor_base.MissingPrerequisite``.
    """

    def __init__(self, what: str, remedy: str) -> None:
        super().__init__(what)
        self.what = what
        self.remedy = remedy

    def skip_line(self) -> str:
        return f"SKIP: {self.what}\n      {self.remedy}"


def tck_is_importable() -> bool:
    """True when ``import tck.adapters.base_adapter`` would succeed."""
    return importlib.util.find_spec("tck.adapters.base_adapter") is not None


def require_tck() -> None:
    """Raise :class:`MissingPrerequisite` unless the TCK is importable."""
    if tck_is_importable():
        return
    if not TCK_CLONE.is_dir():
        raise MissingPrerequisite(
            f"the agent-memory TCK is not cloned at {TCK_CLONE}",
            f"{CLONE_COMMAND} && {INSTALL_COMMAND}",
        )
    raise MissingPrerequisite(
        f"the agent-memory TCK is cloned at {TCK_CLONE} but not importable",
        INSTALL_COMMAND,
    )


__all__ = [
    "CLONE_COMMAND",
    "INSTALL_COMMAND",
    "MissingPrerequisite",
    "REPO_ROOT",
    "TCK_CLONE",
    "TCK_PINNED_COMMIT",
    "TCK_REPO_URL",
    "TCK_VERSION",
    "require_tck",
    "tck_is_importable",
]
