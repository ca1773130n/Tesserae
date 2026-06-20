"""Codex CLI/OAuth adapter for Cognee structured LLM calls."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Type

from pydantic import BaseModel

CodexRunner = Callable[[str, str, int], Awaitable[str]]
OllamaEmbedder = Callable[[List[str], str, str, int], Awaitable[List[List[float]]]]

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
    # cognee 1.x: the real call site binds get_embedding_engine by-name at import
    # time (and the source is @lru_cache'd), so patching only the source module
    # is bypassed — patch the caller's own reference too.
    "cognee.infrastructure.databases.vector.create_vector_engine",
]

COGNEE_GRAPH_UTIL_IMPORT_MODULES = [
    "cognee.modules.graph.utils.retrieve_existing_edges",
    "cognee.modules.graph.utils",
    "cognee.tasks.graph.extract_graph_from_data",
]

# Where ``get_llm_client`` lives, newest layout first. Cognee 1.x moved it under
# ``structured_output_framework/litellm_instructor`` (LLMGateway lazily imports it
# from here at call time, so patching it is enough); 0.1.x had it at the top
# ``infrastructure.llm`` path. Resolved at runtime so one adapter spans versions.
COGNEE_GET_LLM_CLIENT_PATHS = [
    "cognee.infrastructure.llm.structured_output_framework.litellm_instructor.llm.get_llm_client",
    "cognee.infrastructure.llm.get_llm_client",
]

# Environment cognee 1.x needs for an unattended, local, no-API-key cognify
# (set for the duration of the patch, restored on exit):
#   - ENABLE_BACKEND_ACCESS_CONTROL=false — 1.x defaults to auth-required +
#     multi-tenant, which blocks an unattended single-user build.
#   - COGNEE_SKIP_CONNECTION_TEST=true — 1.x probes the embedding endpoint at
#     startup; we use a local deterministic/Ollama engine, so the probe (against
#     a non-existent OpenAI endpoint) only times out.
COGNEE_AUTH_ENV = {
    "ENABLE_BACKEND_ACCESS_CONTROL": "false",
    "COGNEE_SKIP_CONNECTION_TEST": "true",
}


def _cognee_major_version() -> int:
    """Best-effort major version of the installed cognee (1 for unknown/new)."""
    ver = ""
    try:
        import cognee
        ver = getattr(cognee, "__version__", "") or ""
    except Exception:  # noqa: BLE001
        pass
    if not ver:
        try:
            from importlib.metadata import version
            ver = version("cognee")
        except Exception:  # noqa: BLE001
            return 1
    head = str(ver).split(".")[0]
    return int(head) if head.isdigit() else 1


def _resolve_get_llm_client_module():
    """Import the module that defines ``get_llm_client``, across cognee versions."""
    last_err: Optional[Exception] = None
    for path in COGNEE_GET_LLM_CLIENT_PATHS:
        try:
            module = importlib.import_module(path)
        except Exception as exc:  # noqa: BLE001 — try the next known layout
            last_err = exc
            continue
        if hasattr(module, "get_llm_client"):
            return module
    raise CodexCLIError(
        "could not locate cognee's get_llm_client in any known layout "
        f"(tried {COGNEE_GET_LLM_CLIENT_PATHS}); last error: {last_err}"
    )


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

    async def acreate_structured_output(self, text_input: str, system_prompt: str, response_model):
        # cognee 1.x calls this with a plain ``str`` (and other non-pydantic
        # types) for free-text outputs like summaries — not only BaseModels.
        is_model = isinstance(response_model, type) and issubclass(response_model, BaseModel)
        if not is_model:
            raw = await self.runner(build_text_prompt(text_input, system_prompt), self.model, self.timeout)
            text = raw.strip()
            return text if response_model in (str, None) else text  # best-effort plain text
        prompt = build_structured_prompt(text_input, system_prompt, response_model)
        raw = await self.runner(prompt, self.model, self.timeout)
        payload = extract_json_object(raw)
        return response_model.model_validate(payload)

    def show_prompt(self, text_input: str, system_prompt: str) -> str:
        return f"System Prompt:\n{system_prompt}\n\nUser Input:\n{text_input}\n"

    # cognee 1.x's LLMInterface also declares audio/image methods. Tesserae only
    # cognifies text, so these are not exercised — implement them defensively so
    # the adapter satisfies the interface if cognee ever probes for them.
    async def create_transcript(self, input: str):
        raise NotImplementedError("Codex CLI adapter does text-only cognify; audio transcription is unsupported")

    async def transcribe_image(self, input: str):
        raise NotImplementedError("Codex CLI adapter does text-only cognify; image transcription is unsupported")


def build_text_prompt(text_input: str, system_prompt: str) -> str:
    """Prompt for a plain-text response (cognee passes ``response_model=str``)."""
    return f"""You are a writing assistant for Cognee. Follow the instructions and
