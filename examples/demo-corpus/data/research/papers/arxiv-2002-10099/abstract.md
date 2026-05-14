---
type: Paper
arxiv: "2002.10099"
arxiv_url: https://arxiv.org/abs/2002.10099
title: "Implicit Geometric Regularization for Learning Shapes"
authors:
  - "Amos Gropp"
  - "Lior Yariv"
  - "Niv Haim"
  - "Matan Atzmon"
  - "Yaron Lipman"
date: 2020-02-24
sub_topic: Mesh and Surface Reconstruction
license: CC0 (arXiv abstract)
methods: [PointCloud]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Implicit Geometric Regularization for Learning Shapes

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

Representing shapes as level sets of neural networks has been recently proved to be useful for different shape analysis and reconstruction tasks. So far, such representations were computed using either: (i) pre-computed implicit shape representations; or (ii) loss functions explicitly defined over the neural level sets. In this paper we offer a new paradigm for computing high fidelity implicit neural representations directly from raw data (i.e., point clouds, with or without normal information). We observe that a rather simple loss function, encouraging the neural network to vanish on the input point cloud and to have a unit norm gradient, possesses an implicit geometric regularization property that favors smooth and natural zero level set surfaces, avoiding bad zero-loss solutions. We provide a theoretical analysis of this property for the linear case, and show that, in practice, our method leads to state of the art implicit neural representations with higher level-of-details and fidelity compared to previous methods.
