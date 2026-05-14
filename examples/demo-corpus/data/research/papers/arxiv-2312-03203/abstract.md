---
type: Paper
arxiv: "2312.03203"
arxiv_url: https://arxiv.org/abs/2312.03203
title: "Feature 3DGS: Supercharging 3D Gaussian Splatting to Enable Distilled Feature Fields"
authors:
  - "Shijie Zhou"
  - "Haoran Chang"
  - "Sicheng Jiang"
  - "Zhiwen Fan"
  - "Zehao Zhu"
  - "Dejia Xu"
  - "Pradyumna Chari"
  - "Suya You"
  - "Zhangyang Wang"
  - "Achuta Kadambi"
date: 2023-12-06
sub_topic: 3D Gaussian Splatting
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, RadianceField, FeatureField, NovelViewSynthesis, RealTimeRendering]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Feature 3DGS: Supercharging 3D Gaussian Splatting to Enable Distilled Feature Fields

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

3D scene representations have gained immense popularity in recent years. Methods that use Neural Radiance fields are versatile for traditional tasks such as novel view synthesis. In recent times, some work has emerged that aims to extend the functionality of NeRF beyond view synthesis, for semantically aware tasks such as editing and segmentation using 3D feature field distillation from 2D foundation models. However, these methods have two major limitations: (a) they are limited by the rendering speed of NeRF pipelines, and (b) implicitly represented feature fields suffer from continuity artifacts reducing feature quality. Recently, 3D Gaussian Splatting has shown state-of-the-art performance on real-time radiance field rendering. In this work, we go one step further: in addition to radiance field rendering, we enable 3D Gaussian splatting on arbitrary-dimension semantic features via 2D foundation model distillation. This translation is not straightforward: naively incorporating feature fields in the 3DGS framework encounters significant challenges, notably the disparities in spatial resolution and channel consistency between RGB images and feature maps. We propose architectural and training changes to efficiently avert this problem. Our proposed method is general, and our experiments showcase novel view semantic segmentation, language-guided editing and segment anything through learning feature fields from state-of-the-art 2D foundation models such as SAM and CLIP-LSeg. Across experiments, our distillation method is able to provide comparable or better results, while being significantly faster to both train and render. Additionally, to the best of our knowledge, we are the first method to enable point and bounding-box prompting for radiance field manipulation, by leveraging the SAM model. Project website at: https://feature-3dgs.github.io/
