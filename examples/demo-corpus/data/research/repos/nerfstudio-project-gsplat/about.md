---
type: Repository
repo: nerfstudio-project/gsplat
canonical_paper: arxiv-2312-02121
---

# About nerfstudio-project/gsplat

The open, permissive CUDA library for **Gaussian splatting** maintained by the Nerfstudio team, accompanied by the mathematical write-up *Mathematical Supplement for the gsplat Library* (Ye et al., 2023). See [the paper page](../../papers/arxiv-2312-02121/paper.md) for context.

`gsplat` re-implements the core **differentiable rasterization** of 3D Gaussians with explicit, documented gradients suitable for research extensions. It is the backbone used by Nerfstudio's 3DGS training, and downstream methods such as **MVSplat**, **Scaffold-GS**, and **Feature 3DGS** plug into its CUDA kernels. Mirrored here under Apache-2.0; only the README is included.
