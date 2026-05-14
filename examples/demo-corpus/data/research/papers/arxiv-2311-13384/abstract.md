---
type: Paper
arxiv: "2311.13384"
arxiv_url: https://arxiv.org/abs/2311.13384
title: "LucidDreamer: Domain-free Generation of 3D Gaussian Splatting Scenes"
authors:
  - "Jaeyoung Chung"
  - "Suyoung Lee"
  - "Hyeongjin Nam"
  - "Jaerin Lee"
  - "Kyoung Mu Lee"
date: 2023-11-22
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, PointCloud]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# LucidDreamer: Domain-free Generation of 3D Gaussian Splatting Scenes

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

With the widespread usage of VR devices and contents, demands for 3D scene generation techniques become more popular. Existing 3D scene generation models, however, limit the target scene to specific domain, primarily due to their training strategies using 3D scan dataset that is far from the real-world. To address such limitation, we propose LucidDreamer, a domain-free scene generation pipeline by fully leveraging the power of existing large-scale diffusion-based generative model. Our LucidDreamer has two alternate steps: Dreaming and Alignment. First, to generate multi-view consistent images from inputs, we set the point cloud as a geometrical guideline for each image generation. Specifically, we project a portion of point cloud to the desired view and provide the projection as a guidance for inpainting using the generative model. The inpainted images are lifted to 3D space with estimated depth maps, composing a new points. Second, to aggregate the new points into the 3D scene, we propose an aligning algorithm which harmoniously integrates the portions of newly generated 3D scenes. The finally obtained 3D scene serves as initial points for optimizing Gaussian splats. LucidDreamer produces Gaussian splats that are highly-detailed compared to the previous 3D scene generation methods, with no constraint on domain of the target scene. Project page: https://luciddreamer-cvlab.github.io/
