---
type: Paper
arxiv: "2402.07207"
arxiv_url: https://arxiv.org/abs/2402.07207
title: "GALA3D: Towards Text-to-3D Complex Scene Generation via Layout-guided Generative Gaussian Splatting"
authors:
  - "Xiaoyu Zhou"
  - "Xingjian Ran"
  - "Yajiao Xiong"
  - "Jinlin He"
  - "Zhiwei Lin"
  - "Yongtao Wang"
  - "Deqing Sun"
  - "Ming-Hsuan Yang"
date: 2024-02-11
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, TextTo3D]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# GALA3D: Towards Text-to-3D Complex Scene Generation via Layout-guided Generative Gaussian Splatting

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We present GALA3D, generative 3D GAussians with LAyout-guided control, for effective compositional text-to-3D generation. We first utilize large language models (LLMs) to generate the initial layout and introduce a layout-guided 3D Gaussian representation for 3D content generation with adaptive geometric constraints. We then propose an instance-scene compositional optimization mechanism with conditioned diffusion to collaboratively generate realistic 3D scenes with consistent geometry, texture, scale, and accurate interactions among multiple objects while simultaneously adjusting the coarse layout priors extracted from the LLMs to align with the generated scene. Experiments show that GALA3D is a user-friendly, end-to-end framework for state-of-the-art scene-level 3D content generation and controllable editing while ensuring the high fidelity of object-level entities within the scene. The source codes and models will be available at gala3d.github.io.
