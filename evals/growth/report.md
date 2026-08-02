# Does the knowledge graph get smarter as documents accumulate?

Generated 2026-08-02 by `evals/growth/run.py`. Corpus: `examples/demo-corpus/data/research` — papers, repos, digests, syntheses and open questions, compiled in cumulative chronological slices. See `corpus_docs()` for how each kind is dated.

## Growth curve

| documents | through | nodes | edges | edges/node | answerable | controls fired | connected early |
|---|---|---|---|---|---|---|---|
| 12 | 2021-05-13 | 623 | 1539 | 2.47 | **0/15** | 0 | 2 |
| 24 | 2022-01-16 | 1162 | 3027 | 2.60 | **6/15** | 0 | 3 |
| 36 | 2023-06-30 | 1781 | 4698 | 2.64 | **9/15** | 0 | 4 |
| 48 | 2023-11-30 | 2357 | 6196 | 2.63 | **13/15** | 0 | 2 |
| 60 | 2024-03-04 | 2897 | 7617 | 2.63 | **13/15** | 0 | 2 |
| 73 | 2026-05-05 | 3394 | 9106 | 2.68 | **15/15** | 0 | 0 |

`controls fired` must stay 0. The controls ask questions this corpus cannot answer; if a path ever appears between their anchors, the checker is finding spurious connections and every number in this table is suspect.

## When each question became answerable

| question | first answerable at | unlocked by |
|---|---|---|
| gs-slam | N=48 (2023-11-30) | 2308.04079, 2311.11700 |
| gs-dynamic | N=48 (2023-11-30) | 2308.04079, 2310.08528 |
| gs-surface | N=48 (2023-11-30) | 2308.04079, 2311.12775 |
| nerf-antialias | N=24 (2022-01-16) | 2103.13415, 2111.12077 |
| nerf-explicit | N=24 (2022-01-16) | 2112.05131, 2103.14024 |
| diffusion-to-3d | N=36 (2023-06-30) | 2209.14988, 2211.10440 |
| sds-refinement | N=36 (2023-06-30) | 2209.14988, 2305.16213 |
| implicit-surface | N=24 (2022-01-16) | 2106.10689, 2106.12052 |
| monocular-cues | N=36 (2023-06-30) | 2106.10689, 2206.00665 |
| hash-encoding | N=24 (2022-01-16) | 2201.05989, 2003.08934 |
| deformable-nerf | N=24 (2022-01-16) | 2011.12948, 2106.13228 |
| slam-neural-implicit | N=24 (2022-01-16) | 2108.10869, 2112.12130 |
| single-image-3d | N=48 (2023-11-30) | 2303.11328, 2311.04400 |
| gs-densification | N=73 (2026-05-05) | 2308.04079, 2404.06109 |
| sparse-view-gs | N=73 (2026-05-05) | 2308.04079, 2403.14627 |

## Reading this honestly

`answerable` means the graph contains a path of at most 3 hops between the question's two anchor concepts, where both anchors resolve to nodes grounded in documents actually present in the slice, and every document the question requires is present.

It does **not** mean an agent produced a correct answer. It means the graph holds the connection an answer would have to traverse — a precondition, not a demonstration. Grounding is checked separately because the extractor mints nodes for papers cited in related-work sections: a 7-paper graph already contains a `Paper` node for Mip-NeRF 360 with no content behind it, and counting those would make this curve rise for free.

**This table does not validate itself, and `controls fired: 0` is not enough.** Three candidate anchor matchers once reached 15/15 with both controls silent, and so did a null model. Run `evals/growth/probe_anchors.py --work <the same work dir>` and read its output beside this file: it reports the score with the graph removed entirely, how much of the graph each anchor claims, and what fraction of arbitrary anchor pairs connect. A high `answerable` on a dense graph can mean the questions got easier rather than the graph got smarter.

## Per-slice detail

### N=12 — through 2021-05-13 (623 nodes, 1539 edges, 1202s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 0/25 | no | no | — |
| gs-dynamic | 0/6 | no | no | — |
| gs-surface | 0/13 | no | no | — |
| nerf-antialias | 5/2 | no | yes | 1 |
| nerf-explicit | 55/0 | no | no | — |
| diffusion-to-3d | 0/0 | no | no | — |
| sds-refinement | 0/0 | no | no | — |
| implicit-surface | 4/18 | no | yes | 2 |
| monocular-cues | 0/15 | no | no | — |
| hash-encoding | 2/0 | no | no | — |
| deformable-nerf | 13/0 | no | no | — |
| slam-neural-implicit | 2/9 | no | no | — |
| single-image-3d | 0/0 | no | no | — |
| gs-densification | 0/0 | no | no | — |
| sparse-view-gs | 0/0 | no | no | — |
| control-absent-topic *(control)* | 0/55 | yes | no | — |
| control-unrelated-pair *(control)* | 9/0 | yes | no | — |

