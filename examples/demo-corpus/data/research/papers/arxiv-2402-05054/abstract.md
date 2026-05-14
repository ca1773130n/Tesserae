---
type: Paper
arxiv: "2402.05054"
arxiv_url: https://arxiv.org/abs/2402.05054
title: "LGM: Large Multi-View Gaussian Model for High-Resolution 3D Content Creation"
authors:
  - "Jiaxiang Tang"
  - "Zhaoxi Chen"
  - "Xiaokang Chen"
  - "Tengfei Wang"
  - "Gang Zeng"
  - "Ziwei Liu"
date: 2024-02-07
sub_topic: Generative 3D Representations
license: CC0 (arXiv abstract)
methods: [Diffusion, FeedForward]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: 3DTopia/LGM
---

# LGM: Large Multi-View Gaussian Model for High-Resolution 3D Content Creation

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

3D content creation has achieved significant progress in terms of both quality and speed. Although current feed-forward models can produce 3D objects in seconds, their resolution is constrained by the intensive computation required during training. In this paper, we introduce Large Multi-View Gaussian Model (LGM), a novel framework designed to generate high-resolution 3D models from text prompts or single-view images. Our key insights are two-fold: 1) 3D Representation: We propose multi-view Gaussian features as an efficient yet powerful representation, which can then be fused together for differentiable rendering. 2) 3D Backbone: We present an asymmetric U-Net as a high-throughput backbone operating on multi-view images, which can be produced from text or single-view image input by leveraging multi-view diffusion models. Extensive experiments demonstrate the high fidelity and efficiency of our approach. Notably, we maintain the fast speed to generate 3D objects within 5 seconds while boosting the training resolution to 512, thereby achieving high-resolution 3D content generation.
