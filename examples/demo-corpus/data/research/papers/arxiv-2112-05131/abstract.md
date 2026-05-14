---
type: Paper
arxiv: "2112.05131"
arxiv_url: https://arxiv.org/abs/2112.05131
title: "Plenoxels: Radiance Fields without Neural Networks"
authors:
  - "Alex Yu"
  - "Sara Fridovich-Keil"
  - "Matthew Tancik"
  - "Qinhong Chen"
  - "Benjamin Recht"
  - "Angjoo Kanazawa"
date: 2021-12-09
sub_topic: Neural Radiance Fields
license: CC0 (arXiv abstract)
methods: [RadianceField, Plenoxels, NovelViewSynthesis]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: sxyu/svox2
---

# Plenoxels: Radiance Fields without Neural Networks

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We introduce Plenoxels (plenoptic voxels), a system for photorealistic view synthesis. Plenoxels represent a scene as a sparse 3D grid with spherical harmonics. This representation can be optimized from calibrated images via gradient methods and regularization without any neural components. On standard, benchmark tasks, Plenoxels are optimized two orders of magnitude faster than Neural Radiance Fields with no loss in visual quality.
