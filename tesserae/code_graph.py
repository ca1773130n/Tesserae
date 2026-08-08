"""Development-code graph extraction for project-local Tesserae workspaces.

Research notes and code projects share the same validated ResearchGraph pipeline,
but code gets a separate ontology slice: project → source files → symbols →
dependencies. This follows Karpathy's wiki philosophy by treating source files as
immutable raw evidence and generated graph/markdown/site artifacts as projections.

Extraction cache (delta-scoped regeneration): ``.tesserae/code-graph-cache.json``
stores the pure extractor output keyed on a stat manifest of the walked file
list plus a fingerprint of this module, so an unchanged code tree skips the
full AST re-parse on the next compile — whole-layer grain, reuse everything or
re-extract everything. The cache is INPUT state (it carries ``mtime_ns``,
volatile across checkouts), NOT a compiled artifact, and is excluded from
every output-hash scope by construction. Its size is on the order of
``code-graph.json`` itself (tens of MB on repos with large vendored trees,
<5 MB typically).
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .research_graph import ResearchGraph, ResearchGraphBuilder, ResearchNode, ResearchNodeType

logger = logging.getLogger("tesserae.code_graph")

CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".zsh", ".bash", ".sql"}
SKIP_PARTS = {".git", ".tesserae", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next", ".cache", ".pytest_cache", "target"}


class CodeGraphExtractor:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def extract_paths(self, paths: Iterable[str | Path]) -> ResearchGraph:
        return self.extract_files(self.iter_code_files(paths))

    def extract_files(self, files: Sequence[Path]) -> ResearchGraph:
        """Extract from an already-discovered file list (``extract_paths``
        minus discovery) so the compile gate can walk once, stat the manifest,
        and only then decide whether to re-extract."""
        builder = ResearchGraphBuilder()
        project = builder.add_node(
            self.project_root.name,
            ResearchNodeType.CODE_PROJECT,
            description=f"Development code project at {self.project_root}",
            source_path=str(self.project_root),
            metadata={"layer": "project", "source_kind": "CodeProject"},
        )
        for file_path in files:
            self._extract_file(builder, project, file_path)
        return builder.build()

    def iter_code_files(self, paths: Iterable[str | Path]) -> List[Path]:
        files: List[Path] = []
        for raw in paths:
            path = Path(raw)
            if not path.is_absolute():
                path = self.project_root / path
            if path.is_file() and is_code_file(path) and not should_skip(path):
                files.append(path)
            elif path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file() and is_code_file(child) and not should_skip(child):
                        files.append(child)
        return sorted(dict.fromkeys(files))

    def _extract_file(self, builder: ResearchGraphBuilder, project: ResearchNode, path: Path) -> None:
        rel = safe_relative(path, self.project_root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        file_node = builder.add_node(
            rel,
            ResearchNodeType.SOURCE_FILE,
            description=first_nonempty_line(text) or f"Source file {rel}",
            source_path=str(path),
            metadata={"layer": "raw-code", "language": language_for(path), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "bytes": len(text.encode("utf-8"))},
        )
        builder.add_edge(project, "contains", file_node, evidence=f"{rel} is inside {self.project_root.name}")
        if path.suffix == ".py":
            self._extract_python(builder, file_node, text)
        else:
            self._extract_text_symbols(builder, file_node, text)

    def _extract_python(self, builder: ResearchGraphBuilder, file_node: ResearchNode, text: str) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            self._extract_text_symbols(builder, file_node, text)
            return
        class_stack: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for dep in dependencies_from_import(node):
                    dep_node = builder.add_node(dep, ResearchNodeType.DEPENDENCY, metadata={"layer": "dependency"})
                    builder.add_edge(file_node, "imports", dep_node, evidence=f"{file_node.name} imports {dep}")
            elif isinstance(node, ast.ClassDef):
                class_node = builder.add_node(node.name, ResearchNodeType.CODE_CLASS, source_path=file_node.source_path, metadata={"layer": "symbol", "line": node.lineno})
                builder.add_edge(file_node, "defines", class_node, evidence=f"class {node.name} defined in {file_node.name}")
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fn = f"{node.name}.{child.name}"
                        fn_node = builder.add_node(fn, ResearchNodeType.CODE_FUNCTION, source_path=file_node.source_path, metadata={"layer": "symbol", "line": child.lineno, "parent_class": node.name})
                        builder.add_edge(class_node, "contains", fn_node, evidence=f"{fn} is a method on {node.name}")
                        builder.add_edge(file_node, "defines", fn_node, evidence=f"method {fn} defined in {file_node.name}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not class_stack:
                # ast.walk does not expose parents; skip methods already emitted via ClassDef body.
                if "." not in node.name:
                    fn_node = builder.add_node(node.name, ResearchNodeType.CODE_FUNCTION, source_path=file_node.source_path, metadata={"layer": "symbol", "line": node.lineno})
                    builder.add_edge(file_node, "defines", fn_node, evidence=f"function {node.name} defined in {file_node.name}")

    def _extract_text_symbols(self, builder: ResearchGraphBuilder, file_node: ResearchNode, text: str) -> None:
        for name in simple_symbol_names(text):
            fn_node = builder.add_node(name, ResearchNodeType.CODE_FUNCTION, source_path=file_node.source_path, metadata={"layer": "symbol"})
            builder.add_edge(file_node, "defines", fn_node, evidence=f"symbol {name} found in {file_node.name}")


def dependencies_from_import(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Import):
        return sorted({alias.name.split(".")[0] for alias in node.names})
    if isinstance(node, ast.ImportFrom):
        return [node.module.split(".")[0]] if node.module else []
    return []


def is_code_file(path: Path) -> bool:
    return path.suffix.lower() in CODE_SUFFIXES


def should_skip(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def language_for(path: Path) -> str:
    return {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".rs": "rust", ".go": "go"}.get(path.suffix.lower(), path.suffix.lower().lstrip(".") or "text")


def first_nonempty_line(text: str) -> Optional[str]:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#/")
        if stripped:
            return stripped[:160]
    return None


def simple_symbol_names(text: str) -> List[str]:
    import re
    names = []
    for pattern in [r"function\s+([A-Za-z_][A-Za-z0-9_]*)", r"class\s+([A-Za-z_][A-Za-z0-9_]*)", r"def\s+([A-Za-z_][A-Za-z0-9_]*)"]:
        names.extend(re.findall(pattern, text))
    return sorted(set(names))


# --------------------------------------------------------------------------- #
# Extraction cache — stat-manifest gate for delta-scoped regeneration.
#
# A git-based gate would be UNSOUND here: ``iter_code_files`` walks the
# filesystem including gitignored files, so the sound generalization of "did
# the tree change?" is a manifest of ``(rel_path, size, mtime_ns)`` over the
# walked list — git's own index strategy (stat-first change detection).
# Accepted residual risk: a content change preserving both size and
# ``mtime_ns`` is not detected (deliberate tampering; any normal write
# changes ``mtime_ns``).
# --------------------------------------------------------------------------- #

# [[rel_posix_path, size_bytes, mtime_ns], ...] sorted by path. Lists, not
# tuples: JSON round-trips to lists and cache-hit detection needs ``==``.
StatManifest = List[List[object]]


def stat_manifest(files: Sequence[Path], project_root: Path) -> Optional[StatManifest]:
    """Stat every walked file; ``None`` on ANY OSError (caching disabled this run)."""
    entries: StatManifest = []
    try:
        for path in files:
            stat = path.stat()
            entries.append([safe_relative(path, project_root), stat.st_size, stat.st_mtime_ns])
    except OSError:
        return None
    entries.sort(key=lambda entry: str(entry[0]))
    return entries


def extractor_fingerprint() -> Optional[str]:
    """sha256 of the extractor's source — this module plus research_graph.py,
    whose stable_id/dedup/model_dump logic equally shapes the output —
    auto-invalidates the cache on ANY edit, with no version constant to forget."""
    try:
        digest = hashlib.sha256()
        for name in ("code_graph.py", "research_graph.py"):
            digest.update((Path(__file__).parent / name).read_bytes())
        return digest.hexdigest()
    except OSError:
        return None


@dataclass(frozen=True)
class CodeGraphCache:
    fingerprint: str
    manifest: StatManifest
    graph_payload: Dict[str, object]  # model_dump() shape; rehydrate lazily on hit only


def read_code_graph_cache(path: Path) -> Optional[CodeGraphCache]:
    """``None`` on missing file, parse error, or wrong shape. Never raises."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    fingerprint = payload.get("fingerprint")
    manifest = payload.get("manifest")
    graph_payload = payload.get("graph")
    if (
        not isinstance(fingerprint, str)
        or not isinstance(manifest, list)
        or not isinstance(graph_payload, dict)
    ):
        return None
    return CodeGraphCache(fingerprint=fingerprint, manifest=manifest, graph_payload=graph_payload)


