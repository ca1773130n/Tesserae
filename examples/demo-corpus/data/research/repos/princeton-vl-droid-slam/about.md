---
type: Repository
repo: princeton-vl/DROID-SLAM
canonical_paper: arxiv-2108-10869
---

# About princeton-vl/DROID-SLAM

The official PyTorch implementation of *DROID-SLAM: Deep Visual SLAM for Monocular, Stereo, and RGB-D Cameras* (Teed & Deng, 2021). See [the paper page](../../papers/arxiv-2108-10869/paper.md) for context.

DROID-SLAM couples a learned **dense bundle adjustment** layer with **recurrent optical flow** updates (a descendant of **RAFT**) to deliver state-of-the-art monocular, stereo, and RGB-D visual SLAM. The repo provides the trained weights, the differentiable BA CUDA kernels, and evaluation scripts for **EuRoC**, **TartanAir**, and **TUM-RGBD**. It is the deep-SLAM baseline that later neural-implicit SLAM systems in the corpus (**NICE-SLAM**, **Co-SLAM**, **Point-SLAM**) benchmark against. Mirrored under BSD-3-Clause — README only.
