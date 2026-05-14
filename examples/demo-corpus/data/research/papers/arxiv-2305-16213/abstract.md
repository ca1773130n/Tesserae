---
type: Paper
arxiv: "2305.16213"
arxiv_url: https://arxiv.org/abs/2305.16213
title: "ProlificDreamer: High-Fidelity and Diverse Text-to-3D Generation with Variational Score Distillation"
authors:
  - "Zhengyi Wang"
  - "Cheng Lu"
  - "Yikai Wang"
  - "Fan Bao"
  - "Chongxuan Li"
  - "Hang Su"
  - "Jun Zhu"
date: 2023-05-25
sub_topic: Diffusion-based 3D Generation
license: CC0 (arXiv abstract)
methods: [RadianceField, Diffusion, ScoreDistillation, TextTo3D, Variational]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# ProlificDreamer: High-Fidelity and Diverse Text-to-3D Generation with Variational Score Distillation

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Score distillation sampling (SDS) has shown great promise in text-to-3D generation by distilling pretrained large-scale text-to-image diffusion models, but suffers from over-saturation, over-smoothing, and low-diversity problems. In this work, we propose to model the 3D parameter as a random variable instead of a constant as in SDS and present variational score distillation (VSD), a principled particle-based variational framework to explain and address the aforementioned issues in text-to-3D generation. We show that SDS is a special case of VSD and leads to poor samples with both small and large CFG weights. In comparison, VSD works well with various CFG weights as ancestral sampling from diffusion models and simultaneously improves the diversity and sample quality with a common CFG weight (i.e., $7.5$). We further present various improvements in the design space for text-to-3D such as distillation time schedule and density initialization, which are orthogonal to the distillation algorithm yet not well explored. Our overall approach, dubbed ProlificDreamer, can generate high rendering resolution (i.e., $512\times512$) and high-fidelity NeRF with rich structure and complex effects (e.g., smoke and drops). Further, initialized from NeRF, meshes fine-tuned by VSD are meticulously detailed and photo-realistic. Project page and codes: https://ml.cs.tsinghua.edu.cn/prolificdreamer/
