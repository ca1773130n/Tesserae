---
type: Paper
arxiv: "2201.05989"
arxiv_url: https://arxiv.org/abs/2201.05989
title: "Instant Neural Graphics Primitives with a Multiresolution Hash Encoding"
authors:
  - "Thomas Müller"
  - "Alex Evans"
  - "Christoph Schied"
  - "Alexander Keller"
date: 2022-01-16
sub_topic: Neural Radiance Fields
license: CC0 (arXiv abstract)
methods: [HashEncoding]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
oss_repo: NVlabs/instant-ngp
---

# Instant Neural Graphics Primitives with a Multiresolution Hash Encoding

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Neural graphics primitives, parameterized by fully connected neural networks, can be costly to train and evaluate. We reduce this cost with a versatile new input encoding that permits the use of a smaller network without sacrificing quality, thus significantly reducing the number of floating point and memory access operations: a small neural network is augmented by a multiresolution hash table of trainable feature vectors whose values are optimized through stochastic gradient descent. The multiresolution structure allows the network to disambiguate hash collisions, making for a simple architecture that is trivial to parallelize on modern GPUs. We leverage this parallelism by implementing the whole system using fully-fused CUDA kernels with a focus on minimizing wasted bandwidth and compute operations. We achieve a combined speedup of several orders of magnitude, enabling training of high-quality neural graphics primitives in a matter of seconds, and rendering in tens of milliseconds at a resolution of ${1920\!\times\!1080}$.
