# `tesserae ingest` Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `tesserae ingest <file|url>` command that merges a single document or URL into the knowledge base, correctly (byte-identical to a full compile) and — once proven — without re-extracting the whole corpus.

**Architecture:** Persist-then-ingest. A local path is used as-is; a URL is fetched, converted to markdown, and written into the tracked corpus (`data/ingested/<slug>.md`) with provenance front-matter, then the normal pipeline runs on it as a file. The command is registered as a new top-level route (no collision with the namespaced `tesserae code ingest`). Phase 1 ships a *correct* command that drives the existing full compile. Phase 2 adds the incremental fast path behind a per-invocation override and a golden-parity test that gates the default flip; the existing provenance-readiness + over-cap fallback in `Project.compile` is the correctness backstop.

**Tech Stack:** Python 3.11, argparse CLI, pydantic models, pytest (+ `tmp_path`, `monkeypatch`). New optional extra `[ingest-url]` = `httpx` + `markdownify`. Network is always mocked in tests.

**Reference spec:** `docs/superpowers/specs/2026-06-10-ingest-command-design.md`

---

## File Structure

**Create:**
- `tesserae/ingest/__init__.py` — package exports (`ingest_sources`, `is_url`, `fetch_to_source`).
- `tesserae/ingest/fetch.py` — URL detection + fetch-to-markdown-file with provenance front-matter. Behind `[ingest-url]`.
- `tesserae/ingest/orchestrator.py` — resolve inputs → (URL? fetch) → drive compile → report. The B→C seam.
- `tests/test_ingest_fetch.py` — unit tests for `is_url` / `fetch_to_source` (HTTP mocked).
- `tests/test_ingest_orchestrator.py` — unit tests for `ingest_sources` (file + URL + dry-run + exact).
- `tests/test_ingest_cli.py` — CLI arg-parsing + routing tests.
- `tests/test_ingest_parity.py` — golden parity: `ingest(X)` ≡ full `compile(corpus ∪ X)`.
- `docs/commands/ingest.md` — English command doc (+ 7-language i18n in the final task).

**Modify:**
- `pyproject.toml` — add `ingest-url` optional-dependency.
- `tesserae/cli.py` — add `_build_ingest_parser`, `_route_ingest`, `_handle_ingest_docs`; register `"ingest"` in `_NEW_DISPATCH`.
- `tesserae/project.py` — add `incremental_override: Optional[bool] = None` seam to `ingest()` / `compile()` (Phase 2 only).

---

## Phase 1 — Correct, shippable command (full recompile under the hood)

### Task 1: Scaffold the `tesserae/ingest/` package and the `[ingest-url]` extra

**Files:**
- Create: `tesserae/ingest/__init__.py`
- Modify: `pyproject.toml` (the `[project.optional-dependencies]` block)

- [ ] **Step 1: Create the package init**

```python
# tesserae/ingest/__init__.py
"""Single-source ingest: merge one document or URL into the KB.

See docs/superpowers/specs/2026-06-10-ingest-command-design.md.
"""

from tesserae.ingest.fetch import fetch_to_source, is_url
from tesserae.ingest.orchestrator import ingest_sources

__all__ = ["ingest_sources", "fetch_to_source", "is_url"]
```

> NOTE: this imports `fetch` and `orchestrator` which are created in Tasks 2–7. Until then the
> package import will fail — that is expected; do not run the package import until Task 7.

- [ ] **Step 2: Add the optional extra to `pyproject.toml`**

Find the block (currently):
```toml
[project.optional-dependencies]
synthesis-llm = ["anthropic>=0.40"]
raganything = ["raganything>=1.3.0"]
raganything-all = ["raganything[all]>=1.3.0"]
raganything-semantic = ["raganything[all]>=1.3.0", "sentence-transformers>=3.0"]
semantic = ["model2vec>=0.3"]
```
Add one line after `semantic`:
```toml
ingest-url = ["httpx>=0.24", "markdownify>=0.11"]
```

- [ ] **Step 3: Commit**

```bash
git add tesserae/ingest/__init__.py pyproject.toml
git commit -m "feat(ingest): scaffold ingest package + [ingest-url] extra"
```

---

### Task 2: `is_url` helper

**Files:**
- Create: `tesserae/ingest/fetch.py`
- Test: `tests/test_ingest_fetch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest_fetch.py
from tesserae.ingest.fetch import is_url


def test_is_url_true_for_http_and_https():
    assert is_url("http://example.com/a")
    assert is_url("https://arxiv.org/abs/2401.12345")


def test_is_url_false_for_paths():
    assert not is_url("notes/a.md")
    assert not is_url("/abs/path/to/file.md")
    assert not is_url("./relative.md")
    assert not is_url("ftp://example.com/x")  # only http(s) is a URL for ingest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest_fetch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesserae.ingest.fetch'`

- [ ] **Step 3: Write minimal implementation**

