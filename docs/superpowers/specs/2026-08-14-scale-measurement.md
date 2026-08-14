# Where the JSON-plus-sidecar model actually breaks

**Date:** 2026-08-14
**Status:** measurement, not a proposal. Nothing here was optimized; findings are reported as found.

## The claim being tested

It has been asserted in this project, repeatedly and without evidence, that storing the
graph as a `graph.json` artifact plus SQLite sidecars "won't match a graph database at
scale". Nobody had measured it. This report replaces that assertion with numbers.

**The short answer: the storage model is not the problem, and the assertion is aimed at the
wrong component.** Serialization, parsing and the sidecar writes are all linear in corpus
size with comfortable constants, and they stay comfortable well past any plausible corpus.
What breaks is *retrieval*, and it breaks because every `hybrid_search` call scores the
entire corpus with no candidate pruning — a design property that a graph database would not
fix, because it is not a storage problem.

On this machine (16 GB, Apple silicon, Python 3.11) the wall is **memory at query time**,
arriving at roughly **500,000 nodes**, and the specific function is
`tesserae/retrieval/hybrid.py::_embedding_scores`.

## Method

### The generator

Measuring against a graph of the right node *count* but the wrong *shape* would answer an
easier question than the real one. Three properties of the live graph dominate cost and a
uniform random graph has none of them:

- **The degree tail.** The busiest node in the live graph, a `CommunitySummary`, touches
  29% of all edge endpoints. PPR and the depth-2 neighbourhood walk are priced by that hub,
  not by the mean degree of 4.44.
- **Per-type field sizes.** `EvidenceSpan` is 25% of nodes at ~450 serialized bytes each;
  `Session` is 0.3% of nodes at ~5.5 KB each. Giving every node the mean size would
  misstate serialization cost.
- **Token vocabulary.** BM25 posting-list length depends on terms recurring across
  documents. Nodes filled with unique random strings give every term a posting list of one.

So `tests/scale_graph.py` replays a profile measured off this project's own 47,132-node
`.tesserae/graph.json` (`tests/fixtures/graph_shape_47k.json`, read-only; the live artifact
was not modified, verified by mtime afterwards). It holds node-type shares, edge-type
shares, the edges-per-node ratio, per-type field-length means, the degree histogram and hub
degree-as-a-share-of-edges constant as the node count grows.

Fidelity at the baseline size, generated vs. live:

| | generated (47,132) | live (47,132) |
|---|---|---|
| edges | 104,675 | 104,677 |
| mean degree | 4.44 | 4.44 |
| p90 / p99 degree | 6 / 27 | 6 / 31 |
| max degree | 30,343 | 30,342 |
| isolated nodes | 4.4% | 3.9% |
| `graph.json` | 51.5 MB | 55.1 MB |

`tests/test_scale_graph.py` pins these properties as a ratchet, at a size that runs in
0.39 s with no LLM, no compile and no disk.

### The sweep

`scripts/scale_measure.py` runs **one subprocess per size**, because peak RSS is a process
high-water mark, so measuring several sizes in one process would report the largest size's
peak for all of them.

Two independent memory guards, because this machine has 16 GB and swapping would not merely
slow the run but invalidate it (the phases would be timing the swap device):

1. the parent projects peak RSS by least-squares fit over the sizes already measured and
   refuses to start one that would exceed 70% of currently-available memory;
2. the child enforces a hard RSS ceiling and abandons the size at a phase boundary if
   crossed, writing out whatever phases did complete.

Caches are **warm**: the BM25 inverted index and the embedding vector cache are built (and
timed separately, as `bm25_warm_build` / `vector_warm_build`) before the query is measured.
The embedding backend is the real default, Model2Vec `potion-base-8M`, 256 dimensions.

One methodological bug is worth recording because it silently produced a much rosier
picture. The first sweep used a fixed natural-language query, which shares no vocabulary
with a synthetic corpus: the BM25 lane scored **0 documents** and the run measured the
empty-postings path rather than retrieval. The query is now built from the corpus's own
frequent terms, and all three lanes score the full candidate set.

## The numbers

All times in seconds, peak RSS in MB, on an otherwise-loaded 16 GB machine. The exponent is
the fitted power of node count across the measured range: 1.0 is linear.

### Time (seconds)

