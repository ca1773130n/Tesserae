---
type: Paper
arxiv: "2403.17888"
arxiv_url: https://arxiv.org/abs/2403.17888
title: "2D Gaussian Splatting for Geometrically Accurate Radiance Fields"
authors:
  - "Binbin Huang"
  - "Zehao Yu"
  - "Anpei Chen"
  - "Andreas Geiger"
  - "Shenghua Gao"
date: 2024-03-26
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, RadianceField, NovelViewSynthesis, RealTimeRendering]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: hbb1/2d-gaussian-splatting
---

# 2D Gaussian Splatting for Geometrically Accurate Radiance Fields

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

3D Gaussian Splatting (3DGS) has recently revolutionized radiance field reconstruction, achieving high quality novel view synthesis and fast rendering speed without baking. However, 3DGS fails to accurately represent surfaces due to the multi-view inconsistent nature of 3D Gaussians. We present 2D Gaussian Splatting (2DGS), a novel approach to model and reconstruct geometrically accurate radiance fields from multi-view images. Our key idea is to collapse the 3D volume into a set of 2D oriented planar Gaussian disks. Unlike 3D Gaussians, 2D Gaussians provide view-consistent geometry while modeling surfaces intrinsically. To accurately recover thin surfaces and achieve stable optimization, we introduce a perspective-correct 2D splatting process utilizing ray-splat intersection and rasterization. Additionally, we incorporate depth distortion and normal consistency terms to further enhance the quality of the reconstructions. We demonstrate that our differentiable renderer allows for noise-free and detailed geometry reconstruction while maintaining competitive appearance quality, fast training speed, and real-time rendering.
