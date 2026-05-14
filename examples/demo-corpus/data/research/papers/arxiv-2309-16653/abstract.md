---
type: Paper
arxiv: "2309.16653"
arxiv_url: https://arxiv.org/abs/2309.16653
title: "DreamGaussian: Generative Gaussian Splatting for Efficient 3D Content Creation"
authors:
  - "Jiaxiang Tang"
  - "Jiawei Ren"
  - "Hang Zhou"
  - "Ziwei Liu"
  - "Gang Zeng"
date: 2023-09-28
sub_topic: Diffusion-based 3D Generation
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, RadianceField, Diffusion, ScoreDistillation]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: dreamgaussian/dreamgaussian
---

# DreamGaussian: Generative Gaussian Splatting for Efficient 3D Content Creation

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Recent advances in 3D content creation mostly leverage optimization-based 3D generation via score distillation sampling (SDS). Though promising results have been exhibited, these methods often suffer from slow per-sample optimization, limiting their practical usage. In this paper, we propose DreamGaussian, a novel 3D content generation framework that achieves both efficiency and quality simultaneously. Our key insight is to design a generative 3D Gaussian Splatting model with companioned mesh extraction and texture refinement in UV space. In contrast to the occupancy pruning used in Neural Radiance Fields, we demonstrate that the progressive densification of 3D Gaussians converges significantly faster for 3D generative tasks. To further enhance the texture quality and facilitate downstream applications, we introduce an efficient algorithm to convert 3D Gaussians into textured meshes and apply a fine-tuning stage to refine the details. Extensive experiments demonstrate the superior efficiency and competitive generation quality of our proposed approach. Notably, DreamGaussian produces high-quality textured meshes in just 2 minutes from a single-view image, achieving approximately 10 times acceleration compared to existing methods.
