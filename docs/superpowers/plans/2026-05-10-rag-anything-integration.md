# RAG-Anything Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate HKUDS/RAG-Anything (PyPI `raganything`) into LLM-Wiki as both a multimodal ingestion adapter (UA-style native graph projection) and a runtime memory backend (Cognee-sibling), while leaving `CodeGraphExtractor` as the canonical code analyzer.

**Architecture:** Mirror the existing Understand Anything integration: dedicated `raganything_adapter.py` for graph projection, `raganything_refresh.py` for managed parsing, `raganything_query.py` for runtime queries, plus surgical updates to `project.py`/`project_setup.py`/`cli.py` and a new `external_tools[id=raganything]` config entry. Storage lives under `.llm-wiki/external/raganything/`.

**Tech Stack:** Python 3.10+, async (`asyncio`), `raganything>=1.0` (optional extras), LightRAG (transitive), MinerU/Docling/PaddleOCR (parser plugins), pytest, existing LLM-Wiki ResearchGraph pipeline.

**Spec:** `docs/superpowers/specs/2026-05-10-rag-anything-integration-design.md`

---

## File Structure (Decomposition Lock-in)

| File | Responsibility |
|---|---|
| `llm_wiki/raganything_adapter.py` (new) | Pure-Python: read manifest.json → emit `ResearchGraph` nodes/edges with `external_refs`. No subprocess, no async. ~280 LOC. |
| `llm_wiki/raganything_refresh.py` (new) | Subprocess/async wrapper: discover non-code sources, call RAG-Anything, write manifest+meta. CLI entry. ~240 LOC. |
| `llm_wiki/raganything_query.py` (new) | Async query bridge for `project ask` runtime backend. ~90 LOC. |
| `llm_wiki/project.py` (modify) | `default_raganything_backend_config()`, `_merge_configured_raganything_graph()`, wire into compile + memory backends. |
| `llm_wiki/project_setup.py` (modify) | New params on `build_setup_plan`; append `raganything` external_tools entry; default memory_backends entry. |
| `llm_wiki/cli.py` (modify) | New flags + `project refresh-raganything` subcommand. |
| `pyproject.toml` (modify) | Add `raganything` and `raganything-all` extras. |
| `.gitignore` (modify) | Ignore `working_dir/` and `parsed/` under raganything dir. |
| `tests/test_raganything_adapter.py` (new) | Adapter unit + integration tests. |
| `tests/test_raganything_refresh.py` (new) | Refresh wrapper tests (subprocess mocked). |
| `tests/test_raganything_query.py` (new) | Query bridge tests (RAGAnything mocked). |
| `tests/test_project_setup_raganything.py` (new) | Wizard flag tests. |
| `tests/test_project_setup.py` (modify) | Add wiring assertions for raganything entry. |
| `docs/integrations/rag-anything.md` (new) | Integration doc (English). |
| `docs/i18n/integrations/rag-anything.{ko,zh,ja,ru,es,fr}.md` (6 new) | Localized integration docs. |
| `README.md` + 6 i18n (modify) | Bullet + flag block. |
| `docs/quickstart.md`, `docs/installation.md`, `docs/publishing-checklist.md`, `docs/self-dogfood.md` (modify) | Wire new flags into setup recipes. |

---

## Task 1: Add optional dependency extras and gitignore entries

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Locate the `[project.optional-dependencies]` table in `pyproject.toml`.** If absent, add it.

- [ ] **Step 2: Add the new extras.**

```toml
[project.optional-dependencies]
# ... existing extras ...
raganything = ["raganything>=1.0"]
raganything-all = ["raganything[all]>=1.0"]
```

- [ ] **Step 3: Append gitignore entries.**

Append to `.gitignore`:

```
.llm-wiki/external/raganything/working_dir/
.llm-wiki/external/raganything/parsed/
```

- [ ] **Step 4: Verify** with `grep -n raganything pyproject.toml .gitignore`. Both files should show the new lines.

- [ ] **Step 5: Commit.**

```bash
git add pyproject.toml .gitignore
git commit -m "build: add raganything optional extras and gitignore entries"
```

---

## Task 2: Adapter — failing test for `import_payload` core mapping

**Files:**
- Create: `tests/test_raganything_adapter.py`

- [ ] **Step 1: Write the failing test.**

Create `tests/test_raganything_adapter.py`:

```python
import json
from pathlib import Path

import pytest

from llm_wiki.research_graph import ResearchGraph, ResearchNode, ResearchNodeType


def _payload():
    return {
        "version": 1,
        "project": {"name": "demo"},
        "parser": "mineru",
        "documents": [
            {
                "id": "doc-abc123",
                "path": "docs/whitepaper.pdf",
                "sha256": "abc123",
                "parsed_dir": ".llm-wiki/external/raganything/parsed/abc123",
                "content_list": [
                    {"type": "text", "page_idx": 0, "text": "Mermaid rendering is described here."},
                    {"type": "image", "page_idx": 1, "img_path": "p1.png", "img_caption": ["Mermaid pipeline"]},
                    {"type": "table", "page_idx": 2, "table_body": "| a | b |\n| - | - |\n| 1 | 2 |", "table_caption": ["Performance"]},
                    {"type": "equation", "page_idx": 3, "latex": "E = mc^2", "equation_caption": ["Energy"]},
                ],
            }
        ],
    }


def test_import_payload_creates_source_file_with_multimodal_blocks(tmp_path):
    from llm_wiki.raganything_adapter import RagAnythingGraphAdapter

    adapter = RagAnythingGraphAdapter(tmp_path)
    graph, manifest = adapter.import_payload(
        _payload(),
        artifact_rel=".llm-wiki/external/raganything/manifest.json",
        artifact_sha256="deadbeef",
    )
    sources = [n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_FILE]
    assert len(sources) == 1
    src = sources[0]
    assert src.metadata["parser"] == "raganything"
    assert src.source_path == "docs/whitepaper.pdf"
    blocks = src.metadata["multimodal_blocks"]
    types = sorted({b["type"] for b in blocks})
    assert types == ["equation", "image", "table"]
    refs = src.metadata["external_refs"]
    assert refs[0]["system"] == "rag-anything"
    assert refs[0]["id"] == "doc-abc123"
    assert manifest["artifact_sha256"] == "deadbeef"
    assert manifest["imported_documents"]["doc-abc123"] == src.id
```

- [ ] **Step 2: Run the test and verify it fails.**

Run: `pytest tests/test_raganything_adapter.py::test_import_payload_creates_source_file_with_multimodal_blocks -v`
Expected: `ModuleNotFoundError: No module named 'llm_wiki.raganything_adapter'`.

- [ ] **Step 3: Create minimal adapter to make this test pass.**

Create `llm_wiki/raganything_adapter.py`:

