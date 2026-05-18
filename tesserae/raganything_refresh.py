"""Managed RAG-Anything refresh runner for Tesserae.

Discovers non-code sources, parses them via RAG-Anything (MinerU/Docling/PaddleOCR),
and writes `.tesserae/external/raganything/manifest.json` plus `meta.json` so the
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
import concurrent.futures
import hashlib
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


RAGA_ROOT = Path(".tesserae/external/raganything")
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


_UNSET: object = object()


def _artifact_is_current(project: Path, *, precomputed_head: object = _UNSET) -> bool:
    """Return True when the existing manifest is up-to-date with the project's git HEAD.

    For non-git projects (where ``_git_head`` returns None) the manifest is treated as
    current once it exists; the user must pass ``--force`` or ``--full`` to refresh.

    Pass ``precomputed_head`` to reuse an already-fetched HEAD value and avoid a
    second subprocess call.
    """
    manifest = project / RAGA_ROOT / MANIFEST_NAME
    if not manifest.exists():
        return False
    head = _git_head(project) if precomputed_head is _UNSET else precomputed_head
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

_EXCLUDED_DIRS = {".git", ".venv", "node_modules", ".tesserae", ".understand-anything", ".pytest_cache", "__pycache__", "output", "dist", "build"}


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


_TEXT_NATIVE_EXTS = {".md", ".markdown", ".txt", ".rst"}


def _parse_text_native(path: Path) -> list[dict]:
    """Read a text/markdown file as one content_list entry. No parser required."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return [{"type": "text", "page_idx": 0, "text": text}]


def _parse_with_docling(path: Path) -> list[dict]:
    """Use the docling DocumentConverter directly to extract a markdown body.

    Imported lazily so this module loads without docling installed. If docling
    isn't importable, raises RuntimeError; the caller folds that into a
    per-doc error in the result list.
    """
    try:
        from docling.document_converter import DocumentConverter
    except Exception as exc:
        raise RuntimeError("docling is not installed; run `pip install docling`.") from exc
    converter = DocumentConverter()
    result = converter.convert(str(path))
    document = getattr(result, "document", None)
    if document is None:
        raise RuntimeError("docling returned no document")
    markdown = document.export_to_markdown()
    return [{"type": "text", "page_idx": 0, "text": markdown}]


def _pick_construction_parser(routing: Sequence[tuple[Path, str]], *, default: str) -> str:
    """Choose RAGAnything's construction-time parser based on the actual routing.

    Using the most-frequent picked parser avoids the failure mode where
    `RAGAnything.__init__` tries to initialize a heavy parser (e.g. mineru)
    whose models aren't downloaded yet, killing every per-doc call before
    per-call `parser=` overrides can run.

    NOTE: Currently unused — the compile-time `parse_documents` no longer
    instantiates RAGAnything. Reserved for a future RAGAnything-backed
    parse path that would invoke this helper at construction time.
    """
    if not routing:
        return default
    counts: dict[str, int] = {}
    for _, picked in routing:
        counts[picked] = counts.get(picked, 0) + 1
    # Sort by count desc, then by parser id for stability.
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[0][0]


def _install_hint_for(parser: str) -> str:
    if parser == "mineru":
        return (
            "Run `pip install 'mineru[core]'` and verify with `mineru --version`. "
            "MinerU downloads model weights (~GBs) on first parse; "
            "trigger the download with `mineru -p <any.pdf> -o /tmp/mineru-bootstrap -m auto`."
        )
    if parser == "docling":
        return (
            "Run `pip install docling` (the Docling Python package is not bundled with "
            "`raganything[all]` — it must be installed directly). "
            "After install, verify with `python -c 'import docling; print(docling.__version__)'`."
        )
    if parser == "paddleocr":
        return (
            "Run `pip install 'raganything[paddleocr]>=1.3.0'` AND `pip install paddlepaddle` "
            "(see https://www.paddlepaddle.org.cn/install/quick for the right wheel for your platform)."
        )
    return f"See https://github.com/HKUDS/RAG-Anything for installation instructions for parser '{parser}'."


_PARSER_PACKAGE: dict[str, tuple[str, ...]] = {
    "mineru": ("mineru",),
    "docling": ("docling",),
    "paddleocr": ("paddleocr",),
}


