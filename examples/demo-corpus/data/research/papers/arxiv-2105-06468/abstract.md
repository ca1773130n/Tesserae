---
type: Paper
arxiv: "2105.06468"
arxiv_url: https://arxiv.org/abs/2105.06468
title: "Dynamic View Synthesis from Dynamic Monocular Video"
authors:
  - "Chen Gao"
  - "Ayush Saraf"
  - "Johannes Kopf"
  - "Jia-Bin Huang"
date: 2021-05-13
sub_topic: Dynamic and 4D Reconstruction
license: CC0 (arXiv abstract)
methods: [RadianceField, NeuralImplicitSurface, NovelViewSynthesis]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Dynamic View Synthesis from Dynamic Monocular Video

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We present an algorithm for generating novel views at arbitrary viewpoints and any input time step given a monocular video of a dynamic scene. Our work builds upon recent advances in neural implicit representation and uses continuous and differentiable functions for modeling the time-varying structure and the appearance of the scene. We jointly train a time-invariant static NeRF and a time-varying dynamic NeRF, and learn how to blend the results in an unsupervised manner. However, learning this implicit function from a single video is highly ill-posed (with infinitely many solutions that match the input video). To resolve the ambiguity, we introduce regularization losses to encourage a more physically plausible solution. We show extensive quantitative and qualitative results of dynamic view synthesis from casually captured videos.
