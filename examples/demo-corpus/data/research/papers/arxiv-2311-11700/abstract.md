---
type: Paper
arxiv: "2311.11700"
arxiv_url: https://arxiv.org/abs/2311.11700
title: "GS-SLAM: Dense Visual SLAM with 3D Gaussian Splatting"
authors:
  - "Chi Yan"
  - "Delin Qu"
  - "Dan Xu"
  - "Bin Zhao"
  - "Zhigang Wang"
  - "Dong Wang"
  - "Xuelong Li"
date: 2023-11-20
sub_topic: Visual SLAM and MVS
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, NeuralImplicitSurface, SLAM, RealTimeRendering]
datasets: [Replica, TUM-RGBD]
metrics: [PSNR, SSIM, LPIPS]
---

# GS-SLAM: Dense Visual SLAM with 3D Gaussian Splatting

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

In this paper, we introduce \textbf{GS-SLAM} that first utilizes 3D Gaussian representation in the Simultaneous Localization and Mapping (SLAM) system. It facilitates a better balance between efficiency and accuracy. Compared to recent SLAM methods employing neural implicit representations, our method utilizes a real-time differentiable splatting rendering pipeline that offers significant speedup to map optimization and RGB-D rendering. Specifically, we propose an adaptive expansion strategy that adds new or deletes noisy 3D Gaussians in order to efficiently reconstruct new observed scene geometry and improve the mapping of previously observed areas. This strategy is essential to extend 3D Gaussian representation to reconstruct the whole scene rather than synthesize a static object in existing methods. Moreover, in the pose tracking process, an effective coarse-to-fine technique is designed to select reliable 3D Gaussian representations to optimize camera pose, resulting in runtime reduction and robust estimation. Our method achieves competitive performance compared with existing state-of-the-art real-time methods on the Replica, TUM-RGBD datasets. Project page: https://gs-slam.github.io/.
