---
type: Paper
arxiv: "2209.14988"
arxiv_url: https://arxiv.org/abs/2209.14988
title: "DreamFusion: Text-to-3D using 2D Diffusion"
authors:
  - "Ben Poole"
  - "Ajay Jain"
  - "Jonathan T. Barron"
  - "Ben Mildenhall"
date: 2022-09-29
sub_topic: Diffusion-based 3D Generation
license: CC0 (arXiv abstract)
methods: [RadianceField, Diffusion, TextTo3D]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: ashawkey/stable-dreamfusion
---

# DreamFusion: Text-to-3D using 2D Diffusion

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Recent breakthroughs in text-to-image synthesis have been driven by diffusion models trained on billions of image-text pairs. Adapting this approach to 3D synthesis would require large-scale datasets of labeled 3D data and efficient architectures for denoising 3D data, neither of which currently exist. In this work, we circumvent these limitations by using a pretrained 2D text-to-image diffusion model to perform text-to-3D synthesis. We introduce a loss based on probability density distillation that enables the use of a 2D diffusion model as a prior for optimization of a parametric image generator. Using this loss in a DeepDream-like procedure, we optimize a randomly-initialized 3D model (a Neural Radiance Field, or NeRF) via gradient descent such that its 2D renderings from random angles achieve a low loss. The resulting 3D model of the given text can be viewed from any angle, relit by arbitrary illumination, or composited into any 3D environment. Our approach requires no 3D training data and no modifications to the image diffusion model, demonstrating the effectiveness of pretrained image diffusion models as priors.
