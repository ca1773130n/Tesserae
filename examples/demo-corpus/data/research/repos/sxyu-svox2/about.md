---
type: Repository
repo: sxyu/svox2
canonical_paper: arxiv-2112-05131
---

# About sxyu/svox2

The reference implementation of *Plenoxels: Radiance Fields without Neural Networks* (Yu et al., 2021), distributed as the `svox2` library. See [the paper page](../../papers/arxiv-2112-05131/paper.md) for context.

Plenoxels replaces the **NeRF MLP** with a sparse **voxel grid** of spherical-harmonics coefficients optimized directly by gradient descent, achieving NeRF-quality novel view synthesis at a fraction of the training cost. The repo provides CUDA kernels for **trilinear interpolation**, **total variation regularization**, and the unbounded-scene voxel layout. Mirrored here under BSD-2-Clause — README only.
