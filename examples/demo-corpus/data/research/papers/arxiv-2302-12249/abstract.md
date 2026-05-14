---
type: Paper
arxiv: "2302.12249"
arxiv_url: https://arxiv.org/abs/2302.12249
title: "MERF: Memory-Efficient Radiance Fields for Real-time View Synthesis in Unbounded Scenes"
authors:
  - "Christian Reiser"
  - "Richard Szeliski"
  - "Dor Verbin"
  - "Pratul P. Srinivasan"
  - "Ben Mildenhall"
  - "Andreas Geiger"
  - "Jonathan T. Barron"
  - "Peter Hedman"
date: 2023-02-23
sub_topic: Neural Radiance Fields
license: CC0 (arXiv abstract)
methods: [RadianceField, NovelViewSynthesis, RealTimeRendering]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# MERF: Memory-Efficient Radiance Fields for Real-time View Synthesis in Unbounded Scenes

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Neural radiance fields enable state-of-the-art photorealistic view synthesis. However, existing radiance field representations are either too compute-intensive for real-time rendering or require too much memory to scale to large scenes. We present a Memory-Efficient Radiance Field (MERF) representation that achieves real-time rendering of large-scale scenes in a browser. MERF reduces the memory consumption of prior sparse volumetric radiance fields using a combination of a sparse feature grid and high-resolution 2D feature planes. To support large-scale unbounded scenes, we introduce a novel contraction function that maps scene coordinates into a bounded volume while still allowing for efficient ray-box intersection. We design a lossless procedure for baking the parameterization used during training into a model that achieves real-time rendering while still preserving the photorealistic view synthesis quality of a volumetric radiance field.
