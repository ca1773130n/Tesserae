# Does the knowledge graph get smarter as documents accumulate?

Generated 2026-08-01 by `evals/growth/run.py`. Corpus: `examples/demo-corpus/data/research/papers`, compiled in cumulative chronological slices.

> **These numbers predate two changes, both from 2026-08-02.** Regenerate this
> file before quoting the curve.
>
> 1. *The anchor matcher.* They were produced by the substring-only
>    `resolve_anchor`. Re-measured against the same frozen N=50 graph, the
>    label-subset layer takes the last row to **15/15 with controls still at 0**,
>    and every other question keeps its hop count and its path. The earlier
>    slices have not been re-measured — each needs its own compile, ~75 minutes
>    for a full run.
> 2. *The corpus.* Slicing covered 50 papers; `corpus_docs()` now stages all 73
>    units, adding 12 repos that interleave across the paper era plus 6 digests,
>    2 syntheses and 3 questions. Every row below is therefore a different
>    experiment from the one the next run will produce — `documents`, not
>    `papers`.
>
> Two caveats that outlive the rerun, both from
> `evals/growth/probe_anchors.py`:
>
> - The fifteenth question is one label deep. It is carried entirely by
>   `EvidenceSpan: "Evidence: training/rendering speed numbers"`; reword that one
>   LLM-minted span and the score is 14/15 again.
> - A known false positive comes with it: `Direct Sparse Odometry` now reaches
>   `hash encoding` in 3 hops via field membership. It is the second control's
>   construction with a different partner, and it fires. `controls fired: 0`
>   above means the two shipped controls stayed silent, not that no spurious
>   path exists.

## Growth curve

| papers | through | nodes | edges | edges/node | answerable | controls fired | connected early |
|---|---|---|---|---|---|---|
| 8 | 2021-03-25 | 416 | 1026 | 2.47 | **0/15** | 0 |
| 16 | 2021-11-23 | 808 | 2063 | 2.55 | **3/15** | 0 |
| 24 | 2023-02-23 | 1183 | 3063 | 2.59 | **7/15** | 0 |
| 32 | 2023-10-12 | 1614 | 4147 | 2.57 | **9/15** | 0 |
| 40 | 2023-12-06 | 1970 | 5078 | 2.58 | **12/15** | 0 |
| 50 | 2024-04-09 | 2387 | 6169 | 2.58 | **14/15** | 0 |

`controls fired` must stay 0. The controls ask questions this corpus cannot answer; if a path ever appears between their anchors, the checker is finding spurious connections and every number in this table is suspect.

## When each question became answerable

| question | first answerable at | unlocked by |
|---|---|---|
| gs-slam | N=40 (2023-12-06) | 2308.04079, 2311.11700 |
| gs-dynamic | N=32 (2023-10-12) | 2308.04079, 2310.08528 |
| gs-surface | N=40 (2023-12-06) | 2308.04079, 2311.12775 |
| nerf-antialias | N=16 (2021-11-23) | 2103.13415, 2111.12077 |
| nerf-explicit | N=24 (2023-02-23) | 2112.05131, 2103.14024 |
| diffusion-to-3d | N=24 (2023-02-23) | 2209.14988, 2211.10440 |
| sds-refinement | N=32 (2023-10-12) | 2209.14988, 2305.16213 |
| implicit-surface | N=16 (2021-11-23) | 2106.10689, 2106.12052 |
| monocular-cues | N=24 (2023-02-23) | 2106.10689, 2206.00665 |
| hash-encoding | never | 2201.05989, 2003.08934 |
| deformable-nerf | N=16 (2021-11-23) | 2011.12948, 2106.13228 |
| slam-neural-implicit | N=24 (2023-02-23) | 2108.10869, 2112.12130 |
| single-image-3d | N=40 (2023-12-06) | 2303.11328, 2311.04400 |
| gs-densification | N=50 (2024-04-09) | 2308.04079, 2404.06109 |
| sparse-view-gs | N=50 (2024-04-09) | 2308.04079, 2403.14627 |

## Reading this honestly

`answerable` means the graph contains a path of at most 3 hops between the question's two anchor concepts, where both anchors resolve to nodes grounded in documents actually present in the slice, and every document the question requires is present.

It does **not** mean an agent produced a correct answer. It means the graph holds the connection an answer would have to traverse — a precondition, not a demonstration. Grounding is checked separately because the extractor mints nodes for papers cited in related-work sections: a 7-paper graph already contains a `Paper` node for Mip-NeRF 360 with no content behind it, and counting those would make this curve rise for free.

## Per-slice detail

### N=8 — through 2021-03-25 (416 nodes, 1026 edges, 718s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 0/16 | no | no | — |
| gs-dynamic | 0/1 | no | no | — |
| gs-surface | 0/4 | no | no | — |
| nerf-antialias | 4/3 | no | yes | 1 |
| nerf-explicit | 30/0 | no | no | — |
| diffusion-to-3d | 0/0 | no | no | — |
| sds-refinement | 0/0 | no | no | — |
| implicit-surface | 1/6 | no | no | — |
| monocular-cues | 0/0 | no | no | — |
| hash-encoding | 1/0 | no | no | — |
| deformable-nerf | 8/0 | no | no | — |
| slam-neural-implicit | 0/0 | no | no | — |
| single-image-3d | 0/0 | no | no | — |
| gs-densification | 0/0 | no | no | — |
| sparse-view-gs | 0/0 | no | no | — |
| control-absent-topic *(control)* | 0/30 | yes | no | — |
| control-unrelated-pair *(control)* | 4/0 | yes | no | — |

