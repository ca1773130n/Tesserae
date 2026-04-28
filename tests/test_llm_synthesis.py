"""Tests for the gated LLM-backed synthesis path.

The fake Anthropic client lives entirely in this module — no network, no real
SDK required. We inject it via the ``set_client_factory`` test seam in
``llm_wiki.llm_synthesis`` so the rest of the call site is exercised exactly
as it would be in production.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from llm_wiki import llm_synthesis as llm_mod
from llm_wiki.llm_synthesis import (
    LlmSynthesisRequest,
    LlmSynthesizer,
    reset_failure_log_for_tests,
    set_client_factory,
)
from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)
from llm_wiki.synthesis import SynthesisProjector
from llm_wiki.wiki_store import WikiPageStore


# ---------------------------------------------------------------------------
# Fake Anthropic SDK surface — just enough for LlmSynthesizer.synthesize().
# ---------------------------------------------------------------------------


@dataclass
class _FakeBlock:
    type: str
    text: str


@dataclass
class _FakeResponse:
    content: List[_FakeBlock]
    model: str = "claude-sonnet-4-6"


class _FakeRateLimitError(Exception):
    """Stand-in for ``anthropic.RateLimitError`` (no SDK import needed)."""


class _FakeMessages:
    def __init__(self, owner: "_FakeAnthropic") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> _FakeResponse:
        self._owner.calls.append(kwargs)
        if self._owner.raise_on_call is not None:
            err = self._owner.raise_on_call
            self._owner.raise_on_call = None
            raise err
        if self._owner.body_factory is not None:
            text = self._owner.body_factory(kwargs)
        else:
            text = self._owner.fixed_body
        return _FakeResponse(
            content=[_FakeBlock(type="text", text=text)],
            model=kwargs.get("model", "claude-sonnet-4-6"),
        )


@dataclass
class _FakeAnthropic:
    api_key: Optional[str] = None
    timeout: float = 20.0
    fixed_body: str = ""
    body_factory: Any = None
    raise_on_call: Optional[BaseException] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = _FakeMessages(self)


def _factory(*, fixed_body: str = "", body_factory: Any = None,
             raise_on_call: Optional[BaseException] = None) -> Any:
    """Build a client-factory closure that produces a configured fake."""

    holder: Dict[str, _FakeAnthropic] = {}

    def make(api_key: Optional[str] = None, timeout: float = 20.0) -> _FakeAnthropic:
        client = _FakeAnthropic(
            api_key=api_key,
            timeout=timeout,
            fixed_body=fixed_body,
            body_factory=body_factory,
            raise_on_call=raise_on_call,
        )
        holder["client"] = client
        return client

    make.holder = holder  # type: ignore[attr-defined]
    return make


@pytest.fixture(autouse=True)
def _reset_logs_and_factory():
    reset_failure_log_for_tests()
    set_client_factory(None)
    yield
    set_client_factory(None)
    reset_failure_log_for_tests()


# ---------------------------------------------------------------------------
# Unit tests on LlmSynthesizer
# ---------------------------------------------------------------------------


def _basic_request() -> LlmSynthesisRequest:
    return LlmSynthesisRequest(
        kind="pulse",
        title="Project Pulse",
        inputs=(
            {"id": "node-a", "name": "Paper A", "type": "Paper"},
            {"id": "node-b", "name": "Paper B", "type": "Paper"},
        ),
        context={"summary": "snapshot"},
    )


def test_synthesize_returns_parsed_response_and_calls_client_once():
    factory = _factory(
        fixed_body=(
            "Two papers landed this week, both pushing reconstruction "
            "quality forward [node-a] [node-b].\n\n"
            "The dominant thread is volumetric rendering refinements "
            "[node-a]."
        )
    )
    set_client_factory(factory)

    synth = LlmSynthesizer(model="claude-sonnet-4-6")
    response = synth.synthesize(_basic_request())

    assert response is not None
    assert response.body.startswith("Two papers landed this week")
    assert "node-a" in response.citations
    assert "node-b" in response.citations
    assert response.cache_id.startswith("sha256-")
    assert response.model == "claude-sonnet-4-6"

    client = factory.holder["client"]
    assert len(client.calls) == 1


def test_system_block_carries_ephemeral_cache_control():
    factory = _factory(fixed_body="A note about [node-a].")
    set_client_factory(factory)

    LlmSynthesizer().synthesize(_basic_request())

    call = factory.holder["client"].calls[0]
    system = call["system"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["type"] == "text"
    # Long stable preamble — the cached prefix.
    assert "LLM-Wiki" in system[0]["text"]


def test_empty_response_returns_none_and_logs_once():
    factory = _factory(fixed_body="")
    set_client_factory(factory)

    buf = io.StringIO()
    with redirect_stderr(buf):
        out = LlmSynthesizer().synthesize(_basic_request())
        # Second call with same kind: should not log a duplicate line.
        LlmSynthesizer().synthesize(_basic_request())

    assert out is None
    log = buf.getvalue()
    assert log.count("[llm-wiki]") == 1
    assert "empty" in log


def test_missing_citations_rejected_with_log_and_returns_none():
    factory = _factory(
        fixed_body="A perfectly nice paragraph that names no node ids.\n"
    )
    set_client_factory(factory)

    buf = io.StringIO()
    with redirect_stderr(buf):
        out = LlmSynthesizer().synthesize(_basic_request())

    assert out is None
    log = buf.getvalue()
    assert "no [node_id] citations" in log


def test_two_calls_with_same_request_produce_same_body_and_cache_id():
    factory = _factory(fixed_body="See [node-a] and [node-b].")
    set_client_factory(factory)

    synth = LlmSynthesizer()
    a = synth.synthesize(_basic_request())
    b = synth.synthesize(_basic_request())

    assert a is not None and b is not None
    assert a.body == b.body
    assert a.cache_id == b.cache_id


def test_dry_run_skips_api_call_and_emits_stub_body():
    factory = _factory(fixed_body="should-not-be-called")
    set_client_factory(factory)

    synth = LlmSynthesizer(dry_run=True)
    response = synth.synthesize(_basic_request())

    assert response is not None
    assert "(dry-run preview, no API call)" in response.body
    assert "node-a" in response.citations
    # No HTTP call should have happened.
    assert factory.holder == {}


def test_rate_limit_error_returns_none_and_logs():
    factory = _factory(raise_on_call=_FakeRateLimitError("rate limited"))
    set_client_factory(factory)

    buf = io.StringIO()
    with redirect_stderr(buf):
        out = LlmSynthesizer().synthesize(_basic_request())

    assert out is None
    assert "_FakeRateLimitError" in buf.getvalue()


def test_response_frontmatter_and_h1_are_stripped():
    body = (
        "---\nfoo: bar\n---\n"
        "# Project Pulse\n\n"
        "Lead paragraph naming [node-a].\n"
    )
    factory = _factory(fixed_body=body)
    set_client_factory(factory)

    out = LlmSynthesizer().synthesize(_basic_request())
    assert out is not None
    assert "foo: bar" not in out.body
    assert not out.body.lstrip().startswith("# ")
    assert out.body.startswith("Lead paragraph naming")


# ---------------------------------------------------------------------------
# Integration with SynthesisProjector
# ---------------------------------------------------------------------------


def _node(name: str, ntype: ResearchNodeType, source_path: Optional[str] = None,
          **metadata) -> ResearchNode:
    return ResearchNode(
        id=stable_id(ntype.value, name),
        name=name,
        type=ntype,
        aliases=[],
        description="",
        source_path=source_path,
        metadata=metadata,
    )


def _small_graph() -> ResearchGraph:
    field_node = _node("Vision", ResearchNodeType.RESEARCH_FIELD)
    paper_a = _node(
        "Paper A",
        ResearchNodeType.PAPER,
        source_path="data/research/daily/2026-04-25/a.md",
        analysis_date="2026-04-25",
    )
    paper_b = _node(
        "Paper B",
        ResearchNodeType.PAPER,
        source_path="data/research/daily/2026-04-25/b.md",
        analysis_date="2026-04-25",
    )
    family = _node("Splatting Family", ResearchNodeType.APPROACH_FAMILY)
    edges = [
        ResearchEdge(source=paper_a.id, target=field_node.id, type="part_of",
                     evidence=None, metadata={}),
        ResearchEdge(source=paper_b.id, target=field_node.id, type="part_of",
                     evidence=None, metadata={}),
        ResearchEdge(source=paper_a.id, target=family.id,
                     type="belongs_to_approach_family", evidence=None,
                     metadata={}),
        ResearchEdge(source=paper_b.id, target=family.id,
                     type="belongs_to_approach_family", evidence=None,
                     metadata={}),
    ]
    return ResearchGraph(nodes=[field_node, paper_a, paper_b, family], edges=edges)


def _set_env(monkeypatch, **values: Optional[str]) -> None:
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_projector_uses_heuristic_when_env_unset(tmp_path: Path, monkeypatch):
    _set_env(
        monkeypatch,
        LLM_WIKI_SYNTHESIS_LLM=None,
        LLM_WIKI_SYNTHESIS_DRY_RUN=None,
    )

    factory = _factory(fixed_body="MUST NOT BE INVOKED [node-a]")
    set_client_factory(factory)

    store = WikiPageStore(tmp_path / "wiki")
    SynthesisProjector(store).project(_small_graph())

    pulse = (tmp_path / "wiki" / "syntheses" / "pulse.md").read_text(encoding="utf-8")
    assert "generator: heuristic-v1" in pulse
    # Heuristic body — recognizable section header.
    assert "## Counts" in pulse


def test_projector_uses_llm_when_enabled_with_fake_client(tmp_path: Path,
                                                          monkeypatch):
    _set_env(
        monkeypatch,
        LLM_WIKI_SYNTHESIS_LLM="1",
        ANTHROPIC_API_KEY="sk-test",
        LLM_WIKI_SYNTHESIS_DRY_RUN=None,
    )

    def factory_body(kwargs):
        # Pull a node id off the user message so the body always cites
        # something present in INPUTS.
        text = kwargs["messages"][0]["content"]
        # The first id we emit in inputs has form ``ResearchField:...`` etc.
        marker = "\"id\":\""
        idx = text.find(marker)
        if idx == -1:
            return "no inputs"
        end = text.find("\"", idx + len(marker))
        node_id = text[idx + len(marker):end]
        return f"LLM-generated digest referencing [{node_id}]."

    factory = _factory(body_factory=factory_body)
    set_client_factory(factory)

    store = WikiPageStore(tmp_path / "wiki")
    SynthesisProjector(store).project(_small_graph())

    pulse = (tmp_path / "wiki" / "syntheses" / "pulse.md").read_text(encoding="utf-8")
    assert "generator: \"llm-claude-sonnet-4-6\"" in pulse or \
           "generator: llm-claude-sonnet-4-6" in pulse
    assert "LLM-generated digest referencing" in pulse
    assert "## Counts" not in pulse  # heuristic body got replaced


def test_projector_falls_back_to_heuristic_on_rate_limit(tmp_path: Path,
                                                          monkeypatch):
    _set_env(
        monkeypatch,
        LLM_WIKI_SYNTHESIS_LLM="1",
        ANTHROPIC_API_KEY="sk-test",
        LLM_WIKI_SYNTHESIS_DRY_RUN=None,
    )

    factory = _factory(raise_on_call=_FakeRateLimitError("429"))
    # First call raises; subsequent calls return a body. We want the FIRST
    # call (pulse) to fail, and we accept that the rest fail too — what we
    # test is that the *projector keeps going* and emits a heuristic body.
    set_client_factory(factory)

    store = WikiPageStore(tmp_path / "wiki")
    buf = io.StringIO()
    with redirect_stderr(buf):
        SynthesisProjector(store).project(_small_graph())

    pulse = (tmp_path / "wiki" / "syntheses" / "pulse.md").read_text(encoding="utf-8")
    assert "generator: heuristic-v1" in pulse
    log = buf.getvalue()
    assert "[llm-wiki]" in log
    # One log line per (kind, error-class) pair — pulse is the first.
    assert log.count("_FakeRateLimitError") >= 1


def test_projector_dry_run_path(tmp_path: Path, monkeypatch):
    _set_env(
        monkeypatch,
        LLM_WIKI_SYNTHESIS_LLM="1",
        ANTHROPIC_API_KEY="sk-test",
        LLM_WIKI_SYNTHESIS_DRY_RUN="1",
    )

    # No factory needed — dry run skips client construction. Make absolutely
    # sure of that by setting a factory that would explode if called.
    def boom(*a, **kw):
        raise AssertionError("dry-run must not construct a client")
    set_client_factory(boom)

    store = WikiPageStore(tmp_path / "wiki")
    SynthesisProjector(store).project(_small_graph())

    pulse = (tmp_path / "wiki" / "syntheses" / "pulse.md").read_text(encoding="utf-8")
    assert "(dry-run preview, no API call)" in pulse
    assert "generator: \"llm-claude-sonnet-4-6\"" in pulse or \
           "generator: llm-claude-sonnet-4-6" in pulse


def test_projector_disabled_when_api_key_missing(tmp_path: Path, monkeypatch):
    _set_env(
        monkeypatch,
        LLM_WIKI_SYNTHESIS_LLM="1",
        ANTHROPIC_API_KEY="",
        LLM_WIKI_SYNTHESIS_DRY_RUN=None,
    )

    set_client_factory(_factory(fixed_body="should not run [node-a]"))

    store = WikiPageStore(tmp_path / "wiki")
    buf = io.StringIO()
    with redirect_stderr(buf):
        SynthesisProjector(store).project(_small_graph())

    pulse = (tmp_path / "wiki" / "syntheses" / "pulse.md").read_text(encoding="utf-8")
    assert "generator: heuristic-v1" in pulse
    assert "ANTHROPIC_API_KEY" in buf.getvalue()
