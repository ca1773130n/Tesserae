---
type: Paper
arxiv: "2310.08528"
arxiv_url: https://arxiv.org/abs/2310.08528
title: "4D Gaussian Splatting for Real-Time Dynamic Scene Rendering"
authors:
  - "Guanjun Wu"
  - "Taoran Yi"
  - "Jiemin Fang"
  - "Lingxi Xie"
  - "Xiaopeng Zhang"
  - "Wei Wei"
  - "Wenyu Liu"
  - "Qi Tian"
  - "Xinggang Wang"
date: 2023-10-12
sub_topic: Dynamic and 4D Reconstruction
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, RealTimeRendering]
datasets: []
metrics: [FPS]
oss_repo: hustvl/4DGaussians
---

# 4D Gaussian Splatting for Real-Time Dynamic Scene Rendering

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Representing and rendering dynamic scenes has been an important but challenging task. Especially, to accurately model complex motions, high efficiency is usually hard to guarantee. To achieve real-time dynamic scene rendering while also enjoying high training and storage efficiency, we propose 4D Gaussian Splatting (4D-GS) as a holistic representation for dynamic scenes rather than applying 3D-GS for each individual frame. In 4D-GS, a novel explicit representation containing both 3D Gaussians and 4D neural voxels is proposed. A decomposed neural voxel encoding algorithm inspired by HexPlane is proposed to efficiently build Gaussian features from 4D neural voxels and then a lightweight MLP is applied to predict Gaussian deformations at novel timestamps. Our 4D-GS method achieves real-time rendering under high resolutions, 82 FPS at an 800$\times$800 resolution on an RTX 3090 GPU while maintaining comparable or better quality than previous state-of-the-art methods. More demos and code are available at https://guanjunwu.github.io/4dgs/.
