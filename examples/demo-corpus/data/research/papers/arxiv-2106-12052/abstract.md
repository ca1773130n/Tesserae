---
type: Paper
arxiv: "2106.12052"
arxiv_url: https://arxiv.org/abs/2106.12052
title: "Volume Rendering of Neural Implicit Surfaces"
authors:
  - "Lior Yariv"
  - "Jiatao Gu"
  - "Yoni Kasten"
  - "Yaron Lipman"
date: 2021-06-22
sub_topic: Mesh and Surface Reconstruction
license: CC0 (arXiv abstract)
methods: [VolumeRendering, NeuralImplicitSurface]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Volume Rendering of Neural Implicit Surfaces

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Neural volume rendering became increasingly popular recently due to its success in synthesizing novel views of a scene from a sparse set of input images. So far, the geometry learned by neural volume rendering techniques was modeled using a generic density function. Furthermore, the geometry itself was extracted using an arbitrary level set of the density function leading to a noisy, often low fidelity reconstruction. The goal of this paper is to improve geometry representation and reconstruction in neural volume rendering. We achieve that by modeling the volume density as a function of the geometry. This is in contrast to previous work modeling the geometry as a function of the volume density. In more detail, we define the volume density function as Laplace's cumulative distribution function (CDF) applied to a signed distance function (SDF) representation. This simple density representation has three benefits: (i) it provides a useful inductive bias to the geometry learned in the neural volume rendering process; (ii) it facilitates a bound on the opacity approximation error, leading to an accurate sampling of the viewing ray. Accurate sampling is important to provide a precise coupling of geometry and radiance; and (iii) it allows efficient unsupervised disentanglement of shape and appearance in volume rendering. Applying this new density representation to challenging scene multiview datasets produced high quality geometry reconstructions, outperforming relevant baselines. Furthermore, switching shape and appearance between scenes is possible due to the disentanglement of the two.