```python
# tesserae/ingest/fetch.py
"""Fetch a URL into a tracked markdown source. Behind the [ingest-url] extra."""

from __future__ import annotations


def is_url(value: str) -> bool:
    """True only for http(s) URLs — everything else is treated as a local path."""
    return value.startswith("http://") or value.startswith("https://")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest_fetch.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add tesserae/ingest/fetch.py tests/test_ingest_fetch.py
git commit -m "feat(ingest): add is_url helper"
```

---

### Task 3: `_slugify` + provenance front-matter rendering

**Files:**
- Modify: `tesserae/ingest/fetch.py`
- Test: `tests/test_ingest_fetch.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_ingest_fetch.py
from tesserae.ingest.fetch import _slugify, _render_frontmatter


def test_slugify_url_is_filesystem_safe_and_stable():
    slug = _slugify("https://arxiv.org/abs/2401.12345")
    assert slug == "arxiv-org-abs-2401-12345"
    # stable + deterministic
    assert _slugify("https://arxiv.org/abs/2401.12345") == slug


def test_slugify_truncates_long_urls_with_hash_suffix():
    long = "https://example.com/" + "a" * 200
    slug = _slugify(long)
    assert len(slug) <= 80
    # two different long URLs that share an 80-char prefix must not collide
    other = "https://example.com/" + "a" * 199 + "b"
    assert _slugify(long) != _slugify(other)


def test_render_frontmatter_emits_sorted_yaml_block():
    fm = _render_frontmatter(
        {"source_url": "https://x.com", "content_sha256": "abc", "fetched_at": "2026-06-10T00:00:00Z"}
    )
    assert fm.startswith("---\n")
    assert fm.rstrip().endswith("---")
    assert "source_url: https://x.com" in fm
    # keys sorted for deterministic output
    assert fm.index("content_sha256") < fm.index("fetched_at") < fm.index("source_url")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest_fetch.py -v`
Expected: FAIL with `ImportError: cannot import name '_slugify'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/ingest/fetch.py (top: add imports)
import hashlib
import re
from typing import Dict

_SLUG_MAX = 80


def _slugify(value: str) -> str:
    """Filesystem-safe, deterministic slug. Long values get a hash suffix to avoid collisions."""
    stripped = re.sub(r"^https?://", "", value)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", stripped).strip("-").lower()
    if len(slug) <= _SLUG_MAX:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return slug[: _SLUG_MAX - 9].rstrip("-") + "-" + digest


def _render_frontmatter(meta: Dict[str, str]) -> str:
    """Render a deterministic YAML front-matter block (keys sorted)."""
    lines = ["---"]
    for key in sorted(meta):
        lines.append(f"{key}: {meta[key]}")
    lines.append("---")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest_fetch.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add tesserae/ingest/fetch.py tests/test_ingest_fetch.py
git commit -m "feat(ingest): add slugify + frontmatter rendering"
```

---

### Task 4: `fetch_to_source` — fetch URL, convert, persist (HTTP mocked)

**Files:**
- Modify: `tesserae/ingest/fetch.py`
- Test: `tests/test_ingest_fetch.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_ingest_fetch.py
import pytest
from tesserae.ingest.fetch import fetch_to_source


class _FakeResponse:
    def __init__(self, *, status_code=200, text="", content_type="text/html"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_get(response):
    def _get(url, timeout=None, follow_redirects=True, headers=None):
        return response
    return _get


def test_fetch_html_writes_markdown_file_with_frontmatter(tmp_path, monkeypatch):
    html = "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        _fake_get(_FakeResponse(text=html, content_type="text/html")),
    )
    dest = tmp_path / "data" / "ingested"
    path = fetch_to_source("https://example.com/post", dest)

    assert path.parent == dest
    assert path.suffix == ".md"
    body = path.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "source_url: https://example.com/post" in body
    assert "content_sha256:" in body
    assert "fetched_at:" in body
    # markdown conversion happened (heading survives, tags stripped)
    assert "Title" in body
    assert "<h1>" not in body


def test_fetch_non_2xx_raises_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        _fake_get(_FakeResponse(status_code=404, content_type="text/html")),
    )
    dest = tmp_path / "data" / "ingested"
    with pytest.raises(RuntimeError):
        fetch_to_source("https://example.com/missing", dest)
    assert not dest.exists() or not any(dest.iterdir())


def test_fetch_missing_extra_raises_actionable_error(tmp_path, monkeypatch):
    # Simulate httpx/markdownify not installed.
    monkeypatch.setattr("tesserae.ingest.fetch._http_get", None)
    monkeypatch.setattr("tesserae.ingest.fetch._html_to_markdown", None)
    with pytest.raises(ImportError, match=r"pip install tesserae\[ingest-url\]"):
        fetch_to_source("https://example.com/post", tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest_fetch.py -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_to_source'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/ingest/fetch.py
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Lazy, import-guarded bindings so the base install (no [ingest-url]) still imports
# this module. Tests monkeypatch these two names directly.
_http_get: Optional[Callable] = None
_html_to_markdown: Optional[Callable] = None


def _load_url_deps() -> None:
    """Bind _http_get / _html_to_markdown from the optional extra, or raise."""
    global _http_get, _html_to_markdown
    if _http_get is not None and _html_to_markdown is not None:
        return
    try:
        import httpx
        from markdownify import markdownify as _md
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch in tests
        raise ImportError(
            "URL ingest requires the optional extra: pip install tesserae[ingest-url]"
        ) from exc
    if _http_get is None:
        _http_get = lambda url, timeout=None, follow_redirects=True, headers=None: httpx.get(
            url, timeout=timeout, follow_redirects=follow_redirects, headers=headers or {}
        )
    if _html_to_markdown is None:
        _html_to_markdown = lambda html: _md(html)


def fetch_to_source(url: str, dest_dir: Path, *, title: Optional[str] = None) -> Path:
    """Fetch ``url``, convert to markdown, persist under ``dest_dir`` with provenance.

    Returns the written file path. Raises on non-2xx, on a missing [ingest-url] extra,
    and writes nothing on failure.
    """
    if _http_get is None or _html_to_markdown is None:
        _load_url_deps()

    response = _http_get(url, timeout=30.0, follow_redirects=True, headers={"User-Agent": "tesserae-ingest"})
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")

    raw = response.text
    if "html" in content_type:
        body = _html_to_markdown(raw)
    else:
        # text/plain, text/markdown, or unknown: keep raw (extractor tolerates plain md)
        body = raw

    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    meta = {
        "source_url": url,
        "content_sha256": sha,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if title:
        meta["title"] = title

    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{_slugify(url)}.md"
    path.write_text(_render_frontmatter(meta) + "\n" + body.strip() + "\n", encoding="utf-8")
    return path
```

