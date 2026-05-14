---
type: Paper
arxiv: "2104.06405"
arxiv_url: https://arxiv.org/abs/2104.06405
title: "BARF: Bundle-Adjusting Neural Radiance Fields"
authors:
  - "Chen-Hsuan Lin"
  - "Wei-Chiu Ma"
  - "Antonio Torralba"
  - "Simon Lucey"
date: 2021-04-13
sub_topic: Neural Radiance Fields
license: CC0 (arXiv abstract)
methods: [RadianceField, SLAM, BundleAdjustment, NovelViewSynthesis]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# BARF: Bundle-Adjusting Neural Radiance Fields

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Neural Radiance Fields (NeRF) have recently gained a surge of interest within the computer vision community for its power to synthesize photorealistic novel views of real-world scenes. One limitation of NeRF, however, is its requirement of accurate camera poses to learn the scene representations. In this paper, we propose Bundle-Adjusting Neural Radiance Fields (BARF) for training NeRF from imperfect (or even unknown) camera poses -- the joint problem of learning neural 3D representations and registering camera frames. We establish a theoretical connection to classical image alignment and show that coarse-to-fine registration is also applicable to NeRF. Furthermore, we show that naïvely applying positional encoding in NeRF has a negative impact on registration with a synthesis-based objective. Experiments on synthetic and real-world data show that BARF can effectively optimize the neural scene representations and resolve large camera pose misalignment at the same time. This enables view synthesis and localization of video sequences from unknown camera poses, opening up new avenues for visual localization systems (e.g. SLAM) and potential applications for dense 3D mapping and reconstruction.