| Phase | 47,132 | 100,000 | 250,000 | exponent |
|---|---|---|---|---|
| `generate` (harness, not product) | 1.390 | 3.084 | 7.628 | 1.02 |
| `ResearchGraph.model_dump` | 0.734 | 1.634 | 4.440 | 1.08 |
| `ResearchGraph.canonicalized` | 0.048 | 0.141 | 0.371 | 1.23 |
| `json.dumps` | 0.521 | 1.122 | 2.782 | 1.00 |
| write `graph.json` | 0.180 | 0.320 | 0.823 | 0.91 |
| read `graph.json` | 0.008 | 0.019 | 0.044 | 1.04 |
| `json.loads` | 0.223 | 0.498 | 1.185 | 1.00 |
| `graph_from_payload` | 0.255 | 0.563 | 1.364 | 1.00 |
| `upsert_many_nodes` | 0.287 | 0.666 | 1.935 | 1.14 |
| `upsert_many_edges` | 1.151 | 2.695 | 8.499 | 1.20 |
| `Bm25Index.prepare` (cold build) | 2.055 | 5.039 | 18.200 | 1.31 |
| `VectorCache.embed` (cold build) | 2.916 | 5.898 | 17.917 | 1.09 |
| **`hybrid_search`, warm, 3 lanes** | **1.125** | **2.440** | **7.420** | **1.13** |
| `personalized_pagerank` | 1.379 | 3.126 | 9.161 | 1.13 |
| **`compile_context`, end to end** | **2.207** | **6.086** | **16.616** | **1.21** |

### Peak RSS (MB)

| Phase | 47,132 | 100,000 | 250,000 |
|---|---|---|---|
| `ResearchGraph.model_dump` | 190 | 376 | 893 |
| `json.dumps` | 467 | 958 | 2,357 |
| `json.loads` | 521 | 1,077 | 2,651 |
| `graph_from_payload` | 564 | 1,173 | 2,900 |
| `upsert_many_edges` | 367 | 746 | 1,829 |
| `Bm25Index.prepare` (cold build) | 598 | 1,205 | 1,444 |
| `VectorCache.embed` (cold build) | 1,152 | 2,283 | 4,115 |
| **`hybrid_search`, warm** | **1,096** | **2,194** | **4,820** |
| `personalized_pagerank` | 727 | 1,410 | 2,846 |
| **`compile_context`** | **1,103** | **2,207** | **3,961** |

At 250,000 nodes `hybrid_search` is the **single largest memory consumer of any phase**,
above even building the vector cache. One query costs more resident memory than
serializing, parsing and storing the entire graph.

### Artifacts on disk

| | 47,132 | 100,000 | 250,000 | 500,000 (proj.) | 1,000,000 (proj.) |
|---|---|---|---|---|---|
| edges | 104,675 | 222,090 | 555,225 | 1.11 M | 2.22 M |
| `graph.json` | 51.5 MB | 110.8 MB | 275.5 MB | ~551 MB | ~1.10 GB |
| `sqlite.db` | 89.9 MB | 196.9 MB | 486.0 MB | ~973 MB | ~1.95 GB |
| peak RSS, worst phase | 1.15 GB | 2.28 GB | 4.82 GB | ~9.3 GB | ~18.2 GB |
| `compile_context` | 2.2 s | 6.1 s | 16.6 s | ~34 s | ~70 s |

### `hybrid_search` lane split (ms, warm)

| Lane | 47,132 | 100,000 | 250,000 |
|---|---|---|---|
| **embedding** | **877.2** | **1,879.0** | **5,376.9** |
| bm25 | 110.7 | 250.8 | 1,154.9 |
| lexical | 41.8 | 80.2 | 193.6 |

Candidates entering the lanes: 47,132 / 100,000 / 250,000. Admitted after candidate
generation: 47,103 / 99,923 / 249,849 — that is, **99.9% of the corpus reaches scoring on
every query, at every size.** The embedding lane is 73–78% of query time throughout.

## The knee

**Component that degrades first:** the embedding lane of `hybrid_search`, specifically

> `tesserae/retrieval/hybrid.py::_embedding_scores`

**At what size:** by 250,000 nodes it is the largest consumer of *any* phase, at 4.82 GB for
a single query. It becomes the binding constraint at roughly **500,000 nodes**, where the
projected ~9.3 GB query-time peak exceeds what a 16 GB workstation can supply alongside
anything else. At **1,000,000 nodes** the projected ~18.2 GB peak exceeds the machine's
entire physical memory, which is why that size was not attempted.

**Bound by:** **memory first, CPU second, IO not at all.** In order:

1. *Memory.* `_embedding_scores` calls `embed_texts(backend, [query, *corpus_texts], cache)`,
   which materializes a vector for **every node in the graph** as a Python `list` of 256
   floats. At Python's per-object overhead that is ~8.2 GB of vectors alone at 1M nodes, per
   query, and it is why `hybrid_search` peaks at 4.82 GB on a 250k-node graph.
2. *CPU.* The dot product beneath it is a pure-Python `sum(a * b for a, b in zip(...))` per
   document, over every node. That is the 877 → 1,879 → 5,377 ms in the lane table, 73–78%
   of total query time at every size.
3. *IO.* Not a factor. Reading a 275 MB `graph.json` took 0.044 s (page-cached) and writing
   it took 0.823 s, against 7.4 s for a single query at the same size. `upsert_many_edges`
   is the one storage phase with real cost (8.5 s at 250k), and it is still the cheaper half
   of a `compile_context` call.

