# Where the JSON-plus-sidecar model actually breaks

**Date:** 2026-08-14
**Status:** measurement, not a proposal. Nothing here was optimized; findings are reported as found.

> **Partly superseded — see [Re-measurement, 2026-08-15](#re-measurement-2026-08-15-the-knee-moved).**
> PR #160 vectorised the exact function this report named as the knee. Everything
> above that section is the 2026-08-14 measurement, kept unedited so the change is
> visible; the knee it names is no longer where the knee is. Read the two together.

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

## Re-measurement, 2026-08-15: the knee moved

**Date:** 2026-08-15
**Status:** measurement. Same generator, same sweep, same machine, current `main`
(18801429). Everything above this line is left exactly as written on 2026-08-14 so the
move is visible rather than quietly edited away.

### Why re-measure

The report above named one function as the knee:

> `tesserae/retrieval/hybrid.py::_embedding_scores`

and gave two reasons — a Python `list` of 256 floats materialized per node (memory), and a
pure-Python `sum(a * b for a, b in zip(...))` per document (CPU). PR #160 removed both. The
lane now rebuilds the corpus matrix with `np.frombuffer` over the packed blobs SQLite
already stores and scores it as one matrix-vector product. Nothing else in the sweep's
fifteen phases was touched.

So the question this section answers is not "did it get faster" — it did — but **where the
binding constraint sits now that it has.**

### Two methodology changes, both forced by the result

**One sample per size stopped being enough.** The 2026-08-14 sweep took one. That was fine
when the embedding lane spent ~900 ms per query in Python arithmetic, because a few hundred
milliseconds of cold-page-cache SQLite read was noise beside it. At 172 ms the IO *is* the
measurement: one 100,000-node `hybrid_search_warm` sample landed at 3.31 s against 0.99 s and
1.00 s for its siblings. `scripts/scale_measure.py` therefore grew `--repeat` and a median
summary. The median, not the mean — the distribution is one-sided, since nothing makes a run
faster than its warm case, and the mean of those three would have been 1.77 s.

**Repeats alone were not enough either, so runs are now admitted or rejected by a control.**
This machine spent the session 20–22 GB into swap under load this session did not own, with
available memory oscillating between 1.1 GB and 9.8 GB. Under that pressure a phase does not
merely run slower — **it peaks lower**, because the OS refuses it the resident pages it asked
for. That gives a clean, code-independent test:

> `VectorCache.embed` (cold build) is byte-identical between the two sweeps. Its peak RSS is
> therefore a pure measure of what the machine was *willing to give*, uncontaminated by
> anything #160 changed. A run whose control peak fell below 88% of the 2026-08-14 figure at
> that size was starved, and its timings describe the swap device rather than the code.

Sixteen runs were taken; **seven were admitted and nine rejected.** The rejects are not
marginal — a rejected 100,000-node run reported the embedding lane at 3,863 ms while peaking
29% below the control, against 442 ms for an admitted one. The admitted runs then validate
the control by reproducing every untouched phase: `json.dumps` ×1.00, `Bm25Index.prepare`
×1.00, `personalized_pagerank` ×0.93–0.99, and peak RSS for `json.dumps` / `json.loads` /
`graph_from_payload` within 1 MB of 2026-08-14. **Nine untouched phases agreeing to three
digits across two sweeps a day apart is what licenses reading the two that moved.**

Identifying the rejects at all required a prior sweep to compare against, which the next
person will not have. `scripts/scale_measure.py` therefore now samples system-wide available
memory alongside RSS and names any phase that ran with under 1.5 GB free, so a single run is
self-diagnosing. The rule it encodes is the one that cost this session most of a day:

> A phase reporting **more** seconds at **less** peak RSS than a previous sweep was starved,
> not slowed. Bin it.

### Time (seconds): 2026-08-14 single sample → 2026-08-15 median of admitted runs

| Phase | 47,132 before | 47,132 after | 100,000 before | 100,000 after |
|---|---|---|---|---|
| `generate` (harness, not product) | 1.390 | 1.390 | 3.084 | 3.114 |
| `ResearchGraph.model_dump` | 0.734 | 0.741 | 1.634 | 1.593 |
| `ResearchGraph.canonicalized` | 0.048 | 0.052 | 0.141 | 0.118 |
| `json.dumps` | 0.521 | 0.540 | 1.122 | 1.122 |
| write `graph.json` | 0.180 | 0.104 | 0.320 | 0.312 |
| read `graph.json` | 0.008 | 0.009 | 0.019 | 0.017 |
| `json.loads` | 0.223 | 0.227 | 0.498 | 0.480 |
| `graph_from_payload` | 0.255 | 0.265 | 0.563 | 0.565 |
| `upsert_many_nodes` | 0.287 | 0.265 | 0.666 | 0.701 |
| `upsert_many_edges` | 1.151 | 1.112 | 2.695 | 2.639 |
| `Bm25Index.prepare` (cold build) | 2.055 | 2.050 | 5.039 | 5.058 |
| `VectorCache.embed` (cold build) | 2.916 | 2.508 | 5.898 | 5.723 |
| **`hybrid_search`, warm, 3 lanes** | 1.125 | **0.417** | 2.440 | **1.001** |
| `personalized_pagerank` | 1.379 | 1.363 | 3.126 | 2.907 |
| **`compile_context`, end to end** | 2.207 | **1.456** | 6.086 | **4.240** |

n=3 admitted at each size. Spread across admitted runs is tight — `hybrid_search`
0.408–0.459 s at 47,132 and 0.994–1.031 s at 100,000 — which is itself evidence the control
is selecting on the right thing.

**`hybrid_search` is 2.7× faster at 47,132 and 2.4× faster at 100,000. Nothing else moved
outside noise.** `compile_context` inherits the win because it contains a query.

### Peak RSS (MB): before → after

| Phase | 47,132 before | 47,132 after | 100,000 before | 100,000 after |
|---|---|---|---|---|
| `ResearchGraph.model_dump` | 190 | 190 | 376 | 376 |
| `json.dumps` | 467 | 467 | 958 | 958 |
| `json.loads` | 521 | 521 | 1,077 | 1,077 |
| `graph_from_payload` | 564 | 563 | 1,173 | 1,172 |
| `upsert_many_edges` | 367 | 367 | 746 | 746 |
| `Bm25Index.prepare` (cold build) | 598 | 602 | 1,205 | 1,205 |
| `VectorCache.embed` (cold build) | 1,152 | 1,074 | 2,283 | 2,134 |
| **`hybrid_search`, warm** | 1,096 | **817** | 2,194 | **1,566** |
| `personalized_pagerank` | 727 | 832 | 1,410 | 1,596 |
| `compile_context` | 1,103 | 833 | 2,207 | 1,583 |

Peak RSS is a whole-process number, so it carries the resident graph along with the phase's
own appetite. The sweep also records each phase's *delta* over its entry RSS, and that is
the number that names the culprit:

| Phase, delta RSS (MB) | 47,132 | 100,000 | MB per 1,000 nodes |
|---|---|---|---|
| **`VectorCache.embed` (cold build)** | **796** | **1,721** | **17.5** |
| `json.dumps` | 272 | 578 | 5.8 |
| `Bm25Index.prepare` (cold build) | 199 | 425 | 4.3 |
| `json.loads` | 133 | 285 | 2.9 |
| **`hybrid_search`, warm** | **129** | **280** | **2.9** |
| `personalized_pagerank` | 23 | 78 | 1.0 |

The 2026-08-14 report closed on a constant: **17.8 MB of peak RSS per 1,000 nodes**,
attributed to the embedding lane, paid *on every query*. That constant did not go away. It
moved: it is now **17.5 MB per 1,000 nodes paid once, when the vector cache is cold**, and
the query itself costs **2.9 MB per 1,000 nodes** — a 6× reduction in the recurring term,
with the one-off term landing within 2% of where the recurring one used to be.

### `hybrid_search` lane split (ms, warm, median)

| Lane | 47,132 before | 47,132 after | 100,000 before | 100,000 after |
|---|---|---|---|---|
| **embedding** | **877.2** | **172.4** | **1,879.0** | **444.0** |
| bm25 | 110.7 | 109.5 | 250.8 | 244.6 |
| lexical | 41.8 | 36.6 | 80.2 | 79.1 |

The embedding lane is **5.1× faster at 47,132 and 4.2× faster at 100,000**. The other two
lanes are unchanged to within a few percent, as they should be — #160 did not touch them,
and that they did not move is a second control on the first.

Two properties the lane kept, both checked in every run's profile rather than assumed:
`vectorized: True`, and `scored` equal to the full candidate set (47,132 / 100,000 /
250,000). **The lane is still exhaustive.** It got cheaper, not more selective; 99.9% of the
corpus still reaches scoring on every query. Every claim the 2026-08-14 report made about
candidate pruning still stands, because no pruning was added.

The lane's *share* of query time fell from 73–78% to **54–58%**. It is still the largest
lane, but it is no longer most of the query: it ran 7.9× the `bm25` lane at 47,132 before
and runs 1.6× it now (7.5× → 1.8× at 100,000). A further speedup of the embedding lane
alone can now win at most 54–58% of query time, where before it could have won 78%.

### Where the knee is now

There are now two, and separating them is the whole point of this section. The 2026-08-14
report had one knee because one function was both the memory hog and the CPU hog. #160 split
them apart.

**The memory knee moved from the query to the cold vector-cache build:**

> `tesserae/retrieval/vector_cache.py::VectorCache._resolve_blobs`

specifically its miss path, `fresh = backend.embed([pending[key] for key in missing])`
followed by `cached[key] = _encode_vector(vector)` — the whole corpus as Python lists *and*
the whole corpus as packed blobs, both resident at once. At **17.5 MB per 1,000 nodes** it is
the largest single memory demand in the sweep at every measured size, ahead of `json.dumps`
(5.8) and six times ahead of a warm query (2.9).

This is not a harness artifact, which was worth checking before naming it. The sweep warms
the cache through `VectorCache.embed`, whose extra decode-to-lists could in principle have
manufactured the peak; measured directly at 47,132 nodes, `embed` cost 580 MB of delta and
`embed_blobs` 750 MB — the same within run-to-run noise, because both share the miss path
and the miss path is the cost. A cold `hybrid_search` pays it too.

The character of that bound changed more than its size did. The old wall was 4.82 GB **for
one query, every query**. The new wall is ~2 GB at 100,000 nodes for **the first query after
a corpus changes**, after which queries cost 280 MB of working set over the resident graph.
A cost paid once per corpus is a provisioning problem; a cost paid per query is an
architecture problem. **This one stopped being an architecture problem.**

**The time knee stayed in the embedding lane but changed what it is made of:**

> `tesserae/retrieval/vector_cache.py::VectorCache.embed_blobs`
> → `tesserae/graph_stores/sqlite.py::SqliteGraphStore.read_node_vector_blobs`

The lane is still the largest of the three, and at 250,000 nodes it is still ~77% of query
time — but **93–98% of it is now the SQLite read**, not the Python arithmetic the 2026-08-14
report named. The lane fetches the packed corpus back out of the sidecar on every query —
96 MB at 47,132 nodes, 205 MB at 100,000, 512 MB at 250,000 — through 500-key chunked `IN`
lookups, copied three times over on the way in. The arithmetic #160 replaced measures 4 ms
against a 167 ms lane at 47,132, and 24 ms against 5.6 s at 250,000.

**Bound by:** memory first at compile time, IO first at query time, CPU not at all. That is
a different sentence from the 2026-08-14 report's "memory first, CPU second, IO not at all",
and every clause of it changed.

The practical consequence is that **the win is machine-dependent, not corpus-dependent**.
Where the packed vectors plus the resident graph fit the OS page cache, #160 is worth 4–5×
on the lane. Where they do not, it is worth 0× on time and 35% on peak RSS. On this 16 GB
machine that crossover sits between 100,000 and 250,000 nodes; on a 64 GB host it would sit
proportionally further out. Nobody should quote "the embedding lane got 5× faster" without
the size and the machine attached to it.

**What did NOT change, and is now the thing to watch:** `compile_context` at 100,000 nodes
still takes 4.2 s, and `Bm25Index.prepare` still costs 5.1 s with the steepest exponent in
the sweep. Finding 4 of the 2026-08-14 report — that *latency* becomes a usability problem
well before memory becomes a wall — survives this re-measurement intact and is now the more
likely first failure a user meets.

### Re-derived projections (still projections)

Fitted on the two clean measured sizes, worst-phase peak RSS (`VectorCache.embed`, cold):
**19.4 MB per 1,000 nodes with a 153 MB intercept**.

| | 250,000 (proj.) | 500,000 (proj.) | 1,000,000 (proj.) |
|---|---|---|---|
| cold vector-cache build, peak RSS | ~5.0 GB | ~9.9 GB | ~19.6 GB |
| warm `hybrid_search`, peak RSS | ~3.6 GB | ~7.0 GB | ~13.7 GB |
| warm `hybrid_search`, own working set | ~0.7 GB | ~1.4 GB | ~2.8 GB |
| `graph.json` | 275.5 MB (measured) | ~551 MB | ~1.10 GB |
| `sqlite.db`, before vectors | 486.0 MB (measured) | ~973 MB | ~1.95 GB |

**These are extrapolations and must not be quoted as measurements — and the one check
available says they run high.** The cold-build line projects ~5.0 GB at 250,000 nodes, where
2026-08-14 *measured* 4.12 GB for that same, unchanged code path: a 22% overshoot. A
two-point line has no way to see the sublinearity that shows up between 100,000 and 250,000,
and `scripts/scale_measure.py`'s own docstring warns that its linear fit is a refusal
threshold rather than a prediction. Read the 500k and 1M rows as **upper bounds that are
probably ~20% high**, not as forecasts.

The direction of the correction matters more than its size: even discounted, the cold build
still wants roughly 8 GB at 500,000 nodes and 16 GB at 1,000,000, which is essentially where
the 2026-08-14 report put the wall. **#160 did not move the wall. It moved which side of the
compile the wall is on.**

### 250,000 nodes: the speedup is gone, the memory win is not

This size cost seven attempts over three hours to measure once. Six were starved — they
reported the embedding lane at 8.6–10.6 s while peaking 19–32% below the control, which is
how a squeezed process presents: **slower and smaller at the same time.** Reporting any of
them would have said the vectorised lane made retrieval 1.6× *worse*, which is exactly the
false headline the control exists to catch.

One got its memory — its control peaked at 4,250 MB against 2026-08-14's 4,115 MB,
i.e. **above** rather than below — and it says something the two smaller sizes do not:

| Phase at 250,000 | 2026-08-14 | 2026-08-15 (admitted) | |
|---|---|---|---|
| `json.dumps` | 2.782 s | 2.936 s | ×1.06 |
| `json.loads` | 1.185 s | 1.264 s | ×1.07 |
| `graph_from_payload` | 1.364 s | 1.494 s | ×1.10 |
| `upsert_many_edges` | 8.499 s | 7.422 s | ×0.87 |
| `Bm25Index.prepare` | 18.200 s | 14.293 s | ×0.79 |
| `VectorCache.embed` (cold) | 17.917 s | 15.216 s | ×0.85 |
| **`hybrid_search`, warm** | **7.420 s** | **7.296 s** | **×0.98** |
| `personalized_pagerank` | 9.161 s | 10.009 s | ×1.09 |
| `compile_context` | 16.616 s | 23.317 s | ×1.40 |
| **embedding lane** | **5,376.9 ms** | **5,610.8 ms** | **×1.04** |
| `hybrid_search` peak RSS | 4,820 MB | **3,149 MB** | **−35%** |
| `VectorCache.embed` peak RSS | 4,115 MB | 4,250 MB | +3% |

**At 250,000 nodes the vectorised lane is not faster at all.** It is 35% cheaper in peak
resident memory and the same speed, where at 47,132 it was 5.1× faster and at 100,000
4.2× faster. The speedup does not decay gently across the range — it is 4.2× at 100,000 and
1.0× at 250,000.

That discontinuity has a mechanism, and it was measured rather than reasoned about. Taking
the lane apart into its three steps — fetch the packed rows from SQLite, join them, do the
matrix arithmetic — gives this:

| Lane step, first read after a cold build | 47,132 | 100,000 | 250,000 |
|---|---|---|---|
| **`VectorCache.embed_blobs` (the SQLite read)** | **155 ms** | **385 ms** | **6,256 / 8,178 ms** |
| `b"".join(...)` | 10 ms | 24 ms | 59 / 166 ms |
| `q @ docs` + norms (what #160 replaced) | 4 ms | 10 ms | 24 / 81 ms |

Two independent probes at 250,000 nodes; the smaller sizes were stable enough for one each.

**The arithmetic is not the lane and never becomes it.** It is 0.1 ms per 1,000 nodes at
every size — dead linear, 4 ms where the whole lane is 167 ms. What the lane *is*, at every
size, is the read: 93% at 47,132, 95% at 100,000, 98% at 250,000. #160's real achievement
was deleting the Python decode and dot product that sat *around* that read (~720 ms of the
877 ms lane at 47,132); it could not touch the read itself, and past 100,000 nodes the read
is all that is left.

The read is also where the cliff lives. Per 1,000 nodes it costs 3.3 ms at 47,132, 3.9 ms at
100,000 — and 25 ms at 250,000, a **6.5× jump in unit cost** across one size step. The
corpus of packed vectors is 96 MB, 205 MB and 512 MB at those sizes; below the cliff those
pages are still in the OS page cache from the build seconds earlier, above it they are not.
The same probe shows it directly: at 250,000 a second call moments after the first cost
1.15 s against the first call's 6.26 s, purely on cache state.

The read path also carries the packed corpus three times over at once —
`SqliteGraphStore.read_node_vector_blobs` materialises a `bytes` per row into a dict,
`VectorCache.embed_blobs` builds a per-input list over it, and
`_embedding_scores_vectorized` calls `b"".join(...)` — which is ~1.5 GB in flight at
250,000 nodes, and both a memory cost and a reason the cliff arrives when it does.

Two honest caveats on this row. It is **n=1**: six siblings were rejected, so there is no
spread to quote, and the ×0.98 should be read as "no measurable change" rather than as a
precise ratio. And `compile_context` at ×1.40 is **unexplained** — its peak RSS fell 47%
(3,961 → 2,112 MB) while its wall time rose, which is the starvation signature appearing
inside a run the control admitted. It is reported rather than smoothed away.

What every 250,000 run agrees on, because disk size is indifferent to memory pressure:
555,225 edges, `graph.json` 275.5 MB, `sqlite.db` 486.0 MB before vectors — identical to
2026-08-14 to the last recorded digit, confirming the generator still produces the same
corpus it did then.

### What was not measured this time, and why

Everything in the 2026-08-14 "What was not measured" section still applies unchanged: no
graph database, no compiled knowledge graph, no cold-start disk IO, no concurrency, no
incremental compile path, and the hub-scaling assumption is still the biggest soft spot.
Additionally:

- **500,000 and 1,000,000: still not attempted**, and the reason is stronger than last time.
  The cold-build projection is essentially unchanged (~9.9 GB and ~19.6 GB against
  2026-08-14's ~9.3 GB and ~18.2 GB) because #160 did not touch that path. This machine
  could not reliably hold 250,000 nodes today; it will not hold 500,000.
- **150,000 nodes was attempted three times and discarded.** It was added to give the fit a
  third point; all three runs failed the contention control (one reported `json.dumps` at
  4.6 s against 1.7 s expected). It is absent from every table here rather than present with
  a footnote.
- **The no-numpy fallback was not swept.** `_embedding_scores` — the function the
  2026-08-14 report named — still exists and is still exactly as expensive as that report
  described. It is now reached only when numpy is absent. On such a machine every number in
  the 2026-08-14 report is still the current one.
- **The lane's IO half was not optimised, only exposed.** This report says where the time
  goes at 250,000 nodes; it does not test whether a single ordered scan of `node_vectors`
  would beat the chunked `IN` lookups, or whether the three simultaneous copies of the
  packed corpus can be reduced to one. Both are hypotheses, not measurements.
- **Nothing was measured on an uncontended machine.** Both sweeps ran on a 16 GB workstation
  shared with other work; that is why the control exists. The admitted figures are
  defensible because nine untouched phases reproduce inside them. That argument gets weaker
  as size grows, because the window in which the machine will hold the working set gets
  rarer — which is exactly what made 250,000 expensive to measure and 150,000 impossible.

### What this changes in the conclusions above

Findings 1, 3 and 5 of the 2026-08-14 report stand unchanged. Three amendments:

- **Finding 2 is half-executed, and the executed half was the smaller one.** It named the
  fix as "candidate pruning ... and a vectorized dot product". The vectorisation shipped;
  pruning did not, and the lane is still exhaustive by design. But the more useful correction
  is that finding 2 mis-costed its own recommendation: it read the lane as CPU-bound on
  Python arithmetic, and that arithmetic turns out to be 10 ms of a 187 ms lane. Vectorising
  it bought 4–5× only where the sidecar read was already cached, and nothing at all where it
  was not. **The remaining cost is IO, and pruning would attack it** — a bounded rerank set
  reads bounded bytes. That is the first argument in either report for pruning that rests on
  a measurement rather than on an intuition about `for` loops.
- **Finding 4 is promoted.** With the per-query memory cost cut 6×, latency is unambiguously
  the first thing a growing corpus will run into. `Bm25Index.prepare` carried the steepest
  exponent in the 2026-08-14 three-point fit (1.31), was untouched by #160, and is now the
  steepest curve left.
- **Finding 5's constant needs a machine attached to it.** "17.8 MB per 1,000 nodes" is still
  right for the cold build (17.5 measured here) but no longer describes query cost, which is
  2.9. And the 250,000-node result shows the query's *time* now depends on whether the packed
  vectors fit the host's page cache — so "on a 64 GB host the same wall sits past 2M nodes"
  is right about memory and says nothing about latency, which will degrade at whatever size
  the cache stops holding the corpus.

## Reproducing

```sh
uv run --python 3.11 pytest tests/test_scale_graph.py -q      # generator fidelity
uv run --python 3.11 python scripts/scale_measure.py 47132 100000 250000 --repeat 3
```

The sweep refuses sizes it projects will not fit and abandons a size that crosses its RSS
ceiling, so re-running it on a smaller machine yields a shorter curve rather than a hang.

`--repeat` is not optional politeness since #160. Check `free`/`memory_pressure` before
trusting any single row: a phase that reports *more* time at *less* peak RSS than a previous
sweep was starved, not slowed, and belongs in the bin rather than in a table.
