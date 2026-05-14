---
type: Paper
arxiv: "2403.16292"
arxiv_url: https://arxiv.org/abs/2403.16292
title: "latentSplat: Autoencoding Variational Gaussians for Fast Generalizable 3D Reconstruction"
authors:
  - "Christopher Wewer"
  - "Kevin Raj"
  - "Eddy Ilg"
  - "Bernt Schiele"
  - "Jan Eric Lenssen"
date: 2024-03-24
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, Variational]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# latentSplat: Autoencoding Variational Gaussians for Fast Generalizable 3D Reconstruction

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We present latentSplat, a method to predict semantic Gaussians in a 3D latent space that can be splatted and decoded by a light-weight generative 2D architecture. Existing methods for generalizable 3D reconstruction either do not scale to large scenes and resolutions, or are limited to interpolation of close input views. latentSplat combines the strengths of regression-based and generative approaches while being trained purely on readily available real video data. The core of our method are variational 3D Gaussians, a representation that efficiently encodes varying uncertainty within a latent space consisting of 3D feature Gaussians. From these Gaussians, specific instances can be sampled and rendered via efficient splatting and a fast, generative decoder. We show that latentSplat outperforms previous works in reconstruction quality and generalization, while being fast and scalable to high-resolution data.
