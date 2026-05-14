---
type: Paper
arxiv: "1812.04605"
arxiv_url: https://arxiv.org/abs/1812.04605
title: "DeepV2D: Video to Depth with Differentiable Structure from Motion"
authors:
  - "Zachary Teed"
  - "Jia Deng"
date: 2018-12-11
sub_topic: Visual SLAM and MVS
license: CC0 (arXiv abstract)
methods: [StructureFromMotion, DepthEstimation]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# DeepV2D: Video to Depth with Differentiable Structure from Motion

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We propose DeepV2D, an end-to-end deep learning architecture for predicting depth from video. DeepV2D combines the representation ability of neural networks with the geometric principles governing image formation. We compose a collection of classical geometric algorithms, which are converted into trainable modules and combined into an end-to-end differentiable architecture. DeepV2D interleaves two stages: motion estimation and depth estimation. During inference, motion and depth estimation are alternated and converge to accurate depth. Code is available https://github.com/princeton-vl/DeepV2D.