> NOTE on the `_load_url_deps` guard: when tests monkeypatch `_http_get`/`_html_to_markdown`
> to real callables, `fetch_to_source` skips `_load_url_deps`. When a test sets BOTH to
> `None`, `_load_url_deps` runs and (with the extra absent in CI's base env) raises the
> actionable `ImportError`. If CI installs `[ingest-url]`, that specific test must instead
> monkeypatch `_load_url_deps` to raise — adjust in Step 1 if the test env has the extra.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest_fetch.py -v`
Expected: PASS (all). If `test_fetch_missing_extra_raises_actionable_error` fails because the
extra IS installed in the dev env, change that test to
`monkeypatch.setattr("tesserae.ingest.fetch._load_url_deps", lambda: (_ for _ in ()).throw(ImportError("pip install tesserae[ingest-url]")))`.

- [ ] **Step 5: Commit**

```bash
git add tesserae/ingest/fetch.py tests/test_ingest_fetch.py
git commit -m "feat(ingest): fetch_to_source — fetch, convert, persist with provenance"
```

---

### Task 5: arxiv special-case (add `arxiv_id` to front-matter)

**Files:**
- Modify: `tesserae/ingest/fetch.py`
- Test: `tests/test_ingest_fetch.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_ingest_fetch.py
from tesserae.ingest.fetch import _arxiv_id_from_url


def test_arxiv_id_extracted_from_abs_url():
    assert _arxiv_id_from_url("https://arxiv.org/abs/2401.12345") == "2401.12345"
    assert _arxiv_id_from_url("https://arxiv.org/abs/2401.12345v2") == "2401.12345"
    assert _arxiv_id_from_url("https://example.com/post") is None


def test_fetch_arxiv_url_records_arxiv_id_in_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        _fake_get(_FakeResponse(text="<p>abstract</p>", content_type="text/html")),
    )
    path = fetch_to_source("https://arxiv.org/abs/2401.12345", tmp_path / "ing")
    body = path.read_text(encoding="utf-8")
    assert "arxiv_id: 2401.12345" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest_fetch.py -v`
Expected: FAIL with `ImportError: cannot import name '_arxiv_id_from_url'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tesserae/ingest/fetch.py
_ARXIV_RE = re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5})")


def _arxiv_id_from_url(url: str) -> Optional[str]:
    m = _ARXIV_RE.search(url)
    return m.group(1) if m else None
```
Then in `fetch_to_source`, after building `meta` and before writing, add:
```python
    arxiv_id = _arxiv_id_from_url(url)
    if arxiv_id:
        meta["arxiv_id"] = arxiv_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest_fetch.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add tesserae/ingest/fetch.py tests/test_ingest_fetch.py
git commit -m "feat(ingest): record arxiv_id in fetched-source frontmatter"
```

---

### Task 6: `ingest_sources` orchestrator — local-file happy path (full recompile)

**Files:**
- Create: `tesserae/ingest/orchestrator.py`
- Test: `tests/test_ingest_orchestrator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest_orchestrator.py
from pathlib import Path

from tesserae.project import ProjectWiki
from tesserae.ingest.orchestrator import ingest_sources


def _seed_project(root: Path) -> ProjectWiki:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "seed.md").write_text(
        "---\ntype: paper\n---\n# Seed\n\nGraph neural networks for retrieval.\n",
        encoding="utf-8",
    )
    return ProjectWiki.init(root, name="ingest_test")


