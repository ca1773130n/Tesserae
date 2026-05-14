---
type: Paper
arxiv: "2211.10440"
arxiv_url: https://arxiv.org/abs/2211.10440
title: "Magic3D: High-Resolution Text-to-3D Content Creation"
authors:
  - "Chen-Hsuan Lin"
  - "Jun Gao"
  - "Luming Tang"
  - "Towaki Takikawa"
  - "Xiaohui Zeng"
  - "Xun Huang"
  - "Karsten Kreis"
  - "Sanja Fidler"
  - "Ming-Yu Liu"
  - "Tsung-Yi Lin"
date: 2022-11-18
sub_topic: Diffusion-based 3D Generation
license: CC0 (arXiv abstract)
methods: [RadianceField, Diffusion, HashEncoding, TextTo3D]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Magic3D: High-Resolution Text-to-3D Content Creation

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

DreamFusion has recently demonstrated the utility of a pre-trained text-to-image diffusion model to optimize Neural Radiance Fields (NeRF), achieving remarkable text-to-3D synthesis results. However, the method has two inherent limitations: (a) extremely slow optimization of NeRF and (b) low-resolution image space supervision on NeRF, leading to low-quality 3D models with a long processing time. In this paper, we address these limitations by utilizing a two-stage optimization framework. First, we obtain a coarse model using a low-resolution diffusion prior and accelerate with a sparse 3D hash grid structure. Using the coarse representation as the initialization, we further optimize a textured 3D mesh model with an efficient differentiable renderer interacting with a high-resolution latent diffusion model. Our method, dubbed Magic3D, can create high quality 3D mesh models in 40 minutes, which is 2x faster than DreamFusion (reportedly taking 1.5 hours on average), while also achieving higher resolution. User studies show 61.7% raters to prefer our approach over DreamFusion. Together with the image-conditioned generation capabilities, we provide users with new ways to control 3D synthesis, opening up new avenues to various creative applications.
