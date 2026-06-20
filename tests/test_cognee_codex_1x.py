"""CogneeCodexPatch must work against the installed cognee (1.x layout).

These exercise the monkeypatch mechanics — no codex/network calls. Skipped if
cognee isn't importable.
"""

from __future__ import annotations

import os

import pytest

cognee = pytest.importorskip("cognee")

from tesserae.cognee_codex import (  # noqa: E402
    CodexCLICogneeAdapter,
    CogneeCodexPatch,
    DeterministicEmbeddingEngine,
    OllamaEmbeddingEngine,
    _resolve_get_llm_client_module,
)


def test_resolve_get_llm_client_module_finds_it():
    m = _resolve_get_llm_client_module()
    assert hasattr(m, "get_llm_client")


def test_patch_injects_codex_adapter_and_restores():
    m = _resolve_get_llm_client_module()
    original = m.get_llm_client
    assert os.environ.get("ENABLE_BACKEND_ACCESS_CONTROL") in (None, "false", "true")
    with CogneeCodexPatch(model="gpt-5.4", deterministic_embeddings=True, embedding_dimensions=64):
        assert isinstance(m.get_llm_client(), CodexCLICogneeAdapter)
        assert os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] == "false"  # auth posture disabled
    assert m.get_llm_client is original  # restored


def test_adapter_satisfies_1x_llm_interface_methods():
    a = CodexCLICogneeAdapter()
    # 1.x LLMInterface declares these; text cognify never calls them but they exist.
    assert hasattr(a, "acreate_structured_output")
    assert hasattr(a, "create_transcript") and hasattr(a, "transcribe_image")


def test_embedding_engines_have_1x_methods():
    for engine in (DeterministicEmbeddingEngine(32), OllamaEmbeddingEngine(dimensions=32)):
        assert engine.get_vector_size() == 32
        assert engine.get_batch_size() >= 1  # required by cognee 1.x EmbeddingEngine