def test_ingest_local_file_merges_and_reports(tmp_path):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)  # establish baseline graph

    new = tmp_path / "data" / "new.md"
    new.write_text("---\ntype: paper\n---\n# New\n\nDiffusion models for planning.\n", encoding="utf-8")

    result = ingest_sources(wiki, [str(new)], exact=True)

    assert result["path_taken"] == "full-recompile"
    assert result["node_count"] > 0
    assert "graph_path" in result
    # the new doc's content is now in the graph
    graph_text = Path(result["graph_path"]).read_text(encoding="utf-8")
    assert "Diffusion" in graph_text or "diffusion" in graph_text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesserae.ingest.orchestrator'`

- [ ] **Step 3: Write minimal implementation**

```python
# tesserae/ingest/orchestrator.py
"""Orchestrate single-source ingest: resolve inputs, (fetch URLs), drive compile, report.

This is the B->C seam: v1 drives compile synchronously. A future v2 (--async) splits the
compile() call into an instant approximate write + an enqueued reconcile.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from tesserae.ingest.fetch import fetch_to_source, is_url


def ingest_sources(
    wiki,
    inputs: Sequence[str],
    *,
    source_kind: Optional[str] = None,
    title: Optional[str] = None,
    exact: bool = True,
    dry_run: bool = False,
) -> dict:
    """Ingest one or more file paths / URLs into ``wiki``'s knowledge base.

    ``exact=True`` forces a full recompile (correct by construction). ``exact=False`` opts
    the run into the incremental fast path (Phase 2). Returns a report dict with keys:
    ``path_taken``, ``node_count``, ``edge_count``, ``processed_files``, ``skipped_files``,
    ``graph_path``, ``sources`` (resolved input paths).
    """
    resolved: List[str] = []
    dest = Path(wiki.project_root) / "data" / "ingested"
    for item in inputs:
        if is_url(item):
            path = fetch_to_source(item, dest, title=title)
            resolved.append(str(path))
        else:
            resolved.append(item)

    if dry_run:
        return {
            "path_taken": "dry-run",
            "sources": resolved,
            "node_count": 0,
            "edge_count": 0,
            "processed_files": 0,
            "skipped_files": 0,
            "graph_path": str(wiki.paths.graph),
        }

    # Phase 1: always full recompile (correct). Phase 2 flips this on exact=False.
    result = wiki.ingest(resolved, source_kind=source_kind, changed_only=False)
    return {
        "path_taken": "full-recompile",
        "sources": resolved,
        "node_count": result["node_count"],
        "edge_count": result["edge_count"],
        "processed_files": result["processed_files"],
        "skipped_files": result["skipped_files"],
        "graph_path": result["graph_path"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tesserae/ingest/orchestrator.py tests/test_ingest_orchestrator.py
git commit -m "feat(ingest): orchestrator local-file happy path (full recompile)"
```

---

### Task 7: orchestrator — URL input + dry-run

**Files:**
- Test: `tests/test_ingest_orchestrator.py`
- (No new impl — verifies Task 6 code paths; add impl only if a test fails.)

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_ingest_orchestrator.py
def test_ingest_url_fetches_persists_and_merges(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)

    class _Resp:
        status_code = 200
        text = "<h1>Remote</h1><p>Reinforcement learning from human feedback.</p>"
        headers = {"content-type": "text/html"}
        def raise_for_status(self): pass

    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        lambda url, timeout=None, follow_redirects=True, headers=None: _Resp(),
    )
    result = ingest_sources(wiki, ["https://example.com/rlhf"], exact=True)

    persisted = tmp_path / "data" / "ingested"
    assert any(persisted.glob("*.md"))
    assert result["path_taken"] == "full-recompile"
    assert result["node_count"] > 0


