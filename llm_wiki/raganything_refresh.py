"""Managed RAG-Anything refresh runner for LLM-Wiki.

Discovers non-code sources, parses them via RAG-Anything (MinerU/Docling/PaddleOCR),
and writes `.llm-wiki/external/raganything/manifest.json` plus `meta.json` so the
adapter has a stable artifact to import during compile.

Exit codes:
    0 - refresh succeeded (or skipped because artifact was current)
    2 - project directory does not exist
    4 - raganything package is not installed
    5 - every discovered source failed to parse
    6 - Python interpreter is too old (RAG-Anything requires Python 3.10+)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    """Return True when the existing manifest is up-to-date with the project's git HEAD.

    For non-git projects (where ``_git_head`` returns None) the manifest is treated as
    current once it exists; the user must pass ``--force`` or ``--full`` to refresh.
    """
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

_TEXT_EXTS = {".md", ".markdown", ".txt", ".rst"}
_OFFICE_EXTS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
# Anything else under _SUPPORTED_EXT (PDF + images) routes through the default parser.

DEFAULT_TEXT_PARSER = "docling"
DEFAULT_OFFICE_PARSER = "docling"

_EXCLUDED_DIRS = {".git", ".venv", "node_modules", ".llm-wiki", ".understand-anything", ".pytest_cache", "__pycache__", "output", "dist", "build"}


def pick_parser_for_path(
    path: Path,
    *,
    default_parser: str,
    text_parser: str = DEFAULT_TEXT_PARSER,
    office_parser: str = DEFAULT_OFFICE_PARSER,
) -> str:
    """Choose the right parser for a single source file.

    Markdown/text routes to a lightweight parser that doesn't need MinerU;
    Office docs route to docling (better structure preservation per upstream);
    everything else (PDF, images) routes to the user's configured default.
    """
    ext = Path(path).suffix.lower()
    if ext in _TEXT_EXTS:
        return text_parser
    if ext in _OFFICE_EXTS:
        return office_parser
    return default_parser


def _install_hint_for(parser: str) -> str:
    if parser == "mineru":
        return (
            "Run `pip install 'mineru[core]'` and verify with `mineru --version`. "
            "MinerU downloads model weights (~GBs) on first parse; "
            "trigger the download with `mineru -p <any.pdf> -o /tmp/mineru-bootstrap -m auto`."
        )
    if parser == "docling":
        return "Run `pip install 'raganything[all]>=1.3.0'` (Docling ships with the [all] extras)."
    if parser == "paddleocr":
        return (
            "Run `pip install 'raganything[paddleocr]>=1.3.0'` AND `pip install paddlepaddle` "
            "(see https://www.paddlepaddle.org.cn/install/quick for the right wheel for your platform)."
        )
    return f"See https://github.com/HKUDS/RAG-Anything for installation instructions for parser '{parser}'."


def _verify_parsers_or_raise(rag, parsers: Iterable[str]) -> None:
    """Run RAGAnything.check_parser_installation() per parser, raising on first failure."""
    seen: set[str] = set()
    for parser in parsers:
        if parser in seen:
            continue
        seen.add(parser)
        ok = False
        try:
            ok = bool(rag.check_parser_installation(parser_name=parser))
        except TypeError:
            # Older raganything: check_parser_installation() takes no args and only
            # checks the parser configured at construction time. Skip with a warning.
            try:
                ok = bool(rag.check_parser_installation())
            except Exception:
                ok = False
        except Exception:
            ok = False
        if not ok:
            hint = _install_hint_for(parser)
            raise RuntimeError(
                f"RAG-Anything parser '{parser}' is not properly installed. {hint}"
            )


def discover_sources(project: Path, *, roots: Iterable[str] | None = None) -> list[Path]:
    """Return all non-code files under the given roots that RAG-Anything can parse."""
    project = Path(project).resolve()
    candidates: list[Path] = []
    search_roots = [project / r for r in (roots or ["."]) if (project / r).exists()]
    for root in search_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
            for name in filenames:
                path = Path(dirpath) / name
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


def parse_documents(
    project: Path,
    *,
    sources: Sequence[Path],
    parser: str,
    parse_method: str = "auto",
    working_dir: Path | None = None,
    llm_funcs: dict | None = None,
    text_parser: str = DEFAULT_TEXT_PARSER,
    office_parser: str = DEFAULT_OFFICE_PARSER,
) -> list[dict]:
    """Parse the given source files with RAG-Anything and return per-doc content lists.

    Imported lazily so the refresh module can be loaded without `raganything` installed.
    """
    try:
        import asyncio
        from raganything import RAGAnything, RAGAnythingConfig
    except Exception as exc:  # pragma: no cover - depends on env
        raise RuntimeError(
            "raganything is not installed. Run `pip install 'raganything[all]>=1.3.0'` or use --install-raganything."
        ) from exc

    # Resolve per-source parser BEFORE we instantiate RAGAnything so we can pre-flight.
    routing = [
        (src, pick_parser_for_path(src, default_parser=parser, text_parser=text_parser, office_parser=office_parser))
        for src in sources
    ]
    needed_parsers = sorted({p for _, p in routing})

    working_dir = Path(working_dir or (project / RAGA_ROOT / "working_dir")).resolve()
    working_dir.mkdir(parents=True, exist_ok=True)
    parsed_root = (project / RAGA_ROOT / "parsed").resolve()
    parsed_root.mkdir(parents=True, exist_ok=True)

    config = RAGAnythingConfig(working_dir=str(working_dir), parser=parser, parse_method=parse_method)
    funcs = llm_funcs or {}
    rag = RAGAnything(
        config=config,
        llm_model_func=funcs.get("llm_model_func"),
        vision_model_func=funcs.get("vision_model_func"),
        embedding_func=funcs.get("embedding_func"),
    )

    # Pre-flight: bail once with a clear message instead of cascading per-file failures.
    _verify_parsers_or_raise(rag, needed_parsers)

    async def run() -> list[dict]:
        results: list[dict] = []
        for src, picked_parser in routing:
            sha = _sha256_path(src)
            out_dir = parsed_root / sha
            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                await rag.process_document_complete(
                    file_path=str(src),
                    output_dir=str(out_dir),
                    parse_method=parse_method,
                    parser=picked_parser,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"raganything: failed to parse {src}: {exc}", file=sys.stderr)
                results.append({"path": src, "content_list": [], "error": str(exc)})
                continue
            content_list_path = out_dir / "content_list.json"
            content_list = []
            if content_list_path.exists():
                try:
                    content_list = json.loads(content_list_path.read_text(encoding="utf-8"))
                except Exception:
                    content_list = []
            results.append({"path": src, "content_list": content_list})
        return results

    return asyncio.run(run())


def refresh_raganything(
    project: str | Path,
    *,
    parser: str = "mineru",
    parse_method: str = "auto",
    roots: Sequence[str] | None = None,
    force: bool = False,
    full: bool = False,
    llm_funcs: dict | None = None,
    text_parser: str = DEFAULT_TEXT_PARSER,
    office_parser: str = DEFAULT_OFFICE_PARSER,
) -> int:
    root = Path(project).resolve()
    if not root.exists() or not root.is_dir():
        print(f"RAG-Anything refresh failed: project directory does not exist: {root}", file=sys.stderr)
        return 2

    if sys.version_info < (3, 10):
        print(
            f"RAG-Anything requires Python 3.10+; current interpreter is "
            f"{sys.version_info.major}.{sys.version_info.minor}. Skipping refresh.",
            file=sys.stderr,
        )
        return 6

    if not force and not full and _artifact_is_current(root):
        print("RAG-Anything manifest is already current; skipping refresh.")
        return 0

    if full:
        for sub in ("parsed", "working_dir"):
            target = root / RAGA_ROOT / sub
            if target.exists():
                import shutil as _shutil
                _shutil.rmtree(target)

    sources = discover_sources(root, roots=roots)
    if not sources:
        print("RAG-Anything: no parseable sources found; writing empty manifest.")
        write_manifest(root, documents=[], parser=parser, git_commit=_git_head(root))
        return 0

    try:
        documents = parse_documents(
            root,
            sources=sources,
            parser=parser,
            parse_method=parse_method,
            working_dir=None,
            llm_funcs=llm_funcs,
            text_parser=text_parser,
            office_parser=office_parser,
        )
    except RuntimeError as exc:
        print(f"RAG-Anything: {exc}", file=sys.stderr)
        return 4

    failures = sum(1 for d in documents if d.get("error"))
    if failures:
        print(
            f"RAG-Anything: {failures} of {len(sources)} source(s) failed to parse; "
            "continuing with successful documents.",
            file=sys.stderr,
        )
    if failures == len(sources):
        return 5

    successful = [d for d in documents if not d.get("error")]
    write_manifest(
        root,
        documents=successful,
        parser=parser,
        git_commit=_git_head(root) or "",
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser_ = argparse.ArgumentParser(description="Refresh RAG-Anything for an LLM-Wiki project.")
    parser_.add_argument("--project", default=".", help="Project root")
    parser_.add_argument("--parser", default="mineru", choices=["mineru", "docling", "paddleocr"])
    parser_.add_argument("--parse-method", default="auto", choices=["auto", "ocr", "txt"])
    parser_.add_argument("--root", action="append", dest="roots", help="Restrict discovery to this root (repeatable)")
    parser_.add_argument("--force", action="store_true")
    parser_.add_argument("--full", action="store_true", help="Purge parsed/ and working_dir/ before refresh")
    parser_.add_argument(
        "--text-parser",
        default=DEFAULT_TEXT_PARSER,
        choices=["mineru", "docling", "paddleocr"],
        help="Parser for .md/.markdown/.txt/.rst sources (default: docling, no MinerU model download).",
    )
    parser_.add_argument(
        "--office-parser",
        default=DEFAULT_OFFICE_PARSER,
        choices=["mineru", "docling", "paddleocr"],
        help="Parser for Office documents (.doc/.docx/.ppt/.pptx/.xls/.xlsx). Default: docling.",
    )
    args = parser_.parse_args(list(argv) if argv is not None else None)
    return refresh_raganything(
        args.project,
        parser=args.parser,
        parse_method=args.parse_method,
        roots=args.roots,
        force=args.force,
        full=args.full,
        text_parser=args.text_parser,
        office_parser=args.office_parser,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
