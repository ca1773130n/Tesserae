---
type: Paper
arxiv: "2401.04099"
arxiv_url: https://arxiv.org/abs/2401.04099
title: "AGG: Amortized Generative 3D Gaussians for Single Image to 3D"
authors:
  - "Dejia Xu"
  - "Ye Yuan"
  - "Morteza Mardani"
  - "Sifei Liu"
  - "Jiaming Song"
  - "Zhangyang Wang"
  - "Arash Vahdat"
date: 2024-01-08
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, ImageTo3D]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# AGG: Amortized Generative 3D Gaussians for Single Image to 3D

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Given the growing need for automatic 3D content creation pipelines, various 3D representations have been studied to generate 3D objects from a single image. Due to its superior rendering efficiency, 3D Gaussian splatting-based models have recently excelled in both 3D reconstruction and generation. 3D Gaussian splatting approaches for image to 3D generation are often optimization-based, requiring many computationally expensive score-distillation steps. To overcome these challenges, we introduce an Amortized Generative 3D Gaussian framework (AGG) that instantly produces 3D Gaussians from a single image, eliminating the need for per-instance optimization. Utilizing an intermediate hybrid representation, AGG decomposes the generation of 3D Gaussian locations and other appearance attributes for joint optimization. Moreover, we propose a cascaded pipeline that first generates a coarse representation of the 3D data and later upsamples it with a 3D Gaussian super-resolution module. Our method is evaluated against existing optimization-based 3D Gaussian frameworks and sampling-based pipelines utilizing other 3D representations, where AGG showcases competitive generation abilities both qualitatively and quantitatively while being several orders of magnitude faster. Project page: https://ir1d.github.io/AGG/