def test_ingest_dry_run_writes_no_graph_but_fetches(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    new = tmp_path / "data" / "dry.md"
    new.write_text("---\ntype: paper\n---\n# Dry\n\ncontent\n", encoding="utf-8")

    result = ingest_sources(wiki, [str(new)], dry_run=True)
    assert result["path_taken"] == "dry-run"
    assert result["sources"] == [str(new)]
```

- [ ] **Step 2: Run tests to verify they pass (impl already exists)**

Run: `pytest tests/test_ingest_orchestrator.py -v`
Expected: PASS. If `test_ingest_url_...` fails on `data/ingested` not being a discovered
source root, confirm `ProjectWiki.init` seeds `data` as a default source (spec §6 / project.py
default_sources). If `data/ingested/` is not auto-discovered, fix in orchestrator by passing
the persisted path explicitly to `wiki.ingest([...])` (already done — `resolved` carries the
exact path), so the parity here is on the *graph*, not discovery.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ingest_orchestrator.py
git commit -m "test(ingest): URL fetch + dry-run orchestrator paths"
```

---

### Task 8: CLI wiring — `tesserae ingest` top-level command

**Files:**
- Modify: `tesserae/cli.py`
- Test: `tests/test_ingest_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ingest_cli.py
import tesserae.cli as cli


def test_ingest_is_a_top_level_command():
    assert "ingest" in cli._NEW_DISPATCH


def test_ingest_parser_accepts_inputs_and_flags():
    parser = cli._build_ingest_parser()
    args = parser.parse_args(["a.md", "https://x.com", "--exact", "--project", "/tmp/p"])
    assert args.inputs == ["a.md", "https://x.com"]
    assert args.exact is True
    assert args.project == "/tmp/p"
    assert args._handler == "_handle_ingest_docs"


def test_code_ingest_still_exists_unchanged():
    # the namespaced code-graph ingest must remain `tesserae code ingest`
    code_parser = cli._build_code_parser()
    ns = code_parser.parse_args(["ingest", "--project", "."])
    assert ns._handler == "_handle_code_ingest"


def test_ingest_dispatch_calls_orchestrator(monkeypatch, tmp_path):
    captured = {}
    def _fake_ingest_sources(wiki, inputs, **kw):
        captured["inputs"] = list(inputs)
        captured["kw"] = kw
        return {"path_taken": "full-recompile", "node_count": 1, "edge_count": 0,
                "processed_files": 1, "skipped_files": 0,
                "graph_path": str(tmp_path / "g.json"), "sources": list(inputs)}
    monkeypatch.setattr("tesserae.cli.ingest_sources", _fake_ingest_sources)
    monkeypatch.setattr("tesserae.cli.ProjectWiki", _StubWiki)
    rc = cli.main(["ingest", "x.md", "--project", str(tmp_path), "--exact"])
    assert rc == 0
    assert captured["inputs"] == ["x.md"]
    assert captured["kw"]["exact"] is True


class _StubWiki:
    project_root = "."
    @staticmethod
    def load(project):
        return _StubWiki()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest_cli.py -v`
Expected: FAIL (`_build_ingest_parser` missing / `"ingest"` not in `_NEW_DISPATCH`).

- [ ] **Step 3: Write the implementation in `tesserae/cli.py`**

Add the import near the other ingest imports at the top of the dispatch section:
```python
from tesserae.ingest.orchestrator import ingest_sources
```
Add the parser builder (place near `_build_code_parser`, ~line 2412):
```python
def _build_ingest_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae ingest",
        description="Ingest a single document file or URL into the knowledge base.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", help="File path(s) or http(s) URL(s) to ingest")
    parser.add_argument("--project", default=".", help="Project root directory")
    parser.add_argument("--title", default=None, help="Title override (useful for URLs)")
    parser.add_argument("--source-kind", default=None, help="Override source classification")
    parser.add_argument("--exact", action="store_true", help="Force a full recompile (skip the fast path)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + report, write no graph")
    parser.set_defaults(_handler="_handle_ingest_docs")
    return parser
```
Add the route (place near `_route_code`):
```python
def _route_ingest(rest: List[str]) -> int:
    args = _build_ingest_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)
```
Add the handler (place near `_handle_ingest`, ~line 940 — DO NOT modify the existing
`_handle_ingest`, which is used by `compile <paths>`):
```python
def _handle_ingest_docs(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    result = ingest_sources(
        wiki,
        args.inputs,
        source_kind=args.source_kind,
        title=args.title,
        exact=args.exact,
        dry_run=getattr(args, "dry_run", False),
    )
    print(
        f"Ingested ({result['path_taken']}): "
        f"processed={result['processed_files']} skipped={result['skipped_files']} "
        f"nodes={result['node_count']} edges={result['edge_count']}"
    )
    print(f"Sources: {', '.join(result['sources'])}")
    print(f"Graph: {result['graph_path']}")
    return 0
```
Register in `_NEW_DISPATCH` (line ~2938), add one entry:
```python
    "ingest": _route_ingest,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ingest_cli.py -v`
Expected: PASS (all 4)

- [ ] **Step 5: Run the full ingest suite + a smoke run**

Run: `pytest tests/test_ingest_fetch.py tests/test_ingest_orchestrator.py tests/test_ingest_cli.py -v`
Expected: PASS. Then a real smoke run:
```bash
cd /tmp && rm -rf ingsmoke && mkdir ingsmoke && cd ingsmoke
printf -- '---\ntype: paper\n---\n# A\n\ngraph retrieval\n' > a.md
python -m tesserae.cli init --name smoke 2>/dev/null || tesserae init --name smoke
tesserae ingest a.md --exact
```
Expected: prints `Ingested (full-recompile): ... nodes=N ...`.

- [ ] **Step 6: Commit**

```bash
git add tesserae/cli.py tests/test_ingest_cli.py
git commit -m "feat(ingest): wire top-level 'tesserae ingest' command"
```

---

### Task 9: English documentation page

**Files:**
- Create: `docs/commands/ingest.md`

- [ ] **Step 1: Write the doc**

```markdown
# `tesserae ingest`

Merge a single document file or URL into the knowledge base.

## Usage

    tesserae ingest <input>...  [--title T] [--source-kind K] [--exact] [--dry-run]

`<input>` is one or more local file paths or `http(s)` URLs. URLs are fetched, converted to
markdown, and persisted under `data/ingested/<slug>.md` with provenance front-matter
(`source_url`, `fetched_at`, `content_sha256`, and `arxiv_id` when detected), then merged.

URL ingest requires the optional extra:

    pip install tesserae[ingest-url]

## Flags

- `--exact` — force a full recompile (always correct; default until the incremental fast
  path is proven byte-identical).
- `--dry-run` — fetch and report what would be ingested; write no graph.
- `--title` — title override, useful for bare URLs.
- `--source-kind` — override the source classification.

## Relationship to `compile`

`tesserae compile` (no args) re-extracts the whole tracked corpus. `tesserae ingest <x>`
adds one source. It is distinct from `tesserae code ingest`, which mints a code graph from
Python source.
```

- [ ] **Step 2: Verify it renders / links are valid**

Run: `pytest tests/test_link_integrity.py -v` (if the docs are part of the site corpus) or
visually confirm. Expected: no broken-link failures introduced.

- [ ] **Step 3: Commit**

```bash
git add docs/commands/ingest.md
git commit -m "docs(ingest): English command reference"
```

---

### Task 10: i18n — translate the command doc into all 7 languages

**Files:**
- Create: `docs/commands/ingest.{ko,zh,ja,ru,es,fr,de}.md` (or the project's established i18n
  path layout — confirm before writing).

> Project invariant: every doc under `docs/` ships in ko/zh/ja/ru/es/fr/de. Before writing,
> inspect an existing translated doc to copy the EXACT path/naming convention (e.g.
> `docs/i18n/<lang>/commands/ingest.md` vs `docs/commands/ingest.<lang>.md`).

- [ ] **Step 1: Find the established i18n layout**

Run: `ls docs && find docs -name '*.ko.md' -o -path '*/ko/*' | head`
Expected: reveals whether translations are suffix-based or directory-based.

- [ ] **Step 2: Create the 7 translations following that exact layout**

Translate `docs/commands/ingest.md` (Task 9) into ko, zh, ja, ru, es, fr, de, preserving the
code blocks and flag names verbatim (only prose is translated).

- [ ] **Step 3: Verify the i18n completeness check passes**

Run: the project's i18n/doc-coverage test if one exists (search: `pytest -k i18n -v` or
`pytest tests/test_docs_i18n*.py -v`). Expected: PASS / all 7 languages present.

