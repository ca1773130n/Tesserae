---
type: Paper
arxiv: "2111.12077"
arxiv_url: https://arxiv.org/abs/2111.12077
title: "Mip-NeRF 360: Unbounded Anti-Aliased Neural Radiance Fields"
authors:
  - "Jonathan T. Barron"
  - "Ben Mildenhall"
  - "Dor Verbin"
  - "Pratul P. Srinivasan"
  - "Peter Hedman"
date: 2021-11-23
sub_topic: Neural Radiance Fields
license: CC0 (arXiv abstract)
methods: [RadianceField, AntiAliasing, NovelViewSynthesis]
datasets: [Mip-NeRF360]
metrics: [PSNR, SSIM, LPIPS]
---

# Mip-NeRF 360: Unbounded Anti-Aliased Neural Radiance Fields

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Though neural radiance fields (NeRF) have demonstrated impressive view synthesis results on objects and small bounded regions of space, they struggle on "unbounded" scenes, where the camera may point in any direction and content may exist at any distance. In this setting, existing NeRF-like models often produce blurry or low-resolution renderings (due to the unbalanced detail and scale of nearby and distant objects), are slow to train, and may exhibit artifacts due to the inherent ambiguity of the task of reconstructing a large scene from a small set of images. We present an extension of mip-NeRF (a NeRF variant that addresses sampling and aliasing) that uses a non-linear scene parameterization, online distillation, and a novel distortion-based regularizer to overcome the challenges presented by unbounded scenes. Our model, which we dub "mip-NeRF 360" as we target scenes in which the camera rotates 360 degrees around a point, reduces mean-squared error by 57% compared to mip-NeRF, and is able to produce realistic synthesized views and detailed depth maps for highly intricate, unbounded real-world scenes.
