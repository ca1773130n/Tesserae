"""AST-driven code-graph extractor for ``tesserae project ingest-code``.

Feature A (impl-code-graph) producer. Walks a project repository, parses
Python via stdlib :mod:`ast`, and mints a typed slice of the existing
:class:`ResearchGraph` ontology:

* Nodes: ``CODE_FILE``, ``CODE_MODULE``, ``CODE_CLASS``, ``CODE_FUNCTION``,
  ``CODE_METHOD``, ``DEPENDENCY``.
* Edges: ``contains`` (module→class/fn, class→method), ``declared_in``
  (symbol→file), ``imports`` (file→dep), ``calls`` (caller→callee, local
  best-effort), ``inherits_from`` (class→base; falls back to DEPENDENCY
  when the base lives in an imported module).

Distinct from :mod:`tesserae.code_graph` (older ``SOURCE_FILE`` /
``defines`` slice); both can coexist — consumers pick the ontology.
"""

from __future__ import annotations

import ast
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .research_graph import (
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
)


# Deterministic ignore set (no .gitignore parsing) so the extractor stays
# hermetic and idempotent in tests.
DEFAULT_EXCLUDES: frozenset[str] = frozenset({
    ".git", ".venv", "venv", "node_modules", ".tesserae", ".worktrees",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".tox", ".cache",
})


@dataclass(frozen=True)
class IngestCodeResult:
    """CLI summary payload, mirroring ``wiki.ingest()`` / ``wiki.compile()``."""

    graph: ResearchGraph
    processed_files: int
    skipped_dirs: int
    nodes: int
    edges: int


