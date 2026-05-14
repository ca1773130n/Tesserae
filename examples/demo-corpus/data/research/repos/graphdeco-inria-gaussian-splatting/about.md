---
type: Repository
repo: graphdeco-inria/gaussian-splatting
canonical_paper: arxiv-2308-04079
---

# About graphdeco-inria/gaussian-splatting

The official implementation of *3D Gaussian Splatting for Real-Time Radiance Field Rendering* (Kerbl et al., 2023). See [the paper page](../../papers/arxiv-2308-04079/paper.md) for context.

This repository introduced the reference CUDA kernels for **differentiable Gaussian rasterization**, **adaptive density control**, and the **spherical harmonics** view-dependent color model that essentially every downstream 3DGS method builds on. It also ships the training pipeline that takes a sparse **COLMAP** SfM point cloud and densifies it into millions of anisotropic 3D Gaussians. The LLM-Wiki demo corpus mirrors only the README to demonstrate cross-graph linking between papers and their canonical implementations; the upstream license is **non-commercial research only**, so the code itself is not redistributed here.
