"""Codex CLI/OAuth adapter for Cognee structured LLM calls."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional, Type

from pydantic import BaseModel

CodexRunner = Callable[[str, str, int], Awaitable[str]]

COGNEE_LLM_IMPORT_MODULES = [
    "cognee.modules.data.extraction.extract_categories",
    "cognee.modules.data.extraction.extract_summary",
    "cognee.modules.data.extraction.knowledge_graph.extract_content_graph",
    "cognee.tasks.graph.infer_data_ontology",
    "cognee.modules.data.processing.document_types.AudioDocument",
    "cognee.modules.data.processing.document_types.ImageDocument",
]

COGNEE_EMBEDDING_IMPORT_MODULES = [
    "cognee.infrastructure.databases.vector.embeddings",
    "cognee.infrastructure.databases.vector.get_vector_engine",
    "cognee.infrastructure.databases.graph.get_graph_engine",
]

COGNEE_GRAPH_UTIL_IMPORT_MODULES = [
    "cognee.modules.graph.utils.retrieve_existing_edges",
    "cognee.modules.graph.utils",
    "cognee.tasks.graph.extract_graph_from_data",
]


class CodexCLIError(RuntimeError):
    pass


def ensure_event_loop() -> None:
    """Ensure Cognee imports that create asyncio locks have a current loop."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


class CodexCLICogneeAdapter:
    """Cognee LLMInterface-compatible adapter backed by `codex exec` OAuth.

    Cognee expects `acreate_structured_output(text_input, system_prompt,
    response_model)`. This adapter prompts Codex CLI with the response model JSON
    schema and validates the final JSON back into that Pydantic model.
    """

    name = "Codex CLI"

    def __init__(self, model: str = "gpt-5.4", timeout: int = 300, runner: Optional[CodexRunner] = None) -> None:
        self.model = model
        self.timeout = timeout
        self.runner = runner or run_codex_cli

    async def acreate_structured_output(self, text_input: str, system_prompt: str, response_model: Type[BaseModel]) -> BaseModel:
        prompt = build_structured_prompt(text_input, system_prompt, response_model)
        raw = await self.runner(prompt, self.model, self.timeout)
        payload = extract_json_object(raw)
        return response_model.model_validate(payload)

    def show_prompt(self, text_input: str, system_prompt: str) -> str:
        return f"System Prompt:\n{system_prompt}\n\nUser Input:\n{text_input}\n"


def build_structured_prompt(text_input: str, system_prompt: str, response_model: Type[BaseModel]) -> str:
    schema = response_model.model_json_schema()
    return f"""You are a structured-output adapter for Cognee.

Return ONLY one valid JSON object. No markdown fences, no commentary.
The JSON MUST validate against this Pydantic JSON Schema for {response_model.__name__}:
{json.dumps(schema, ensure_ascii=False, indent=2)}

System instructions from Cognee:
{system_prompt}

Input text:
{text_input}
"""


async def run_codex_cli(prompt: str, model: str, timeout: int) -> str:
    """Run Codex CLI with prompt on stdin and return the final message text."""
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as handle:
        output_path = Path(handle.name)
    try:
        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            model,
            "--output-last-message",
            str(output_path),
            "-",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise CodexCLIError(f"codex exec timed out after {timeout}s") from exc
        if proc.returncode != 0:
            raise CodexCLIError(f"codex exec exited {proc.returncode}: {stderr.decode('utf-8', errors='replace') or stdout.decode('utf-8', errors='replace')}")
        final = output_path.read_text(encoding="utf-8", errors="replace") if output_path.exists() else ""
        return final or stdout.decode("utf-8", errors="replace")
    finally:
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass


def extract_json_object(text: str) -> Dict[str, object]:
    stripped = text.strip()
    parsed = _try_json_loads(stripped)
    if isinstance(parsed, dict):
        return parsed

    if "```" in stripped:
        fence_start = stripped.find("```")
        fence_end = stripped.rfind("```")
        if fence_end > fence_start:
            fenced = stripped[fence_start:fence_end + 3]
            inner = _strip_markdown_fence(fenced)
            parsed = _try_json_loads(inner)
            if isinstance(parsed, dict):
                return parsed

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise CodexCLIError("No JSON object found in Codex output")
    parsed = _try_json_loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise CodexCLIError("Codex output JSON is not an object")
    return parsed