```python
"""Native RAG-Anything graph importer.

Reads a `manifest.json` produced by `raganything_refresh` and projects
its parsed `content_list` into LLM-Wiki's controlled `ResearchGraph`,
preserving stable RAG-Anything ↔ LLM-Wiki id mappings and provenance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    normalize_display_name,
    stable_id,
)


_BLOCK_TYPES = ("text", "image", "table", "equation")


@dataclass(frozen=True)
class RagAnythingImportResult:
    graph: ResearchGraph
    manifest: dict


def _artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _doc_external_ref(artifact_rel: str, doc_id: str) -> dict:
    return {
        "system": "rag-anything",
        "id": doc_id,
        "type": "document",
        "artifact": artifact_rel,
    }


def _block_summary(block: Mapping[str, object]) -> dict:
    btype = str(block.get("type") or "").lower()
    summary: dict = {"type": btype, "page": block.get("page_idx")}
    if btype == "image":
        summary["img_path"] = block.get("img_path")
        summary["caption"] = list(block.get("img_caption") or [])
    elif btype == "table":
        summary["table_body"] = block.get("table_body") or block.get("table_html")
        summary["caption"] = list(block.get("table_caption") or [])
    elif btype == "equation":
        summary["latex"] = block.get("latex") or block.get("text")
        summary["caption"] = list(block.get("equation_caption") or [])
    elif btype == "text":
        summary["text"] = block.get("text")
    return summary


def _collect_text(content_list: Iterable[Mapping[str, object]]) -> str:
    chunks: list[str] = []
    for block in content_list:
        if str(block.get("type") or "").lower() == "text":
            text = str(block.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n\n".join(chunks)


class RagAnythingGraphAdapter:
    """Project a `manifest.json` into LLM-Wiki graph nodes/edges."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def import_artifact(self, artifact: str | Path) -> RagAnythingImportResult:
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = self.project_root / artifact_path
        artifact_path = artifact_path.resolve()
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact_rel = _rel(self.project_root, artifact_path)
        graph, manifest = self.import_payload(
            payload,
            artifact_rel=artifact_rel,
            artifact_sha256=_artifact_sha256(artifact_path),
        )
        return RagAnythingImportResult(graph=graph, manifest=manifest)

    def import_payload(
        self,
        payload: Mapping[str, object],
        *,
        artifact_rel: str = ".llm-wiki/external/raganything/manifest.json",
        artifact_sha256: str = "",
    ) -> tuple[ResearchGraph, dict]:
        documents = payload.get("documents") if isinstance(payload, dict) else None
        if not isinstance(documents, list):
            documents = []
        builder = ResearchGraphBuilder()
        doc_to_node: dict[str, ResearchNode] = {}

        for doc in documents:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("id") or doc.get("sha256") or "")
            if not doc_id:
                continue
            path = str(doc.get("path") or "")
            content_list = doc.get("content_list") if isinstance(doc.get("content_list"), list) else []
            blocks = [
                _block_summary(b) for b in content_list
                if isinstance(b, dict) and str(b.get("type") or "").lower() in _BLOCK_TYPES and str(b.get("type")).lower() != "text"
            ]
            description = _collect_text(content_list)
            metadata = {
                "parser": "raganything",
                "parser_version": str(payload.get("parser_version") or ""),
                "external_system": "rag-anything",
                "external_id": doc_id,
                "external_refs": [_doc_external_ref(artifact_rel, doc_id)],
                "multimodal_blocks": blocks,
            }
            equations = [b for b in blocks if b["type"] == "equation"]
            if equations:
                metadata["equations"] = equations
            node = builder.add_node(
                path or doc_id,
                ResearchNodeType.SOURCE_FILE,
                description=description or None,
                source_path=path or None,
                metadata=metadata,
                id_seed=f"raganything:{doc_id}",
            )
            doc_to_node[doc_id] = node

        graph = builder.build()
        manifest = {
            "artifact": artifact_rel,
            "artifact_sha256": artifact_sha256,
            "imported_documents": {doc_id: node.id for doc_id, node in sorted(doc_to_node.items())},
        }
        return graph, manifest
```

- [ ] **Step 4: Run the test and verify it passes.**

Run: `pytest tests/test_raganything_adapter.py::test_import_payload_creates_source_file_with_multimodal_blocks -v`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/raganything_adapter.py tests/test_raganything_adapter.py
git commit -m "feat: add RagAnythingGraphAdapter import_payload with multimodal blocks"
```

---

## Task 3: Adapter — `import_artifact` reads file + computes sha256

**Files:**
- Modify: `tests/test_raganything_adapter.py`

- [ ] **Step 1: Add a failing test.**

Append to `tests/test_raganything_adapter.py`:

```python
def test_import_artifact_reads_file_and_records_sha256(tmp_path):
    from llm_wiki.raganything_adapter import RagAnythingGraphAdapter

    artifact = tmp_path / ".llm-wiki" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")

    result = RagAnythingGraphAdapter(tmp_path).import_artifact(artifact)
    assert result.manifest["artifact"].endswith("manifest.json")
    assert len(result.manifest["artifact_sha256"]) == 64  # sha256 hex
    assert result.graph.nodes  # at least one node
```

- [ ] **Step 2: Run, verify it passes** (already implemented by Task 2).

Run: `pytest tests/test_raganything_adapter.py::test_import_artifact_reads_file_and_records_sha256 -v`
Expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add tests/test_raganything_adapter.py
git commit -m "test: cover RagAnythingGraphAdapter.import_artifact file IO + sha256"
```

---

## Task 4: Adapter — `merge_raganything_graph` convenience function

**Files:**
- Modify: `tests/test_raganything_adapter.py`
- Modify: `llm_wiki/raganything_adapter.py`

- [ ] **Step 1: Add failing test for merge function.**

Append to `tests/test_raganything_adapter.py`:

```python
def test_merge_raganything_graph_appends_to_existing_graph_and_writes_manifest(tmp_path):
    from llm_wiki.raganything_adapter import merge_raganything_graph

    artifact = tmp_path / ".llm-wiki" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")
    sync_path = tmp_path / ".llm-wiki" / "external" / "raganything-sync.json"

    base = ResearchGraph(nodes=[], edges=[])
    merged, manifest = merge_raganything_graph(
        base,
        project_root=tmp_path,
        artifact=artifact,
        sync_manifest_path=sync_path,
    )
    assert merged.nodes  # at least one source file node added
    assert sync_path.exists()
    written = json.loads(sync_path.read_text(encoding="utf-8"))
    assert written == manifest
```

- [ ] **Step 2: Run, verify it fails** with `ImportError`.

Run: `pytest tests/test_raganything_adapter.py::test_merge_raganything_graph_appends_to_existing_graph_and_writes_manifest -v`
Expected: FAIL.

- [ ] **Step 3: Implement `merge_raganything_graph`.**

Append to `llm_wiki/raganything_adapter.py`:

```python
def merge_raganything_graph(
    graph: ResearchGraph,
    *,
    project_root: str | Path,
    artifact: str | Path,
    sync_manifest_path: Optional[str | Path] = None,
) -> tuple[ResearchGraph, dict]:
    """Merge a RAG-Anything manifest into an existing graph and optionally persist sync manifest."""
    adapter = RagAnythingGraphAdapter(project_root)
    result = adapter.import_artifact(artifact)
    nodes_by_id: dict[str, ResearchNode] = {n.id: n for n in graph.nodes}
    for node in result.graph.nodes:
        nodes_by_id[node.id] = node
    edges_by_key: dict[tuple[str, str, str], ResearchEdge] = {
        (e.source, e.type, e.target): e for e in graph.edges
    }
    for edge in result.graph.edges:
        edges_by_key[(edge.source, edge.type, edge.target)] = edge

    merged = ResearchGraph(
        nodes=list(nodes_by_id.values()),
        edges=list(edges_by_key.values()),
    )

    if sync_manifest_path is not None:
        path = Path(sync_manifest_path)
        if not path.is_absolute():
            path = Path(project_root) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return merged, result.manifest
```

