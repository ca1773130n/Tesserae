---
type: Paper
arxiv: "2008.02268"
arxiv_url: https://arxiv.org/abs/2008.02268
title: "NeRF in the Wild: Neural Radiance Fields for Unconstrained Photo Collections"
authors:
  - "Ricardo Martin-Brualla"
  - "Noha Radwan"
  - "Mehdi S. M. Sajjadi"
  - "Jonathan T. Barron"
  - "Alexey Dosovitskiy"
  - "Daniel Duckworth"
date: 2020-08-05
sub_topic: Neural Radiance Fields
license: CC0 (arXiv abstract)
methods: [RadianceField]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# NeRF in the Wild: Neural Radiance Fields for Unconstrained Photo Collections

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We present a learning-based method for synthesizing novel views of complex scenes using only unstructured collections of in-the-wild photographs. We build on Neural Radiance Fields (NeRF), which uses the weights of a multilayer perceptron to model the density and color of a scene as a function of 3D coordinates. While NeRF works well on images of static subjects captured under controlled settings, it is incapable of modeling many ubiquitous, real-world phenomena in uncontrolled images, such as variable illumination or transient occluders. We introduce a series of extensions to NeRF to address these issues, thereby enabling accurate reconstructions from unstructured image collections taken from the internet. We apply our system, dubbed NeRF-W, to internet photo collections of famous landmarks, and demonstrate temporally consistent novel view renderings that are significantly closer to photorealism than the prior state of the art.
