---
type: Paper
arxiv: "2112.12130"
arxiv_url: https://arxiv.org/abs/2112.12130
title: "NICE-SLAM: Neural Implicit Scalable Encoding for SLAM"
authors:
  - "Zihan Zhu"
  - "Songyou Peng"
  - "Viktor Larsson"
  - "Weiwei Xu"
  - "Hujun Bao"
  - "Zhaopeng Cui"
  - "Martin R. Oswald"
  - "Marc Pollefeys"
date: 2021-12-22
sub_topic: Visual SLAM and MVS
license: CC0 (arXiv abstract)
methods: [NeuralImplicitSurface, SLAM]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# NICE-SLAM: Neural Implicit Scalable Encoding for SLAM

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Neural implicit representations have recently shown encouraging results in various domains, including promising progress in simultaneous localization and mapping (SLAM). Nevertheless, existing methods produce over-smoothed scene reconstructions and have difficulty scaling up to large scenes. These limitations are mainly due to their simple fully-connected network architecture that does not incorporate local information in the observations. In this paper, we present NICE-SLAM, a dense SLAM system that incorporates multi-level local information by introducing a hierarchical scene representation. Optimizing this representation with pre-trained geometric priors enables detailed reconstruction on large indoor scenes. Compared to recent neural implicit SLAM systems, our approach is more scalable, efficient, and robust. Experiments on five challenging datasets demonstrate competitive results of NICE-SLAM in both mapping and tracking quality. Project page: https://pengsongyou.github.io/nice-slam