- [ ] **Step 4: Run test, verify pass.**

Run: `pytest tests/test_raganything_adapter.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/raganything_adapter.py tests/test_raganything_adapter.py
git commit -m "feat: add merge_raganything_graph convenience"
```

---

## Task 5: Refresh wrapper — staleness detection

**Files:**
- Create: `tests/test_raganything_refresh.py`
- Create: `llm_wiki/raganything_refresh.py`

- [ ] **Step 1: Write failing test for staleness detection.**

Create `tests/test_raganything_refresh.py`:

```python
import json
from pathlib import Path


def test_artifact_is_current_returns_false_when_manifest_missing(tmp_path):
    from llm_wiki.raganything_refresh import _artifact_is_current
    assert _artifact_is_current(tmp_path) is False


def test_artifact_is_current_returns_true_when_meta_matches_head(tmp_path, monkeypatch):
    import llm_wiki.raganything_refresh as mod
    base = tmp_path / ".llm-wiki" / "external" / "raganything"
    base.mkdir(parents=True)
    (base / "manifest.json").write_text("{}", encoding="utf-8")
    (base / "meta.json").write_text(json.dumps({"gitCommitHash": "abc"}), encoding="utf-8")
    monkeypatch.setattr(mod, "_git_head", lambda p: "abc")
    assert mod._artifact_is_current(tmp_path) is True


def test_artifact_is_current_returns_false_when_meta_differs(tmp_path, monkeypatch):
    import llm_wiki.raganything_refresh as mod
    base = tmp_path / ".llm-wiki" / "external" / "raganything"
    base.mkdir(parents=True)
    (base / "manifest.json").write_text("{}", encoding="utf-8")
    (base / "meta.json").write_text(json.dumps({"gitCommitHash": "old"}), encoding="utf-8")
    monkeypatch.setattr(mod, "_git_head", lambda p: "new")
    assert mod._artifact_is_current(tmp_path) is False
```

- [ ] **Step 2: Run, verify it fails** with `ModuleNotFoundError`.

Run: `pytest tests/test_raganything_refresh.py -v`
Expected: FAIL.

- [ ] **Step 3: Create initial `raganything_refresh.py` with staleness detection.**

Create `llm_wiki/raganything_refresh.py`:

```python
"""Managed RAG-Anything refresh runner for LLM-Wiki.

Discovers non-code sources, parses them via RAG-Anything (MinerU/Docling/PaddleOCR),
and writes `.llm-wiki/external/raganything/manifest.json` plus `meta.json` so the
adapter has a stable artifact to import during compile.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
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
```

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_raganything_refresh.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/raganything_refresh.py tests/test_raganything_refresh.py
git commit -m "feat: add raganything_refresh staleness detection"
```

---

## Task 6: Refresh wrapper — non-code source discovery

**Files:**
- Modify: `tests/test_raganything_refresh.py`
- Modify: `llm_wiki/raganything_refresh.py`

- [ ] **Step 1: Write failing test.**

Append to `tests/test_raganything_refresh.py`:

```python
def test_discover_sources_returns_non_code_files_only(tmp_path):
    from llm_wiki.raganything_refresh import discover_sources
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("# code")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("# spec")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "paper.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "data" / "img.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "data" / "notes.docx").write_bytes(b"PK\x03\x04")
    (tmp_path / ".llm-wiki").mkdir()
    (tmp_path / ".llm-wiki" / "scratch.md").write_text("# excluded")

    sources = sorted(str(p.relative_to(tmp_path)) for p in discover_sources(tmp_path, roots=["docs", "data"]))
    assert sources == ["data/img.png", "data/notes.docx", "data/paper.pdf", "docs/spec.md"]
```

- [ ] **Step 2: Run, verify it fails** with `ImportError`.

Run: `pytest tests/test_raganything_refresh.py::test_discover_sources_returns_non_code_files_only -v`
Expected: FAIL.

- [ ] **Step 3: Implement discovery.**

Append to `llm_wiki/raganything_refresh.py`:

```python
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
    search_roots = [project / r for r in (roots or [".") ] if (project / r).exists()]
    for root in search_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _EXCLUDED_DIRS for part in path.relative_to(project).parts):
                continue
            if path.suffix.lower() in _SUPPORTED_EXT:
                candidates.append(path)
    return sorted(candidates)
```

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_raganything_refresh.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/raganything_refresh.py tests/test_raganything_refresh.py
git commit -m "feat: discover non-code sources for raganything refresh"
```

---

## Task 7: Refresh wrapper — manifest writer (parser-only path)

**Files:**
- Modify: `tests/test_raganything_refresh.py`
- Modify: `llm_wiki/raganything_refresh.py`

This task implements `write_manifest()` from a list of pre-parsed `(path, content_list)` pairs. The actual call to RAG-Anything is wrapped in `parse_documents()` (Task 8) which can be patched in tests.

- [ ] **Step 1: Write failing test.**

Append to `tests/test_raganything_refresh.py`:

```python
def test_write_manifest_serializes_documents_with_sha256(tmp_path):
    from llm_wiki.raganything_refresh import write_manifest

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 hello")
    documents = [
        {
            "path": pdf,
            "content_list": [
                {"type": "text", "page_idx": 0, "text": "Hello"},
                {"type": "image", "page_idx": 0, "img_path": "x.png"},
            ],
        }
    ]
    manifest_path = write_manifest(
        tmp_path,
        documents=documents,
        parser="mineru",
        parser_version="2.0",
        git_commit="abc123",
    )

    assert manifest_path.exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["parser"] == "mineru"
    assert payload["git_commit"] == "abc123"
    assert payload["documents"][0]["path"] == "doc.pdf"
    assert len(payload["documents"][0]["sha256"]) == 64
    meta = json.loads((tmp_path / ".llm-wiki" / "external" / "raganything" / "meta.json").read_text())
    assert meta["gitCommitHash"] == "abc123"
    assert meta["parser"] == "mineru"
```

- [ ] **Step 2: Run, verify it fails.**

Run: `pytest tests/test_raganything_refresh.py::test_write_manifest_serializes_documents_with_sha256 -v`
Expected: FAIL.

- [ ] **Step 3: Implement `write_manifest`.**

Append to `llm_wiki/raganything_refresh.py`:

