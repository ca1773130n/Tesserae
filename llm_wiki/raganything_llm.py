"""RAG-Anything LLM/embedding adapters for LLM-Wiki.

Wraps LLM-Wiki's existing CLI primitives (`run_codex_cli`, `run_claude_cli`) as
the async callables RAGAnything's LightRAG backend expects. Embeddings default
to a deterministic hash-based scheme so the integration works without any
external embedding service.

Use:
    funcs = build_runtime_funcs(backend_config)
    rag = RAGAnything(config=..., **funcs)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Awaitable, Callable, List, Optional


def _flatten_prompt(prompt: str, system_prompt: Optional[str], history: Optional[list]) -> str:
    parts: list[str] = []
    if system_prompt:
        parts.append(f"[system]\n{system_prompt}")
    if history:
        for msg in history:
            role = (msg.get("role") if isinstance(msg, dict) else None) or "user"
            content = (msg.get("content") if isinstance(msg, dict) else str(msg)) or ""
            parts.append(f"[{role}]\n{content}")
    parts.append(prompt)
    return "\n\n".join(parts)


def make_codex_llm_func(
    *,
    model: str = "gpt-5.4",
    timeout: int = 300,
) -> Callable[..., Awaitable[str]]:
    """Return an async llm_model_func that routes through `codex exec` OAuth."""

    async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
        # Import lazily so the module loads even when cognee_codex's deps aren't ready.
        from . import cognee_codex as _cc

        flat = _flatten_prompt(prompt, system_prompt, history_messages)
        return await _cc.run_codex_cli(flat, model=model, timeout=timeout)

    return llm_model_func


def make_claude_llm_func(
    *,
    config_dir: Optional[str] = None,
    model: Optional[str] = None,
    timeout: int = 300,
) -> Callable[..., Awaitable[str]]:
    """Return an async llm_model_func that routes through `claude -p` with optional CLAUDE_CONFIG_DIR."""

    async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
        from . import llm_extractor as _le

        # Resolve at call time so env-var fallback honors any post-construction changes
        # (matters for tests that monkeypatch CLAUDE_CONFIG_DIR between calls).
        resolved_config_dir = (
            config_dir
            or os.environ.get("CLAUDE_CONFIG_DIR")
            or str(Path.home() / ".claude")
        )
        flat = _flatten_prompt(prompt, system_prompt, history_messages)
        return await asyncio.to_thread(
            _le.run_claude_cli, flat, resolved_config_dir, model or "", timeout
        )

    return llm_model_func


def make_llm_func(*, provider: str, **opts) -> Callable[..., Awaitable[str]]:
    if provider == "codex":
        return make_codex_llm_func(
            model=opts.get("model") or "gpt-5.4",
            timeout=int(opts.get("timeout") or 300),
        )
    if provider == "claude":
        return make_claude_llm_func(
            config_dir=opts.get("config_dir") or opts.get("claude_config_dir"),
            model=opts.get("model"),
            timeout=int(opts.get("timeout") or 300),
        )
    raise ValueError(
        f"Unsupported raganything llm provider: {provider!r} (expected 'codex' or 'claude')"
    )


# ---- Embeddings ----


def _deterministic_embedding(text: str, dim: int) -> list[float]:
    """Hash-based pseudo-embedding. Deterministic, dependency-free, low semantic quality.

    Suitable as a placeholder so LightRAG can initialize without an embedding
    service. Retrieval quality will be limited; users wanting real semantics
    should configure provider="ollama" or upstream OPENAI_API_KEY env vars.
    """
    seed = hashlib.sha512(text.encode("utf-8", errors="replace")).digest()
    floats: list[float] = []
    # Expand seed deterministically until we have `dim` floats.
    buf = bytearray(seed)
    while len(floats) < dim:
        for byte in bytearray(buf):
            floats.append((byte / 127.5) - 1.0)
            if len(floats) >= dim:
                break
        buf = bytearray(hashlib.sha512(bytes(buf)).digest())
    return floats[:dim]


def make_deterministic_embedding_func(*, dim: int):
    """Return an EmbeddingFunc-shaped object for deterministic embeddings."""
    try:
        from lightrag.utils import EmbeddingFunc  # type: ignore
    except Exception:
        EmbeddingFunc = None  # type: ignore[assignment]

    async def embed(texts: List[str]) -> List[List[float]]:
        return [_deterministic_embedding(text, dim) for text in texts]

    if EmbeddingFunc is None:
        # Fallback: a plain async callable. Some LightRAG versions accept this directly.
        return embed
    return EmbeddingFunc(embedding_dim=dim, max_token_size=8192, func=embed)


def make_ollama_embedding_func(
    *,
    dim: int,
    model: str = "nomic-embed-text",
    endpoint: str = "http://localhost:11434",
):
    """Return an EmbeddingFunc that calls an Ollama embedding endpoint."""
    try:
        from lightrag.utils import EmbeddingFunc  # type: ignore
    except Exception:
        EmbeddingFunc = None  # type: ignore[assignment]
    import json as _json
    import urllib.request

    async def embed_one(text: str) -> List[float]:
        def call() -> List[float]:
            req = urllib.request.Request(
                f"{endpoint.rstrip('/')}/api/embeddings",
                data=_json.dumps({"model": model, "prompt": text}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                payload = _json.loads(resp.read().decode("utf-8"))
            vec = payload.get("embedding") or []
            if len(vec) < dim:
                vec = list(vec) + [0.0] * (dim - len(vec))
            return vec[:dim]

        return await asyncio.to_thread(call)

    async def embed(texts: List[str]) -> List[List[float]]:
        return [await embed_one(text) for text in texts]

    if EmbeddingFunc is None:
        return embed
    return EmbeddingFunc(embedding_dim=dim, max_token_size=8192, func=embed)


def make_sentence_transformers_embedding_func(
    *,
    dim: int,
    model: str = "all-MiniLM-L6-v2",
):
    """Local semantic embeddings via `sentence-transformers`.

    No API keys, no daemon. The model downloads on first use into the
    default HuggingFace cache (~/.cache/huggingface/), ~90 MB for the
    default ``all-MiniLM-L6-v2`` (384-dim). This is the recommended
    provider for raganything's runtime when API keys aren't available
    — retrieval quality is genuinely semantic, unlike the deterministic
    hash-based fallback.

    Native model dim is 384 for ``all-MiniLM-L6-v2``. If a caller asks
    for a different ``dim``, we pad with zeros or truncate so LightRAG's
    fixed-dim store stays consistent — but for best results pick a
    ``dim`` that matches the model.
    """
    try:
        from lightrag.utils import EmbeddingFunc  # type: ignore
    except Exception:
        EmbeddingFunc = None  # type: ignore[assignment]

    # Lazy-load the model so importing this module doesn't pay the cost.
    _model_holder: dict = {}

    def _model():
        if "m" not in _model_holder:
            from sentence_transformers import SentenceTransformer  # type: ignore
            _model_holder["m"] = SentenceTransformer(model)
        return _model_holder["m"]

    async def embed(texts: List[str]):
        """Return numpy.ndarray of shape (len(texts), dim).

        LightRAG's internals call ``.size`` on the result, so a plain list
        of lists won't work — must be ndarray. We use it as the canonical
        output type.
        """
        import numpy as np  # local import keeps module load cheap

        def encode():
            mdl = _model()
            arr = mdl.encode(list(texts), show_progress_bar=False, convert_to_numpy=True)
            # Right-pad or truncate per row to match the requested dim
            n, native = arr.shape
            if native == dim:
                return arr.astype(np.float32, copy=False)
            if native < dim:
                padded = np.zeros((n, dim), dtype=np.float32)
                padded[:, :native] = arr
                return padded
            return arr[:, :dim].astype(np.float32, copy=False)

        return await asyncio.to_thread(encode)

    if EmbeddingFunc is None:
        return embed
    return EmbeddingFunc(embedding_dim=dim, max_token_size=8192, func=embed)


def make_embedding_func(*, provider: str, dim: int = 768, **opts):
    if provider == "deterministic":
        return make_deterministic_embedding_func(dim=dim)
    if provider == "ollama":
        return make_ollama_embedding_func(
            dim=dim,
            model=opts.get("model") or "nomic-embed-text",
            endpoint=opts.get("endpoint") or "http://localhost:11434",
        )
    if provider in ("sentence-transformers", "st", "local"):
        return make_sentence_transformers_embedding_func(
            dim=dim,
            model=opts.get("model") or "all-MiniLM-L6-v2",
        )
    raise ValueError(f"Unsupported raganything embedding provider: {provider!r}")


# ---- Aggregate ----


def build_runtime_funcs(backend_config: dict) -> dict:
    """Build the kwargs dict that RAGAnything's constructor expects.

    Returns {"llm_model_func": ..., "embedding_func": ..., "vision_model_func": None}.
    Vision is not configured in v1; pass None so RAGAnything falls back to text-only.
    """
    llm_cfg = (backend_config.get("llm") or {})
    embed_cfg = (backend_config.get("embedding") or {})
    return {
        "llm_model_func": make_llm_func(
            provider=str(llm_cfg.get("provider") or "codex"),
            model=llm_cfg.get("model"),
            timeout=llm_cfg.get("timeout"),
            config_dir=llm_cfg.get("claude_config_dir"),
        ),
        "embedding_func": make_embedding_func(
            provider=str(embed_cfg.get("provider") or "deterministic"),
            dim=int(embed_cfg.get("dim") or 768),
            model=embed_cfg.get("model"),
            endpoint=embed_cfg.get("endpoint"),
        ),
        "vision_model_func": None,
    }
