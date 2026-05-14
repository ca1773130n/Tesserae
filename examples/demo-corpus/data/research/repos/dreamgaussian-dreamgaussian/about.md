---
type: Repository
repo: dreamgaussian/dreamgaussian
canonical_paper: arxiv-2309-16653
---

# About dreamgaussian/dreamgaussian

The official implementation of *DreamGaussian: Generative Gaussian Splatting for Efficient 3D Content Creation* (Tang et al., 2023). See [the paper page](../../papers/arxiv-2309-16653/paper.md) for context.

DreamGaussian replaces the **NeRF** backbone used by **DreamFusion** with a **3D Gaussian Splatting** representation, then distills a **Zero-1-to-3** image-conditioned diffusion prior into it via **SDS** before extracting a textured mesh. The result is a ~2-minute image-to-3D pipeline that opened the *Diffusion-based 3D Generation* section of the corpus to feed-forward Gaussian generators like **LGM** and **AGG**. Mirrored under MIT — README only.