```python
import hashlib
from datetime import datetime, timezone


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
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    meta = {
        "gitCommitHash": git_commit or "",
        "parser": parser,
        "parser_version": parser_version,
        "document_count": len(serialized),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path
```

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_raganything_refresh.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/raganything_refresh.py tests/test_raganything_refresh.py
git commit -m "feat: write_manifest serializes raganything parse results"
```

---

## Task 8: Refresh wrapper — async parse driver + main entry

**Files:**
- Modify: `tests/test_raganything_refresh.py`
- Modify: `llm_wiki/raganything_refresh.py`

This wraps the RAG-Anything async API behind `parse_documents(...)` so tests can patch it without `raganything` installed. The CLI `main` function ties discovery + parse + write_manifest together.

- [ ] **Step 1: Write failing test.**

Append to `tests/test_raganything_refresh.py`:

```python
def test_refresh_runs_parse_documents_and_writes_manifest(tmp_path, monkeypatch):
    import llm_wiki.raganything_refresh as mod

    (tmp_path / "data").mkdir()
    pdf = tmp_path / "data" / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    fake_called = {}

    def fake_parse(project, *, sources, parser, parse_method, working_dir, llm_funcs):
        fake_called["sources"] = sorted(str(s.relative_to(project)) for s in sources)
        fake_called["parser"] = parser
        return [
            {
                "path": pdf,
                "content_list": [{"type": "text", "page_idx": 0, "text": "ok"}],
            }
        ]

    monkeypatch.setattr(mod, "parse_documents", fake_parse)
    monkeypatch.setattr(mod, "_git_head", lambda p: "deadbeef")

    rc = mod.refresh_raganything(tmp_path, parser="mineru", roots=["data"], force=True)
    assert rc == 0
    assert fake_called["sources"] == ["data/paper.pdf"]
    manifest = tmp_path / ".llm-wiki" / "external" / "raganything" / "manifest.json"
    assert manifest.exists()
    payload = json.loads(manifest.read_text())
    assert payload["parser"] == "mineru"
    assert payload["git_commit"] == "deadbeef"


def test_refresh_skips_when_artifact_current(tmp_path, monkeypatch, capsys):
    import llm_wiki.raganything_refresh as mod
    base = tmp_path / ".llm-wiki" / "external" / "raganything"
    base.mkdir(parents=True)
    (base / "manifest.json").write_text("{}")
    (base / "meta.json").write_text(json.dumps({"gitCommitHash": "abc"}))
    monkeypatch.setattr(mod, "_git_head", lambda p: "abc")
    monkeypatch.setattr(mod, "parse_documents", lambda *a, **k: pytest.fail("should not parse"))

    rc = mod.refresh_raganything(tmp_path, parser="mineru")
    assert rc == 0
    out = capsys.readouterr().out
    assert "already current" in out