return ONLY the requested text — no JSON, no markdown fences, no commentary.

System instructions from Cognee:
{system_prompt}

Input text:
{text_input}
"""


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


class _ApproxTokenizer:
    """Tokenizer cognee 1.x reads off the embedding engine for chunk sizing.

    cognee calls ``embedding_engine.tokenizer.count_tokens(text)`` to size
    chunks; an approximate count (~4 chars/token) is plenty for that — we don't
    need a model-exact tokenizer for a local, no-API-key build.
    """

    def count_tokens(self, text) -> int:
        return max(1, len(str(text)) // 4)


class DeterministicEmbeddingEngine:
    """Small local embedding engine for no-API-key Cognee smoke runs.

    This is not semantic embedding quality; it is a deterministic substrate that
    lets Cognee cognify smoke tests run without OpenAI embedding keys. Use a real
    local embedding provider such as Ollama for production retrieval quality.
    """

    def __init__(self, dimensions: int = 128) -> None:
        self.dimensions = dimensions
        # cognee 1.x reads these off the engine for chunk sizing.
        self.max_completion_tokens = 8191
        self.tokenizer = _ApproxTokenizer()

    async def embed_text(self, text):
        return [self._embed_one(item) for item in text]

    def get_vector_size(self) -> int:
        return self.dimensions

    def get_batch_size(self) -> int:  # required by cognee 1.x EmbeddingEngine
        return 100

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


class OllamaEmbeddingEngine:
    """Cognee-compatible embedding engine backed by Ollama `/api/embed`.

    Use Qwen3 embedding models for real no-API-key retrieval quality. Queries are
    instruction-prefixed because Qwen3 Embedding is instruction-aware; documents
    are embedded as-is, matching Qwen's retrieval guidance.
    """

    DEFAULT_QUERY_INSTRUCTION = "Given a research intelligence query, retrieve relevant papers, claims, evidence spans, methods, datasets, benchmarks, and technical concepts."

    def __init__(
        self,
        model: str = "qwen3-embedding:0.6b",
        dimensions: int = 1024,
        endpoint: str = "http://127.0.0.1:11434/api/embed",
        timeout: int = 120,
        query_instruction: str = DEFAULT_QUERY_INSTRUCTION,
        embedder: Optional[OllamaEmbedder] = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.endpoint = endpoint
        self.timeout = timeout
        self.query_instruction = query_instruction
        self.embedder = embedder or ollama_embed
        # cognee 1.x reads these off the engine for chunk sizing.
        self.max_completion_tokens = 8191
        self.tokenizer = _ApproxTokenizer()

    async def embed_text(self, text):
        return await self.embedder(list(text), self.model, self.endpoint, self.timeout)

    async def embed_query(self, query: str):
        instructed = f"Instruct: {self.query_instruction}\nQuery: {query}"
        return await self.embed_text([instructed])

    def get_vector_size(self) -> int:
        return self.dimensions

    def get_batch_size(self) -> int:  # required by cognee 1.x EmbeddingEngine
        return 100


async def ollama_embed(texts: List[str], model: str, endpoint: str, timeout: int) -> List[List[float]]:
    return await asyncio.to_thread(_ollama_embed_sync, texts, model, endpoint, timeout)


def _ollama_embed_sync(texts: List[str], model: str, endpoint: str, timeout: int) -> List[List[float]]:
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama embedding request failed for {model} at {endpoint}: {exc}") from exc
    parsed = json.loads(raw)
    embeddings = parsed.get("embeddings")
    if embeddings is None and "embedding" in parsed:
        embeddings = [parsed["embedding"]]
    if not isinstance(embeddings, list):
        raise RuntimeError(f"Ollama response did not contain embeddings: {raw[:500]}")
    return embeddings


async def retrieve_existing_edges_uuid_safe(data_chunks, chunk_graphs, graph_engine) -> Dict[str, bool]:
    """Cognee 0.1.20-compatible retrieve_existing_edges that stringifies UUIDs.

    Cognee's implementation builds keys with `edge[0] + edge[1] + edge[2]` even
    though graph engines may return UUID objects for the first two columns. This
    runtime wrapper keeps the patch in Tesserae instead of modifying site-packages.
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

    def __init__(self, model: str = "gpt-5.4", timeout: int = 300, runner: Optional[CodexRunner] = None, deterministic_embeddings: bool = False, ollama_embeddings: bool = False, ollama_model: str = "qwen3-embedding:0.6b", ollama_endpoint: str = "http://127.0.0.1:11434/api/embed", ollama_timeout: int = 120, embedding_dimensions: int = 128) -> None:
        self.model = model
        self.timeout = timeout
        self.runner = runner
        self.deterministic_embeddings = deterministic_embeddings
        self.ollama_embeddings = ollama_embeddings
        self.ollama_model = ollama_model
        self.ollama_endpoint = ollama_endpoint
        self.ollama_timeout = ollama_timeout
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
        # Disable cognee 1.x's auth/access-control posture for this local build
        # (restored on exit). setdefault-style: remember the prior value.
        self._auth_env_prev = {k: os.environ.get(k) for k in COGNEE_AUTH_ENV}
        os.environ.update(COGNEE_AUTH_ENV)

        llm_module = _resolve_get_llm_client_module()

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
        if self.deterministic_embeddings or self.ollama_embeddings:
            self._embedding_module = importlib.import_module("cognee.infrastructure.databases.vector.embeddings.get_embedding_engine")
            self._original_embedding = self._embedding_module.get_embedding_engine

            def patched_get_embedding_engine():
                if self.ollama_embeddings:
                    return OllamaEmbeddingEngine(
                        model=self.ollama_model,
                        dimensions=self.embedding_dimensions,
                        endpoint=self.ollama_endpoint,
                        timeout=self.ollama_timeout,
                    )
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
            # cognee 1.x @lru_cache's get_embedding_engine and may cache the
            # vector-engine singleton; clear them so our patched engine is used
            # even if cognee already built one (e.g. during a connection probe).
            for orig in (self._original_embedding, *(o for _, o in self._patched_embedding_refs)):
                cc = getattr(orig, "cache_clear", None)
                if callable(cc):
                    try:
                        cc()
                    except Exception:  # noqa: BLE001
                        pass
            for mod_name in ("cognee.infrastructure.databases.vector.get_vector_engine",
                             "cognee.infrastructure.databases.vector.create_vector_engine"):
                try:
                    mod = importlib.import_module(mod_name)
                    for attr in ("get_vector_engine", "create_vector_engine"):
                        fn = getattr(mod, attr, None)
                        cc = getattr(fn, "cache_clear", None)
                        if callable(cc):
                            cc()
                except Exception:  # noqa: BLE001
                    pass
        # The retrieve_existing_edges UUID-dedup shim worked around a bug in
        # cognee 0.x and takes a ``graph_engine`` arg that 1.x no longer passes
        # (1.x fixed the bug and changed the signature). Applying it on 1.x
        # breaks edge processing — so only patch on 0.x.
        if _cognee_major_version() < 1:
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
        for key, prev in getattr(self, "_auth_env_prev", {}).items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        return False
