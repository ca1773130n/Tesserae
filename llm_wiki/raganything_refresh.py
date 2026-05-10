"""Managed RAG-Anything refresh runner for LLM-Wiki.

Discovers non-code sources, parses them via RAG-Anything (MinerU/Docling/PaddleOCR),
and writes `.llm-wiki/external/raganything/manifest.json` plus `meta.json` so the
adapter has a stable artifact to import during compile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


RAGA_ROOT = Path(".llm-wiki/external/raganything")
MANIFEST_NAME = "manifest.json"
META_NAME = "meta.json"


def _git_head(project: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project,
            text=True,
            capture_output=True,
            timeout=20,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _stored_commit(project: Path) -> str | None:
    meta_path = project / RAGA_ROOT / META_NAME
    if not meta_path.exists():
        return None
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for key in ("gitCommitHash", "commit", "head"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _artifact_is_current(project: Path) -> bool:
    manifest = project / RAGA_ROOT / MANIFEST_NAME
    if not manifest.exists():
        return False
    head = _git_head(project)
    if not head:
        return True
    stored = _stored_commit(project)
    return stored == head


_SUPPORTED_EXT = {
    ".md", ".markdown", ".txt", ".rst",
    ".pdf",
    ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp",
}

_EXCLUDED_DIRS = {".git", ".venv", "node_modules", ".llm-wiki", ".understand-anything", ".pytest_cache", "__pycache__", "output", "dist", "build"}


def discover_sources(project: Path, *, roots: Iterable[str] | None = None) -> list[Path]:
    """Return all non-code files under the given roots that RAG-Anything can parse."""
    project = Path(project).resolve()
    candidates: list[Path] = []
    search_roots = [project / r for r in (roots or ["."]) if (project / r).exists()]
    for root in search_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _EXCLUDED_DIRS for part in path.relative_to(project).parts):
                continue
            if path.suffix.lower() in _SUPPORTED_EXT:
                candidates.append(path)
    return sorted(candidates)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_manifest(
    project: Path,
    *,
    documents: Sequence[dict],
    parser: str,
    parser_version: str = "",
    git_commit: str | None = None,
) -> Path:
    project = Path(project).resolve()
    out_dir = project / RAGA_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)

    serialized: list[dict] = []
    for doc in documents:
        path = Path(doc["path"]).resolve()
        rel = str(path.relative_to(project)).replace("\\", "/") if path.is_relative_to(project) else str(path)
        sha = _sha256_path(path)
        serialized.append({
            "id": f"doc-{sha[:16]}",
            "path": rel,
            "sha256": sha,
            "parsed_dir": str((out_dir / "parsed" / sha).relative_to(project)).replace("\\", "/"),
            "content_list": list(doc.get("content_list") or []),
        })

    manifest = {
        "version": 1,
        "project": {"name": project.name, "root": "."},
        "parser": parser,
        "parser_version": parser_version,
        "git_commit": git_commit or "",
        "documents": serialized,
    }
    manifest_path = out_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    meta = {
        "gitCommitHash": git_commit or "",
        "parser": parser,
        "parser_version": parser_version,
        "document_count": len(serialized),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path
