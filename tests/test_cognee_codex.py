import asyncio
import importlib
import json

from pydantic import BaseModel

from llm_wiki.cognee_codex import (
    CodexCLICogneeAdapter,
    CogneeCodexPatch,
    DeterministicEmbeddingEngine,
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


def test_deterministic_embedding_engine_is_stable_and_sized():
    engine = DeterministicEmbeddingEngine(dimensions=8)

    first = asyncio.run(engine.embed_text(["same", "same", "different"]))

    assert len(first) == 3
    assert len(first[0]) == 8
    assert first[0] == first[1]
    assert first[0] != first[2]
    assert engine.get_vector_size() == 8
