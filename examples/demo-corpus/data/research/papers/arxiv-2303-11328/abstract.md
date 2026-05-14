---
type: Paper
arxiv: "2303.11328"
arxiv_url: https://arxiv.org/abs/2303.11328
title: "Zero-1-to-3: Zero-shot One Image to 3D Object"
authors:
  - "Ruoshi Liu"
  - "Rundi Wu"
  - "Basile Van Hoorick"
  - "Pavel Tokmakov"
  - "Sergey Zakharov"
  - "Carl Vondrick"
date: 2023-03-20
sub_topic: Diffusion-based 3D Generation
license: CC0 (arXiv abstract)
methods: [Diffusion, ImageTo3D, NovelViewSynthesis]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: cvlab-columbia/zero123
---

# Zero-1-to-3: Zero-shot One Image to 3D Object

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We introduce Zero-1-to-3, a framework for changing the camera viewpoint of an object given just a single RGB image. To perform novel view synthesis in this under-constrained setting, we capitalize on the geometric priors that large-scale diffusion models learn about natural images. Our conditional diffusion model uses a synthetic dataset to learn controls of the relative camera viewpoint, which allow new images to be generated of the same object under a specified camera transformation. Even though it is trained on a synthetic dataset, our model retains a strong zero-shot generalization ability to out-of-distribution datasets as well as in-the-wild images, including impressionist paintings. Our viewpoint-conditioned diffusion approach can further be used for the task of 3D reconstruction from a single image. Qualitative and quantitative experiments show that our method significantly outperforms state-of-the-art single-view 3D reconstruction and novel view synthesis models by leveraging Internet-scale pre-training.
