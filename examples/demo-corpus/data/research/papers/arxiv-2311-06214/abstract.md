---
type: Paper
arxiv: "2311.06214"
arxiv_url: https://arxiv.org/abs/2311.06214
title: "Instant3D: Fast Text-to-3D with Sparse-View Generation and Large Reconstruction Model"
authors:
  - "Jiahao Li"
  - "Hao Tan"
  - "Kai Zhang"
  - "Zexiang Xu"
  - "Fujun Luan"
  - "Yinghao Xu"
  - "Yicong Hong"
  - "Kalyan Sunkavalli"
  - "Greg Shakhnarovich"
  - "Sai Bi"
date: 2023-11-10
sub_topic: Generative 3D Representations
license: CC0 (arXiv abstract)
methods: [RadianceField, Diffusion, ScoreDistillation, TextTo3D, TransformerArchitecture, LargeReconstructionModel]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Instant3D: Fast Text-to-3D with Sparse-View Generation and Large Reconstruction Model

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Text-to-3D with diffusion models has achieved remarkable progress in recent years. However, existing methods either rely on score distillation-based optimization which suffer from slow inference, low diversity and Janus problems, or are feed-forward methods that generate low-quality results due to the scarcity of 3D training data. In this paper, we propose Instant3D, a novel method that generates high-quality and diverse 3D assets from text prompts in a feed-forward manner. We adopt a two-stage paradigm, which first generates a sparse set of four structured and consistent views from text in one shot with a fine-tuned 2D text-to-image diffusion model, and then directly regresses the NeRF from the generated images with a novel transformer-based sparse-view reconstructor. Through extensive experiments, we demonstrate that our method can generate diverse 3D assets of high visual quality within 20 seconds, which is two orders of magnitude faster than previous optimization-based methods that can take 1 to 10 hours. Our project webpage: https://jiahao.ai/instant3d/.