### N=16 — through 2021-11-23 (808 nodes, 2063 edges, 735s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 2/55 | no | no | — |
| gs-dynamic | 2/9 | no | no | — |
| gs-surface | 2/30 | no | no | — |
| nerf-antialias | 5/11 | yes | yes | 1 |
| nerf-explicit | 57/0 | no | no | — |
| diffusion-to-3d | 0/0 | no | no | — |
| sds-refinement | 0/0 | no | no | — |
| implicit-surface | 9/31 | yes | yes | 0 |
| monocular-cues | 0/9 | no | no | — |
| hash-encoding | 1/0 | no | no | — |
| deformable-nerf | 22/3 | yes | yes | 0 |
| slam-neural-implicit | 0/12 | no | no | — |
| single-image-3d | 0/0 | no | no | — |
| gs-densification | 0/2 | no | no | — |
| sparse-view-gs | 0/2 | no | no | — |
| control-absent-topic *(control)* | 0/57 | yes | no | — |
| control-unrelated-pair *(control)* | 5/0 | yes | no | — |

### N=24 — through 2023-02-23 (1183 nodes, 3063 edges, 724s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 3/95 | no | yes | 3 |
| gs-dynamic | 3/11 | no | yes | 3 |
| gs-surface | 3/38 | no | yes | 3 |
| nerf-antialias | 5/20 | yes | yes | 1 |
| nerf-explicit | 103/4 | yes | yes | 0 |
| diffusion-to-3d | 3/22 | yes | yes | 0 |
| sds-refinement | 4/3 | no | yes | 0 |
| implicit-surface | 12/36 | yes | yes | 0 |
| monocular-cues | 5/24 | yes | yes | 1 |
| hash-encoding | 10/0 | yes | no | — |
| deformable-nerf | 22/3 | yes | yes | 0 |
| slam-neural-implicit | 2/38 | yes | yes | 0 |
| single-image-3d | 0/0 | no | no | — |
| gs-densification | 0/3 | no | no | — |
| sparse-view-gs | 1/3 | no | no | — |
| control-absent-topic *(control)* | 0/103 | yes | no | — |
| control-unrelated-pair *(control)* | 5/22 | yes | no | — |

### N=32 — through 2023-10-12 (1614 nodes, 4147 edges, 831s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 21/154 | no | yes | 1 |
| gs-dynamic | 21/22 | yes | yes | 0 |
| gs-surface | 21/42 | no | yes | 2 |
| nerf-antialias | 5/24 | yes | yes | 1 |
| nerf-explicit | 128/6 | yes | yes | 0 |
| diffusion-to-3d | 19/36 | yes | yes | 0 |
| sds-refinement | 15/7 | yes | yes | 0 |
| implicit-surface | 12/36 | yes | yes | 0 |
| monocular-cues | 5/24 | yes | yes | 1 |
| hash-encoding | 10/0 | yes | no | — |
| deformable-nerf | 24/3 | yes | yes | 0 |
| slam-neural-implicit | 2/38 | yes | yes | 0 |
| single-image-3d | 2/8 | no | yes | 3 |
| gs-densification | 10/21 | no | yes | 1 |
| sparse-view-gs | 1/21 | no | yes | 3 |
| control-absent-topic *(control)* | 0/128 | yes | no | — |
| control-unrelated-pair *(control)* | 5/36 | yes | no | — |

### N=40 — through 2023-12-06 (1970 nodes, 5078 edges, 698s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 46/187 | yes | yes | 0 |
| gs-dynamic | 46/22 | yes | yes | 0 |
| gs-surface | 46/44 | yes | yes | 1 |
| nerf-antialias | 5/24 | yes | yes | 1 |
| nerf-explicit | 144/6 | yes | yes | 0 |
| diffusion-to-3d | 21/44 | yes | yes | 0 |
| sds-refinement | 18/7 | yes | yes | 0 |
| implicit-surface | 13/37 | yes | yes | 0 |
| monocular-cues | 5/25 | yes | yes | 1 |
| hash-encoding | 10/2 | yes | no | — |
| deformable-nerf | 24/3 | yes | yes | 0 |
| slam-neural-implicit | 5/42 | yes | yes | 0 |
| single-image-3d | 7/25 | yes | yes | 1 |
| gs-densification | 10/46 | no | yes | 1 |
| sparse-view-gs | 2/46 | no | yes | 2 |
| control-absent-topic *(control)* | 0/144 | yes | no | — |
| control-unrelated-pair *(control)* | 5/44 | yes | no | — |

### N=50 — through 2024-04-09 (2387 nodes, 6169 edges, 847s)

| question | anchors resolved | sources present | connected | hops |
|---|---|---|---|---|
| gs-slam | 64/208 | yes | yes | 0 |
| gs-dynamic | 64/29 | yes | yes | 0 |
| gs-surface | 64/45 | yes | yes | 1 |
| nerf-antialias | 5/24 | yes | yes | 1 |
| nerf-explicit | 153/6 | yes | yes | 0 |
| diffusion-to-3d | 23/50 | yes | yes | 0 |
| sds-refinement | 20/14 | yes | yes | 0 |
| implicit-surface | 13/37 | yes | yes | 0 |
| monocular-cues | 5/28 | yes | yes | 1 |
| hash-encoding | 10/4 | yes | no | — |
| deformable-nerf | 30/4 | yes | yes | 0 |
| slam-neural-implicit | 5/45 | yes | yes | 0 |
| single-image-3d | 16/49 | yes | yes | 0 |
| gs-densification | 22/64 | yes | yes | 0 |
| sparse-view-gs | 2/64 | yes | yes | 2 |
| control-absent-topic *(control)* | 0/153 | yes | no | — |
| control-unrelated-pair *(control)* | 5/50 | yes | no | — |