def _try_json_loads(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _strip_markdown_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


class DeterministicEmbeddingEngine:
    """Small local embedding engine for no-API-key Cognee smoke runs.

    This is not semantic embedding quality; it is a deterministic substrate that
    lets Cognee cognify smoke tests run without OpenAI embedding keys. Use a real
    local embedding provider such as Ollama for production retrieval quality.
    """

    def __init__(self, dimensions: int = 128) -> None:
        self.dimensions = dimensions

    async def embed_text(self, text):
        return [self._embed_one(item) for item in text]

    def get_vector_size(self) -> int:
        return self.dimensions

    def _embed_one(self, text: str):
        values = []
        counter = 0
        while len(values) < self.dimensions:
            digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8", errors="replace")).digest()
            for byte in digest:
                values.append((byte / 127.5) - 1.0)
                if len(values) >= self.dimensions:
                    break
            counter += 1
        return values


async def retrieve_existing_edges_uuid_safe(data_chunks, chunk_graphs, graph_engine) -> Dict[str, bool]:
    """Cognee 0.1.20-compatible retrieve_existing_edges that stringifies UUIDs.

    Cognee's implementation builds keys with `edge[0] + edge[1] + edge[2]` even
    though graph engines may return UUID objects for the first two columns. This
    runtime wrapper keeps the patch in LLM-Wiki instead of modifying site-packages.
    """
    from cognee.modules.engine.utils import generate_node_id

    processed_nodes = {}
    type_node_edges = []
    entity_node_edges = []
    type_entity_edges = []
    graph_node_edges = []

    for index, data_chunk in enumerate(data_chunks):
        graph = chunk_graphs[index]
        if graph is None:
            continue

        for node in graph.nodes:
            type_node_id = generate_node_id(node.type)
            entity_node_id = generate_node_id(node.id)

            if str(type_node_id) not in processed_nodes:
                type_node_edges.append((data_chunk.id, type_node_id, "exists_in"))
                processed_nodes[str(type_node_id)] = True

            if str(entity_node_id) not in processed_nodes:
                entity_node_edges.append((data_chunk.id, entity_node_id, "mentioned_in"))
                type_entity_edges.append((entity_node_id, type_node_id, "is_a"))
                processed_nodes[str(entity_node_id)] = True

        graph_node_edges.extend(
            (edge.target_node_id, edge.source_node_id, edge.relationship_name)
            for edge in graph.edges
        )

    existing_edges = await graph_engine.has_edges([
        *type_node_edges,
        *entity_node_edges,
        *type_entity_edges,
        *graph_node_edges,
    ])

    existing_edges_map = {}
    for edge in existing_edges:
        existing_edges_map[str(edge[0]) + str(edge[1]) + str(edge[2])] = True
    return existing_edges_map


class CogneeCodexPatch:
    """Runtime patch Cognee's get_llm_client() to return CodexCLICogneeAdapter."""

    def __init__(self, model: str = "gpt-5.4", timeout: int = 300, runner: Optional[CodexRunner] = None, deterministic_embeddings: bool = False, embedding_dimensions: int = 128) -> None:
        self.model = model
        self.timeout = timeout
        self.runner = runner
        self.deterministic_embeddings = deterministic_embeddings
        self.embedding_dimensions = embedding_dimensions
        self._module = None
        self._original = None
        self._embedding_module = None
        self._original_embedding = None
        self._patched_llm_refs = []
        self._patched_embedding_refs = []
        self._patched_graph_refs = []

    def __enter__(self):
        ensure_event_loop()
        import cognee.infrastructure.llm.get_llm_client as llm_module

        self._module = llm_module
        self._original = llm_module.get_llm_client

        def patched_get_llm_client():
            return CodexCLICogneeAdapter(model=self.model, timeout=self.timeout, runner=self.runner)

        llm_module.get_llm_client = patched_get_llm_client
        for module_name in COGNEE_LLM_IMPORT_MODULES:
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            if hasattr(module, "get_llm_client"):
                self._patched_llm_refs.append((module, module.get_llm_client))
                module.get_llm_client = patched_get_llm_client
        if self.deterministic_embeddings:
            self._embedding_module = importlib.import_module("cognee.infrastructure.databases.vector.embeddings.get_embedding_engine")
            self._original_embedding = self._embedding_module.get_embedding_engine

            def patched_get_embedding_engine():
                return DeterministicEmbeddingEngine(dimensions=self.embedding_dimensions)

            self._embedding_module.get_embedding_engine = patched_get_embedding_engine
            for module_name in COGNEE_EMBEDDING_IMPORT_MODULES:
                try:
                    module = importlib.import_module(module_name)
                except Exception:
                    continue
                if hasattr(module, "get_embedding_engine"):
                    self._patched_embedding_refs.append((module, module.get_embedding_engine))
                    module.get_embedding_engine = patched_get_embedding_engine
        for module_name in COGNEE_GRAPH_UTIL_IMPORT_MODULES:
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            if hasattr(module, "retrieve_existing_edges"):
                self._patched_graph_refs.append((module, module.retrieve_existing_edges))
                module.retrieve_existing_edges = retrieve_existing_edges_uuid_safe
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._module is not None and self._original is not None:
            self._module.get_llm_client = self._original
        for module, original in self._patched_llm_refs:
            module.get_llm_client = original
        if self._embedding_module is not None and self._original_embedding is not None:
            self._embedding_module.get_embedding_engine = self._original_embedding
        for module, original in self._patched_embedding_refs:
            module.get_embedding_engine = original
        for module, original in self._patched_graph_refs:
            module.retrieve_existing_edges = original
        return False
