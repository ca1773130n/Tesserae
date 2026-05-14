---
type: Paper
arxiv: "2103.13415"
arxiv_url: https://arxiv.org/abs/2103.13415
title: "Mip-NeRF: A Multiscale Representation for Anti-Aliasing Neural Radiance Fields"
authors:
  - "Jonathan T. Barron"
  - "Ben Mildenhall"
  - "Matthew Tancik"
  - "Peter Hedman"
  - "Ricardo Martin-Brualla"
  - "Pratul P. Srinivasan"
date: 2021-03-24
sub_topic: Neural Radiance Fields
license: CC0 (arXiv abstract)
methods: [RadianceField, AntiAliasing]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: google/mipnerf
---

# Mip-NeRF: A Multiscale Representation for Anti-Aliasing Neural Radiance Fields

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

The rendering procedure used by neural radiance fields (NeRF) samples a scene with a single ray per pixel and may therefore produce renderings that are excessively blurred or aliased when training or testing images observe scene content at different resolutions. The straightforward solution of supersampling by rendering with multiple rays per pixel is impractical for NeRF, because rendering each ray requires querying a multilayer perceptron hundreds of times. Our solution, which we call "mip-NeRF" (a la "mipmap"), extends NeRF to represent the scene at a continuously-valued scale. By efficiently rendering anti-aliased conical frustums instead of rays, mip-NeRF reduces objectionable aliasing artifacts and significantly improves NeRF's ability to represent fine details, while also being 7% faster than NeRF and half the size. Compared to NeRF, mip-NeRF reduces average error rates by 17% on the dataset presented with NeRF and by 60% on a challenging multiscale variant of that dataset that we present. Mip-NeRF is also able to match the accuracy of a brute-force supersampled NeRF on our multiscale dataset while being 22x faster.
