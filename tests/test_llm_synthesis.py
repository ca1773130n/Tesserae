"""Tests for the gated LLM-backed synthesis path.

The fake Anthropic client lives entirely in this module — no network, no real
SDK required. We inject it via the ``set_client_factory`` test seam in
``tesserae.llm_synthesis`` so the rest of the call site is exercised exactly
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

from tesserae import llm_synthesis as llm_mod
from tesserae.llm_synthesis import (
    LlmSynthesisRequest,
    LlmSynthesizer,
    reset_failure_log_for_tests,
    set_client_factory,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)
from tesserae.synthesis import SynthesisProjector
from tesserae.wiki_store import WikiPageStore


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
    assert "Tesserae" in system[0]["text"]


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
    assert log.count("[tesserae]") == 1
    assert "empty" in log


def test_missing_citations_rejected_with_log_and_returns_none():
    factory = _factory(
        fixed_body=(
            "A perfectly nice paragraph that names no node ids and is "
            "intentionally written to exceed the minimum body length so the "
            "validator rejects it specifically for the missing citations.\n"
        )
    )
    set_client_factory(factory)

    buf = io.StringIO()
    with redirect_stderr(buf):
        out = LlmSynthesizer().synthesize(_basic_request())

    assert out is None
    log = buf.getvalue()
    assert "no [node_id] citations" in log


def test_two_calls_with_same_request_produce_same_body_and_cache_id():
    factory = _factory(
        fixed_body=(
            "See [node-a] and [node-b]. The wiki tracks both papers under "
            "the same approach family and they share two contributing "
            "concepts."
        )
    )
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


def test_system_block_contains_canonical_rules_text():
    """The cached preamble carries every Rule-N marker verbatim.

    Any byte change to this block invalidates the prompt cache for every
    subsequent page in the run, so the test is intentionally strict — it
    fails loudly if someone edits the rule text without thinking about it.
    """

    factory = _factory(
        fixed_body=(
            "Cache-control sanity check [node-a]. The system block must "
            "carry the exact canonical rules text."
        )
    )
    set_client_factory(factory)

    LlmSynthesizer().synthesize(_basic_request())

    sysblock = factory.holder["client"].calls[0]["system"][0]
    text = sysblock["text"]
    for marker in (
        "RULE 1 — DO NOT INVENT FACTS",
        "RULE 2 — CITE EVERY CLAIM",
        "RULE 3 — STAY ON TOPIC",
        "RULE 4 — TONE",
        "RULE 5 — FORMAT",
        "RULE 6 — LANGUAGE",
        "pulse        : project-wide weekly snapshot",
        "daily_digest : one paragraph per noteworthy paper",
        "Type:slug:hash",
    ):
        assert marker in text, f"missing canonical marker: {marker!r}"
    assert sysblock["cache_control"] == {"type": "ephemeral"}


def test_user_message_contains_kind_title_inputs_and_heuristic_body():
    """User message carries the per-kind shape + INPUTS + EDITORIAL ANGLE."""

    factory = _factory(fixed_body="Sanity body [node-a].")
    set_client_factory(factory)

    request = LlmSynthesisRequest(
        kind="topic",
        title="Topic — Gaussian Splatting",
        inputs=(
            {
                "id": "Paper:gs:abc123def456",
                "name": "Gaussian Splatting v2",
                "type": "Paper",
                "description": "Photometric splat refinement.",
                "metadata": {"arxiv_id": "2604.20329"},
            },
            {
                "id": "ApproachFamily:splatting:xyz789",
                "name": "Splatting Family",
                "type": "ApproachFamily",
            },
        ),
        context={
            "kind": "topic",
            "site_title": "Tesserae",
            "total_nodes": 2859,
            "total_edges": 4316,
            "field": "3D Reconstruction",
            "days": ["2026-04-25", "2026-04-26"],
            "summary": "Topic synthesis for Gaussian Splatting.",
            "heuristic_body": (
                "# Topic — Gaussian Splatting\n\n"
                "Two papers contribute to this topic this week.\n"
            ),
        },
    )
    LlmSynthesizer().synthesize(request)

    user_msg = factory.holder["client"].calls[0]["messages"][0]["content"]
    assert "SYNTHESIS_KIND: topic" in user_msg
    assert "TITLE: Topic — Gaussian Splatting" in user_msg
    assert "Paper:gs:abc123def456" in user_msg
    assert "ApproachFamily:splatting:xyz789" in user_msg
    assert "Gaussian Splatting v2" in user_msg
    assert "field name: 3D Reconstruction" in user_msg
    assert "total nodes in graph: 2859" in user_msg
    assert "total edges: 4316" in user_msg
    assert "2026-04-25" in user_msg and "2026-04-26" in user_msg
    assert "EDITORIAL ANGLE" in user_msg
    assert "Two papers contribute to this topic this week." in user_msg
    # Per-kind shape descriptor anchors Rule 3 to "topic".
    assert "narrative about" in user_msg


def test_user_message_caps_inputs_at_25():
    """More than 25 inputs are truncated; the user message stays bounded."""

    factory = _factory(fixed_body="Body referencing [node-0] only.")
    set_client_factory(factory)

    big_inputs = tuple(
        {"id": f"Paper:p{i}:{i:012x}", "name": f"Paper {i}", "type": "Paper"}
        for i in range(40)
    )
    request = LlmSynthesisRequest(
        kind="weekly",
        title="Weekly",
        inputs=big_inputs,
        context={"kind": "weekly"},
    )
    LlmSynthesizer().synthesize(request)
    user_msg = factory.holder["client"].calls[0]["messages"][0]["content"]
    # The first 25 inputs are present; the 26th and beyond are not.
    assert "Paper:p0:" in user_msg
    assert "Paper:p24:" in user_msg
    assert "Paper:p25:" not in user_msg
    assert "Paper:p39:" not in user_msg


def test_validate_response_rejects_short_body():
    """``_validate_response`` enforces the 80-char minimum."""

    from tesserae.llm_synthesis import _validate_response

    short_with_citation = "Tiny [node-a].\n"
    assert _validate_response(short_with_citation, {"node-a"}) is None


def test_validate_response_rejects_zero_citations():
    """A long body with no [id] markers fails validation."""

    from tesserae.llm_synthesis import _validate_response

    long_no_citation = (
        "This paragraph is plenty long but it does not name any node by id "
        "in square brackets, so the validator should reject it.\n"
    )
    assert _validate_response(long_no_citation, {"node-a"}) is None


def test_validate_response_accepts_valid_body():
    """A long body citing only input node ids is accepted."""

    from tesserae.llm_synthesis import _validate_response

    body = (
        "The wiki tightened around 3D reconstruction this week, with three "
        "new papers contributing to the geometry-grounded splatting family "
        "[Paper:geometry-grounded:abcdef123456].\n"
    )
    citations = _validate_response(
        body, {"Paper:geometry-grounded:abcdef123456", "node-b"}
    )
    assert citations == ["Paper:geometry-grounded:abcdef123456"]


def test_validate_response_rejects_ungrounded_citation():
    """A long body citing an id absent from the inputs fails validation."""

    from tesserae.llm_synthesis import _validate_response

    body = (
        "This paragraph is plenty long and cites a node id, but the id was "
        "fabricated by the model rather than drawn from the inputs "
        "[Paper:made-up:123456abcdef].\n"
    )
    assert _validate_response(body, {"node-a", "node-b"}) is None


def test_korean_inputs_render_through_to_user_message_and_system_has_language_rule():
    """When 80%+ of input names are Korean, the cached system rule still
    instructs the model to match the dominant language. The user message
    carries the Korean names verbatim so the model can detect the bias.

    We assert via prompt content shape (Rule 6 present + Korean names land
    in the user message) — there is no separate code-side language flag.
    """

    factory = _factory(fixed_body="국문 본문 [node-a]. 위키는 한국어 자료를 정리한다.")
    set_client_factory(factory)

    request = LlmSynthesisRequest(
        kind="daily_digest",
        title="일일 다이제스트 — 2026-04-25",
        inputs=(
            {"id": "node-a", "name": "한국어 논문 한 편", "type": "Paper"},
            {"id": "node-b", "name": "두 번째 한국어 논문", "type": "Paper"},
            {"id": "node-c", "name": "세 번째 한국어 논문", "type": "Paper"},
            {"id": "node-d", "name": "네 번째 논문", "type": "Paper"},
            {"id": "node-e", "name": "English Outlier", "type": "Paper"},
        ),
        context={"kind": "daily_digest", "summary": "한국어 자료의 일일 다이제스트"},
    )
    LlmSynthesizer().synthesize(request)

    call = factory.holder["client"].calls[0]
    sys_text = call["system"][0]["text"]
    user_msg = call["messages"][0]["content"]

    # System block carries the language rule verbatim — the model sees both
    # the rule AND the Korean-dominant inputs and is responsible for picking
    # the language.
    assert "RULE 6 — LANGUAGE" in sys_text
    assert "Korean" in sys_text
    # Korean strings land in the user message intact.
    assert "한국어 논문 한 편" in user_msg
    assert "일일 다이제스트" in user_msg


def test_short_response_logs_short_message_and_returns_none():
    """A model that returns 'OK [node-a].' must be rejected on length."""

    factory = _factory(fixed_body="OK [node-a].")
    set_client_factory(factory)

    buf = io.StringIO()
    with redirect_stderr(buf):
        out = LlmSynthesizer().synthesize(_basic_request())

    assert out is None
    log = buf.getvalue()
    assert "shorter than" in log


def test_ungrounded_citation_logs_and_returns_none():
    """A body citing a fabricated node id is rejected with a log line."""

    factory = _factory(
        fixed_body=(
            "A long-enough paragraph that cites one real input and one "
            "fabricated node id the model invented out of thin air "
            "[node-a] [Paper:made-up:123456abcdef].\n"
        )
    )
    set_client_factory(factory)

    buf = io.StringIO()
    with redirect_stderr(buf):
        out = LlmSynthesizer().synthesize(_basic_request())

    assert out is None
    log = buf.getvalue()
    assert "not present in the inputs" in log
    assert "Paper:made-up:123456abcdef" in log


def test_response_frontmatter_and_h1_are_stripped():
    body = (
        "---\nfoo: bar\n---\n"
        "# Project Pulse\n\n"
        "Lead paragraph naming [node-a]. The wiki captured two recent "
        "papers and both connect to the same approach family.\n"
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
        TESSERAE_SYNTHESIS_LLM=None,
        TESSERAE_SYNTHESIS_DRY_RUN=None,
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
        TESSERAE_SYNTHESIS_LLM="1",
        ANTHROPIC_API_KEY="sk-test",
        TESSERAE_SYNTHESIS_DRY_RUN=None,
    )

    def factory_body(kwargs):
        # Pull a node id off the user message so the body always cites
        # something present in INPUTS. The user message is YAML-shaped,
        # with each input rendered as ``  - id: <node-id>`` on its own line.
        text = kwargs["messages"][0]["content"]
        marker = "  - id: "
        idx = text.find(marker)
        if idx == -1:
            return (
                "no inputs found in the user message; this body is long "
                "enough to pass the minimum-length gate but lacks any "
                "citation, so the validator should reject it."
            )
        end = text.find("\n", idx + len(marker))
        node_id = text[idx + len(marker):end].strip()
        return (
            f"LLM-generated digest referencing [{node_id}]. The wiki "
            "tightened around this neighborhood, with multiple contributing "
            "papers and one shared approach family."
        )

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
        TESSERAE_SYNTHESIS_LLM="1",
        ANTHROPIC_API_KEY="sk-test",
        TESSERAE_SYNTHESIS_DRY_RUN=None,
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
    assert "[tesserae]" in log
    # One log line per (kind, error-class) pair — pulse is the first.
    assert log.count("_FakeRateLimitError") >= 1


def test_projector_dry_run_path(tmp_path: Path, monkeypatch):
    _set_env(
        monkeypatch,
        TESSERAE_SYNTHESIS_LLM="1",
        ANTHROPIC_API_KEY="sk-test",
        TESSERAE_SYNTHESIS_DRY_RUN="1",
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
        TESSERAE_SYNTHESIS_LLM="1",
        ANTHROPIC_API_KEY="",
        TESSERAE_SYNTHESIS_DRY_RUN=None,
    )

    set_client_factory(_factory(fixed_body="should not run [node-a]"))

    store = WikiPageStore(tmp_path / "wiki")
    buf = io.StringIO()
    with redirect_stderr(buf):
        SynthesisProjector(store).project(_small_graph())

    pulse = (tmp_path / "wiki" / "syntheses" / "pulse.md").read_text(encoding="utf-8")
    assert "generator: heuristic-v1" in pulse
    assert "ANTHROPIC_API_KEY" in buf.getvalue()
