---
type: Paper
arxiv: "2404.06109"
arxiv_url: https://arxiv.org/abs/2404.06109
title: "Revising Densification in Gaussian Splatting"
authors:
  - "Samuel Rota Bulò"
  - "Lorenzo Porzi"
  - "Peter Kontschieder"
date: 2024-04-09
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, NovelViewSynthesis]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Revising Densification in Gaussian Splatting

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

In this paper, we address the limitations of Adaptive Density Control (ADC) in 3D Gaussian Splatting (3DGS), a scene representation method achieving high-quality, photorealistic results for novel view synthesis. ADC has been introduced for automatic 3D point primitive management, controlling densification and pruning, however, with certain limitations in the densification logic. Our main contribution is a more principled, pixel-error driven formulation for density control in 3DGS, leveraging an auxiliary, per-pixel error function as the criterion for densification. We further introduce a mechanism to control the total number of primitives generated per scene and correct a bias in the current opacity handling strategy of ADC during cloning operations. Our approach leads to consistent quality improvements across a variety of benchmark scenes, without sacrificing the method's efficiency.
