---
type: Paper
arxiv: "1607.02565"
arxiv_url: https://arxiv.org/abs/1607.02565
title: "Direct Sparse Odometry"
authors:
  - "Jakob Engel"
  - "Vladlen Koltun"
  - "Daniel Cremers"
date: 2016-07-09
sub_topic: Visual SLAM and MVS
license: CC0 (arXiv abstract)
methods: [RealTimeRendering]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Direct Sparse Odometry

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We propose a novel direct sparse visual odometry formulation. It combines a fully direct probabilistic model (minimizing a photometric error) with consistent, joint optimization of all model parameters, including geometry -- represented as inverse depth in a reference frame -- and camera motion. This is achieved in real time by omitting the smoothness prior used in other direct methods and instead sampling pixels evenly throughout the images. Since our method does not depend on keypoint detectors or descriptors, it can naturally sample pixels from across all image regions that have intensity gradient, including edges or smooth intensity variations on mostly white walls. The proposed model integrates a full photometric calibration, accounting for exposure time, lens vignetting, and non-linear response functions. We thoroughly evaluate our method on three different datasets comprising several hours of video. The experiments show that the presented approach significantly outperforms state-of-the-art direct and indirect methods in a variety of real-world settings, both in terms of tracking accuracy and robustness.
