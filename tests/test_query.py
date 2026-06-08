"""Tests for the wiki query / Q&A surface (``tesserae.query``).

The fake Anthropic SDK lives in this module so no real network call ever
fires. We swap it in via the ``set_client_factory`` test seam, mirroring
``tests/test_llm_synthesis.py``.

The fixture below hand-crafts a tiny ``.tesserae/`` workspace — just the
``site/search-index.json`` and matching ``wiki/<kind>/<slug>.md`` pages —
because spinning up a full ``ProjectWiki.compile`` is overkill for a search
unit test and would couple us to the extractor's behavior on synthetic
prose.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tesserae.query import (
    WikiQuery,
    ask_project,
    reset_failure_log_for_tests,
    set_client_factory,
)


class _StubResult:
    def to_dict(self):
        return {"hits": [], "answer": None, "used_llm": False, "fallback_reason": "LLM disabled"}


class _StubWiki:
    """Minimal ProjectWiki surface for ask_project's wiki-fallback path."""

    def __init__(self, backends=None):
        self._backends = backends or {}
        self.last_use_llm = None

    def config(self):
        return {"memory_backends": self._backends}

    def query(self, question, *, top_k, use_llm):
        self.last_use_llm = use_llm
        return _StubResult()


def test_ask_project_wiki_fallback_honors_use_llm_param():
    # Regression: the wiki fallback hardcoded use_llm=False, so ask could
    # never synthesize. It must now thread the caller's use_llm.
    wiki = _StubWiki()
    ask_project(wiki, "q?", backend="auto", use_llm=False)
    assert wiki.last_use_llm is False
    ask_project(wiki, "q?", backend="auto", use_llm=True)
    assert wiki.last_use_llm is True


def test_ask_project_wiki_fallback_honors_env_gate(monkeypatch):
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")
    wiki = _StubWiki()
    ask_project(wiki, "q?", backend="auto")  # use_llm defaults False, env enables
    assert wiki.last_use_llm is True


def test_ask_project_auto_records_cognee_failure_note(monkeypatch):
    import tesserae.cognee_query as cq

    def _boom(*a, **k):
        raise RuntimeError("auth required")

    monkeypatch.setattr(cq, "search_cognee", _boom)
    wiki = _StubWiki(backends={"cognee": {"enabled": True, "dataset": "d"}})
    env = ask_project(wiki, "q?", backend="auto")
    assert env["backend"] == "wiki"
    assert env.get("auto_notes") and "cognee failed" in env["auto_notes"][0]


# ---------------------------------------------------------------------------
# Fake Anthropic SDK surface — just enough for WikiQuery.answer().
# ---------------------------------------------------------------------------


@dataclass
class _FakeBlock:
    type: str
    text: str


@dataclass
class _FakeResponse:
    content: List[_FakeBlock]
    model: str = "claude-sonnet-4-6"


class _FakeMessages:
    def __init__(self, owner: "_FakeAnthropic") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> _FakeResponse:
        self._owner.calls.append(kwargs)
        if self._owner.raise_on_call is not None:
            err = self._owner.raise_on_call
            self._owner.raise_on_call = None
            raise err
        text = self._owner.fixed_body
        return _FakeResponse(
            content=[_FakeBlock(type="text", text=text)],
            model=kwargs.get("model", "claude-sonnet-4-6"),
        )


@dataclass
class _FakeAnthropic:
    api_key: Optional[str] = None
    timeout: float = 30.0
    fixed_body: str = ""
    raise_on_call: Optional[BaseException] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = _FakeMessages(self)


def _factory(*, fixed_body: str = "", raise_on_call: Optional[BaseException] = None) -> Any:
    holder: Dict[str, _FakeAnthropic] = {}

    def make(api_key: Optional[str] = None, timeout: float = 30.0) -> _FakeAnthropic:
        client = _FakeAnthropic(
            api_key=api_key,
            timeout=timeout,
            fixed_body=fixed_body,
            raise_on_call=raise_on_call,
        )
        holder["client"] = client
        return client

    make.holder = holder  # type: ignore[attr-defined]
    return make