**The load-bearing detail:** nothing here is badly superlinear. Every exponent sits between
0.91 and 1.31. There is no algorithmic cliff — the system walks into a wall at a nearly
constant slope of **17.8 MB of peak RSS per 1,000 nodes**, of which the embedding lane is
the dominant term.

The three exponents meaningfully above 1.0 are `Bm25Index.prepare` (1.31), `compile_context`
(1.21) and `upsert_many_edges` (1.20). `compile_context`'s is the hub's doing: a depth-2
neighbourhood around a node holding 29% of edge endpoints is most of the graph, so the walk
grows faster than the corpus does.

## What this implies

**The original assertion is wrong as stated, and the real finding is more useful than it.**

1. **The storage model is fine.** At 1M nodes `graph.json` projects to ~1.1 GB and the
   SQLite sidecar to ~1.95 GB. Serializing projects to ~11 s, parsing ~5 s, the sidecar
   writes ~40 s. For an artifact rewritten on compile rather than on every query, none of
   that is a crisis, and none of it is what a graph database would be bought to fix.

2. **Retrieval is the constraint, and swapping in Neo4j or Kuzu would not fix it.** The cost
   is not "finding the nodes" — it is scoring all of them once found. A graph database
   would serve the same 99.9%-admitted candidate set to the same pure-Python scoring loop.
   The fix, whenever it is wanted, is candidate pruning (an ANN index over the vectors, or
   BM25-first retrieval feeding a bounded rerank set) and a vectorized dot product. That is
   a retrieval-architecture change, not a storage migration.

3. **For any plausible corpus, the current model is fine — and this is the honest headline.**
   The live graph is 47,132 nodes and compiles context in 2.2 s at a 1.1 GB peak. A corpus
   would have to grow **10× to 500,000 nodes** before memory becomes the binding constraint,
   and even then the failure is a slow, linear, entirely predictable one. Nothing here
   justifies a storage migration, and this report should not be cited as if it did.

4. **Query latency degrades before memory does, and is the thing users would notice.** At
   250,000 nodes `compile_context` already takes 16.6 s and a bare `hybrid_search` 7.4 s.
   Those are not memory failures; they sit well inside the machine. But a 16-second
   context compile is a usability problem roughly 2× sooner than the memory wall arrives.

5. **The number worth watching is peak RSS at query time, not corpus size.** 17.8 MB per
   1,000 nodes is the constant that decides when this matters on a given machine. On a
   64 GB host the same wall sits somewhere past 2M nodes.

## What was not measured, and why

- **500,000 and 1,000,000 nodes: not attempted.** Projected peaks of ~9.3 GB and ~18.2 GB
  against a 16 GB machine whose available memory oscillated between 1 and 9 GB throughout
  the session under load this session did not own. 1M exceeds physical memory outright, and
  500k could not be given a safe window. Every 500k/1M figure in this report is a linear
  extrapolation from three measured sizes, marked as such, and must not be quoted as a
  measurement.
- **No graph database was benchmarked.** This report measures where *Tesserae* breaks. It
  does not compare against Neo4j or Kuzu, so it cannot and does not claim Tesserae is faster
  or slower than one. Finding 2 argues the comparison is aimed at the wrong layer, which is
  an argument, not a measurement.
- **No knowledge graph was compiled.** LLM extraction is slow, costs quota and is
  non-deterministic; per project policy it never runs in a test or a harness. Every graph
  here is synthetic, so **extraction cost is entirely absent from these numbers** — a real
  1M-node corpus would pay an LLM bill this report says nothing about.
- **Cold-start disk IO.** `graph.json` reads were served from the OS page cache after the
  write in the same process. The read numbers are best-case.
- **Concurrency.** Single process throughout. Nothing here says how the SQLite sidecar
  behaves under concurrent readers and a writer, which is a plausible separate knee.
- **The incremental compile path**, `merge_ledger`, and the markdown/vault projections.
- **The hub-scaling assumption is the biggest soft spot.** The generator holds the dominant
  hub's degree constant *as a share of total edges*, so at 1M nodes one node has ~644,000
  edges. That is the pessimistic reading. If a real corpus instead grew the *number* of
  community summaries and held each one's fanout roughly fixed, the hub-driven costs (PPR,
  and `compile_context`'s 1.21 exponent) would be overstated here.

## Reproducing

```sh
uv run --python 3.11 pytest tests/test_scale_graph.py -q      # generator fidelity
uv run --python 3.11 python scripts/scale_measure.py 47132 100000 250000
```

The sweep refuses sizes it projects will not fit and abandons a size that crosses its RSS
ceiling, so re-running it on a smaller machine yields a shorter curve rather than a hang.
