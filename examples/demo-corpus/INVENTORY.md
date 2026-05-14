# LLM-Wiki Demo Corpus — Inventory

A human-readable index of the curated 3D-reconstruction corpus that powers the LLM-Wiki GitHub Pages demo. See `LICENSES.md` for provenance and `README.md` for context.

## At a glance

- **50 arXiv paper abstracts** (verbatim CC0), grouped into 7 sub-topics below.
- **12 OSS repos** queued for Phase 3 README mirroring (listed at the bottom).
- **Date range:** 2016–2024, with the bulk of activity in 2021–2024 to feed a continuous timeline view.

## Sub-topic distribution

| Sub-topic | Count |
|---|---|
| 3D Gaussian Splatting | 12 |
| Neural Radiance Fields | 10 |
| Visual SLAM and MVS | 8 |
| Diffusion-based 3D Generation | 6 |
| Mesh and Surface Reconstruction | 5 |
| Dynamic and 4D Reconstruction | 5 |
| Generative 3D Representations | 4 |
| **Total** | **50** |

## Papers by sub-topic

### 3D Gaussian Splatting (12)

| arXiv id | Title | First author | Year | OSS bridge |
|---|---|---|---|---|
| [2308.04079](./data/research/papers/arxiv-2308-04079/abstract.md) | 3D Gaussian Splatting for Real-Time Radiance Field Rendering | Kerbl | 2023 | [`graphdeco-inria/gaussian-splatting`](https://github.com/graphdeco-inria/gaussian-splatting) |
| [2311.12775](./data/research/papers/arxiv-2311-12775/abstract.md) | SuGaR: Surface-Aligned Gaussian Splatting for Efficient 3D Mesh Reconstructio... | Guédon | 2023 | [`Anttwo/SuGaR`](https://github.com/Anttwo/SuGaR) |
| [2311.13384](./data/research/papers/arxiv-2311-13384/abstract.md) | LucidDreamer: Domain-free Generation of 3D Gaussian Splatting Scenes | Chung | 2023 | — |
| [2312.00109](./data/research/papers/arxiv-2312-00109/abstract.md) | Scaffold-GS: Structured 3D Gaussians for View-Adaptive Rendering | Lu | 2023 | — |
| [2312.02121](./data/research/papers/arxiv-2312-02121/abstract.md) | Mathematical Supplement for the $\texttt{gsplat}$ Library | Ye | 2023 | [`nerfstudio-project/gsplat`](https://github.com/nerfstudio-project/gsplat) |
| [2312.03203](./data/research/papers/arxiv-2312-03203/abstract.md) | Feature 3DGS: Supercharging 3D Gaussian Splatting to Enable Distilled Feature... | Zhou | 2023 | — |
| [2401.04099](./data/research/papers/arxiv-2401-04099/abstract.md) | AGG: Amortized Generative 3D Gaussians for Single Image to 3D | Xu | 2024 | — |
| [2402.07207](./data/research/papers/arxiv-2402-07207/abstract.md) | GALA3D: Towards Text-to-3D Complex Scene Generation via Layout-guided Generat... | Zhou | 2024 | — |
| [2403.14627](./data/research/papers/arxiv-2403-14627/abstract.md) | MVSplat: Efficient 3D Gaussian Splatting from Sparse Multi-View Images | Chen | 2024 | — |
| [2403.16292](./data/research/papers/arxiv-2403-16292/abstract.md) | latentSplat: Autoencoding Variational Gaussians for Fast Generalizable 3D Rec... | Wewer | 2024 | — |
| [2403.17888](./data/research/papers/arxiv-2403-17888/abstract.md) | 2D Gaussian Splatting for Geometrically Accurate Radiance Fields | Huang | 2024 | [`hbb1/2d-gaussian-splatting`](https://github.com/hbb1/2d-gaussian-splatting) |
| [2404.06109](./data/research/papers/arxiv-2404-06109/abstract.md) | Revising Densification in Gaussian Splatting | Bulò | 2024 | — |

### Neural Radiance Fields (10)

| arXiv id | Title | First author | Year | OSS bridge |
|---|---|---|---|---|
| [2003.08934](./data/research/papers/arxiv-2003-08934/abstract.md) | NeRF: Representing Scenes as Neural Radiance Fields for View Synthesis | Mildenhall | 2020 | [`bmild/nerf`](https://github.com/bmild/nerf) |
| [2008.02268](./data/research/papers/arxiv-2008-02268/abstract.md) | NeRF in the Wild: Neural Radiance Fields for Unconstrained Photo Collections | Martin-Brualla | 2020 | — |
| [2103.13415](./data/research/papers/arxiv-2103-13415/abstract.md) | Mip-NeRF: A Multiscale Representation for Anti-Aliasing Neural Radiance Fields | Barron | 2021 | [`google/mipnerf`](https://github.com/google/mipnerf) |
| [2103.14024](./data/research/papers/arxiv-2103-14024/abstract.md) | PlenOctrees for Real-time Rendering of Neural Radiance Fields | Yu | 2021 | — |
| [2104.06405](./data/research/papers/arxiv-2104-06405/abstract.md) | BARF: Bundle-Adjusting Neural Radiance Fields | Lin | 2021 | — |
| [2111.12077](./data/research/papers/arxiv-2111-12077/abstract.md) | Mip-NeRF 360: Unbounded Anti-Aliased Neural Radiance Fields | Barron | 2021 | — |
| [2112.05131](./data/research/papers/arxiv-2112-05131/abstract.md) | Plenoxels: Radiance Fields without Neural Networks | Yu | 2021 | [`sxyu/svox2`](https://github.com/sxyu/svox2) |
| [2201.05989](./data/research/papers/arxiv-2201-05989/abstract.md) | Instant Neural Graphics Primitives with a Multiresolution Hash Encoding | Müller | 2022 | [`NVlabs/instant-ngp`](https://github.com/NVlabs/instant-ngp) |
| [2301.10241](./data/research/papers/arxiv-2301-10241/abstract.md) | K-Planes: Explicit Radiance Fields in Space, Time, and Appearance | Fridovich-Keil | 2023 | [`sarafridov/K-Planes`](https://github.com/sarafridov/K-Planes) |
| [2302.12249](./data/research/papers/arxiv-2302-12249/abstract.md) | MERF: Memory-Efficient Radiance Fields for Real-time View Synthesis in Unboun... | Reiser | 2023 | — |

### Visual SLAM and MVS (8)

| arXiv id | Title | First author | Year | OSS bridge |
|---|---|---|---|---|
| [1607.02565](./data/research/papers/arxiv-1607-02565/abstract.md) | Direct Sparse Odometry | Engel | 2016 | — |
| [1812.04605](./data/research/papers/arxiv-1812-04605/abstract.md) | DeepV2D: Video to Depth with Differentiable Structure from Motion | Teed | 2018 | — |
| [2108.10869](./data/research/papers/arxiv-2108-10869/abstract.md) | DROID-SLAM: Deep Visual SLAM for Monocular, Stereo, and RGB-D Cameras | Teed | 2021 | [`princeton-vl/DROID-SLAM`](https://github.com/princeton-vl/DROID-SLAM) |
| [2112.12130](./data/research/papers/arxiv-2112-12130/abstract.md) | NICE-SLAM: Neural Implicit Scalable Encoding for SLAM | Zhu | 2021 | — |
| [2304.04278](./data/research/papers/arxiv-2304-04278/abstract.md) | Point-SLAM: Dense Neural Point Cloud-based SLAM | Sandström | 2023 | — |
| [2304.14377](./data/research/papers/arxiv-2304-14377/abstract.md) | Co-SLAM: Joint Coordinate and Sparse Parametric Encodings for Neural Real-Tim... | Wang | 2023 | — |
| [2311.11700](./data/research/papers/arxiv-2311-11700/abstract.md) | GS-SLAM: Dense Visual SLAM with 3D Gaussian Splatting | Yan | 2023 | — |
| [2312.06741](./data/research/papers/arxiv-2312-06741/abstract.md) | Gaussian Splatting SLAM | Matsuki | 2023 | [`muskie82/MonoGS`](https://github.com/muskie82/MonoGS) |

### Diffusion-based 3D Generation (6)

| arXiv id | Title | First author | Year | OSS bridge |
|---|---|---|---|---|
| [2209.14988](./data/research/papers/arxiv-2209-14988/abstract.md) | DreamFusion: Text-to-3D using 2D Diffusion | Poole | 2022 | [`ashawkey/stable-dreamfusion`](https://github.com/ashawkey/stable-dreamfusion) |
| [2211.10440](./data/research/papers/arxiv-2211-10440/abstract.md) | Magic3D: High-Resolution Text-to-3D Content Creation | Lin | 2022 | — |
| [2303.11328](./data/research/papers/arxiv-2303-11328/abstract.md) | Zero-1-to-3: Zero-shot One Image to 3D Object | Liu | 2023 | [`cvlab-columbia/zero123`](https://github.com/cvlab-columbia/zero123) |
| [2305.16213](./data/research/papers/arxiv-2305-16213/abstract.md) | ProlificDreamer: High-Fidelity and Diverse Text-to-3D Generation with Variati... | Wang | 2023 | — |
| [2306.17843](./data/research/papers/arxiv-2306-17843/abstract.md) | Magic123: One Image to High-Quality 3D Object Generation Using Both 2D and 3D... | Qian | 2023 | — |
| [2309.16653](./data/research/papers/arxiv-2309-16653/abstract.md) | DreamGaussian: Generative Gaussian Splatting for Efficient 3D Content Creation | Tang | 2023 | [`dreamgaussian/dreamgaussian`](https://github.com/dreamgaussian/dreamgaussian) |

### Mesh and Surface Reconstruction (5)

| arXiv id | Title | First author | Year | OSS bridge |
|---|---|---|---|---|
| [2002.10099](./data/research/papers/arxiv-2002-10099/abstract.md) | Implicit Geometric Regularization for Learning Shapes | Gropp | 2020 | — |
| [2104.10078](./data/research/papers/arxiv-2104-10078/abstract.md) | UNISURF: Unifying Neural Implicit Surfaces and Radiance Fields for Multi-View... | Oechsle | 2021 | — |
| [2106.10689](./data/research/papers/arxiv-2106-10689/abstract.md) | NeuS: Learning Neural Implicit Surfaces by Volume Rendering for Multi-view Re... | Wang | 2021 | [`Totoro97/NeuS`](https://github.com/Totoro97/NeuS) |
| [2106.12052](./data/research/papers/arxiv-2106-12052/abstract.md) | Volume Rendering of Neural Implicit Surfaces | Yariv | 2021 | — |
| [2206.00665](./data/research/papers/arxiv-2206-00665/abstract.md) | MonoSDF: Exploring Monocular Geometric Cues for Neural Implicit Surface Recon... | Yu | 2022 | — |

### Dynamic and 4D Reconstruction (5)

| arXiv id | Title | First author | Year | OSS bridge |
|---|---|---|---|---|
| [2011.12948](./data/research/papers/arxiv-2011-12948/abstract.md) | Nerfies: Deformable Neural Radiance Fields | Park | 2020 | — |
| [2105.06468](./data/research/papers/arxiv-2105-06468/abstract.md) | Dynamic View Synthesis from Dynamic Monocular Video | Gao | 2021 | — |
| [2106.13228](./data/research/papers/arxiv-2106-13228/abstract.md) | HyperNeRF: A Higher-Dimensional Representation for Topologically Varying Neur... | Park | 2021 | — |
| [2310.08528](./data/research/papers/arxiv-2310-08528/abstract.md) | 4D Gaussian Splatting for Real-Time Dynamic Scene Rendering | Wu | 2023 | [`hustvl/4DGaussians`](https://github.com/hustvl/4DGaussians) |
| [2402.03307](./data/research/papers/arxiv-2402-03307/abstract.md) | 4D-Rotor Gaussian Splatting: Towards Efficient Novel View Synthesis for Dynam... | Duan | 2024 | — |

### Generative 3D Representations (4)

| arXiv id | Title | First author | Year | OSS bridge |
|---|---|---|---|---|
| [2311.04400](./data/research/papers/arxiv-2311-04400/abstract.md) | LRM: Large Reconstruction Model for Single Image to 3D | Hong | 2023 | — |
| [2311.06214](./data/research/papers/arxiv-2311-06214/abstract.md) | Instant3D: Fast Text-to-3D with Sparse-View Generation and Large Reconstructi... | Li | 2023 | — |
| [2402.05054](./data/research/papers/arxiv-2402-05054/abstract.md) | LGM: Large Multi-View Gaussian Model for High-Resolution 3D Content Creation | Tang | 2024 | [`3DTopia/LGM`](https://github.com/3DTopia/LGM) |
| [2403.02151](./data/research/papers/arxiv-2403-02151/abstract.md) | TripoSR: Fast 3D Object Reconstruction from a Single Image | Tochilkin | 2024 | [`VAST-AI-Research/TripoSR`](https://github.com/VAST-AI-Research/TripoSR) |

## Phase 3 OSS bridges (12 repos to mirror)

These repos will get a `data/research/repos/<org>-<name>/readme.md` mirror in Phase 3, plus a one-line `about.md` cross-reference back to their canonical paper. Listed here so the corpus index is complete from Phase 1 onward.

| Repo | License | Canonical paper |
|---|---|---|
| `graphdeco-inria/gaussian-splatting` | Gaussian-Splatting License (non-commercial) | [2308.04079](./data/research/papers/arxiv-2308-04079/abstract.md) — 3D Gaussian Splatting for Real-Time Radiance Field Rendering |
| `nerfstudio-project/gsplat` | Apache-2.0 | [2312.02121](./data/research/papers/arxiv-2312-02121/abstract.md) — Mathematical Supplement for the $\texttt{gsplat}$ Library |
| `bmild/nerf` | MIT | [2003.08934](./data/research/papers/arxiv-2003-08934/abstract.md) — NeRF: Representing Scenes as Neural Radiance Fields for V... |
| `NVlabs/instant-ngp` | NVIDIA Source Code License-NC | [2201.05989](./data/research/papers/arxiv-2201-05989/abstract.md) — Instant Neural Graphics Primitives with a Multiresolution... |
| `sxyu/svox2` | BSD-2-Clause | [2112.05131](./data/research/papers/arxiv-2112-05131/abstract.md) — Plenoxels: Radiance Fields without Neural Networks |
| `princeton-vl/DROID-SLAM` | BSD-3-Clause | [2108.10869](./data/research/papers/arxiv-2108-10869/abstract.md) — DROID-SLAM: Deep Visual SLAM for Monocular, Stereo, and R... |
| `Totoro97/NeuS` | MIT | [2106.10689](./data/research/papers/arxiv-2106-10689/abstract.md) — NeuS: Learning Neural Implicit Surfaces by Volume Renderi... |
| `cvlab-columbia/zero123` | MIT | [2303.11328](./data/research/papers/arxiv-2303-11328/abstract.md) — Zero-1-to-3: Zero-shot One Image to 3D Object |
| `ashawkey/stable-dreamfusion` | Apache-2.0 | [2209.14988](./data/research/papers/arxiv-2209-14988/abstract.md) — DreamFusion: Text-to-3D using 2D Diffusion |
| `dreamgaussian/dreamgaussian` | MIT | [2309.16653](./data/research/papers/arxiv-2309-16653/abstract.md) — DreamGaussian: Generative Gaussian Splatting for Efficien... |
| `VAST-AI-Research/TripoSR` | MIT | [2403.02151](./data/research/papers/arxiv-2403-02151/abstract.md) — TripoSR: Fast 3D Object Reconstruction from a Single Image |
| `hustvl/4DGaussians` | MIT | [2310.08528](./data/research/papers/arxiv-2310-08528/abstract.md) — 4D Gaussian Splatting for Real-Time Dynamic Scene Rendering |

## File layout

```
examples/demo-corpus/
├── README.md           # Corpus framing (what's real, what's synthetic)
├── LICENSES.md         # Provenance ledger (every external source)
├── INVENTORY.md        # This file
└── data/
    └── research/
        └── papers/
            └── arxiv-YYYY-NNNNN/
                └── abstract.md   # YAML frontmatter + CC0 abstract
```

Phases 2–5 will add: paper bodies (`paper.md`), repo READMEs (`data/research/repos/`), daily digests (`data/research/daily/`), weekly syntheses (`data/research/weekly/`), open questions (`data/research/questions/`), and agent session transcripts (`.agent-sessions/`).