def _parser_is_importable(parser: str) -> bool:
    """Return True if every Python package required for `parser` can be imported."""
    modules = _PARSER_PACKAGE.get(parser)
    if not modules:
        # Unknown parser id — defer to upstream rather than blocking.
        return True
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception:
            return False
    return True


def _verify_parsers_or_raise(rag, parsers: Iterable[str]) -> None:
    """Raise once with every missing parser and its install hint.

    Uses direct import probes (the upstream `RAGAnything.check_parser_installation`
    only inspects the parser configured on the instance and includes model-weight
    checks that fail before first parse — neither matches what we want here).
    The `rag` argument is kept for signature stability but unused.
    """
    seen: set[str] = set()
    missing: list[tuple[str, str]] = []
    for parser in parsers:
        if parser in seen:
            continue
        seen.add(parser)
        if not _parser_is_importable(parser):
            missing.append((parser, _install_hint_for(parser)))
    if missing:
        lines = ["RAG-Anything cannot run because the following parsers are not properly installed:"]
        for parser, hint in missing:
            lines.append(f"  - {parser}: {hint}")
        raise RuntimeError("\n".join(lines))


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
    tmp_manifest = manifest_path.with_suffix(".json.tmp")
    tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, manifest_path)

    meta = {
        "gitCommitHash": git_commit or "",
        "parser": parser,
        "parser_version": parser_version,
        "document_count": len(serialized),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = out_dir / META_NAME
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_meta, meta_path)
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
    """Parse the given source files and return per-doc content lists.

    The compile path uses parsers directly (native read for text files,
    docling for everything else) — RAG-Anything's full pipeline is reserved
    for the runtime query backend at `raganything_query.py` because that
    pipeline requires LLM/embedding/vision callables we don't have at compile
    time.

    The ``parse_method``, ``working_dir``, and ``llm_funcs`` kwargs are
    accepted for signature stability but are no longer used here. Only
    ``text_parser`` / ``office_parser`` (via ``pick_parser_for_path``) and the
    routing distribution still influence behavior.
    """
    routing = [
        (src, pick_parser_for_path(src, default_parser=parser, text_parser=text_parser, office_parser=office_parser))
        for src in sources
    ]
    # Pre-flight only the parsers that will actually be invoked. Native text
    # parsing has no package dep so we exclude it.
    needed_parsers = sorted({
        p
        for src, p in routing
        if Path(src).suffix.lower() not in _TEXT_NATIVE_EXTS
    })
    if needed_parsers:
        _verify_parsers_or_raise(rag=None, parsers=needed_parsers)

    parsed_root = (project / RAGA_ROOT / "parsed").resolve()
    parsed_root.mkdir(parents=True, exist_ok=True)

    def _parse_one(src_and_parser: tuple) -> dict:
        src, picked_parser = src_and_parser
        sha = _sha256_path(src)
        out_dir = parsed_root / sha
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            if src.suffix.lower() in _TEXT_NATIVE_EXTS:
                content_list = _parse_text_native(src)
            elif picked_parser == "docling":
                content_list = _parse_with_docling(src)
            else:
                # mineru / paddleocr — fall through to docling for now since
                # the upstream RAGAnything pipeline requires LLM funcs we don't
                # have.
                content_list = _parse_with_docling(src)
        except Exception as exc:  # noqa: BLE001
            print(f"raganything: failed to parse {src}: {exc}", file=sys.stderr)
            return {"path": src, "content_list": [], "error": str(exc)}
        # Persist the per-doc content_list to disk.
        try:
            (out_dir / "content_list.json").write_text(
                json.dumps(content_list, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"raganything: failed to persist content_list for {src}: {exc}", file=sys.stderr)
        return {"path": src, "content_list": content_list}

    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(routing))) as executor:
        futures = {executor.submit(_parse_one, pair): pair for pair in routing}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    return results


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

    git_commit = _git_head(root)

    if not force and not full and _artifact_is_current(root, precomputed_head=git_commit):
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
        write_manifest(root, documents=[], parser=parser, git_commit=git_commit)
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
        git_commit=git_commit or "",
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser_ = argparse.ArgumentParser(description="Refresh RAG-Anything for an Tesserae project.")
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
