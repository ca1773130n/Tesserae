---
type: Repository
repo: cvlab-columbia/zero123
canonical_paper: arxiv-2303-11328
---

# About cvlab-columbia/zero123

The official release of *Zero-1-to-3: Zero-shot One Image to 3D Object* (Liu et al., 2023). See [the paper page](../../papers/arxiv-2303-11328/paper.md) for context.

Zero-1-to-3 fine-tunes **Stable Diffusion** to perform **viewpoint-conditioned novel view synthesis** from a single image, then uses the resulting model as a **diffusion prior** for **Score Distillation Sampling (SDS)** lifting to 3D. The repo provides the **Objaverse**-trained checkpoint and inference scripts that downstream methods in the corpus — **Magic123**, **DreamGaussian**, **SyncDreamer** — depend on. Mirrored under MIT — README only.
