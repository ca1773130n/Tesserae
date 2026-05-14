---
type: Repository
repo: bmild/nerf
canonical_paper: arxiv-2003-08934
---

# About bmild/nerf

The original Tensorflow reference implementation of *NeRF: Representing Scenes as Neural Radiance Fields for View Synthesis* (Mildenhall et al., 2020). See [the paper page](../../papers/arxiv-2003-08934/paper.md) for context.

This repo defines the canonical training loop for the **5D radiance field MLP**, **positional encoding**, **hierarchical volume sampling**, and the **LLFF** / **Blender synthetic** dataset loaders that became standard NeRF evaluation harnesses. Nearly every later NeRF paper in the corpus (**Mip-NeRF**, **Mip-NeRF 360**, **Instant-NGP**, **Plenoxels**, **NeRF-W**, **BARF**) compares against this baseline. Mirrored here under MIT — README only.
