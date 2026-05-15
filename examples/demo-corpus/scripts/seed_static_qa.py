"""Pre-render ~12 demo question/answer pairs against the seeded LightRAG store.

The live GH Pages deploy is static HTML with no /api/ask backend, so the
per-page ask widget normally collapses to a one-liner saying "host this
wiki with llm_wiki project serve...". This script bakes answers for a
small curated set of demo questions so the widget's degraded mode can
show "Try a demo question" instead — visitors get a real taste of the
RAG retrieval without us needing to run a hosted backend.

Inputs:
    examples/demo-corpus/raganything-store/   (committed LightRAG store)
Outputs:
    examples/demo-corpus/qa-cache.json        ([{id, question, answer}, ...])

Codex cost: one CLI call per question (~30s). 12 questions ≈ 6 min wall.

Re-running with the same questions produces the same answers from
LightRAG's response cache, so it's effectively free after the first run.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_ROOT = REPO_ROOT / "examples" / "demo-corpus"
STORE_DIR = DEMO_ROOT / "raganything-store"
OUT_PATH = DEMO_ROOT / "qa-cache.json"

sys.path.insert(0, str(REPO_ROOT))

# Curated to cover the 3D-reconstruction corpus's main themes:
# representations (NeRF, 3DGS, SfM), training tricks (instant-ngp,
# differentiable rasterization), generative pipelines (DreamFusion, SDS,
# Zero-1-to-3), dynamic scenes (4D Gaussians), and SLAM (DROID-SLAM).
QUESTIONS: list[tuple[str, str]] = [
    ("3dgs-intro", "What is 3D Gaussian Splatting and what makes it real-time?"),
    ("3dgs-vs-nerf", "How does 3D Gaussian Splatting differ from NeRF?"),
    ("nerf-intro", "How does NeRF represent a 3D scene and what are its main limitations?"),
    ("instant-ngp", "How does Instant-NGP achieve such fast NeRF training?"),
    ("dreamfusion-sds", "What is Score Distillation Sampling and how does DreamFusion use it?"),
    ("zero123", "What does Zero-1-to-3 enable that earlier image-to-3D methods couldn't?"),
    ("4d-gaussians", "How does 4D Gaussian Splatting extend 3DGS to dynamic scenes?"),
    ("droid-slam", "How does DROID-SLAM differ from classical SLAM systems?"),
    ("sfm-role", "What role does Structure from Motion play in modern 3D reconstruction pipelines?"),
    ("explicit-vs-implicit", "What are the trade-offs between explicit primitives (like 3DGS) and implicit neural fields (like NeRF)?"),
    ("diff-rasterization", "What is differentiable rasterization and why does it matter for 3D Gaussian Splatting?"),
    ("dynamic-recon-challenges", "What are the main open challenges in dynamic 3D reconstruction?"),
]


async def main() -> int:
    from raganything import RAGAnything, RAGAnythingConfig
    from lightrag import LightRAG
    from llm_wiki.raganything_llm import build_runtime_funcs

    if not STORE_DIR.exists() or not any(STORE_DIR.glob("*.json")):
        print(f"ERROR: seeded store not found at {STORE_DIR}", file=sys.stderr)
        print("Run seed_raganything_store.py first.", file=sys.stderr)
        return 2

    cfg = {
        "working_dir": str(STORE_DIR),
        "llm": {"provider": "codex", "model": "gpt-5.4", "timeout": 360},
        "embedding": {
            "provider": "sentence-transformers",
            "dim": 384,
            "model": "all-MiniLM-L6-v2",
        },
    }
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
    lr = LightRAG(
        working_dir=cfg["working_dir"],
        llm_model_func=funcs["llm_model_func"],
        embedding_func=funcs["embedding_func"],
    )
    await lr.initialize_storages()
    rag.lightrag = lr

    print(f"[{time.strftime('%H:%M:%S')}] querying {len(QUESTIONS)} questions...", flush=True)
    out: list[dict] = []
    for idx, (qid, question) in enumerate(QUESTIONS, 1):
        t0 = time.time()
        try:
            answer = await rag.aquery(question, mode="mix")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{idx}/{len(QUESTIONS)}] {qid}: FAILED ({exc})", flush=True)
            continue
        dt = time.time() - t0
        answer = _strip_provenance_footer(answer or "").strip()
        if not answer:
            print(f"  [{idx}/{len(QUESTIONS)}] {qid}: empty answer; skipping", flush=True)
            continue
        out.append({"id": qid, "question": question, "answer": answer})
        preview = answer[:80].replace("\n", " ")
        print(f"  [{idx}/{len(QUESTIONS)}] {qid} ({dt:.1f}s): {preview}...", flush=True)

    OUT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[{time.strftime('%H:%M:%S')}] wrote {len(out)} entries to {OUT_PATH.relative_to(REPO_ROOT)}", flush=True)

    await rag.finalize_storages()
    return 0


def _strip_provenance_footer(text: str) -> str:
    """Drop the trailing `### References` block LightRAG adds.

    The widget doesn't render markdown headings nicely and the citation
    bullets just dilute the answer. The full citations are still
    discoverable via the live /api/ask path during `llm_wiki serve`.
    """
    return re.sub(r"\n+#+\s*References.*\Z", "", text, flags=re.DOTALL).rstrip()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
