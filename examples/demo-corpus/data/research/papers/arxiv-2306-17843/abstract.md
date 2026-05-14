---
type: Paper
arxiv: "2306.17843"
arxiv_url: https://arxiv.org/abs/2306.17843
title: "Magic123: One Image to High-Quality 3D Object Generation Using Both 2D and 3D Diffusion Priors"
authors:
  - "Guocheng Qian"
  - "Jinjie Mai"
  - "Abdullah Hamdi"
  - "Jian Ren"
  - "Aliaksandr Siarohin"
  - "Bing Li"
  - "Hsin-Ying Lee"
  - "Ivan Skorokhodov"
  - "Peter Wonka"
  - "Sergey Tulyakov"
  - "Bernard Ghanem"
date: 2023-06-30
sub_topic: Diffusion-based 3D Generation
license: CC0 (arXiv abstract)
methods: [RadianceField, Diffusion, ImageTo3D, DepthEstimation]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Magic123: One Image to High-Quality 3D Object Generation Using Both 2D and 3D Diffusion Priors

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We present Magic123, a two-stage coarse-to-fine approach for high-quality, textured 3D meshes generation from a single unposed image in the wild using both2D and 3D priors. In the first stage, we optimize a neural radiance field to produce a coarse geometry. In the second stage, we adopt a memory-efficient differentiable mesh representation to yield a high-resolution mesh with a visually appealing texture. In both stages, the 3D content is learned through reference view supervision and novel views guided by a combination of 2D and 3D diffusion priors. We introduce a single trade-off parameter between the 2D and 3D priors to control exploration (more imaginative) and exploitation (more precise) of the generated geometry. Additionally, we employ textual inversion and monocular depth regularization to encourage consistent appearances across views and to prevent degenerate solutions, respectively. Magic123 demonstrates a significant improvement over previous image-to-3D techniques, as validated through extensive experiments on synthetic benchmarks and diverse real-world images. Our code, models, and generated 3D assets are available at https://github.com/guochengqian/Magic123.
