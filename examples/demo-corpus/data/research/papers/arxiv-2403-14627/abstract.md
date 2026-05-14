---
type: Paper
arxiv: "2403.14627"
arxiv_url: https://arxiv.org/abs/2403.14627
title: "MVSplat: Efficient 3D Gaussian Splatting from Sparse Multi-View Images"
authors:
  - "Yuedong Chen"
  - "Haofei Xu"
  - "Chuanxia Zheng"
  - "Bohan Zhuang"
  - "Marc Pollefeys"
  - "Andreas Geiger"
  - "Tat-Jen Cham"
  - "Jianfei Cai"
date: 2024-03-21
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, FeedForward]
datasets: []
metrics: [FPS]
---

# MVSplat: Efficient 3D Gaussian Splatting from Sparse Multi-View Images

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We introduce MVSplat, an efficient model that, given sparse multi-view images as input, predicts clean feed-forward 3D Gaussians. To accurately localize the Gaussian centers, we build a cost volume representation via plane sweeping, where the cross-view feature similarities stored in the cost volume can provide valuable geometry cues to the estimation of depth. We also learn other Gaussian primitives' parameters jointly with the Gaussian centers while only relying on photometric supervision. We demonstrate the importance of the cost volume representation in learning feed-forward Gaussians via extensive experimental evaluations. On the large-scale RealEstate10K and ACID benchmarks, MVSplat achieves state-of-the-art performance with the fastest feed-forward inference speed (22~fps). More impressively, compared to the latest state-of-the-art method pixelSplat, MVSplat uses $10\times$ fewer parameters and infers more than $2\times$ faster while providing higher appearance and geometry quality as well as better cross-dataset generalization.
