---
type: Paper
arxiv: "2108.10869"
arxiv_url: https://arxiv.org/abs/2108.10869
title: "DROID-SLAM: Deep Visual SLAM for Monocular, Stereo, and RGB-D Cameras"
authors:
  - "Zachary Teed"
  - "Jia Deng"
date: 2021-08-24
sub_topic: Visual SLAM and MVS
license: CC0 (arXiv abstract)
methods: [SLAM, StructureFromMotion, BundleAdjustment]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: princeton-vl/DROID-SLAM
---

# DROID-SLAM: Deep Visual SLAM for Monocular, Stereo, and RGB-D Cameras

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We introduce DROID-SLAM, a new deep learning based SLAM system. DROID-SLAM consists of recurrent iterative updates of camera pose and pixelwise depth through a Dense Bundle Adjustment layer. DROID-SLAM is accurate, achieving large improvements over prior work, and robust, suffering from substantially fewer catastrophic failures. Despite training on monocular video, it can leverage stereo or RGB-D video to achieve improved performance at test time. The URL to our open source code is https://github.com/princeton-vl/DROID-SLAM.