- [ ] **Step 4: Commit**

```bash
git add docs/commands/ingest.*.md docs/i18n 2>/dev/null
git commit -m "docs(ingest): i18n translations (ko/zh/ja/ru/es/fr/de)"
```

---

## Phase 2 — Incremental fast path, gated by a golden-parity test

> Ship gate (spec §7): `ingest` keeps `--exact` (full recompile) as the DEFAULT until the
> parity test in Task 13 is green. Only then does Task 14 flip the default.

### Task 11: `incremental_override` seam in `Project.ingest` / `Project.compile`

**Files:**
- Modify: `tesserae/project.py` (the `incremental_enabled` lookup, ~line 537; both `ingest`
  and `compile` signatures, lines 448 and 1324)
- Test: `tests/test_ingest_incremental_seam.py`

- [ ] **Step 1: Read the current incremental gate before editing**

Run: open `tesserae/project.py` lines 448–470, 534–557, 1324–1380. Confirm `compile()`
delegates to `ingest()` and that `incremental_enabled = bool(cfg.get("incremental_compile", False))`
is the single gate.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_ingest_incremental_seam.py
from pathlib import Path
from tesserae.project import ProjectWiki


def _seed(root: Path) -> ProjectWiki:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "s.md").write_text("---\ntype: paper\n---\n# S\n\nretrieval graphs\n", encoding="utf-8")
    return ProjectWiki.init(root, name="seam_test")


