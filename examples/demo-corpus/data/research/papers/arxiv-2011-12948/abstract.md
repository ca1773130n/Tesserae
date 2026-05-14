---
type: Paper
arxiv: "2011.12948"
arxiv_url: https://arxiv.org/abs/2011.12948
title: "Nerfies: Deformable Neural Radiance Fields"
authors:
  - "Keunhong Park"
  - "Utkarsh Sinha"
  - "Jonathan T. Barron"
  - "Sofien Bouaziz"
  - "Dan B Goldman"
  - "Steven M. Seitz"
  - "Ricardo Martin-Brualla"
date: 2020-11-25
sub_topic: Dynamic and 4D Reconstruction
license: CC0 (arXiv abstract)
methods: [RadianceField, DeformationField]
datasets: []
metrics: [PSNR, SSIM, LPIPS]
---

# Nerfies: Deformable Neural Radiance Fields

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We present the first method capable of photorealistically reconstructing deformable scenes using photos/videos captured casually from mobile phones. Our approach augments neural radiance fields (NeRF) by optimizing an additional continuous volumetric deformation field that warps each observed point into a canonical 5D NeRF. We observe that these NeRF-like deformation fields are prone to local minima, and propose a coarse-to-fine optimization method for coordinate-based models that allows for more robust optimization. By adapting principles from geometry processing and physical simulation to NeRF-like models, we propose an elastic regularization of the deformation field that further improves robustness. We show that our method can turn casually captured selfie photos/videos into deformable NeRF models that allow for photorealistic renderings of the subject from arbitrary viewpoints, which we dub "nerfies." We evaluate our method by collecting time-synchronized data using a rig with two mobile phones, yielding train/validation images of the same pose at different viewpoints. We show that our method faithfully reconstructs non-rigidly deforming scenes and reproduces unseen views with high fidelity.