# ---------------------------------------------------------------------------
# Tmp-project fixture helpers.
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    """Hand-build a tiny ``.tesserae/`` workspace with a search index + pages.

    The corpus is the smoke-test trio used elsewhere in the repo: a
    ``vision-banana`` Concept page that should win on the "vision banana"
    query, a tangential ``apple-vision`` Concept that uses overlapping
    tokens, and a Gaussian Splatting page so kind-filter tests have a
    second kind available.
    """

    project = tmp_path / "demo"
    wiki = project / ".tesserae" / "wiki"
    site = project / ".tesserae" / "site"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "papers").mkdir(parents=True)
    site.mkdir(parents=True)

    pages = [
        {
            "kind": "concepts",
            "slug": "vision-banana",
            "title": "Vision Banana",
            "summary": "Vision banana is a banana-shaped vision benchmark for object recognition.",
            "tokens": ["vision", "banana", "benchmark", "object", "recognition"],
            "node_id": "Concept:VisionBanana",
            "body": (
                "---\n"
                "title: Vision Banana\n"
                "kind: concepts\n"
                "node_id: Concept:VisionBanana\n"
                "---\n"
                "# Vision Banana\n\n"
                "Vision Banana is a banana-themed benchmark used to probe object recognition "
                "robustness. It contains 1024 banana photos with adversarial backgrounds.\n\n"
                "It is unrelated to fruit recognition tasks despite the name.\n"
            ),
        },
        {
            "kind": "concepts",
            "slug": "apple-vision",
            "title": "Apple Vision",
            "summary": "Apple Vision is a generic vision API.",
            "tokens": ["apple", "vision", "api", "framework"],
            "node_id": "Concept:AppleVision",
            "body": (
                "---\n"
                "title: Apple Vision\n"
                "kind: concepts\n"
                "node_id: Concept:AppleVision\n"
                "---\n"
                "# Apple Vision\n\n"
                "Apple Vision is a high-level vision API that ships in macOS and iOS.\n"
            ),
        },
        {
            "kind": "papers",
            "slug": "2308-04079-gaussian-splatting",
            "title": "Gaussian Splatting",
            "summary": "3D Gaussian Splatting for real-time radiance fields.",
            "tokens": ["gaussian", "splatting", "radiance", "fields", "novel", "view"],
            "node_id": "Paper:2308.04079",
            "body": (
                "---\n"
                "title: Gaussian Splatting\n"
                "kind: papers\n"
                "node_id: Paper:2308.04079\n"
                "arxiv_id: 2308.04079\n"
                "---\n"
                "# Gaussian Splatting\n\n"
                "Gaussian Splatting represents 3D scenes with millions of anisotropic Gaussian "
                "primitives, enabling real-time novel-view synthesis at high fidelity.\n"
            ),
        },
    ]

    for page in pages:
        path = wiki / page["kind"] / f"{page['slug']}.md"
        path.write_text(page["body"], encoding="utf-8")

    index = []
    for page in pages:
        href = f"{page['kind']}/{page['slug']}.html"
        index.append(
            {
                "id": page["node_id"],
                "title": page["title"],
                "kind": page["kind"],
                "href": href,
                "summary": page["summary"],
                "source_path": "",
                "tokens": page["tokens"],
                "len": len(page["tokens"]),
                "created_ts": 1_700_000_000,
            }
        )

    (site / "search-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Optional: a wiki overview the LLM-mode prompt builder will read.
    (project / ".tesserae" / "wiki" / "overview.md").write_text(
        "# Demo Wiki\n\nA tiny demo wiki for query tests.\n",
        encoding="utf-8",
    )
    return project


@pytest.fixture(autouse=True)
def _reset_factory_and_env(monkeypatch):
    reset_failure_log_for_tests()
    set_client_factory(None)
    # Tests own these env vars — never leak from the host shell.
    for var in ("TESSERAE_QUERY_LLM", "TESSERAE_QUERY_DRY_RUN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield
    set_client_factory(None)
    reset_failure_log_for_tests()


# ---------------------------------------------------------------------------
# search() unit tests
# ---------------------------------------------------------------------------


def test_search_returns_top_hit_for_vision_banana(tmp_path):
    project = _make_project(tmp_path)
    wq = WikiQuery(project)

    hits = wq.search("vision banana")

    assert hits
    assert hits[0].title == "Vision Banana"
    assert hits[0].kind == "concepts"
    assert hits[0].node_id == "Concept:VisionBanana"
    # The excerpt should come from the page body, not the index summary.
    assert "banana-themed benchmark" in hits[0].excerpt
    assert hits[0].page_path is not None
    assert hits[0].page_path.exists()


def test_search_kind_filter_narrows_results(tmp_path):
    project = _make_project(tmp_path)

    wq_papers = WikiQuery(project, kind_filter="papers")
    paper_hits = wq_papers.search("vision")
    # No paper matches "vision" in tokens — kind-filter strips the concept hits.
    assert paper_hits == []

    wq_concepts = WikiQuery(project, kind_filter="concepts")
    concept_hits = wq_concepts.search("vision")
    assert all(hit.kind == "concepts" for hit in concept_hits)
    assert {hit.title for hit in concept_hits} == {"Vision Banana", "Apple Vision"}


def test_search_returns_empty_when_index_missing(tmp_path):
    project = tmp_path / "empty-project"
    (project / ".tesserae" / "site").mkdir(parents=True)
    wq = WikiQuery(project)

    assert wq.search("anything") == []


def test_search_arxiv_id_extracted_from_frontmatter(tmp_path):
    project = _make_project(tmp_path)
    wq = WikiQuery(project)

    hits = wq.search("gaussian splatting")
    assert hits
    paper_hit = next(hit for hit in hits if hit.kind == "papers")
    assert paper_hit.arxiv_id == "2308.04079"


# ---------------------------------------------------------------------------
# answer() — gating
# ---------------------------------------------------------------------------


def test_answer_without_env_or_flag_returns_search_only(tmp_path):
    project = _make_project(tmp_path)
    wq = WikiQuery(project)

    result = wq.answer("vision banana")

    assert result.used_llm is False
    assert result.answer is None
    assert result.fallback_reason == "LLM disabled"
    assert result.hits  # search still runs


def test_answer_with_no_llm_flag_short_circuits(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")
    wq = WikiQuery(project)

    result = wq.answer("vision banana", force_no_llm=True)

    assert result.used_llm is False
    assert result.fallback_reason == "LLM disabled"


def test_answer_dry_run_returns_stub_without_calling_sdk(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")
    monkeypatch.setenv("TESSERAE_QUERY_DRY_RUN", "1")

    factory = _factory(fixed_body="should-never-be-called")
    set_client_factory(factory)

    wq = WikiQuery(project)
    result = wq.answer("vision banana", model="claude-sonnet-4-6")

    assert result.used_llm is True
    assert result.model == "claude-sonnet-4-6"
    assert result.answer is not None
    assert "(dry-run preview, no API call)" in result.answer
    # No fake client was constructed because dry-run never reaches the SDK.
    assert factory.holder == {}


def test_answer_dry_run_via_force_llm_flag(tmp_path, monkeypatch):
    """``force_llm=True`` (CLI ``--llm``) should be enough — no env needed."""

    project = _make_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_DRY_RUN", "1")

    wq = WikiQuery(project)
    result = wq.answer("vision banana", force_llm=True)

    assert result.used_llm is True
    assert result.answer and "(dry-run preview" in result.answer


# ---------------------------------------------------------------------------
# answer() — LLM path with fake SDK
# ---------------------------------------------------------------------------


def test_answer_llm_path_returns_body_with_citations(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    factory = _factory(
        fixed_body=(
            "Vision Banana is a banana-themed object recognition "
            "benchmark [Concept:VisionBanana]."
        )
    )
    set_client_factory(factory)

    wq = WikiQuery(project)
    result = wq.answer("vision banana")

    assert result.used_llm is True
    assert result.fallback_reason is None
    assert result.answer is not None
    assert "[Concept:VisionBanana]" in result.answer
    # The system block is cached.
    call = factory.holder["client"].calls[0]
    system = call["system"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # The user message bundles a <source> wrapper per hit.
    user_msg = call["messages"][-1]["content"]
    assert "<source kind=\"concepts\"" in user_msg
    assert "node_id=\"Concept:VisionBanana\"" in user_msg


def test_answer_no_citation_falls_back(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    factory = _factory(fixed_body="A perfectly nice answer that names no node ids.")
    set_client_factory(factory)

    wq = WikiQuery(project)
    result = wq.answer("vision banana")

    assert result.used_llm is False
    assert result.answer is None
    assert result.fallback_reason == "model produced no citations"


def test_answer_api_exception_falls_back_with_log(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    factory = _factory(raise_on_call=RuntimeError("simulated outage"))
    set_client_factory(factory)

    buf = io.StringIO()
    with redirect_stderr(buf):
        result = WikiQuery(project).answer("vision banana")

    assert result.used_llm is False
    assert result.answer is None
    assert result.fallback_reason and result.fallback_reason.startswith("API error:")
    log = buf.getvalue()
    assert "RuntimeError" in log


def test_answer_missing_api_key_returns_search_only(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")
    # No ANTHROPIC_API_KEY, no client factory: the gate must refuse.
    set_client_factory(None)

    result = WikiQuery(project).answer("vision banana")

    assert result.used_llm is False
    assert result.fallback_reason == "ANTHROPIC_API_KEY not set"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def _subprocess_env(**extra: str) -> Dict[str, str]:
    """Inherit os.environ but make sure the subprocess can import ``tesserae``.

    On systems where pytest is run via a non-editable Python (e.g. the
    macOS Command Line Tools python), ``python -m tesserae.cli`` would
    fail. We splice the project root onto ``PYTHONPATH`` so the subprocess
    finds the module the same way the test process does.
    """

    env = dict(os.environ)
    repo_root = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    parts = [p for p in (repo_root, existing) if p]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.update(extra)
    return env


def test_cli_one_shot_json_emits_valid_payload(tmp_path):
    project = _make_project(tmp_path)
    cmd = [
        sys.executable,
        "-m",
        "tesserae.cli",
        "query",
        "vision banana",
        "--project",
        str(project),
        "--json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_path), env=_subprocess_env())
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["question"] == "vision banana"
    assert payload["used_llm"] is False
    assert payload["hits"]
    assert payload["hits"][0]["title"] == "Vision Banana"


def test_cli_dry_run_llm_returns_stub_answer(tmp_path):
    project = _make_project(tmp_path)
    env = _subprocess_env(
        TESSERAE_QUERY_LLM="1",
        TESSERAE_QUERY_DRY_RUN="1",
    )
    cmd = [
        sys.executable,
        "-m",
        "tesserae.cli",
        "query",
        "What is Gaussian Splatting?",
        "--project",
        str(project),
        "--json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["used_llm"] is True
    assert payload["model"] == "claude-sonnet-4-6"
    assert payload["answer"] and "(dry-run preview" in payload["answer"]
