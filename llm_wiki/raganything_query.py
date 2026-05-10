"""Runtime query bridge for RAG-Anything memory backend.

Exposes a single synchronous ``query(question, *, backend_config)`` entry point
that loads ``raganything`` lazily and runs an async ``aquery`` call.
Returns ``None`` whenever the backend is disabled, the package is missing,
or the underlying call raises — callers can fall through to other backends.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Optional


def _load_raganything(cfg: dict):
    try:
        from raganything import RAGAnything, RAGAnythingConfig
    except Exception as exc:
        raise RuntimeError("raganything is not installed") from exc
    from .raganything_llm import build_runtime_funcs

    config = RAGAnythingConfig(
        working_dir=str(cfg["working_dir"]),
        parser=cfg.get("parser", "mineru"),
        parse_method=cfg.get("parse_method", "auto"),
    )
    funcs = build_runtime_funcs(cfg)
    return RAGAnything(config=config, **funcs)


def query(question: str, *, backend_config: dict) -> Optional[str]:
    if not backend_config or not backend_config.get("enabled"):
        return None
    if sys.version_info < (3, 10):
        print(
            f"raganything backend disabled: requires Python 3.10+, "
            f"current is {sys.version_info.major}.{sys.version_info.minor}",
            file=sys.stderr,
        )
        return None
    try:
        rag = _load_raganything(backend_config)
    except RuntimeError as exc:
        print(f"raganything backend disabled: {exc}", file=sys.stderr)
        return None

    mode = backend_config.get("query_mode", "hybrid")
    vlm = bool(backend_config.get("vlm_enhanced", False))

    async def run() -> str:
        return await rag.aquery(question, mode=mode, vlm_enhanced=vlm)

    try:
        return asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        print(f"raganything query failed: {exc}", file=sys.stderr)
        return None