```

(Also add `import pytest` at the top of the file if not already present.)

- [ ] **Step 2: Run, verify it fails.**

Run: `pytest tests/test_raganything_refresh.py -v`
Expected: 2 new tests FAIL with `AttributeError`.

- [ ] **Step 3: Implement `parse_documents`, `refresh_raganything`, and `main`.**

Append to `llm_wiki/raganything_refresh.py`:

```python
def parse_documents(
    project: Path,
    *,
    sources: Sequence[Path],
    parser: str,
    parse_method: str = "auto",
    working_dir: Path | None = None,
    llm_funcs: dict | None = None,
) -> list[dict]:
    """Parse the given source files with RAG-Anything and return per-doc content lists.

    Imported lazily so the refresh module can be loaded without `raganything` installed.
    """
    try:
        import asyncio
        from raganything import RAGAnything, RAGAnythingConfig
    except Exception as exc:  # pragma: no cover - depends on env
        raise RuntimeError(
            "raganything is not installed. Run `pip install 'raganything[all]'` or use --install-raganything."
        ) from exc

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

    async def run() -> list[dict]:
        results: list[dict] = []
        for src in sources:
            sha = _sha256_path(src)
            out_dir = parsed_root / sha
            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                await rag.process_document_complete(
                    file_path=str(src),
                    output_dir=str(out_dir),
                    parse_method=parse_method,
                    parser=parser,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"raganything: failed to parse {src}: {exc}", file=sys.stderr)
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
) -> int:
    root = Path(project).resolve()
    if not root.exists() or not root.is_dir():
        print(f"RAG-Anything refresh failed: project directory does not exist: {root}", file=sys.stderr)
        return 2

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
            llm_funcs=llm_funcs,
        )
    except RuntimeError as exc:
        print(f"RAG-Anything: {exc}", file=sys.stderr)
        return 4

    write_manifest(
        root,
        documents=documents,
        parser=parser,
        git_commit=_git_head(root) or "",
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh RAG-Anything for an LLM-Wiki project.")
    parser.add_argument("--project", default=".", help="Project root")
    parser.add_argument("--parser", default="mineru", choices=["mineru", "docling", "paddleocr"])
    parser.add_argument("--parse-method", default="auto", choices=["auto", "ocr", "txt"])
    parser.add_argument("--root", action="append", dest="roots", help="Restrict discovery to this root (repeatable)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--full", action="store_true", help="Purge parsed/ and working_dir/ before refresh")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return refresh_raganything(
        args.project,
        parser=args.parser,
        parse_method=args.parse_method,
        roots=args.roots,
        force=args.force,
        full=args.full,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_raganything_refresh.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/raganything_refresh.py tests/test_raganything_refresh.py
git commit -m "feat: refresh_raganything orchestrates discovery, parse, manifest write"
```

---

## Task 9: Query bridge for runtime memory backend

**Files:**
- Create: `tests/test_raganything_query.py`
- Create: `llm_wiki/raganything_query.py`

- [ ] **Step 1: Write failing test.**

Create `tests/test_raganything_query.py`:

```python
import pytest


def test_query_returns_string_via_aquery_when_backend_available(monkeypatch, tmp_path):
    import llm_wiki.raganything_query as mod

    captured = {}

    class FakeRag:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        async def aquery(self, question, mode="hybrid", vlm_enhanced=False):
            captured["question"] = question
            captured["mode"] = mode
            return "answer-text"

    monkeypatch.setattr(mod, "_load_raganything", lambda cfg: FakeRag(working_dir=cfg["working_dir"]))

    answer = mod.query(
        "What does the paper say?",
        backend_config={
            "enabled": True,
            "working_dir": str(tmp_path),
            "query_mode": "hybrid",
            "vlm_enhanced": True,
        },
    )
    assert answer == "answer-text"
    assert captured["question"] == "What does the paper say?"
    assert captured["mode"] == "hybrid"


def test_query_returns_none_when_disabled(tmp_path):
    from llm_wiki.raganything_query import query
    assert query("q", backend_config={"enabled": False, "working_dir": str(tmp_path)}) is None


def test_query_returns_none_when_module_missing(monkeypatch, tmp_path):
    import llm_wiki.raganything_query as mod

    def boom(cfg):
        raise RuntimeError("raganything not installed")

    monkeypatch.setattr(mod, "_load_raganything", boom)
    assert mod.query("q", backend_config={"enabled": True, "working_dir": str(tmp_path)}) is None
```

- [ ] **Step 2: Run, verify it fails.**

Run: `pytest tests/test_raganything_query.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement query bridge.**

Create `llm_wiki/raganything_query.py`:

```python
"""Runtime query bridge for RAG-Anything memory backend."""

from __future__ import annotations

import asyncio
import sys
from typing import Optional


def _load_raganything(cfg: dict):
    try:
        from raganything import RAGAnything, RAGAnythingConfig
    except Exception as exc:
        raise RuntimeError("raganything is not installed") from exc
    config = RAGAnythingConfig(
        working_dir=str(cfg["working_dir"]),
        parser=cfg.get("parser", "mineru"),
        parse_method=cfg.get("parse_method", "auto"),
    )
    return RAGAnything(config=config)


def query(question: str, *, backend_config: dict) -> Optional[str]:
    if not backend_config or not backend_config.get("enabled"):
        return None
    try:
        rag = _load_raganything(backend_config)
    except RuntimeError as exc:
        print(f"raganything backend disabled: {exc}", file=sys.stderr)
        return None

    mode = backend_config.get("query_mode", "hybrid")
    vlm = bool(backend_config.get("vlm_enhanced", False))

    async def run() -> str:
        return await rag.aquery(question, mode=mode, vlm_enhanced=vlm)

    try:
        return asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        print(f"raganything query failed: {exc}", file=sys.stderr)
        return None
```

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_raganything_query.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/raganything_query.py tests/test_raganything_query.py
git commit -m "feat: add raganything_query runtime backend bridge"
```

---

## Task 10: `default_raganything_backend_config` in project.py

**Files:**
- Modify: `llm_wiki/project.py`
- Modify: `tests/test_project_setup.py` (or create new test file if more convenient)

- [ ] **Step 1: Write failing test.**

Create `tests/test_default_raganything_backend_config.py`:

```python
def test_default_raganything_backend_config_has_required_fields():
    from llm_wiki.project import default_raganything_backend_config

    cfg = default_raganything_backend_config("demo")
    assert cfg["enabled"] is False  # opt-in: keys may not be configured
    assert cfg["working_dir"] == ".llm-wiki/external/raganything/working_dir"
    assert cfg["parser"] == "mineru"
    assert cfg["parse_method"] == "auto"
    assert cfg["query_mode"] == "hybrid"
    assert cfg["vlm_enhanced"] is True
    assert cfg["install"]["command"].startswith("{python} -m pip install")
    assert cfg["install"]["auto_install"] is False
```

- [ ] **Step 2: Run, verify it fails** with `ImportError`.

Run: `pytest tests/test_default_raganything_backend_config.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the function** to `llm_wiki/project.py`. Place it adjacent to `default_cognee_backend_config` (around line 852).

```python
def default_raganything_backend_config(name: str = "llm_wiki") -> dict:
    return {
        "enabled": False,
        "working_dir": ".llm-wiki/external/raganything/working_dir",
        "parser": "mineru",
        "parse_method": "auto",
        "query_mode": "hybrid",
        "vlm_enhanced": True,
        "install": {
            "command": "{python} -m pip install 'raganything[all]'",
            "auto_install": False,
        },
    }
```

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_default_raganything_backend_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/project.py tests/test_default_raganything_backend_config.py
git commit -m "feat: default_raganything_backend_config"
```

---

## Task 11: `_merge_configured_raganything_graph` in compile path

**Files:**
- Modify: `llm_wiki/project.py`
- Create: `tests/test_project_compile_raganything.py`

- [ ] **Step 1: Write failing integration test.**

Create `tests/test_project_compile_raganything.py`:

```python
import json

from llm_wiki.project import ProjectWiki, load_graph_file
from llm_wiki.research_graph import ResearchNodeType


def _payload():
    return {
        "version": 1,
        "project": {"name": "demo"},
        "parser": "mineru",
        "documents": [
            {
                "id": "doc-deadbeef",
                "path": "data/paper.pdf",
                "sha256": "deadbeef" * 8,
                "parsed_dir": ".llm-wiki/external/raganything/parsed/deadbeef",
                "content_list": [
                    {"type": "text", "page_idx": 0, "text": "Mermaid rendering pipeline"},
                    {"type": "image", "page_idx": 0, "img_path": "x.png", "img_caption": ["Pipeline"]},
                ],
            }
        ],
    }


def test_project_compile_merges_configured_raganything_native_graph(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# demo\n", encoding="utf-8")
    artifact = project / ".llm-wiki" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")

    wiki = ProjectWiki.init(project, name="demo", sources=["README.md"])
    cfg = wiki.config()
    cfg["external_tools"] = [
        {
            "id": "raganything",
            "artifact": ".llm-wiki/external/raganything/manifest.json",
            "sync_mode": "native_graph",
            "enabled": True,
            "auto_refresh": False,
        }
    ]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    wiki.compile(cognify=None)

    graph = load_graph_file(wiki.paths.graph)
    sources = [n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_FILE and n.metadata.get("parser") == "raganything"]
    assert len(sources) == 1
    assert sources[0].metadata["external_refs"][0]["system"] == "rag-anything"
    sync = json.loads((project / ".llm-wiki" / "external" / "raganything-sync.json").read_text())
    assert sync["imported_documents"]["doc-deadbeef"] == sources[0].id
```

- [ ] **Step 2: Run, verify it fails.**

Run: `pytest tests/test_project_compile_raganything.py -v`
Expected: FAIL (no node with `parser=raganything` in graph).

- [ ] **Step 3: Add merge method to `ProjectWiki` in `llm_wiki/project.py`.**

Locate `_merge_configured_understand_anything_graph` (around line 360). Immediately after that method, add:

```python
    def _merge_configured_raganything_graph(self, graph: "ResearchGraph", cfg: dict) -> "ResearchGraph":
        """Merge configured RAG-Anything manifest artifacts natively."""
        from .raganything_adapter import merge_raganything_graph

        for tool in cfg.get("external_tools", []) or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("id") != "raganything" or tool.get("enabled", True) is False:
                continue
            sync_mode = str(tool.get("sync_mode") or "native_graph")
            if sync_mode not in {"native_graph", "both"}:
                continue
            artifact = self.project_root / str(
                tool.get("artifact") or ".llm-wiki/external/raganything/manifest.json"
            )
            if not artifact.exists():
                continue
            sync_path = self.project_root / ".llm-wiki" / "external" / "raganything-sync.json"
            graph, _ = merge_raganything_graph(
                graph,
                project_root=self.project_root,
                artifact=artifact,
                sync_manifest_path=sync_path,
            )
        return graph
```

Then call it from `compile()` directly after the existing UA merge call. Locate the line `graph = self._merge_configured_understand_anything_graph(graph, cfg)` (around line 310) and add the new line below it:

```python
        graph = self._merge_configured_understand_anything_graph(graph, cfg)
        graph = self._merge_configured_raganything_graph(graph, cfg)
```

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_project_compile_raganything.py -v`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/project.py tests/test_project_compile_raganything.py
git commit -m "feat: merge raganything manifest into compile graph"
```

---

## Task 12: Wire raganything into setup wizard plan

**Files:**
- Modify: `llm_wiki/project_setup.py`
- Create: `tests/test_project_setup_raganything.py`

- [ ] **Step 1: Write failing test.**

Create `tests/test_project_setup_raganything.py`:

```python
from llm_wiki.project_setup import build_setup_plan


def test_build_setup_plan_with_raganything_appends_external_tool_and_backend(tmp_path):
    plan = build_setup_plan(
        tmp_path,
        name="demo",
        sources=["README.md"],
        include_raganything=True,
        install_raganything=True,
        raganything_parser="mineru",
        run_raganything=True,
    )
    raga = next(t for t in plan.external_tools if t["id"] == "raganything")
    assert raga["sync_mode"] == "native_graph"
    assert raga["parser"] == "mineru"
    assert raga["auto_refresh"] is True
    assert raga["artifact"] == ".llm-wiki/external/raganything/manifest.json"
    assert raga["install"]["auto_install"] is True
    assert plan.memory_backends["raganything"]["enabled"] is True
    assert plan.memory_backends["raganything"]["parser"] == "mineru"


def test_build_setup_plan_without_raganything_does_not_add_entry(tmp_path):
    plan = build_setup_plan(tmp_path, name="demo", sources=["README.md"])
    assert all(t["id"] != "raganything" for t in plan.external_tools)
    assert "raganything" not in (plan.memory_backends or {})
```

- [ ] **Step 2: Run, verify it fails.**

Run: `pytest tests/test_project_setup_raganything.py -v`
Expected: FAIL — `build_setup_plan` doesn't accept `include_raganything`.

- [ ] **Step 3: Modify `build_setup_plan` in `llm_wiki/project_setup.py`.**

Find the function signature (around line 80–110) and add the new keyword arguments. Locate the existing UA branch (around line 117 — `if include_understand_anything or ua_artifact:`) and add an analogous block after it:

```python
def build_setup_plan(
    root,
    *,
    name: str | None = None,
    sources: List[str] | None = None,
    include_understand_anything: bool = False,
    install_understand_anything: Optional[bool] = None,
    understand_anything_command: str | None = None,
    understand_anything_platform: str = "codex",
    run_understand_anything: bool = False,
    include_raganything: bool = False,
    install_raganything: bool = False,
    raganything_parser: str = "mineru",
    raganything_extras: str = "all",
    run_raganything: bool = False,
    install_cognee: bool = False,
    run_cognee: bool = False,
    cognee_skip_install: bool = False,
) -> SetupPlan:
    # ... existing body ...
    # After existing UA block:
    memory_backends: dict = {"cognee": default_cognee_backend_config(name or sanitize_server_name(root.name))}

    if include_raganything:
        from .project import default_raganything_backend_config
        backend = default_raganything_backend_config(name or sanitize_server_name(root.name))
        backend["enabled"] = True
        backend["parser"] = raganything_parser
        if install_raganything:
            backend["install"]["auto_install"] = True
            backend["install"]["command"] = (
                "{python} -m pip install 'raganything[" + raganything_extras + "]'"
                if raganything_extras else "{python} -m pip install raganything"
            )
        memory_backends["raganything"] = backend

        external_tools.append({
            "id": "raganything",
            "name": "RAG-Anything",
            "artifact": ".llm-wiki/external/raganything/manifest.json",
            "source": ".llm-wiki/external/raganything/manifest.json",
            "refresh_command": f"{sys.executable} -m llm_wiki.raganything_refresh --project . --parser {raganything_parser}",
            "auto_refresh": bool(run_raganything),
            "sync_mode": "native_graph",
            "parser": raganything_parser,
            "extras": raganything_extras,
            "managed_refresh": True,
            "install": {
                "auto_install": bool(install_raganything),
                "command": (
                    "{python} -m pip install 'raganything[" + raganything_extras + "]'"
                    if raganything_extras else "{python} -m pip install raganything"
                ),
            },
            "enabled": True,
        })
```

(If the existing `memory_backends` assignment differs, integrate analogously without duplicating the cognee assignment.)

Add `import sys` at top of `project_setup.py` if not already present.

Update the `SetupPlan(... memory_backends=memory_backends ...)` construction near the bottom of `build_setup_plan` to use the dict built above.

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_project_setup_raganything.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/project_setup.py tests/test_project_setup_raganything.py
git commit -m "feat: wire raganything into project setup plan"
```

---

## Task 13: CLI flags + `refresh-raganything` subcommand

**Files:**
- Modify: `llm_wiki/cli.py`
- Create: `tests/test_cli_raganything.py`

- [ ] **Step 1: Write failing test.**

Create `tests/test_cli_raganything.py`:

```python
import json


def test_cli_setup_passes_raganything_flags_to_plan(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli

    captured = {}

    def fake_build(root, **kwargs):
        captured.update(kwargs)
        from llm_wiki.project_setup import SetupPlan
        return SetupPlan(name="demo", sources=["README.md"])

    monkeypatch.setattr(cli, "build_setup_plan", fake_build)
    monkeypatch.setattr(cli, "run_external_tools", lambda plan, **kw: [])
    monkeypatch.setattr(cli, "write_setup_files", lambda *a, **kw: None)

    rc = cli.main([
        "project", "setup", "--yes",
        "--with-raganything", "--install-raganything",
        "--raganything-parser", "docling",
        "--raganything-extras", "all",
        "--run-raganything",
        "--project-root", str(tmp_path),
    ])
    assert rc == 0
    assert captured["include_raganything"] is True
    assert captured["install_raganything"] is True
    assert captured["raganything_parser"] == "docling"
    assert captured["raganything_extras"] == "all"
    assert captured["run_raganything"] is True


def test_cli_refresh_raganything_invokes_refresh_main(monkeypatch):
    from llm_wiki import cli
    captured = {}

    def fake_refresh_main(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(cli, "_raganything_refresh_main", fake_refresh_main)
    rc = cli.main(["project", "refresh-raganything", "--parser", "mineru", "--full"])
    assert rc == 0
    assert "--parser" in captured["argv"]
    assert "mineru" in captured["argv"]
    assert "--full" in captured["argv"]
```

(Adapt monkeypatched names to whatever the CLI module actually imports — read the existing cli.py around the `setup` subcommand and `refresh-understand-anything` to align.)

- [ ] **Step 2: Run, verify it fails.**

Run: `pytest tests/test_cli_raganything.py -v`
Expected: FAIL (flags not present, subcommand absent).

- [ ] **Step 3: Add flags to the setup subparser** in `llm_wiki/cli.py`. Locate the block of `--with-understand-anything` flags (around line 233). Append:

```python
    setup_parser.add_argument("--with-raganything", action="store_true", help="Enable RAG-Anything multimodal ingestion + memory backend")
    setup_parser.add_argument("--skip-raganything", action="store_true", help="Disable RAG-Anything even if previously configured")
    setup_parser.add_argument("--install-raganything", action="store_true", help="Auto-install raganything during setup")
    setup_parser.add_argument("--skip-install-raganything", action="store_true", help="Do not auto-install raganything")
    setup_parser.add_argument("--raganything-parser", choices=["mineru", "docling", "paddleocr"], default="mineru", help="Parser backend for RAG-Anything (default: mineru)")
    setup_parser.add_argument("--raganything-extras", default="all", help="pip extras to use when installing raganything (default: all)")
    setup_parser.add_argument("--run-raganything", action="store_true", help="Auto-refresh RAG-Anything on every compile")
```

In the setup-handler body where it calls `build_setup_plan(...)`, add the new keyword arguments alongside existing UA ones:

```python
    plan = build_setup_plan(
        project_root,
        name=args.name,
        sources=sources,
        include_understand_anything=args.with_understand_anything,
        install_understand_anything=(False if args.skip_install_understand_anything else True if args.install_understand_anything else None),
        understand_anything_platform=args.understand_anything_platform,
        run_understand_anything=False,  # existing
        include_raganything=(False if args.skip_raganything else args.with_raganything),
        install_raganything=(False if args.skip_install_raganything else args.install_raganything),
        raganything_parser=args.raganything_parser,
        raganything_extras=args.raganything_extras,
        run_raganything=args.run_raganything,
        install_cognee=args.install_cognee,
        run_cognee=args.run_cognee,
        cognee_skip_install=args.skip_install_cognee,
    )
```

Add a `project refresh-raganything` subcommand. Locate the existing `project refresh-understand-anything` registration and add:

```python
    refresh_raga_parser = project_subparsers.add_parser(
        "refresh-raganything",
        help="Run the managed RAG-Anything refresh wrapper",
    )
    refresh_raga_parser.add_argument("--parser", default="mineru", choices=["mineru", "docling", "paddleocr"])
    refresh_raga_parser.add_argument("--parse-method", default="auto", choices=["auto", "ocr", "txt"])
    refresh_raga_parser.add_argument("--root", action="append", dest="roots", help="Restrict to this root (repeatable)")
    refresh_raga_parser.add_argument("--force", action="store_true")
    refresh_raga_parser.add_argument("--full", action="store_true")
```

Add a dispatcher branch:

```python
    if args.subcommand == "refresh-raganything":
        from llm_wiki.raganything_refresh import main as _raganything_refresh_main
        forwarded: list[str] = []
        forwarded += ["--parser", args.parser]
        forwarded += ["--parse-method", args.parse_method]
        for r in (args.roots or []):
            forwarded += ["--root", r]
        if args.force:
            forwarded.append("--force")
        if args.full:
            forwarded.append("--full")
        return _raganything_refresh_main(forwarded)
```

(Ensure `_raganything_refresh_main` is exposed at the module level so tests can monkeypatch it. Easiest: `from llm_wiki.raganything_refresh import main as _raganything_refresh_main` at the top of cli.py.)

- [ ] **Step 4: Run, verify pass.**

Run: `pytest tests/test_cli_raganything.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit.**

```bash
git add llm_wiki/cli.py tests/test_cli_raganything.py
git commit -m "feat: CLI flags and refresh-raganything subcommand"
```

---

## Task 14: Run full test suite

**Files:**
- (none modified — verification only)

- [ ] **Step 1: Run the entire test suite.**

Run: `pytest -x -q`
Expected: All tests PASS. If any pre-existing test breaks, examine the diff and fix the regression rather than skipping.

- [ ] **Step 2: Run linters (if configured).**

Run: `python -m mypy llm_wiki/raganything_adapter.py llm_wiki/raganything_refresh.py llm_wiki/raganything_query.py 2>&1 | head -40` if mypy is configured; otherwise skip.

- [ ] **Step 3: Commit any followup fixes.**

If no fixes needed, no commit. If fixes were needed:

```bash
git add -p
git commit -m "test: stabilize integration after raganything wiring"
```

---

## Task 15: Integration documentation (English)

**Files:**
- Create: `docs/integrations/rag-anything.md`

- [ ] **Step 1: Write the integration doc.** Use `docs/integrations/understand-anything.md` as the structural template (read it first). Cover the same sections in this order: header + i18n links, "Why use both?", "Current low-friction workflow", "Manual equivalent", "Native graph synchronization" (with mapping table), "Collaboration principle".

Recommended skeleton:

```markdown
# RAG-Anything multimodal companion

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/rag-anything.ko.md">한국어</a> · <a href="../i18n/integrations/rag-anything.zh.md">中文</a> · <a href="../i18n/integrations/rag-anything.ja.md">日本語</a> · <a href="../i18n/integrations/rag-anything.ru.md">Русский</a> · <a href="../i18n/integrations/rag-anything.es.md">Español</a> · <a href="../i18n/integrations/rag-anything.fr.md">Français</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) is a multimodal RAG framework (built on LightRAG) that parses PDFs, Office docs, images, and equations through MinerU/Docling/PaddleOCR. LLM-Wiki integrates it both as a multimodal ingestion pipeline (UA-style native graph projection) and as a runtime memory backend alongside Cognee.

## Why use both?
- LLM-Wiki — long-lived agent memory, wiki compilation, graph projection.
- RAG-Anything — multimodal ingestion + LightRAG runtime retrieval.

## Current low-friction workflow

```bash
llm_wiki project setup \
  --yes \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything
llm_wiki project compile
```

LLM-Wiki stores a managed refresh command rather than asking users to invent one:

```bash
llm_wiki project refresh-raganything --parser mineru
```

During compile, LLM-Wiki:

1. checks whether `.llm-wiki/external/raganything/manifest.json` exists and matches the current git commit;
2. runs the managed refresh wrapper if missing/stale or `--refresh-external-tools` is passed;
3. discovers non-code sources (PDFs, Office, images, markdown) and parses them via the configured parser;
4. writes `manifest.json` + `meta.json`;
5. continues the normal memory compile.

## Manual equivalent

```bash
pip install 'raganything[all]'
python -m llm_wiki.raganything_refresh --project . --parser mineru
llm_wiki project compile
```

## Native graph synchronization

Mapping (manifest content_list → ResearchGraph):

| RAG-Anything | LLM-Wiki direction |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | folded into `SourceFile.description`; concepts via existing extractor |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (with `img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (with `table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` + `metadata.equations[]` (LaTeX) |

The sync manifest written by the adapter:

```text
.llm-wiki/external/raganything-sync.json
```

## Runtime memory backend

`memory_backends.raganything` (default produced by `default_raganything_backend_config`) coexists with Cognee. `project ask` tries backends in priority order; per-project priority can be set via `memory_backends.priority`.

## Collaboration principle

LLM-Wiki remains the memory compiler. RAG-Anything remains an independent companion: a multimodal parser + LightRAG retrieval engine.
```

- [ ] **Step 2: Verify links resolve** with `grep -n 'rag-anything' docs/integrations/rag-anything.md`. (Linked i18n files will be created in Task 16.)

- [ ] **Step 3: Commit.**

```bash
git add docs/integrations/rag-anything.md
git commit -m "docs: add RAG-Anything integration page"
```

---

## Task 16: Localized integration docs (6 languages)

**Files:**
- Create: `docs/i18n/integrations/rag-anything.ko.md`
- Create: `docs/i18n/integrations/rag-anything.zh.md`
- Create: `docs/i18n/integrations/rag-anything.ja.md`
- Create: `docs/i18n/integrations/rag-anything.ru.md`
- Create: `docs/i18n/integrations/rag-anything.es.md`
- Create: `docs/i18n/integrations/rag-anything.fr.md`

- [ ] **Step 1: For each language, copy the corresponding `understand-anything.<lang>.md`** as a template to capture the same translations-block format and tone. Then translate the section bodies of the new English `rag-anything.md` into that language. Headings, code blocks, file paths, and CLI commands stay verbatim. Replace the translation strip's links to point to the other 5 RAG-Anything language files (and to `../../integrations/rag-anything.md` for English).

- [ ] **Step 2: Verify all 6 files exist** with `ls docs/i18n/integrations/rag-anything.*.md`. Should list 6 files.

- [ ] **Step 3: Commit.**

```bash
git add docs/i18n/integrations/rag-anything.*.md
git commit -m "docs: localize RAG-Anything integration page"
```

---

## Task 17: README updates (English + 6 i18n)

**Files:**
- Modify: `README.md`
- Modify: `README.es.md`, `README.fr.md`, `README.ja.md`, `README.ko.md`, `README.ru.md`, `README.zh.md`

- [ ] **Step 1: In `README.md`,** locate the optional-integrations / setup-snippet section that references Understand Anything and Cognee. Add a sibling line for RAG-Anything. Update the automated-setup snippet to include:

```bash
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
```

Add a bullet to the integrations list:

```markdown
- [RAG-Anything](docs/integrations/rag-anything.md) — multimodal ingestion (PDF/Office/images) + LightRAG runtime backend.
```

- [ ] **Step 2: Repeat the same edits in each `README.<lang>.md`,** preserving each language's existing tone and the same code-block content (commands stay verbatim).

- [ ] **Step 3: Verify** with `grep -l 'with-raganything' README*.md`. All 7 files should match.

- [ ] **Step 4: Commit.**

```bash
git add README.md README.*.md
git commit -m "docs: surface RAG-Anything in README and localized variants"
```

---

## Task 18: Update auxiliary docs

**Files:**
- Modify: `docs/quickstart.md`
- Modify: `docs/installation.md`
- Modify: `docs/publishing-checklist.md`
- Modify: `docs/self-dogfood.md`

- [ ] **Step 1: `docs/quickstart.md`** — in the Optional integrations section, add the raganything flags to the example command. Add a row to the flag table:

```markdown
| `--with-raganything` | Enable multimodal ingestion via RAG-Anything. |
| `--install-raganything` | Install raganything[all] during setup. |
| `--raganything-parser` | Parser choice: mineru (default), docling, paddleocr. |
| `--run-raganything` | Auto-refresh RAG-Anything on every compile. |
```

- [ ] **Step 2: `docs/installation.md`** — add an `pip install 'llm-wiki[raganything-all]'` extras line and a LibreOffice prerequisite note for Office documents.

- [ ] **Step 3: `docs/publishing-checklist.md`** — add a checkbox: `- [ ] RAG-Anything index refreshed (if enabled)`.

- [ ] **Step 4: `docs/self-dogfood.md`** — add a short paragraph showing the dogfood path of running RAG-Anything against LLM-Wiki's own docs/ and assets/.

- [ ] **Step 5: Commit.**

```bash
git add docs/quickstart.md docs/installation.md docs/publishing-checklist.md docs/self-dogfood.md
git commit -m "docs: thread RAG-Anything through quickstart, installation, checklists"
```

---

## Task 19: End-to-end smoke test (no live RAG-Anything)

**Files:**
- (verification only)

- [ ] **Step 1: Initialize a throwaway project.**

```bash
mkdir -p /tmp/llm-wiki-rag-smoke && cd /tmp/llm-wiki-rag-smoke
echo "# smoke" > README.md
mkdir -p data
printf '%%PDF-1.4 placeholder' > data/sample.pdf
git init -q && git add -A && git commit -q -m "init"
```

- [ ] **Step 2: Run setup with raganything flags but skip install** (we don't want to pull MinerU models in a smoke test).

```bash
llm_wiki project setup --yes --with-raganything --skip-install-raganything --raganything-parser mineru
```

Expected: exits 0; `.llm-wiki/config.json` has `external_tools[id=raganything]` and `memory_backends.raganything`.

- [ ] **Step 3: Hand-craft a manifest** so compile can run without real parsing:

```bash
mkdir -p .llm-wiki/external/raganything
cat > .llm-wiki/external/raganything/manifest.json <<'EOF'
{"version":1,"project":{"name":"smoke","root":"."},"parser":"mineru","documents":[{"id":"doc-smoke","path":"data/sample.pdf","sha256":"00","parsed_dir":".llm-wiki/external/raganything/parsed/00","content_list":[{"type":"text","page_idx":0,"text":"smoke"}]}]}
EOF
```

- [ ] **Step 4: Compile.**

```bash
llm_wiki project compile
```

Expected: exits 0; `.llm-wiki/external/raganything-sync.json` exists; the compiled graph contains a SourceFile node for `data/sample.pdf` with `metadata.parser="raganything"`.

- [ ] **Step 5: Cleanup.**

```bash
cd / && rm -rf /tmp/llm-wiki-rag-smoke
```

- [ ] **Step 6: If smoke test fails,** open a follow-up commit fixing the issue, then re-run. Otherwise no commit.

---

## Task 20: Final verification + push prompt

- [ ] **Step 1: Run full test suite once more.**

```bash
pytest -q
```

Expected: all green.

- [ ] **Step 2: Confirm git status is clean.**

```bash
git status
```

Expected: `nothing to commit, working tree clean` (assuming all earlier task commits succeeded).

- [ ] **Step 3: Print log summary.**

```bash
git log --oneline -25
```

Expected: All RAG-Anything commits visible at the top.

- [ ] **Step 4: Stop here.** Do not push. Hand off to user.

---

## Self-Review

**Spec coverage check:**
- §1 Goal — Tasks 2–4 (adapter), 5–8 (refresh), 9 (query), 11 (compile merge): ✓
- §4 Storage Layout — Tasks 1 (.gitignore), 7 (write_manifest), 8 (refresh dirs): ✓
- §5 Components — every file in §5.1/§5.2 has a task: ✓
- §6 Adapter mapping — Task 2 covers all four block types + external_refs schema: ✓
- §7.1 Compile path — Task 11: ✓
- §7.2 Ask path — Task 9 (query bridge); priority order itself is enforced by existing memory_backends consumer (deferred wiring lives in `project ask`, which already handles dict iteration; no separate priority code change is needed because `memory_backends` is a dict and runtime-side dispatch is in caller code). NOTE: if `project ask` hard-codes Cognee, add a follow-up task — but inspection of `query.py`/`cognee_query.py` showed no hard-coded backend gate beyond Cognee itself; new backends register via the dict.
- §8 Setup flags — Tasks 12, 13: ✓
- §9 CLI surface — Task 13: ✓
- §10 Errors/staleness — Tasks 5, 8 (skip-when-current, error log returns nonzero, --full purge): ✓
- §11 Tests — Tasks 2–13 each include their tests: ✓
- §12 Docs — Tasks 15, 16, 17, 18: ✓
- §13 Risks — addressed inline (parser-only mode, optional extras, project-relative working_dir): ✓

**Placeholder scan:** None found. Code blocks complete.

**Type/name consistency:**
- `RagAnythingGraphAdapter`, `RagAnythingImportResult`, `merge_raganything_graph` — used consistently across Tasks 2/3/4/11.
- `refresh_raganything`, `parse_documents`, `discover_sources`, `write_manifest` — consistent across Tasks 5–8.
- Manifest schema (`version=1`, `documents[].{id,path,sha256,parsed_dir,content_list}`) — consistent across Tasks 2, 7, 11, 19.
- Config keys (`enabled`, `working_dir`, `parser`, `parse_method`, `query_mode`, `vlm_enhanced`, `install`) — consistent across Tasks 9, 10, 12.

Plan is internally consistent.
