---
type: Repository
repo: ashawkey/stable-dreamfusion
canonical_paper: arxiv-2209-14988
---

# About ashawkey/stable-dreamfusion

The widely used open reimplementation of *DreamFusion: Text-to-3D using 2D Diffusion* (Poole et al., 2022) by Jiaxiang Tang. See [the paper page](../../papers/arxiv-2209-14988/paper.md) for context.

Because the original DreamFusion used Google's closed **Imagen** diffusion model, this repo recreates the pipeline on top of **Stable Diffusion**, providing reference implementations of **Score Distillation Sampling (SDS)**, a hash-grid backed **NeRF/DMTet** geometry representation, and the **text-to-3D** optimization loop. It is the de-facto starting point that **Magic3D**, **ProlificDreamer**, and **Magic123** in the corpus extend. Mirrored under Apache-2.0 — README only.
