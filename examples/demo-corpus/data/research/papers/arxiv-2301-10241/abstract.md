---
type: Paper
arxiv: "2301.10241"
arxiv_url: https://arxiv.org/abs/2301.10241
title: "K-Planes: Explicit Radiance Fields in Space, Time, and Appearance"
authors:
  - "Sara Fridovich-Keil"
  - "Giacomo Meanti"
  - "Frederik Warburg"
  - "Benjamin Recht"
  - "Angjoo Kanazawa"
date: 2023-01-24
sub_topic: Neural Radiance Fields
license: CC0 (arXiv abstract)
methods: [RadianceField]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: sarafridov/K-Planes
---

# K-Planes: Explicit Radiance Fields in Space, Time, and Appearance

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We introduce k-planes, a white-box model for radiance fields in arbitrary dimensions. Our model uses d choose 2 planes to represent a d-dimensional scene, providing a seamless way to go from static (d=3) to dynamic (d=4) scenes. This planar factorization makes adding dimension-specific priors easy, e.g. temporal smoothness and multi-resolution spatial structure, and induces a natural decomposition of static and dynamic components of a scene. We use a linear feature decoder with a learned color basis that yields similar performance as a nonlinear black-box MLP decoder. Across a range of synthetic and real, static and dynamic, fixed and varying appearance scenes, k-planes yields competitive and often state-of-the-art reconstruction fidelity with low memory usage, achieving 1000x compression over a full 4D grid, and fast optimization with a pure PyTorch implementation. For video results and code, please see https://sarafridov.github.io/K-Planes.