def write_code_graph_cache(
    path: Path, graph: ResearchGraph, manifest: StatManifest, fingerprint: str
) -> None:
    """Atomic (tmp + ``os.replace``) and deterministic given its inputs:
    ``model_dump()`` is the canonical content-derived ordering graph.json
    already uses, the manifest is sorted by rel path, and the envelope keys
    are ordered by construction — two compiles over an identical tree write
    byte-identical cache files. Deliberately NOT ``sort_keys=True``: that
    would re-order the metadata dicts inside the graph payload, and
    ``to_json`` preserves insertion order, so a rehydrated graph would no
    longer serialize byte-identically to the freshly extracted one. Never
    raises to the caller."""
    payload = {"fingerprint": fingerprint, "graph": graph.model_dump(), "manifest": manifest}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        logger.exception("code-graph cache write failed; compile unaffected")


def manifest_delta(old: Optional[StatManifest], new: StatManifest) -> Dict[str, int]:
    """Changed/added/removed file counts comparing by path; ``old=None``
    (no prior cache) counts everything as added."""
    old_by_path = {str(entry[0]): entry[1:] for entry in (old or [])}
    new_by_path = {str(entry[0]): entry[1:] for entry in new}
    return {
        "changed": sum(
            1
            for rel, stat in new_by_path.items()
            if rel in old_by_path and old_by_path[rel] != stat
        ),
        "added": sum(1 for rel in new_by_path if rel not in old_by_path),
        "removed": sum(1 for rel in old_by_path if rel not in new_by_path),
    }
