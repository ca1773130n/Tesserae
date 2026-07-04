"""Direct Cognee ingestion helper for Tesserae export bundles."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

CogneeAdd = Callable[..., Awaitable[object]]
CogneeCognify = Callable[..., Awaitable[object]]
CogneeConfigure = Callable[..., None]


class CogneeDirectImporter:
    """Add a generated Cognee JSONL bundle to Cognee.

    `cognify` is optional because it may invoke configured LLM/embedding providers.
    For cost-aware operation, the default is add-only ingestion of the explicit
    Tesserae JSONL records.
    """

    def __init__(self, add_func: Optional[CogneeAdd] = None, cognify_func: Optional[CogneeCognify] = None, configure_func: Optional[CogneeConfigure] = None) -> None:
        self.add_func = add_func
        self.cognify_func = cognify_func
        self.configure_func = configure_func

    async def add_bundle(self, bundle_dir: str | Path, dataset_name: str = "tesserae_research_graph", cognify: bool = False, system_root: str | Path | None = None, data_root: str | Path | None = None) -> Dict[str, object]:
        root = Path(bundle_dir)
        files = [root / "nodes.jsonl", root / "edges.jsonl"]
        missing = [str(path) for path in files if not path.exists()]
        if missing:
            raise FileNotFoundError(f"Cognee bundle missing required files: {missing}")
        if system_root or data_root:
            configure_func = self.configure_func or configure_cognee_roots
            configure_func(system_root=system_root, data_root=data_root)
        add_func = self.add_func or import_cognee_add()
        await add_func([str(path) for path in files], dataset_name=dataset_name)
        cognify_result = None
        if cognify:
            cognify_func = self.cognify_func or import_cognee_cognify()
            cognify_result = await cognify_func(datasets=[dataset_name])
        result = {
            "dataset_name": dataset_name,
            "files_added": len(files),
            "cognified": bool(cognify),
        }
        if cognify:
            result["cognify_result"] = cognify_result
        return result


def _installed_cognee_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version("cognee")
    except Exception:  # noqa: BLE001 — unknown version → skip the guard
        return None


# Artifacts a real cognee system store contains. We refuse to delete a
# ``databases`` dir that doesn't hold at least one of these — so a user-supplied
# ``system_root`` that merely happens to have a ``databases`` child is never
# wiped by mistake.
_COGNEE_STORE_MARKERS = (
    "cognee_db",
    "cognee.lancedb",
    "cognee.graph",
    "cognee_db__main_staging",
)


def _looks_like_cognee_store(databases: Path) -> bool:
    try:
        return any((databases / marker).exists() for marker in _COGNEE_STORE_MARKERS)
    except OSError:
        return False


def reset_stale_cognee_system_db(system_root: str | Path) -> bool:
    """Drop the cognee system DBs if they were created by a different cognee version.

    cognee does NOT migrate its own relational schema across upgrades — e.g. 1.x
    added ``users.tenant_id``, so a ``system_root`` dir seeded by an older cognee
    makes cognify fail with ``no such column: users.tenant_id``. We stamp the dir
    with the cognee version that created it and, on a mismatch (or a pre-stamp
    dir like the one this fixes), delete the ``databases`` subdir so cognee
    rebuilds a schema-current store from the bundle on the next cognify. Returns
    True when a reset happened. Best-effort: never raises.

    Safety: only ever deletes a ``databases`` dir that actually looks like a
    cognee store, never follows a symlinked root/dir, and only stamps the current
    version once deletion is confirmed — a failed delete stays unstamped so the
    next run retries instead of masking the broken store.
    """
    current = _installed_cognee_version()
    if not current:
        return False
    root = Path(system_root)
    stamp = root / ".cognee_version"
    try:
        prior = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else None
        if prior == current:
            return False  # same cognee version → keep the accumulated store
        databases = root / "databases"
        if not databases.exists():
            # Fresh store (nothing to reset) — just record the version.
            root.mkdir(parents=True, exist_ok=True)
            stamp.write_text(current, encoding="utf-8")
            return False
        # Destructive guard: never delete a symlinked root/dir, and never delete
        # a ``databases`` dir that isn't recognizably a cognee store.
        if root.is_symlink() or databases.is_symlink() or not _looks_like_cognee_store(databases):
            logger.warning(
                "cognee system dir at %s is not a recognizable cognee store (or "
                "is a symlink); leaving it untouched.", databases,
            )
            return False
        # Delete WITHOUT ignore_errors so a failure is visible; only stamp after
        # the store is confirmed gone, else leave it for the next run to retry.
        shutil.rmtree(databases)
        if databases.exists():
            return False  # partial/failed delete — do NOT mask it with a stamp
        logger.warning(
            "cognee system store at %s was created by a different cognee version "
            "(%s vs installed %s); reset it (cognee has no schema migration). It "
            "rebuilds on the next cognify.", databases, prior or "unstamped", current,
        )
        stamp.write_text(current, encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 — cleanup is best-effort; never propagate
        logger.warning("cognee system-store reset skipped (unexpected error)", exc_info=True)
        return False


def configure_cognee_roots(system_root: str | Path | None = None, data_root: str | Path | None = None) -> None:
    try:
        import cognee
    except ImportError as exc:
        raise RuntimeError("cognee is not installed. Install it with: python3 -m pip install --user cognee") from exc
    if system_root:
        reset_stale_cognee_system_db(system_root)
        cognee.config.system_root_directory(str(Path(system_root)))
    if data_root:
        cognee.config.data_root_directory(str(Path(data_root)))


def import_cognee_add():
    try:
        import cognee
    except ImportError as exc:
        raise RuntimeError("cognee is not installed. Install it with: python3 -m pip install --user cognee") from exc
    return cognee.add


def import_cognee_cognify():
    try:
        import cognee
    except ImportError as exc:
        raise RuntimeError("cognee is not installed. Install it with: python3 -m pip install --user cognee") from exc
    return cognee.cognify
