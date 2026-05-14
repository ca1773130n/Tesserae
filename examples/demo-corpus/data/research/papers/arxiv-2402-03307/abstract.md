---
type: Paper
arxiv: "2402.03307"
arxiv_url: https://arxiv.org/abs/2402.03307
title: "4D-Rotor Gaussian Splatting: Towards Efficient Novel View Synthesis for Dynamic Scenes"
authors:
  - "Yuanxing Duan"
  - "Fangyin Wei"
  - "Qiyu Dai"
  - "Yuhang He"
  - "Wenzheng Chen"
  - "Baoquan Chen"
date: 2024-02-05
sub_topic: Dynamic and 4D Reconstruction
license: CC0 (arXiv abstract)
methods: [GaussianSplatting, DeformationField, RotorRepresentation, NovelViewSynthesis, RealTimeRendering]
datasets: []
metrics: [FPS]
---

# 4D-Rotor Gaussian Splatting: Towards Efficient Novel View Synthesis for Dynamic Scenes

> Verbatim CC0 abstract mirrored from arXiv. No editorial changes.

We consider the problem of novel-view synthesis (NVS) for dynamic scenes. Recent neural approaches have accomplished exceptional NVS results for static 3D scenes, but extensions to 4D time-varying scenes remain non-trivial. Prior efforts often encode dynamics by learning a canonical space plus implicit or explicit deformation fields, which struggle in challenging scenarios like sudden movements or generating high-fidelity renderings. In this paper, we introduce 4D Gaussian Splatting (4DRotorGS), a novel method that represents dynamic scenes with anisotropic 4D XYZT Gaussians, inspired by the success of 3D Gaussian Splatting in static scenes. We model dynamics at each timestamp by temporally slicing the 4D Gaussians, which naturally compose dynamic 3D Gaussians and can be seamlessly projected into images. As an explicit spatial-temporal representation, 4DRotorGS demonstrates powerful capabilities for modeling complicated dynamics and fine details--especially for scenes with abrupt motions. We further implement our temporal slicing and splatting techniques in a highly optimized CUDA acceleration framework, achieving real-time inference rendering speeds of up to 277 FPS on an RTX 3090 GPU and 583 FPS on an RTX 4090 GPU. Rigorous evaluations on scenes with diverse motions showcase the superior efficiency and effectiveness of 4DRotorGS, which consistently outperforms existing methods both quantitatively and qualitatively.
