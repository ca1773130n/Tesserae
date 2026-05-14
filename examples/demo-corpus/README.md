# LLM-Wiki Demo Corpus: 3D Reconstruction (2016–2024)

A curated, synthetic research log used to populate the LLM-Wiki GitHub Pages demo at <https://ca1773130n.github.io/LLM-Wiki/>. The corpus covers 3D reconstruction broadly — neural radiance fields, Gaussian splatting, visual SLAM and multi-view stereo, diffusion-based 3D generation, neural-implicit surface and mesh extraction, dynamic / 4D reconstruction, and feed-forward generative 3D representations (LRMs).

## What's real and what's not

- **50 paper abstracts** — verbatim from arXiv, mirrored under arXiv's CC0-equivalent abstract policy. See `LICENSES.md` for the full provenance ledger and `INVENTORY.md` for the per-sub-topic index.
- **~12 OSS repo READMEs** *(Phase 3, not in this commit)* — mirrored from public projects with license attribution preserved. Most are MIT / Apache-2.0 / BSD; a few canonical era-defining repos (Inria's `gaussian-splatting`, NVlabs's `instant-ngp`) ship under research-only licenses and we mirror only the README with attribution.
- **6 daily digests + 2 weekly syntheses + 3 open questions** *(Phase 4, not in this commit)* — hand-written narrative glue, original work for the demo. They cite real corpus papers/repos but the prose is fabricated.
- **5 agent session transcripts** under `.agent-sessions/` — scripted demonstrations of LLM-Wiki's MCP query workflow. Tool names are real (verified against `llm_wiki/mcp_server.py`); the conversations themselves are written for the demo. *These ship as file-tree artifacts in the corpus only — the LLM-Wiki harness session importer (`llm_wiki/harness_sessions.py`) ingests native Claude Code / Codex transcripts (from `~/.claude/projects/...` and `~/.codex/sessions/...`), not the synthetic showcase format used here. Visitors who clone the repo can read them directly; they do not appear in the deployed site's `/sessions/` index.*

The goal is to make the GitHub Pages demo a believable "your literature review could look like this" experience without claiming any of the editorial work (digests, syntheses, sessions) is genuine research output.

## Why 3D reconstruction

3D reconstruction is a hot, fast-moving area with:

- a clearly identifiable canonical paper lineage (NeRF → instant-NGP → 3D Gaussian Splatting → variants),
- dense cross-citation that produces an interesting knowledge graph,
- a large pool of widely-cited public papers with permissive abstracts,
- many real public OSS implementations whose READMEs make natural paper↔repo bridges.

That gives the deployed graph view a recognizable dense cluster around each sub-topic, plus visible bridges between Gaussian Splatting and SLAM, between NeRF and surface reconstruction, and between diffusion-based generation and feed-forward LRMs.

## Sub-topic distribution

| Sub-topic | Papers |
|---|---|
| 3D Gaussian Splatting (canonical + variants) | 12 |
| Neural Radiance Fields (NeRF, mip-NeRF, Plenoxels, K-Planes, MERF, …) | 10 |
| Visual SLAM / Multi-View Stereo (DSO, DROID-SLAM, NICE-SLAM, GS-SLAM, …) | 8 |
| Diffusion-based 3D Generation (DreamFusion, Magic3D, Zero-1-to-3, …) | 6 |
| Mesh & Surface Reconstruction (NeuS, VolSDF, UNISURF, MonoSDF, IGR) | 5 |
| Dynamic / 4D Reconstruction (Nerfies, HyperNeRF, NSFF, 4D-GS, …) | 5 |
| Generative 3D Representations (LRM, TripoSR, LGM, Instant3D) | 4 |
| **Total** | **50** |

See `INVENTORY.md` for the full per-paper listing.

## Reading order for a first-time visitor

1. **`README.md`** *(you are here)* — framing.
2. **`INVENTORY.md`** — the map of the corpus, grouped by sub-topic, with links to each `abstract.md`.
3. **`LICENSES.md`** — the provenance ledger.
4. **`data/research/papers/arxiv-2308-04079/abstract.md`** — sample paper page (3D Gaussian Splatting).
5. *(Once Phase 4 ships)* `data/research/weekly/*/synthesis.md` — example weekly synthesis.
6. *(Once Phase 5 ships)* `.agent-sessions/*/transcript.jsonl` — example agent session.

## File layout

```
examples/demo-corpus/
├── README.md           # This file
├── LICENSES.md         # Provenance ledger (every external source)
├── INVENTORY.md        # Per-sub-topic paper index
└── data/
    └── research/
        └── papers/
            └── arxiv-YYYY-NNNNN/
                └── abstract.md   # YAML frontmatter + verbatim CC0 abstract
```

Phases 2–5 add `paper.md` summaries, `data/research/repos/`, `data/research/daily/`, `data/research/weekly/`, `data/research/questions/`, and `.agent-sessions/`.

## Phasing

This corpus is built in six commits following `docs/superpowers/specs/2026-05-15-demo-corpus-plan.md`. This commit is **Phase 1** — license hygiene, the inventory, and 50 CC0 abstracts. Phases 2–6 (paper bodies, repo READMEs, digests, syntheses, sessions, CI wiring) follow in subsequent commits.

## Snapshot date

Everything in this corpus is pinned to **2026-05-15**. It is not a live feed.
