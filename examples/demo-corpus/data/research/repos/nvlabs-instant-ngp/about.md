---
type: Repository
repo: NVlabs/instant-ngp
canonical_paper: arxiv-2201-05989
---

# About NVlabs/instant-ngp

NVIDIA's reference implementation of *Instant Neural Graphics Primitives with a Multiresolution Hash Encoding* (Müller et al., 2022). See [the paper page](../../papers/arxiv-2201-05989/paper.md) for context.

Introduces the **multiresolution hash grid encoding** and **tiny-CUDA-NN** fused MLP runtime that together brought NeRF training time from hours to seconds, and the **Instant-NGP** viewer that made interactive radiance-field editing practical. The encoding is reused by **MERF**, **Co-SLAM**, **NICE-SLAM**, and many SLAM/4D follow-ups in the corpus. The upstream license is **NVIDIA non-commercial research only**, so this corpus mirrors only the README and explicitly omits the code.
