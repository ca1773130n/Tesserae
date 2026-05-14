---
type: Paper
arxiv: "2304.04278"
arxiv_url: https://arxiv.org/abs/2304.04278
title: "Point-SLAM: Dense Neural Point Cloud-based SLAM"
authors:
  - "Erik Sandström"
  - "Yue Li"
  - "Luc Van Gool"
  - "Martin R. Oswald"
date: 2023-04-09
sub_topic: Visual SLAM and MVS
license: CC0 (arXiv abstract)
methods: [SLAM, PointCloud]
datasets: [ScanNet, Replica, TUM-RGBD]
metrics: [PSNR, SSIM, LPIPS]
---

# Point-SLAM: Dense Neural Point Cloud-based SLAM

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We propose a dense neural simultaneous localization and mapping (SLAM) approach for monocular RGBD input which anchors the features of a neural scene representation in a point cloud that is iteratively generated in an input-dependent data-driven manner. We demonstrate that both tracking and mapping can be performed with the same point-based neural scene representation by minimizing an RGBD-based re-rendering loss. In contrast to recent dense neural SLAM methods which anchor the scene features in a sparse grid, our point-based approach allows dynamically adapting the anchor point density to the information density of the input. This strategy reduces runtime and memory usage in regions with fewer details and dedicates higher point density to resolve fine details. Our approach performs either better or competitive to existing dense neural RGBD SLAM methods in tracking, mapping and rendering accuracy on the Replica, TUM-RGBD and ScanNet datasets. The source code is available at https://github.com/eriksandstroem/Point-SLAM.
