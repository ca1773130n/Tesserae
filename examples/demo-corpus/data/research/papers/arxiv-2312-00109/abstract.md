---
type: Paper
arxiv: "2312.00109"
arxiv_url: https://arxiv.org/abs/2312.00109
title: "Scaffold-GS: Structured 3D Gaussians for View-Adaptive Rendering"
authors:
  - "Tao Lu"
  - "Mulin Yu"
  - "Linning Xu"
  - "Yuanbo Xiangli"
  - "Limin Wang"
  - "Dahua Lin"
  - "Bo Dai"
date: 2023-11-30
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Scaffold-GS: Structured 3D Gaussians for View-Adaptive Rendering

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Neural rendering methods have significantly advanced photo-realistic 3D scene rendering in various academic and industrial applications. The recent 3D Gaussian Splatting method has achieved the state-of-the-art rendering quality and speed combining the benefits of both primitive-based representations and volumetric representations. However, it often leads to heavily redundant Gaussians that try to fit every training view, neglecting the underlying scene geometry. Consequently, the resulting model becomes less robust to significant view changes, texture-less area and lighting effects. We introduce Scaffold-GS, which uses anchor points to distribute local 3D Gaussians, and predicts their attributes on-the-fly based on viewing direction and distance within the view frustum. Anchor growing and pruning strategies are developed based on the importance of neural Gaussians to reliably improve the scene coverage. We show that our method effectively reduces redundant Gaussians while delivering high-quality rendering. We also demonstrates an enhanced capability to accommodate scenes with varying levels-of-detail and view-dependent observations, without sacrificing the rendering speed.
