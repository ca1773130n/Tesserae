# R4: passage-node PPR on the bridge benchmark (negative)

2026-11-05. Retrieval only: no LLM calls and no compile.

## Question

Roadmap item R4 asked whether three changes together beat a dense second hop on
lexically-disjoint bridge items:

- document nodes (`SOURCE_DOCUMENT`, `PAPER`, `SOURCE_FILE`) taking part in the
  Personalized PageRank walk;
- node→document edges weighted by `node_mentions` density, replacing the flat
  0.25 `mentioned_in` downweight;
- dense-similarity document seeds.

The ship rule was set before the run. A product change ships only if an arm beats
`dense_2hop` at p<0.05 on **both** corpora, on both bridge-doc@5 and answer-doc
R@10.

## Corpora

The brief named the 148-paper and 1,414-document graphs from 2026-08/09. Neither
exists on disk any more. The run used two compiled HypePaper expert graphs
instead, copied read-only from `~/HypePaperData/tesserae-experts/`:

| Corpus | Docs | Nodes | Edges | Items | Kept (lexically disjoint) |
|---|---|---|---|---|---|
| generative-ai | 410 | 28,044 | 91,682 | 400 | 248 |
| 3d-reconstruction | 419 | 28,049 | 95,189 | 400 | 235 |

Both are paper corpora of about the same size. The pair is therefore narrower
than the brief's (papers vs. a mixed 1,414-document set), and the result does not
cover a heterogeneous corpus.

## Arms

| Arm | What it is |
|---|---|
| `dense_2hop` | baseline: bge chunk retrieval, then a second dense hop |
| `tess_hybrid` | what ships today: `hybrid_search` node scores summed per document |
| `ppr_flat` | PPR on the shipped graph (flat `mentioned_in` weight) + dense doc seeds |
| `ppr_density` | the R4 arm: density-weighted node→doc edges + dense doc seeds |
| `ppr_density_nodes` | `ppr_density` without the dense doc seeds |
| `ppr_density_hr2` | `ppr_density` with doc-seed mass cut from 0.5 to 0.05 |

## Results: kept stratum

Paired permutation test, two-sided. `d` is the mean difference against the
baseline named in the heading.

### Against `dense_2hop` (the ship rule)

| Arm | genai bridge-doc@5 | genai answer R@10 | 3d bridge-doc@5 | 3d answer R@10 |
|---|---|---|---|---|
| `ppr_density` (R4 as specified) | −0.008, p=0.63 | −0.040, p=0.0001 | −0.009, p=0.50 | −0.020, p=0.0002 |
| `ppr_flat` | −0.008, p=0.63 | −0.038, p=0.0005 | −0.009, p=0.50 | −0.018, p=0.0016 |
| `ppr_density_nodes` | +0.081, p=0.0002 | +0.087, p=0.0001 | +0.030, **p=0.065** | +0.050, p<0.0001 |
| `ppr_density_hr2` | +0.073, p=0.0003 | +0.071, p=0.0022 | +0.025, **p=0.11** | +0.023, p=0.049 |
| `tess_hybrid` (shipped) | +0.044, p=0.0074 | +0.077, p=0.0004 | +0.025, p=0.070 | +0.038, p=0.0028 |

No arm clears all four cells. With four arms, two metrics and two corpora, a
multiple-comparison correction would leave the two marginal arms further from the
bar.

### Against `tess_hybrid` (what already ships)

| Arm | genai bridge-doc@5 | genai answer R@10 | 3d bridge-doc@5 | 3d answer R@10 |
|---|---|---|---|---|
| `ppr_density_nodes` | +0.036, p=0.080 | +0.009, p=0.61 | +0.004, p=1.0 | +0.012, p=0.25 |
| `ppr_density_hr2` | +0.028, p=0.17 | −0.007, p=0.74 | +0.000, p=1.0 | −0.014, p=0.038 |

Passage-node PPR adds nothing measurable over the retrieval that already ships.

The bench does not print this table, because its summary compares only against
`dense_2hop`. It was computed afterwards from each run's `items.jsonl`, using the
bench's own `metrics` and `paired_permutation_p`.

## Reading

1. **R4 as specified loses.** The kept stratum is, by construction, the items
   where BM25 and the dense chunk store both miss at 10. Dense document seeds
   anchor the walk on documents that are wrong for those items, so `ppr_density`
   and `ppr_flat` score close to zero there. On the easy stratum they do well
   (genai bridge-doc@5 0.263 vs `dense_2hop` 0.099), but that stratum is not the
   question.
2. **Density weighting itself contributes nothing.** `ppr_density` and `ppr_flat`
   are within 0.012 of each other in every cell of both strata. One confound:
   the 6 (genai) and 15 (3d) synthetic document nodes exist only in the density
   graph, so `ppr_flat` cannot seed or rank them. The comparison is null either
   way.
3. **Without dense seeds, the walk beats `dense_2hop` on one corpus.** It does not
   beat the shipped hybrid retrieval anywhere.
4. Absolute levels are low: the best kept-stratum bridge-doc@5 is 0.093 on
   ~410 documents.

This matches the 2026-09-02 run of this benchmark on the older graphs: graph
walks reach the bridge node, but turning that into a document ranking does not
beat what a hybrid lexical-plus-dense search already returns.

## Decision

No product code change. `evals/bridge/bridge_bench.py` keeps the arms so the
experiment can be rerun when the node→document projection changes.

## Reproduce

```bash
S=~/.blackhole/Tesserae/2026-11-05/scratch-r4-ppr-passage-nodes
uv run --extra semantic --with sentence-transformers \
  python -m evals.bridge.bridge_bench $S/generative-ai $S/out-generative-ai \
  --corpus-dir papers --max-items 400
```

Each corpus directory holds `papers/` plus `.tesserae/graph.json` and
`.tesserae/sqlite.db`, copied from `~/HypePaperData/tesserae-experts/<corpus>/`.
Chunk embedding (bge-base-en-v1.5) takes about 25-50 minutes per corpus on CPU
and is cached in the output directory. The graph arms take 15-30 minutes.

Provenance: the 3d-reconstruction run used commit `71aaa03b`, which calls
`personalized_pagerank` directly. The generative-ai run resumed partway: its
first 20 queries came from a direct-call launch, and the other 380 used
`FrozenPPR`, which caches the transition matrix. `FrozenPPR` was checked to equal
the direct call exactly on its first 3 queries × 4 walks, so the arm definitions
are the same throughout.