class CodeGraphExtractor:
    """Walk a project root and emit a typed code graph."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        excludes: Optional[Iterable[str]] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.excludes = frozenset(excludes) if excludes is not None else DEFAULT_EXCLUDES

    def extract(self, paths: Optional[Sequence[str | Path]] = None) -> IngestCodeResult:
        """Discover ``.py`` files under ``paths`` (or the project root) and mint a graph."""

        builder = ResearchGraphBuilder()
        project_node = builder.add_node(
            self.project_root.name,
            ResearchNodeType.CODE_PROJECT,
            description=f"Code project at {self.project_root}",
            source_path=str(self.project_root),
            metadata={"layer": "code-project"},
        )

        files, skipped_dirs = self._discover_files(paths)

        # Pass 1: mint file/module/symbol nodes + contains/declared_in/imports.
        # Pass 2 below resolves calls + inherits across the project symbol index.
        per_file: Dict[Path, _FileSymbols] = {}
        for path in files:
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                # Keep the file in the graph even when ast fails.
                file_node, module_node = self._mint_file_and_module(builder, project_node, path)
                per_file[path] = _FileSymbols(file_node=file_node, module_node=module_node)
                continue
            file_node, module_node = self._mint_file_and_module(builder, project_node, path)
            file_syms = _FileSymbols(file_node=file_node, module_node=module_node)
            self._emit_symbols(builder, file_syms, tree)
            self._emit_imports(builder, file_syms, tree)
            per_file[path] = file_syms

        # Pass 2: resolve calls + inherits across the symbol index.
        symbol_index = _build_symbol_index(per_file)
        for path, file_syms in per_file.items():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            self._emit_calls(builder, file_syms, tree, symbol_index)
            self._emit_inherits(builder, file_syms, tree, symbol_index)

        graph = builder.build()
        return IngestCodeResult(
            graph=graph,
            processed_files=len(files),
            skipped_dirs=skipped_dirs,
            nodes=len(graph.nodes),
            edges=len(graph.edges),
        )

    def _discover_files(
        self, paths: Optional[Sequence[str | Path]]
    ) -> Tuple[List[Path], int]:
        roots: List[Path] = []
        if paths:
            for raw in paths:
                p = Path(raw)
                if not p.is_absolute():
                    p = self.project_root / p
                roots.append(p.resolve())
        else:
            roots.append(self.project_root)

        out: List[Path] = []
        skipped_dirs = 0
        seen: Set[Path] = set()
        for root in roots:
            if root.is_file():
                if root.suffix == ".py" and root not in seen:
                    out.append(root)
                    seen.add(root)
                continue
            if not root.is_dir():
                continue
            # Manual walk so we can prune excluded dirs in-place.
            for current, dirnames, filenames in os.walk(root):
                pruned = [d for d in dirnames if d in self.excludes]
                for d in pruned:
                    dirnames.remove(d)
                skipped_dirs += len(pruned)
                for name in filenames:
                    if not name.endswith(".py"):
                        continue
                    candidate = Path(current) / name
                    if candidate in seen:
                        continue
                    out.append(candidate)
                    seen.add(candidate)
        return sorted(out), skipped_dirs

    def _mint_file_and_module(
        self,
        builder: ResearchGraphBuilder,
        project_node: ResearchNode,
        path: Path,
    ) -> Tuple[ResearchNode, ResearchNode]:
        rel = _relative(path, self.project_root)
        module_name = _module_name(rel)
        file_node = builder.add_node(
            rel,
            ResearchNodeType.CODE_FILE,
            source_path=rel,
            metadata={"layer": "code-file", "language": "python"},
        )
        module_node = builder.add_node(
            module_name,
            ResearchNodeType.CODE_MODULE,
            source_path=rel,
            metadata={"layer": "code-module", "file": rel},
        )
        builder.add_edge(project_node, "contains", file_node, evidence=f"{rel} in project")
        builder.add_edge(
            module_node,
            "declared_in",
            file_node,
            evidence=f"module {module_name} declared in {rel}",
        )
        return file_node, module_node

    def _emit_symbols(
        self,
        builder: ResearchGraphBuilder,
        file_syms: "_FileSymbols",
        tree: ast.Module,
    ) -> None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._emit_class(builder, file_syms, node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_node = builder.add_node(
                    node.name,
                    ResearchNodeType.CODE_FUNCTION,
                    source_path=file_syms.file_node.source_path,
                    metadata={"layer": "symbol", "line": node.lineno},
                    id_seed=f"{file_syms.module_node.name}.{node.name}",
                )
                builder.add_edge(file_syms.module_node, "contains", fn_node, evidence=f"{node.name} in module")
                builder.add_edge(fn_node, "declared_in", file_syms.file_node, evidence=f"{node.name} declared in {file_syms.file_node.name}")
                file_syms.functions[node.name] = fn_node

    def _emit_class(
        self,
        builder: ResearchGraphBuilder,
        file_syms: "_FileSymbols",
        node: ast.ClassDef,
    ) -> None:
        class_node = builder.add_node(
            node.name,
            ResearchNodeType.CODE_CLASS,
            source_path=file_syms.file_node.source_path,
            metadata={"layer": "symbol", "line": node.lineno},
            id_seed=f"{file_syms.module_node.name}.{node.name}",
        )
        builder.add_edge(file_syms.module_node, "contains", class_node, evidence=f"class {node.name} in module")
        builder.add_edge(class_node, "declared_in", file_syms.file_node, evidence=f"class {node.name} declared in {file_syms.file_node.name}")
        file_syms.classes[node.name] = class_node
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_qual = f"{node.name}.{child.name}"
                method_node = builder.add_node(
                    method_qual,
                    ResearchNodeType.CODE_METHOD,
                    source_path=file_syms.file_node.source_path,
                    metadata={"layer": "symbol", "line": child.lineno, "parent_class": node.name},
                    id_seed=f"{file_syms.module_node.name}.{method_qual}",
                )
                builder.add_edge(class_node, "contains", method_node, evidence=f"{method_qual} on {node.name}")
                builder.add_edge(method_node, "declared_in", file_syms.file_node, evidence=f"method {method_qual} declared in {file_syms.file_node.name}")
                file_syms.methods[method_qual] = method_node

    def _emit_imports(
        self,
        builder: ResearchGraphBuilder,
        file_syms: "_FileSymbols",
        tree: ast.Module,
    ) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    dep = builder.add_node(top, ResearchNodeType.DEPENDENCY, metadata={"layer": "dependency"})
                    builder.add_edge(file_syms.file_node, "imports", dep, evidence=f"import {alias.name}")
                    file_syms.imports[alias.asname or top] = dep
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".")[0]
                if not module:
                    continue
                dep = builder.add_node(module, ResearchNodeType.DEPENDENCY, metadata={"layer": "dependency"})
                builder.add_edge(file_syms.file_node, "imports", dep, evidence=f"from {node.module} import …")
                for alias in node.names:
                    file_syms.imports[alias.asname or alias.name] = dep

    def _emit_calls(
        self,
        builder: ResearchGraphBuilder,
        file_syms: "_FileSymbols",
        tree: ast.Module,
        symbol_index: "_SymbolIndex",
    ) -> None:
        # Iterate ``tree.body`` (module-level statements only) instead of
        # ``ast.walk(tree)``. ``ast.walk`` also yields every ``FunctionDef``
        # nested inside class bodies, which would cause this loop to scan
        # method bodies a second time as if they were top-level functions
        # — and when a module-level ``def foo()`` shares its name with a
        # method ``A.foo``, ``file_syms.functions.get("foo")`` would
        # mis-attribute every call inside ``A.foo`` to the top-level
        # ``foo``. Module-level traversal is sufficient: nested methods
        # are already handled by the ``ClassDef`` branch below.
        for parent in tree.body:
            if isinstance(parent, ast.ClassDef):
                for child in parent.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        caller = file_syms.methods.get(f"{parent.name}.{child.name}")
                        if caller is not None:
                            self._scan_calls(builder, caller, child, file_syms, symbol_index)
            elif isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                caller = file_syms.functions.get(parent.name)
                if caller is not None:
                    self._scan_calls(builder, caller, parent, file_syms, symbol_index)

    def _scan_calls(
        self,
        builder: ResearchGraphBuilder,
        caller: ResearchNode,
        body: ast.AST,
        file_syms: "_FileSymbols",
        symbol_index: "_SymbolIndex",
    ) -> None:
        seen: Set[str] = set()
        for sub in ast.walk(body):
            if not isinstance(sub, ast.Call):
                continue
            name = _call_name(sub.func)
            if not name or name in seen:
                continue
            seen.add(name)
            target = file_syms.functions.get(name) or file_syms.classes.get(name)
            if target is None:
                target = symbol_index.lookup(name)
            if target is None:
                continue
            builder.add_edge(caller, "calls", target, evidence=f"{caller.name} calls {name}")

    def _emit_inherits(
        self,
        builder: ResearchGraphBuilder,
        file_syms: "_FileSymbols",
        tree: ast.Module,
        symbol_index: "_SymbolIndex",
    ) -> None:
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            sub = file_syms.classes.get(node.name)
            if sub is None:
                continue
            for base in node.bases:
                name = _call_name(base)
                if not name:
                    continue
                # Prefer local CodeClass; fall back to DEPENDENCY when the
                # base lives in an imported package so the link survives.
                target = (
                    file_syms.classes.get(name)
                    or symbol_index.lookup(name)
                    or file_syms.imports.get(name)
                )
                if target is None:
                    continue
                builder.add_edge(sub, "inherits_from", target, evidence=f"class {node.name}({name})")


# Per-file scratch (mutable; intentionally not a frozen dataclass).


class _FileSymbols:
    __slots__ = ("file_node", "module_node", "classes", "functions", "methods", "imports")

    def __init__(self, *, file_node: ResearchNode, module_node: ResearchNode) -> None:
        self.file_node = file_node
        self.module_node = module_node
        self.classes: Dict[str, ResearchNode] = {}
        self.functions: Dict[str, ResearchNode] = {}
        self.methods: Dict[str, ResearchNode] = {}
        self.imports: Dict[str, ResearchNode] = {}


class _SymbolIndex:
    """Project-wide symbol lookup. Last-write-wins on collisions — fine
    for the best-effort call resolver."""

    def __init__(self) -> None:
        self._by_name: Dict[str, ResearchNode] = {}

    def add(self, name: str, node: ResearchNode) -> None:
        self._by_name[name] = node

    def lookup(self, name: str) -> Optional[ResearchNode]:
        return self._by_name.get(name)


def _build_symbol_index(per_file: Dict[Path, _FileSymbols]) -> _SymbolIndex:
    idx = _SymbolIndex()
    for syms in per_file.values():
        for name, node in syms.classes.items():
            idx.add(name, node)
        for name, node in syms.functions.items():
            idx.add(name, node)
    return idx


# Path / module helpers.


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _module_name(rel: str) -> str:
    """Turn ``pkg/sub/mod.py`` into ``pkg.sub.mod``; ``__init__`` collapses."""

    parts = rel.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else rel


def _call_name(node: ast.AST) -> Optional[str]:
    """Leaf name of a callable expression: ``foo()`` → ``"foo"``,
    ``mod.bar()`` / ``self.bar()`` → ``"bar"``. Best-effort; no alias chains."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# Disk I/O — atomic write with PID + random suffix (mirrors
# ``tesserae/batch.py::_write_manifest``) so concurrent writers don't
# collide on a shared ``.tmp`` suffix.


def write_code_graph(graph: ResearchGraph, target: Path) -> Path:
    """Persist ``graph`` to ``target`` atomically via ``os.replace``."""

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        tmp.write_text(
            graph.to_json(indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return target


__all__ = [
    "CodeGraphExtractor",
    "DEFAULT_EXCLUDES",
    "IngestCodeResult",
    "write_code_graph",
]
