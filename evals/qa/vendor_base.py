"""Loads the system-agnostic QA benchmark ABC out of the vendored cognee clone.

``evals/cognee/evals/src/qa/qa_benchmark_base.py`` already defines the contract
four competitors are driven through — ``initialize_rag`` / ``insert_document``
/ ``query_rag`` / ``cleanup_rag`` plus ``from_jsons``. Nothing in it assumes
cognee, so Tesserae subclasses it rather than growing a fifth harness.

The clone is **gitignored, 877 MB, and carries uncommitted local work** (see
AGENTS.md). It is therefore absent on a fresh checkout and on CI, and it is
never modified from here — this module only reads one file out of it.

Loaded by explicit file path rather than by putting the clone's ``src/`` on
``sys.path``: the module we want has no relative imports of its own, and a bare
``qa`` package name on ``sys.path`` would shadow *our* ``evals.qa`` for anything
importing later.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Tuple

#: Repo root — evals/qa/vendor_base.py → evals/qa → evals → root.
REPO_ROOT = Path(__file__).resolve().parents[2]
VENDORED_BASE = (
    REPO_ROOT / "evals" / "cognee" / "evals" / "src" / "qa" / "qa_benchmark_base.py"
)
CORPUS_JSON = REPO_ROOT / "evals" / "cognee" / "evals" / "src" / "hotpot_qa_24_corpus.json"
QA_PAIRS_JSON = REPO_ROOT / "evals" / "cognee" / "evals" / "src" / "hotpot_qa_24_qa_pairs.json"

#: Module name under which the vendored ABC is registered. Namespaced so it can
#: never collide with a real top-level ``qa``.
_MODULE_NAME = "evals_qa_vendored_benchmark_base"


class MissingPrerequisite(RuntimeError):
    """A prerequisite for RUNNING the benchmark is absent.

    Carries the exact command that satisfies it, so the runner's SKIP line can
    tell the operator what to do instead of only what failed.
    """

    def __init__(self, what: str, remedy: str) -> None:
        super().__init__(what)
        self.what = what
        self.remedy = remedy

    def skip_line(self) -> str:
        return f"SKIP: {self.what}\n      {self.remedy}"


def load_base_module() -> ModuleType:
    """Import the vendored ``qa_benchmark_base`` module, or raise."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    if not VENDORED_BASE.is_file():
        raise MissingPrerequisite(
            f"vendored QA benchmark base not found at {VENDORED_BASE}",
            "clone it: git clone https://github.com/topoteretes/cognee evals/cognee",
        )
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, VENDORED_BASE)
    if spec is None or spec.loader is None:
        raise MissingPrerequisite(
            f"could not build an import spec for {VENDORED_BASE}",
            "check the file is readable",
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:  # dotenv / tqdm
        del sys.modules[_MODULE_NAME]
        raise MissingPrerequisite(
            f"the vendored QA benchmark base needs a dependency it cannot import: {exc}",
            "uv sync --python 3.11 --all-extras",
        ) from exc
    return module


def load_qa_benchmark_base() -> Tuple[Any, Any]:
    """``(QABenchmarkRAG, QABenchmarkConfig)`` from the vendored clone."""
    module = load_base_module()
    return module.QABenchmarkRAG, module.QABenchmarkConfig


__all__ = [
    "CORPUS_JSON",
    "MissingPrerequisite",
    "QA_PAIRS_JSON",
    "REPO_ROOT",
    "VENDORED_BASE",
    "load_base_module",
    "load_qa_benchmark_base",
]
