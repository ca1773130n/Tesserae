import asyncio
import importlib
import json

import pytest
from pydantic import BaseModel

from tesserae.cognee_codex import (
    CodexCLICogneeAdapter,
    CogneeCodexPatch,
    DeterministicEmbeddingEngine,
    OllamaEmbeddingEngine,
    extract_json_object,
    build_structured_prompt,
    ensure_event_loop,
    retrieve_existing_edges_uuid_safe,
)


class TinyResponse(BaseModel):
    ok: bool
    label: str


def test_extract_json_object_handles_codex_transcript_and_plain_json():
    assert extract_json_object('{"ok": true, "label": "plain"}') == {"ok": True, "label": "plain"}
    transcript = 'reasoning...\nfinal answer:\n```json\n{"ok": true, "label": "fenced"}\n```\n'
    assert extract_json_object(transcript) == {"ok": True, "label": "fenced"}


def test_build_structured_prompt_includes_schema_and_inputs():
    prompt = build_structured_prompt("paper text", "system rules", TinyResponse)

    assert "Return ONLY one valid JSON object" in prompt
    assert "paper text" in prompt
    assert "system rules" in prompt
    assert "TinyResponse" in prompt or "label" in prompt


def test_codex_cli_cognee_adapter_validates_response_model():
    calls = []

    async def fake_runner(prompt, model, timeout):
        calls.append({"prompt": prompt, "model": model, "timeout": timeout})
        return '{"ok": true, "label": "codex"}'

    adapter = CodexCLICogneeAdapter(model="gpt-5.4", timeout=9, runner=fake_runner)
    result = asyncio.run(adapter.acreate_structured_output("input", "system", TinyResponse))

    assert result == TinyResponse(ok=True, label="codex")
    assert calls[0]["model"] == "gpt-5.4"
    assert calls[0]["timeout"] == 9
    assert "input" in calls[0]["prompt"]


def test_cognee_codex_patch_replaces_and_restores_get_llm_client(monkeypatch):
    # cognee >=1.1 removed the `cognee.infrastructure.llm.get_llm_client`
    # module (the patch target) in favour of LLMGateway. Skip cleanly on
    # incompatible cognee versions rather than failing on import; the patch
    # logic itself is still exercised against whatever cognee is installed
    # once that module exists again. See CogneeCodexPatch in tesserae/cognee_codex.py.
    pytest.importorskip(
        "cognee.infrastructure.llm.get_llm_client",
        reason="installed cognee removed cognee.infrastructure.llm.get_llm_client",
    )
    ensure_event_loop()
    import cognee.infrastructure.llm.get_llm_client as llm_module
    embed_module = importlib.import_module("cognee.infrastructure.databases.vector.embeddings.get_embedding_engine")

    original = object()
    original_embedding = object()
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: original)
    monkeypatch.setattr(embed_module, "get_embedding_engine", lambda: original_embedding)

    with CogneeCodexPatch(model="gpt-5.4", timeout=7, deterministic_embeddings=True, embedding_dimensions=8):
        patched = llm_module.get_llm_client()
        embedding = embed_module.get_embedding_engine()
        kg_module = importlib.import_module("cognee.modules.data.extraction.knowledge_graph.extract_content_graph")
        assert isinstance(kg_module.get_llm_client(), CodexCLICogneeAdapter)
        assert isinstance(patched, CodexCLICogneeAdapter)
        assert patched.model == "gpt-5.4"
        assert patched.timeout == 7
        assert isinstance(embedding, DeterministicEmbeddingEngine)
        assert embedding.get_vector_size() == 8

    assert llm_module.get_llm_client() is original
    assert embed_module.get_embedding_engine() is original_embedding


def test_retrieve_existing_edges_uuid_safe_stringifies_uuid_edges():
    import uuid
    from types import SimpleNamespace

    class FakeGraphEngine:
        async def has_edges(self, edges):
            return [(uuid.uuid4(), uuid.uuid4(), "mentions")]

    result = asyncio.run(retrieve_existing_edges_uuid_safe(
        data_chunks=[SimpleNamespace(id=uuid.uuid4())],
        chunk_graphs=[SimpleNamespace(nodes=[], edges=[])],
        graph_engine=FakeGraphEngine(),
    ))

    assert len(result) == 1
    assert next(iter(result.values())) is True
    assert isinstance(next(iter(result.keys())), str)


def test_ollama_embedding_engine_formats_instructed_queries_and_documents():
    calls = []

    async def fake_embed(texts, model, endpoint, timeout):
        calls.append({"texts": texts, "model": model, "endpoint": endpoint, "timeout": timeout})
        return [[float(index), float(index + 1), float(index + 2)] for index, _ in enumerate(texts)]

    engine = OllamaEmbeddingEngine(
        model="qwen3-embedding:0.6b",
        dimensions=3,
        endpoint="http://ollama.test/api/embed",
        timeout=11,
        embedder=fake_embed,
    )

    query = asyncio.run(engine.embed_query("Gaussian Splatting limitations"))
    docs = asyncio.run(engine.embed_text(["paper passage", "claim evidence"]))

    assert query == [[0.0, 1.0, 2.0]]
    assert docs == [[0.0, 1.0, 2.0], [1.0, 2.0, 3.0]]
    assert calls[0]["model"] == "qwen3-embedding:0.6b"
    assert calls[0]["endpoint"] == "http://ollama.test/api/embed"
    assert calls[0]["timeout"] == 11
    assert calls[0]["texts"] == ["Instruct: Given a research intelligence query, retrieve relevant papers, claims, evidence spans, methods, datasets, benchmarks, and technical concepts.\nQuery: Gaussian Splatting limitations"]
    assert calls[1]["texts"] == ["paper passage", "claim evidence"]
    assert engine.get_vector_size() == 3


def test_cognee_codex_patch_can_use_ollama_embedding_engine(monkeypatch):
    pytest.importorskip(
        "cognee.infrastructure.llm.get_llm_client",
        reason="installed cognee removed cognee.infrastructure.llm.get_llm_client",
    )
    ensure_event_loop()
    import cognee.infrastructure.llm.get_llm_client as llm_module
    embed_module = importlib.import_module("cognee.infrastructure.databases.vector.embeddings.get_embedding_engine")

    original = object()
    original_embedding = object()
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: original)
    monkeypatch.setattr(embed_module, "get_embedding_engine", lambda: original_embedding)

    with CogneeCodexPatch(model="gpt-5.4", ollama_embeddings=True, ollama_model="qwen3-embedding:0.6b", embedding_dimensions=1024):
        embedding = embed_module.get_embedding_engine()
        assert isinstance(embedding, OllamaEmbeddingEngine)
        assert embedding.model == "qwen3-embedding:0.6b"
        assert embedding.get_vector_size() == 1024

    assert llm_module.get_llm_client() is original
    assert embed_module.get_embedding_engine() is original_embedding


def test_deterministic_embedding_engine_is_stable_and_sized():
    engine = DeterministicEmbeddingEngine(dimensions=8)

    first = asyncio.run(engine.embed_text(["same", "same", "different"]))

    assert len(first) == 3
    assert len(first[0]) == 8
    assert first[0] == first[1]
    assert first[0] != first[2]
    assert engine.get_vector_size() == 8
