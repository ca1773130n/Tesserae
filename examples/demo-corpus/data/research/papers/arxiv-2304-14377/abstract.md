---
type: Paper
arxiv: "2304.14377"
arxiv_url: https://arxiv.org/abs/2304.14377
title: "Co-SLAM: Joint Coordinate and Sparse Parametric Encodings for Neural Real-Time SLAM"
authors:
  - "Hengyi Wang"
  - "Jingwen Wang"
  - "Lourdes Agapito"
date: 2023-04-27
sub_topic: Visual SLAM and MVS
license: CC0 (arXiv abstract)
methods: [SLAM, StructureFromMotion, BundleAdjustment, RealTimeRendering]
datasets: [ScanNet, Replica]
metrics: [PSNR, SSIM, LPIPS]
---

# Co-SLAM: Joint Coordinate and Sparse Parametric Encodings for Neural Real-Time SLAM

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We present Co-SLAM, a neural RGB-D SLAM system based on a hybrid representation, that performs robust camera tracking and high-fidelity surface reconstruction in real time. Co-SLAM represents the scene as a multi-resolution hash-grid to exploit its high convergence speed and ability to represent high-frequency local features. In addition, Co-SLAM incorporates one-blob encoding, to encourage surface coherence and completion in unobserved areas. This joint parametric-coordinate encoding enables real-time and robust performance by bringing the best of both worlds: fast convergence and surface hole filling. Moreover, our ray sampling strategy allows Co-SLAM to perform global bundle adjustment over all keyframes instead of requiring keyframe selection to maintain a small number of active keyframes as competing neural SLAM approaches do. Experimental results show that Co-SLAM runs at 10-17Hz and achieves state-of-the-art scene reconstruction results, and competitive tracking performance in various datasets and benchmarks (ScanNet, TUM, Replica, Synthetic RGBD). Project page: https://hengyiwang.github.io/projects/CoSLAM
