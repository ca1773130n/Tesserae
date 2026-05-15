"""Seed the runtime LightRAG store for the demo corpus.

Walks ``examples/demo-corpus/data/research/`` markdown documents, batches
all texts into a single ``LightRAG.ainsert`` call, and persists the result
to ``examples/demo-corpus/raganything-store/`` so CI can copy it into
``.llm-wiki/external/raganything/working_dir/`` at deploy time without
re-burning Codex tokens.

Why direct ``ainsert`` instead of ``RAGAnything.insert_content_list``:
the wrapper's first-insert path mis-flags content as duplicate against an
empty store and skips chunking. Driving LightRAG's primitive directly
avoids that — confirmed via the 1-doc smoke at
``/tmp/llm-wiki-demo-raga2/smoke_one_doc.py``.

Budget: ~85 docs (50 papers + 24 repo pages + 11 synthesis pages),
~170 Codex calls at async-LLM concurrency 8, ~15-25 min wall time.

Skips ``abstract.md`` since ``paper.md`` already contains the abstract.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_ROOT = REPO_ROOT / "examples" / "demo-corpus"
DATA_ROOT = DEMO_ROOT / "data" / "research"
STORE_OUT = DEMO_ROOT / "raganything-store"

# Use the project's venv interpreter — assumes the caller already activated it
# or invoked this via .venv/bin/python.
sys.path.insert(0, str(REPO_ROOT))


def collect_docs() -> list[Path]:
    """Return ordered list of markdown documents to ingest.

    abstract.md is omitted because paper.md duplicates its content and we
    want one chunk-graph per paper, not two competing ones. _index.md
    files are listings of children and add no retrieval value.
    """
    docs: list[Path] = []
    for path in sorted(DATA_ROOT.rglob("*.md")):
        if path.name == "_index.md":
            continue
        if path.name == "abstract.md":
            continue
        docs.append(path)
    return docs


async def main() -> int:
    from raganything import RAGAnything, RAGAnythingConfig
    from lightrag import LightRAG
    from llm_wiki.raganything_llm import build_runtime_funcs

    cfg = {
        "working_dir": str(STORE_OUT),
        "llm": {"provider": "codex", "model": "gpt-5.4", "timeout": 360},
        "embedding": {
            "provider": "sentence-transformers",
            "dim": 384,
            "model": "all-MiniLM-L6-v2",
        },
    }
    STORE_OUT.mkdir(parents=True, exist_ok=True)

    funcs = build_runtime_funcs(cfg)

    rag = RAGAnything(
        config=RAGAnythingConfig(
            working_dir=cfg["working_dir"],
            parser="docling",
            parse_method="txt",
        ),
        llm_model_func=funcs["llm_model_func"],
        embedding_func=funcs["embedding_func"],
    )

    # Construct LightRAG directly — RAGAnything.aquery lazy-init would also
    # work, but we want explicit control so this script is idempotent.
    print(f"[{time.strftime('%H:%M:%S')}] building LightRAG at {STORE_OUT}", flush=True)
    lr = LightRAG(
        working_dir=cfg["working_dir"],
        llm_model_func=funcs["llm_model_func"],
        embedding_func=funcs["embedding_func"],
        # Doc-level concurrency. Default is 2; bumping to 4 keeps wall time
        # under ~30 min for the 85-doc demo corpus. Codex OAuth handles 8-16
        # concurrent CLI invocations cleanly (each is a fresh subprocess).
        max_parallel_insert=4,
    )
    await lr.initialize_storages()
    rag.lightrag = lr

    paths = collect_docs()
    print(f"[{time.strftime('%H:%M:%S')}] collected {len(paths)} documents", flush=True)

    texts: list[str] = []
    file_paths: list[str] = []
    skipped: list[tuple[Path, str]] = []
    for p in paths:
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            skipped.append((p, f"read-failed: {exc}"))
            continue
        if len(txt.strip()) < 80:
            skipped.append((p, f"too-short ({len(txt.strip())} chars)"))
            continue
        texts.append(txt)
        file_paths.append(str(p.relative_to(REPO_ROOT)))

    print(f"[{time.strftime('%H:%M:%S')}] ingesting {len(texts)} docs "
          f"({len(skipped)} skipped)", flush=True)
    for p, reason in skipped:
        print(f"  skip {p.relative_to(REPO_ROOT)}: {reason}", flush=True)

    t0 = time.time()
    try:
        await lr.ainsert(texts, file_paths=file_paths)
    except Exception as exc:  # noqa: BLE001
        print(f"[{time.strftime('%H:%M:%S')}] ainsert FAILED: {type(exc).__name__}: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        await rag.finalize_storages()
        return 1
    dt = time.time() - t0
    print(f"[{time.strftime('%H:%M:%S')}] ainsert done in {dt:.1f}s "
          f"({dt/max(len(texts), 1):.1f}s/doc avg)", flush=True)

    # Quick post-seed inspection
    import json as _json
    wd = Path(cfg["working_dir"])
    print(f"\n[{time.strftime('%H:%M:%S')}] store summary:", flush=True)
    for f in sorted(wd.glob("*.json")):
        try:
            data = _json.loads(f.read_text())
            n = len(data) if isinstance(data, (list, dict)) else "?"
            print(f"  {f.name}: {n}", flush=True)
        except Exception:
            pass

    # Smoke query to confirm retrieval works post-seed
    print(f"\n[{time.strftime('%H:%M:%S')}] sanity query...", flush=True)
    try:
        answer = await rag.aquery(
            "What is 3D Gaussian Splatting and how does it differ from NeRF?",
            mode="mix",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  query failed: {type(exc).__name__}: {exc}", flush=True)
        answer = None
    if answer:
        preview = answer[:400].replace("\n", " ")
        print(f"  ANSWER (preview): {preview}...", flush=True)

    await rag.finalize_storages()
    print(f"[{time.strftime('%H:%M:%S')}] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