def test_incremental_override_enables_without_config_flag(tmp_path, monkeypatch):
    wiki = _seed(tmp_path)
    wiki.compile(changed_only=False)
    captured = {}
    # Spy on the gate by reading the warning the incremental path logs when enabled.
    import logging
    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())
    logging.getLogger("tesserae.project").addHandler(handler)

    (tmp_path / "data" / "n.md").write_text("---\ntype: paper\n---\n# N\n\ndiffusion planning\n", encoding="utf-8")
    # override=True must take the incremental branch even though config flag is default-False
    wiki.ingest([str(tmp_path / "data" / "n.md")], changed_only=True, incremental_override=True)
    assert any("incremental_compile is ENABLED" in m for m in records)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_ingest_incremental_seam.py -v`
Expected: FAIL — `ingest() got an unexpected keyword argument 'incremental_override'`.

- [ ] **Step 4: Implement the seam**

In `tesserae/project.py`, add `incremental_override: Optional[bool] = None` to BOTH the
`ingest()` signature (line 448) and the `compile()` signature (line 1324), and thread it from
`compile()` into its internal `ingest()` call. Then change the gate (line 537) from:
```python
incremental_enabled = bool(cfg.get("incremental_compile", False))
```
to:
```python
incremental_enabled = (
    incremental_override
    if incremental_override is not None
    else bool(cfg.get("incremental_compile", False))
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_ingest_incremental_seam.py -v`
Expected: PASS

- [ ] **Step 6: Run the incremental parity suite to confirm no regression**

Run: `pytest tests/test_incremental_parity.py -v`
Expected: PASS (the seam is inert when `incremental_override is None`).

- [ ] **Step 7: Commit**

```bash
git add tesserae/project.py tests/test_ingest_incremental_seam.py
git commit -m "feat(ingest): per-invocation incremental_override seam in compile/ingest"
```

---

### Task 12: orchestrator fast path (`exact=False` → incremental override)

**Files:**
- Modify: `tesserae/ingest/orchestrator.py`
- Test: `tests/test_ingest_orchestrator.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_ingest_orchestrator.py
def test_ingest_fast_path_uses_incremental_override(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)
    seen = {}
    real_ingest = wiki.ingest
    def _spy(inputs, **kw):
        seen.update(kw)
        return real_ingest(inputs, **kw)
    monkeypatch.setattr(wiki, "ingest", _spy)

    (tmp_path / "data" / "f.md").write_text("---\ntype: paper\n---\n# F\n\ncontent\n", encoding="utf-8")
    result = ingest_sources(wiki, [str(tmp_path / "data" / "f.md")], exact=False)

    assert seen.get("changed_only") is True
    assert seen.get("incremental_override") is True
    assert result["path_taken"] == "incremental"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest_orchestrator.py::test_ingest_fast_path_uses_incremental_override -v`
Expected: FAIL (orchestrator still always full-recompiles; `path_taken` == "full-recompile").

- [ ] **Step 3: Implement the fast path**

In `tesserae/ingest/orchestrator.py`, replace the Phase-1 single-branch compile with:
```python
    if exact:
        result = wiki.ingest(resolved, source_kind=source_kind, changed_only=False)
        path_taken = "full-recompile"
    else:
        result = wiki.ingest(
            resolved, source_kind=source_kind, changed_only=True, incremental_override=True
        )
        path_taken = "incremental"
    return {
        "path_taken": path_taken,
        "sources": resolved,
        "node_count": result["node_count"],
        "edge_count": result["edge_count"],
        "processed_files": result["processed_files"],
        "skipped_files": result["skipped_files"],
        "graph_path": result["graph_path"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest_orchestrator.py -v`
Expected: PASS (all). Existing `exact=True` tests still report `full-recompile`.

- [ ] **Step 5: Commit**

```bash
git add tesserae/ingest/orchestrator.py tests/test_ingest_orchestrator.py
git commit -m "feat(ingest): incremental fast path behind exact=False"
```

---

### Task 13: GOLDEN PARITY — `ingest(X)` ≡ full `compile(corpus ∪ X)`

**Files:**
- Test: `tests/test_ingest_parity.py`

- [ ] **Step 1: Write the parity test (the correctness contract)**

```python
# tests/test_ingest_parity.py
"""Golden parity: ingest(X) graph.json == full compile(corpus ∪ X), byte-for-byte."""
from pathlib import Path

from tesserae.project import ProjectWiki
from tesserae.ingest.orchestrator import ingest_sources


def _write(root: Path, name: str, body: str) -> Path:
    (root / "data").mkdir(parents=True, exist_ok=True)
    p = root / "data" / name
    p.write_text(body, encoding="utf-8")
    return p


_CORPUS = {
    "a.md": "---\ntype: paper\n---\n# A\n\nGraph neural networks for retrieval.\n",
    "b.md": "---\ntype: paper\n---\n# B\n\nRetrieval augmented generation.\n",
}
_NEW = ("c.md", "---\ntype: paper\n---\n# C\n\nDiffusion models for planning.\n")


def _full_compile_bytes(root: Path) -> bytes:
    wiki = ProjectWiki.init(root, name="parity")
    for name, body in _CORPUS.items():
        _write(root, name, body)
    _write(root, _NEW[0], _NEW[1])
    wiki.compile(changed_only=False)
    return wiki.paths.graph.read_bytes()


def _ingest_bytes(root: Path, *, exact: bool) -> bytes:
    wiki = ProjectWiki.init(root, name="parity")
    for name, body in _CORPUS.items():
        _write(root, name, body)
    wiki.compile(changed_only=False)  # baseline WITHOUT the new doc
    new = _write(root, _NEW[0], _NEW[1])
    ingest_sources(wiki, [str(new)], exact=exact)
    return wiki.paths.graph.read_bytes()


def test_ingest_exact_matches_full_compile(tmp_path):
    full = _full_compile_bytes(tmp_path / "full")
    ing = _ingest_bytes(tmp_path / "ing", exact=True)
    assert ing == full, "ingest --exact graph.json must equal a full compile"


def test_ingest_fast_matches_full_compile(tmp_path):
    full = _full_compile_bytes(tmp_path / "full2")
    ing = _ingest_bytes(tmp_path / "ing2", exact=False)
    assert ing == full, (
        "INCREMENTAL ingest graph.json diverged from a full compile. If this fails, the "
        "additive case has hit a residual gap (multi-owner winner-change / untracked "
        "post-pass edges). Fix by extending the full-recompile fallback condition in "
        "Project.compile (~line 843) to detect this case — do NOT relax the assertion."
    )
```

- [ ] **Step 2: Run the parity tests**

Run: `pytest tests/test_ingest_parity.py -v`
Expected: `test_ingest_exact_matches_full_compile` PASS. `test_ingest_fast_matches_full_compile`
is the gate — it MUST pass before Task 14. If it fails, follow the assertion message: extend
the fallback in `Project.compile` so the offending additive shape degrades to a full recompile,
then re-run. Do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ingest_parity.py
git commit -m "test(ingest): golden parity — ingest == full compile"
```

---

### Task 14: Flip the default to the fast path (only if Task 13 is green)

**Files:**
- Modify: `tesserae/cli.py` (`_build_ingest_parser`), `tesserae/ingest/orchestrator.py`
  (default `exact`), `docs/commands/ingest.md`
- Test: `tests/test_ingest_cli.py`

- [ ] **Step 1: Confirm the gate**

Run: `pytest tests/test_ingest_parity.py -v`
Expected: BOTH parity tests PASS. If `test_ingest_fast_matches_full_compile` is not green,
STOP — do not flip the default.

- [ ] **Step 2: Write the failing test for the new default**

```python
# add to tests/test_ingest_cli.py
def test_ingest_defaults_to_fast_path():
    parser = cli._build_ingest_parser()
    args = parser.parse_args(["a.md"])
    assert args.exact is False  # fast path by default; --exact opts into full recompile
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_ingest_cli.py::test_ingest_defaults_to_fast_path -v`
Expected: FAIL (currently `--exact` is a `store_true` defaulting False — this already passes
IF the parser default is False). If it already passes, the only real change is making the
orchestrator default `exact=False` and updating docs; proceed to Step 4.

- [ ] **Step 4: Make fast the default**

In `tesserae/ingest/orchestrator.py`, change the `ingest_sources` signature default
`exact: bool = True` → `exact: bool = False`. In `tesserae/cli.py` `_handle_ingest_docs`,
the flag already maps `--exact`→True, so no CLI change needed beyond confirming. Update
`docs/commands/ingest.md` to state the default is now the incremental fast path with
automatic full-recompile fallback.

- [ ] **Step 5: Run the entire ingest + parity suite**

Run: `pytest tests/test_ingest_fetch.py tests/test_ingest_orchestrator.py tests/test_ingest_cli.py tests/test_ingest_parity.py tests/test_ingest_incremental_seam.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Run the full project suite (no regressions)**

Run: `pytest -q`
Expected: green (matches the project's known-green baseline).

- [ ] **Step 7: Commit**

```bash
git add tesserae/ingest/orchestrator.py tesserae/cli.py docs/commands/ingest.md tests/test_ingest_cli.py
git commit -m "feat(ingest): default to incremental fast path (parity-gated)"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** §4 architecture → Tasks 6–8; §5 command surface + flags → Task 8;
  §6 components (fetch, orchestrator, CLI) → Tasks 2–8; persisted-source convention → Tasks 4–6;
  §7 correctness guard (override + existing fallback + parity gate) → Tasks 11–14; §8 error
  handling (non-2xx, missing extra, dry-run) → Tasks 4, 7; §9 testing (golden parity, guard,
  fetch mocked, CLI, i18n) → Tasks 4–14; §10 phasing (B now, C seam) → orchestrator docstring
  Task 6; §11 R2 dependency choice → Task 1 (httpx + markdownify). All sections mapped.
- **Placeholder scan:** every code step contains complete code; the only "find the convention
  first" step (Task 10 i18n layout) is a concrete inspection instruction, not a placeholder.
- **Type consistency:** `ingest_sources(wiki, inputs, *, source_kind, title, exact, dry_run)`,
  `fetch_to_source(url, dest_dir, *, title)`, `is_url(str)`, report dict keys
  (`path_taken`/`node_count`/`edge_count`/`processed_files`/`skipped_files`/`graph_path`/`sources`)
  and handler name `_handle_ingest_docs` are consistent across all tasks.

## Open items deferred to the implementer (from spec §11)

- **R2** resolved to `httpx + markdownify`; swap in Task 1 if the project prefers stdlib
  `urllib` + a different converter. PDF handling is NOT in v1 (HTML/text only).
- **Q1** multiple inputs: handled — `ingest_sources` accepts a list and passes all resolved
  paths to a single `wiki.ingest([...])` (one compile over all new sources).
- **Q2** slug collisions: handled by `_slugify`'s hash suffix for long/colliding URLs.