### N=24 — through 2022-01-16 (1162 nodes, 3027 edges, 1227s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 2/123 | no | yes | 3 |
| gs-dynamic | 2/11 | no | yes | 3 |
| gs-surface | 2/30 | no | yes | 3 |
| nerf-antialias | 6/9 | yes | yes | 1 |
| nerf-explicit | 100/1 | yes | yes | 0 |
| diffusion-to-3d | 0/3 | no | no | — |
| sds-refinement | 0/0 | no | no | — |
| implicit-surface | 16/47 | yes | yes | 0 |
| monocular-cues | 0/22 | no | no | — |
| hash-encoding | 18/1 | yes | yes | 1 |
| deformable-nerf | 24/6 | yes | yes | 0 |
| slam-neural-implicit | 4/28 | yes | yes | 1 |
| single-image-3d | 0/0 | no | no | — |
| gs-densification | 0/2 | no | no | — |
| sparse-view-gs | 2/2 | no | no | — |
| control-absent-topic *(control)* | 0/100 | yes | no | — |
| control-unrelated-pair *(control)* | 11/3 | yes | no | — |

### N=36 — through 2023-06-30 (1781 nodes, 4698 edges, 1152s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 4/184 | no | yes | 3 |
| gs-dynamic | 4/14 | no | yes | 3 |
| gs-surface | 4/37 | no | yes | 3 |
| nerf-antialias | 6/16 | yes | yes | 1 |
| nerf-explicit | 129/1 | yes | yes | 0 |
| diffusion-to-3d | 30/39 | yes | yes | 0 |
| sds-refinement | 19/8 | yes | yes | 0 |
| implicit-surface | 16/49 | yes | yes | 0 |
| monocular-cues | 4/36 | yes | yes | 0 |
| hash-encoding | 18/1 | yes | yes | 1 |
| deformable-nerf | 24/6 | yes | yes | 0 |
| slam-neural-implicit | 6/44 | yes | yes | 1 |
| single-image-3d | 8/4 | no | yes | 2 |
| gs-densification | 0/4 | no | no | — |
| sparse-view-gs | 4/4 | no | no | — |
| control-absent-topic *(control)* | 0/129 | yes | no | — |
| control-unrelated-pair *(control)* | 11/39 | yes | no | — |

### N=48 — through 2023-11-30 (2357 nodes, 6196 edges, 1258s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 45/222 | yes | yes | 0 |
| gs-dynamic | 45/38 | yes | yes | 0 |
| gs-surface | 45/38 | yes | yes | 2 |
| nerf-antialias | 6/20 | yes | yes | 1 |
| nerf-explicit | 159/5 | yes | yes | 0 |
| diffusion-to-3d | 38/56 | yes | yes | 0 |
| sds-refinement | 28/8 | yes | yes | 0 |
| implicit-surface | 18/49 | yes | yes | 0 |
| monocular-cues | 4/38 | yes | yes | 0 |
| hash-encoding | 18/1 | yes | yes | 1 |
| deformable-nerf | 30/6 | yes | yes | 0 |
| slam-neural-implicit | 9/50 | yes | yes | 1 |
| single-image-3d | 19/29 | yes | yes | 0 |
| gs-densification | 12/45 | no | yes | 1 |
| sparse-view-gs | 5/45 | no | yes | 3 |
| control-absent-topic *(control)* | 0/159 | yes | no | — |
| control-unrelated-pair *(control)* | 11/56 | yes | no | — |

### N=60 — through 2024-03-04 (2897 nodes, 7617 edges, 1117s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 76/250 | yes | yes | 0 |
| gs-dynamic | 76/45 | yes | yes | 0 |
| gs-surface | 76/38 | yes | yes | 2 |
| nerf-antialias | 6/20 | yes | yes | 1 |
| nerf-explicit | 177/5 | yes | yes | 0 |
| diffusion-to-3d | 41/62 | yes | yes | 0 |
| sds-refinement | 33/8 | yes | yes | 0 |
| implicit-surface | 18/51 | yes | yes | 0 |
| monocular-cues | 4/38 | yes | yes | 0 |
| hash-encoding | 18/1 | yes | yes | 1 |
| deformable-nerf | 40/11 | yes | yes | 0 |
| slam-neural-implicit | 9/51 | yes | yes | 1 |
| single-image-3d | 45/69 | yes | yes | 0 |
| gs-densification | 15/76 | no | yes | 1 |
| sparse-view-gs | 5/76 | no | yes | 3 |
| control-absent-topic *(control)* | 0/177 | yes | no | — |
| control-unrelated-pair *(control)* | 11/62 | yes | no | — |

### N=73 — through 2026-05-05 (3394 nodes, 9106 edges, 1125s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 92/315 | yes | yes | 0 |
| gs-dynamic | 92/46 | yes | yes | 0 |
| gs-surface | 92/56 | yes | yes | 0 |
| nerf-antialias | 6/23 | yes | yes | 1 |
| nerf-explicit | 198/5 | yes | yes | 0 |
| diffusion-to-3d | 43/60 | yes | yes | 0 |
| sds-refinement | 40/24 | yes | yes | 0 |
| implicit-surface | 22/54 | yes | yes | 0 |
| monocular-cues | 7/47 | yes | yes | 0 |
| hash-encoding | 18/4 | yes | yes | 1 |
| deformable-nerf | 43/10 | yes | yes | 0 |
| slam-neural-implicit | 9/53 | yes | yes | 1 |
| single-image-3d | 47/101 | yes | yes | 0 |
| gs-densification | 39/92 | yes | yes | 1 |
| sparse-view-gs | 6/92 | yes | yes | 2 |
| control-absent-topic *(control)* | 0/198 | yes | no | — |
| control-unrelated-pair *(control)* | 14/60 | yes | no | — |
