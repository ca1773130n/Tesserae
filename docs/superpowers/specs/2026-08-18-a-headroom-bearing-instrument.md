# A headroom-bearing instrument for the self-improvement claim

**Date:** 2026-08-18
**Status:** built and run. **Its comparison was unfair in our favour** — see
§8, added 2026-08-18 after a nine-agent confound hunt.
**Supersedes:** the metric choice in
[2026-08-17-lifelong-learning-evidence.md](2026-08-17-lifelong-learning-evidence.md)
§3. That document's experiment design, arms, sequencing and honesty constraints
all stand. Its **headline metric does not**, and this document says why and what
replaces it.

---

## 1. What running it actually established

Experiment 1 ran on 2026-08-18 against a scratch compile of `examples/demo-corpus`
(135 documents, `fallbacks=0`, 3,130 nodes / 5,114 edges), three consolidation
cycles, zero documents ingested:

| cycle | Tesserae | BM25 | Dense | Tesserae edges | LLM calls |
|---|---:|---:|---:|---:|---:|
| 0 | **15/15** | 12/15 | 9/15 | 5,491 | 0 |
| 1 | 15/15 | 12/15 | 9/15 | **9,448** | 0 |
| 2 | 15/15 | 12/15 | 9/15 | 9,448 | 0 |
| 3 | 15/15 | 12/15 | 9/15 | 9,448 | 0 |

Three things are real and worth keeping:

- **The retrieval margin.** 15/15 against BM25's 12/15 and dense's 9/15, on an
  identical corpus under an identical K budget. This is the first Tesserae-vs-
  baselines number this repository has ever produced.
- **Association is free and idempotent, as designed.** 3,957 edges discovered
  at cycle 1 for zero LLM calls, byte-identical thereafter.
- **The concept layer is worth +3 questions.** The same question set scored
  12/15 on a graph where 108 of 135 documents had fallen back to deterministic
  extraction, and 15/15 once they were recovered. That is a measurement of what
  LLM extraction buys, obtained by accident.

**The self-improvement claim was not tested.** Tesserae starts at ceiling, so
Δ = +0 carries no information about the mechanism. The design predicted a step;
what it got was a number that had nowhere to step to.

## 2. Two separate reasons it saturated

### 2.1 The question set tops out at full corpus by construction

`evals/growth/` measures *cumulative slices* — its questions are chosen to
become answerable as documents arrive, so all 15 being answerable at the full
corpus is the intended end state, not a defect. `growth/run.py`'s own hop sweep
records it plainly: at `MAX_HOPS=3` the full graph scores 14-15/15.

Experiment 1 froze the **full** corpus and reused those questions. It therefore
began where the growth curve *ends*.

### 2.2 The metric is monotone in edges, which makes it a density counter

`answerable` = both anchors ground **and** a path ≤ `MAX_HOPS` links them. Adding
an edge can only create paths, never destroy them. So on any edge-adding
operation:

- the Tesserae arm can only rise or stay flat — **it cannot detect a harmful
  association**;
- `controls fired` can only rise — the sole downward signal is a counter of
  spurious paths, not a measure of answer quality.

A metric that cannot move down is not measuring quality. It is measuring
density, and consolidation adds density by definition. This is the deeper
problem, and more questions alone will not fix it.

## 3. The control that fired is probably calibration, not a bad association

At cycle 1 one control fired and stayed fired. Before reading that as
"association invented a false fact", note what the hop sweep already documented:

```
MAX_HOPS=1  controls PASS   13-14/15
MAX_HOPS=2  controls PASS   14-15/15
MAX_HOPS=3  controls PASS   14-15/15   <- chosen
MAX_HOPS=4  controls FAIL (3 firings)  15/15
```

Association shortens paths. Measuring an association-enriched graph at a hop
budget swept on the **base** graph is measuring at an effectively larger budget,
and a larger budget is already known to fire controls. So:

> **The hop budget must be re-swept on the post-association graph** before any
> control firing there is attributed to association quality. `evals/growth/
> sweep_hops.py` exists; it has never been run against an overlay-merged graph.

This is cheap — no LLM calls — and it gates the interpretation of every future
run. It should be done first.

## 4. What a headroom-bearing instrument requires

Three requirements, and the second is the one that matters.

| # | requirement | why | without it |
|---|---|---|---|
| 1 | Questions unanswerable at T0 | headroom to move | Δ is pinned at 0 |
| 2 | **A metric that can go down** | consolidation must be able to *hurt* | a density counter dressed as quality |
| 3 | Controls that scale with the set | negative signal is the only guard | 2 controls over 60 questions guards nothing |

### The metric that can go down

`evals/qa/` already ships an EM + token-F1 scorer (#179), and the superseded
spec lists it as a *secondary* metric run twice. **It should be primary.** A QA
score is two-sided: an association that pulls irrelevant context into the answer
window lowers it, and one that supplies a missing bridge raises it. That is the
property connectivity structurally lacks.

Ranked retrieval (recall@k, MRR) against a gold document set is the cheaper
alternative with the same two-sidedness, and does not need an LLM in the loop.
It is the better first move if answer generation is the expensive part.

## 5. Scope

| # | item | cost | depends on |
|---|---|---|---|
| 1 | Re-sweep `MAX_HOPS` on an overlay-merged graph; record the table beside the existing one | minutes, no LLM | — |
| 2 | Switch the Tesserae arm's headline metric to ranked retrieval (recall@k / MRR) over a gold set; keep `answerable` as a reported secondary | ~half a day | 1 |
| 3 | Grow the question set to ~60 live + ~12 controls, written so a meaningful share are unanswerable on the frozen full corpus | ~1-2 days of authoring, plus review | — |
| 4 | Re-run experiment 1 on the new instrument | one scratch compile (already have it) + 3 free cycles | 2, 3 |
| 5 | Only then, experiment 2 (accumulation with baselines) per the superseded spec §4 | reuses 4's machinery | 4 |

Item 3 is the expensive one and the one that cannot be automated honestly — a
question set generated from the graph under test will be answerable by that
graph. The questions must come from the corpus documents, written against what
the papers say, the way `evals/growth/questions.yaml` was.

## 6. What this will and will not prove

**Will**, if the numbers land: that a Tesserae memory measurably improves on a
corpus that is not changing, that the improvement survives a metric which could
have shown it degrading, and that two standard baselines provably cannot move at
all.

**Will not**, and no amount of work here changes it: any comparison to Mem0's,
HippoRAG's or SegTreeMem's published figures. The protocol's judge and embedder
remain unreachable on this machine; `tests/test_docs_comparative_claims.py`
fails the build on that class of claim, correctly.

Every constraint in the superseded spec §7 continues to apply.

## 7. Decisions this needs before item 3 starts

1. **Ranked retrieval or QA accuracy as the headline?** Retrieval is cheaper and
   LLM-free; QA is closer to what a user experiences. The scope above assumes
   retrieval first, QA later.
2. **How many questions is enough?** 60 is a guess sized to leave headroom, not
   a computed number. A power calculation against the expected effect size would
   be better, and we have no effect-size estimate yet.
3. **Which corpus?** `examples/demo-corpus` is compiled, small, and already
   understood. It is also the corpus the current questions were written against,
   which risks authoring the new set against a graph we have been staring at.


---

## 8. The comparison was measuring lane count (added 2026-08-18)

Designing experiment 2 turned up a defect in experiment 1, already merged.

`rank_documents_graph` seeds from `hybrid_search(mode="hybrid")` — an RRF fusion
of **three** lanes (bm25 + lexical + embedding, all weighted 1.0), scored over
node text at node granularity. BM25 and Dense are **one** lane each, over raw
markdown, at document granularity. Beating them measures lane count and
granularity. It does not measure architecture, and §1 of this document reported
it as though it did.

The ladder, measured on the frozen corpus, 59 live questions, K=10, overlay off:

| arm | lanes | text | granularity | R@10 |
|---|---:|---|---|---:|
| Dense | 1 | markdown | document | 0.728 |
| BM25 | 1 | markdown | document | 0.738 |
| Tesserae−edges | 3 | node text | node | 0.754 |
| Tesserae (+ PPR) | 3 | node text | node | 0.763 |
| NodeText-doc | 3 | node text | document | 0.802 |
| **Hybrid-doc** | 3 | markdown | document | **0.827** |

Paired bootstrap, 4000 resamples, question-clustered:

    Tesserae − Tesserae−edges   +0.009  [-0.045, +0.062]   <- the graph itself
    Tesserae − BM25             +0.026  [-0.034, +0.088]
    Tesserae − Hybrid-doc       -0.063  [-0.116, -0.011]   <- excludes zero

Three things follow.

**The architecture's own contribution is +0.009 with a CI spanning zero.** The
rest of the +0.026 margin over BM25 is lane count and node-level scoring, both
properties of the retrieval wrapper rather than of the memory.

**Against a fusion-matched baseline this memory loses**, by a margin whose
confidence interval does not include zero. `Hybrid-doc` is now in the default
arm list of `evals/selfimprove/curve.py` so that result cannot be omitted by
forgetting a flag, and a test fails if it is dropped from either loop.

**n=59 cannot resolve the question.** Against a +0.009 effect and a ~0.06
half-width, resolution needs roughly 36× the sample — about 2,100 questions.
More consolidation cycles do not help. What this corpus can honestly deliver is
a bound: **the edges contribute less than 0.06 R@10 at 73 documents.**

## 9. Experiment 2 is NOT ready to build

The design failed its own adversarial review, and the failures are worth keeping
so the broken version does not get built later:

- **The raw per-slice curve is the gold-availability calendar.** Every arm tracks
  the fraction of each question's gold set that exists at cut N. A
  constant-skill arm produces exactly the "rises fast, decelerates, plateaus"
  shape the claim predicts, and a *higher* constant skill rises faster and
  plateaus higher. Same class of defect as `answerable` being monotone in edges.
- **Event-time normalisation was proposed to fix that, and its headline is not
  schedule-invariant** as claimed: mean R@10 at unlock moves 0.814–0.861 across
  legal slice schedules, and the Hybrid−BM25 delta moves −0.002 → +0.040 — 79%
  of the pre-registered minimum detectable effect, from a free parameter.
- **The one resolvable effect is manufactured by a step the design itself bars
  from quotation**: the final 64→73 cut adds nine never-gold aggregator
  documents and zero gold. Ending at 64 instead drops the effect below the MDE;
  restricted to papers, its CI straddles zero.
- **One document decides the sign.** `arxiv-2308-04079` is gold for 22 of 59
  questions. Removing it moves BM25's uptake 0.727 → 0.785 and flips the
  headline.
- **The compile is not free, contrary to the design's own cost model.** Cache
  addressing includes the resolved provider/model, and `compile_slice(first=True)`
  re-inits without one — falling back to the machine default and missing every
  cached entry. Up to 1,215 extraction calls where the plan predicted zero. Any
  future run must pass `--llm-provider claude --claude-config-dir` explicitly.

The prerequisite for experiment 2 is not more slices. It is a question set large
enough to resolve the effect, on a corpus where no single document carries a
third of the gold.


---

## 10. n = 284, and the sign flips (added 2026-08-18)

§8 reported the ladder at n = 59 and called the graph's own contribution
"+0.009 with a CI spanning zero". The question set was then grown to **284 live
questions + 48 controls** — 225 new ones authored across 12 themes from the paper
text, 95% surviving adversarial verification, with the hub paper 2308.04079 cut
from 37% of gold sets to 13% and all 50 papers now gold for something.

At that sample the earlier reading does not survive.

| arm | lanes | text | granularity | R@10 | MRR |
|---|---:|---|---|---:|---:|
| Dense | 1 | markdown | document | 0.786 | 0.820 |
| Tesserae (+ PPR) | 3 | node text | node | 0.804 | 0.795 |
| Tesserae−edges | 3 | node text | node | 0.806 | 0.860 |
| BM25 | 1 | markdown | document | 0.861 | 0.888 |
| **Hybrid-doc** | 3 | markdown | document | **0.896** | **0.943** |

Paired bootstrap, 4000 resamples, n = 284:

    Tesserae − Tesserae−edges   -0.002  [-0.025, +0.022]
    Tesserae − BM25             -0.057  [-0.083, -0.029]
    Tesserae − Hybrid-doc       -0.092  [-0.116, -0.068]
    Hybrid-doc − BM25           +0.035  [+0.018, +0.054]

**The graph contributes nothing measurable.** Removing the PPR walk moves R@10 by
−0.002. The interval is now tight enough to state a real bound rather than a
shrug: on this corpus the edges are worth **less than 0.025 R@10**.

**And this pipeline loses to plain BM25 over raw markdown**, by a margin whose
confidence interval excludes zero. At n = 59 the same ladder read +0.026
[−0.034, +0.088] and looked like a win. It was a small-sample artifact.

That is the most useful thing this instrument has produced. Every earlier version
of it — `answerable`, the single-lane comparison, the n = 59 ladder — reported a
Tesserae win. Each time the win dissolved when the instrument got stricter, and
this is the first version strict enough to say so with an interval off zero.

### What this does not establish

Not that Tesserae's *product* retrieval is worse. This arm scores at **node**
granularity, and the doc-pooled variant of the same node text scored 0.802
against the node-level 0.763 at n = 59 — pooling was the largest single lever
anywhere in the ladder, larger than the edges by a factor of five. Whether
`compile_context` and `ask` pool to documents the way this harness does was the
open question. **It is now answered — they do not.** See §11.

Also unchanged: one corpus, one embedder (model2vec), K = 10, and `SEED_K = 25`
still unswept.


---

## 11. The product path is node-level, so this is a product finding (added 2026-08-18)

`context_compiler.compile_context` dedupes on `node.id`, never on `source_path`.
Measured on the scratch corpus, three questions, default budget:

    citations returned    distinct source documents
            20                       8
            20                       7
            20                      11

**2.39 citations per document.** A twenty-slot evidence budget buys an agent
material from roughly eight documents. Nothing in the retrieval path pools node
text to its document before spending that budget, which is exactly the
granularity the harness scored — so §10's numbers describe the product, not an
artifact of how the harness happened to rank.

### What pooling is worth, measured

Same node text, same three-lane fusion, the only change being that each
document's nodes are concatenated into one unit before ranking. n = 284:

| ranking unit | R@10 | MRR |
|---|---:|---:|
| Tesserae, node-level (what ships) | 0.804 | 0.795 |
| **same node text, pooled per document** | **0.854** | 0.834 |

    pooled − node-level   +0.049  [+0.024, +0.075]

The CI excludes zero, and the effect is **twenty times** the edges' −0.002. On
this corpus, at this K, choosing the ranking unit matters enormously more than
the graph structure does.

### The part that pooling does not fix

Pooled node text still loses to raw markdown: 0.854 against BM25's 0.861 and
Hybrid-doc's 0.896. So the ordering of causes, largest first, is:

1. **Extraction is lossy for retrieval.** Even at the best granularity, the text
   Tesserae mints scores below the source markdown it was minted from. That is
   the biggest single gap in the ladder and nothing about graph structure
   addresses it.
2. **Ranking unit**, worth ~0.05 and free to change.
3. **Edges**, worth less than 0.025 and indistinguishable from zero.

### What this licenses, and what it does not

Licenses: pooling per document before spending the context budget is a
measurable improvement with a CI off zero, on this corpus. It is a change to
ranking, not to what gets stored.

Does not license: any claim about answer quality. This metric is gold-document
recall. A bundle of twenty citations from eight documents may serve an agent
better than twenty documents' worth of shallower evidence — that is a different
experiment (`evals/qa/` has the scorer) and it has not been run. Nor does it
license a conclusion beyond one corpus, one embedder, and K = 10.

## §14. Extraction text loss, not the retrieval unit

§13 blamed `merge_node_group`'s singular `source_path` for the LongMemEval
deficit. That was real but it was the smaller half. Decomposed on group 0:

| what is retrieved | recall@10 |
|---|---|
| whole session documents (BM25) | 0.911 |
| each session's concatenated GRAPH text, unit still the session | 0.756 |
| graph nodes | 0.710 |

The first step, **−0.155, is pure extraction text loss**; the second, −0.046,
is the node-not-session retrieval unit that §13 named. Extraction loss is over
three times larger.

The cause is `_node_text` (`tesserae/retrieval/hybrid.py`): a node's searchable
string is id + name + type + description + aliases + metadata. A 14k-character
chat session was therefore reachable only through 88-character concept
summaries. One question asked what speed a new internet plan was; the extractor
minted 66 nodes for that session and not one mentions Mbps. The raw file does,
and it was on disk the whole time at `node.source_path`.

Giving document-anchor nodes their own file back, in the BM25 and lexical lanes
only (PR #213), measured through the shipped adapter:

| method | recall@10 | MRR |
|---|---|---|
| BM25 | 0.911 | 0.803 |
| Tesserae, patched | **0.820** | **0.707** |
| Tesserae, before | 0.705 | 0.584 |

+0.115 / +0.123, 8.4x the noise floor, 56% of the gap to BM25 closed on both
metrics. The BM25 and Dense rows are bit-identical across the two runs, which
is the control that the change touched only the arm that opted in.

**The lexical-only gate is load-bearing.** Raw text in all three lanes scores
0.803/0.612 — worse than lexical-only's 0.820/0.707 — because 8k characters
mean-pooled into 256 dimensions is the per-file pooling failure already on
record at 0.7857 -> 0.6578. A version of this change that "just adds the source
text" would have measured as a smaller win and hidden the reason.

This is the shape the competitor audit found everywhere: HippoRAG 2 adds
passage nodes and says why in the paper ("concepts are concise but often entail
information loss"); cognee keeps `DocumentChunk` text verbatim; A-Mem keeps the
original interaction. The three systems that replace text with an extracted
fact string — Zep, Mem0, Graphiti/MegaMem — report no LongMemEval retrieval
recall at all. Structure should select text, not replace it.

Does not license: any comparison to a published LongMemEval figure. The
protocol here uses a local 256-dimension embedder where the published one fixes
text-embedding-3-small, no answering was done, and the retrieval unit is this
harness's choice. Nor does it license enabling `source_root` on the product
query path: this raises prompt volume, and §12's audit blames volume for the
52.9% fabrication rate. That measurement has not been run.

## §15. The lane weights were already right

`DEFAULT_WEIGHTS = {bm25: 1.0, lexical: 1.0, embedding: 1.0}` is a default that
predates any measurement, and on group 0 the BM25 lane scores 0.911 alone while
the dense lane scores 0.425 — an obvious-looking case for reweighting. It is
not one.

Swept over 32 configurations (bm25 pinned at 1.0; lexical and embedding each
over {0, 0.25, 0.5, 1.0}), scored two ways: full-set, and 2-fold cross-validated
by even/odd question index — tune on one fold, report the held-out other, both
directions. Script:
`~/.blackhole/Tesserae/2026-08-21/weight_sweep.py`.

| | equal weights (CV) | best (CV) | full-set argmax | optimism gap |
|---|---|---|---|---|
| before #213 | 0.705 | 0.706 | 0.744 | **+0.037** |
| with #213 | 0.820 | 0.824 | 0.861 | **+0.037** |

Reweighting buys **+0.001 and +0.004** cross-validated. The optimism gap is
+0.037 in both blocks, which almost exactly accounts for the "+0.039 from
reweighting alone" that the benchmark-first design proposal reported and that
this document previously repeated. That figure was a full-set argmax over 32
configurations scored on the same 60 questions. It does not survive a held-out
split and should not be quoted again.

The two folds do not even agree on the winner: tuning on even picks
`1/0.25/0.5`, tuning on odd picks `1/0.0/0.5`. An argmax unstable across a
coin-flip split is noise being read as structure.

What the grid does establish, against the intuition that a weak lane dilutes a
strong one: **both non-BM25 lanes pay for themselves.** With #213 on, removing
the embedding lane (`1/1.0/0.0`) gives 0.758 and removing the lexical lane
(`1/0.0/0.0`) gives 0.802, both below equal weights' 0.820. RRF fuses RANKS, so
a lane with a worse average still contributes on the questions where it is
right. "We fuse a 0.911 lane with a 0.425 lane at equal weight" is a true
sentence that licenses no conclusion.

Does not license: extending this to another corpus. It is one split of one
group, n=60, and the relevant claim is only that this knob is not where the
remaining 0.091 to BM25 is hiding.

## §16. Semantic reach pays where words do not match — the graph does not

Every question set used against Tesserae was authored FROM its documents, so
questions quote their sources. Measured on the 284: the median question already
contains **30%** of its gold answer's content words (IDF-weighted 26%), and only
**12 of 284 (4.2%)** share none. That is the regime where lexical matching is
strongest and structure is dead weight, and it is where every comparison in this
document was run.

The claim worth testing is an INTERACTION, not a level: does non-lexical
retrieval pay MORE as the question stops sharing vocabulary with its answer?
Stratifying the same 284 into overlap quartiles (n=71 each, mean overlap 0.104 /
0.244 / 0.358 / 0.534) and scoring gold-document recall@10, paired bootstrap
5,000 resamples:

| stratum | overlap | BM25 | Hybrid − BM25 | Graph − BM25 |
|---|---|---|---|---|
| Q1 words don't match | 0.104 | 0.740 | **+0.076 [+0.029, +0.126]** | −0.007 [−0.064, +0.054] |
| Q2 | 0.244 | 0.867 | +0.022 [−0.014, +0.058] | **−0.085 [−0.146, −0.025]** |
| Q3 | 0.358 | 0.906 | +0.023 [−0.005, +0.052] | −0.038 [−0.085, +0.005] |
| Q4 words do match | 0.534 | 0.930 | +0.019 [−0.005, +0.042] | **−0.097 [−0.146, −0.052]** |

**The trend is confirmed: +0.058 [+0.006, +0.110].** The hybrid's edge over BM25
is four times larger where words do not match, and the CI on the difference of
gaps excludes zero. This is the first statistically significant win for
non-lexical retrieval anywhere in this document.

Two things follow, and they point opposite ways.

**The regime is real.** Fusion beats BM25 significantly in Q1 and nowhere else —
every other stratum's CI straddles zero. The +0.035 fusion win recorded earlier
was an average over a set that is 96% lexically-assisted; it is not a uniform
gain, it is a large gain on a quarter of the questions and nothing on the rest.

**The graph is not what delivers it.** The graph re-ranker is statistically tied
with BM25 in Q1 and significantly WORSE in Q2 and Q4. It is least harmful
exactly where the ontology argument predicts it should be strongest, and it
costs −0.097 on the questions BM25 already answers. What buys semantic reach
today is a 256-dimension static embedding, not concepts and relationships.

This refines §1's "graph contribution −0.002 [−0.025, +0.022]". That average hid
a real structure: not uniformly flat, but tied where lexical fails and clearly
harmful where it succeeds.

Two experiments follow directly, neither needing a recompile:

1. **Gate the graph on lexical confidence.** −0.097 in Q4 is the price of
   consulting it when BM25 is already right. A gate cannot help Q1 but it makes
   the re-ranker conditionally neutral instead of uniformly negative.
2. **Seed PPR the way HippoRAG 2 does.** `curve.SEED_K = 25`, and §12 measured
   the planner's walk delivering exactly ONE node. HippoRAG 2 seeds PPR with
   ALL passage nodes and says why: activating a broad set is what uncovers
   multi-hop chains. That is the largest architectural divergence from the
   system that wins this benchmark family.

Does not license: a claim that a knowledge graph cannot buy semantic reach. It
licenses only that THIS graph, at THIS seeding, on THIS corpus, does not — while
a 256-dimension embedding does, significantly, in the quarter of questions where
it matters. Reproduce with
`~/.blackhole/Tesserae/2026-08-21/lexical_strata.py` and `strata_ci.py`.

## §17. The graph beats BM25 — once the walk is seeded the way the field seeds it

§16 found the regime (questions whose words do not match their answer) but also
found the graph tied-to-harmful inside it. That was a property of the SEEDING,
not of the graph.

`personalized_pagerank` spread teleport mass uniformly over its seeds, so the
only way to seed widely was to seed badly: uniform mass over every node is not a
personalized walk, it is plain PageRank. The shipped caller compensated by
seeding narrowly — top 25 — which can re-rank what lexical search already found
and cannot reach past it. `seed_weights` (committed 9921206c) is the half of
HippoRAG 2's design Tesserae could not express.

Measured on Q1, the 71 lowest-overlap questions, R@10 against BM25's 0.740,
paired bootstrap 4,000 resamples:

| seeding | R@10 | gap vs BM25 |
|---|---|---|
| k=25, uniform (what shipped) | 0.733 | −0.007 [−0.065, +0.056] |
| **k=200, weighted** | **0.814** | **+0.075 [+0.019, +0.133]** |
| ALL nodes, weighted | 0.622 | −0.118 [−0.199, −0.042] |
| ALL nodes, uniform | 0.130 | −0.609 [−0.681, −0.539] |

**The two controls carry the argument.** Broad + uniform collapses to 0.130 —
the predicted degeneration to plain PageRank, and the reason "just seed
everything" is not the lesson. The same broad seeds, weighted, recover ~+0.49.
Breadth is worthless without the personalization; the personalization is what
makes breadth affordable. And breadth past ~300 stops paying regardless.

**It survives a held-out split**, which the lane-weight lever in §15 did not:

| seed k | 50 | 100 | 150 | 200 | 300 | 400 | 600 |
|---|---|---|---|---|---|---|---|
| gap | +0.062 | +0.078 | +0.070 | +0.075 | +0.063 | +0.031 | −0.008 |
| CI excludes 0 | yes | yes | yes | yes | yes | no | no |

Cross-validated gap **+0.063** against a full-set argmax of +0.078 — an optimism
gap of +0.015, versus §15's +0.037 which consumed its whole effect. A smooth
plateau from 50 to 300 with graceful decay past 400 is what a real effect looks
like; a spike at one k would not have been one.

`SEED_K` is therefore 150 — the CENTRE of the plateau, deliberately not the
k=100 argmax, because picking the argmax on the questions you score is the error
§15 records.

**This is the first time in this document that the graph beats a lexical
baseline on anything.** It is also narrow: the win lives in one stratum, and in
the high-overlap stratum the walk still costs −0.060. The honest claim is
conditional — structure pays where vocabulary fails, and should not be consulted
where it does not.

Does not license: enabling this on the product ask path. That path's PPR is
budget-capped at `_CONTEXT_BUDGET = 1_800` against 4,000-character bodies and
§12 measured it delivering exactly ONE node, so the seeding change cannot help
there until the budget is fixed too. Nor does it license a claim about
LongMemEval: this is the demo corpus, gold-DOCUMENT recall, n=71 in the stratum
that matters. Reproduce with `~/.blackhole/Tesserae/2026-08-21/broad_seed.py`
and `seed_k_cv.py`.

## §18. The bundle delivered one node because the first body ate the budget

§12 measured the ask path's `compile_context` bundle delivering exactly ONE node
in 31/31 runs, and in 28/31 that node's source document was already among the
ten the fusion lane had ranked — so the graph contributed essentially no text
the prompt did not already have. §17's better seeding could not reach a user
through that.

The cause is an arithmetic mismatch, not a design choice.
`SOURCE_EXCERPT_CHARS = 4_000` is the per-node body; `_CONTEXT_BUDGET = 1_800`
is the ask path's whole bundle. The first ranked body overflows the budget on
its own, hits the "always include the FIRST selectable node, truncating to fit"
branch, and breaks the walk.

**Raising the budget is the wrong fix.** 1,800 is sized so the assembled bundle
survives `_EVIDENCE_CLIP = 2_500` downstream; a larger bundle is computed, paid
for, and thrown away. Spending the SAME budget across several nodes costs no
extra prompt bytes — which matters, because §12 blames prompt VOLUME for the
52.9% fabrication rate.

Each node now claims at most `budget // _TARGET_BUNDLE_NODES` (5), and the raw
source substitution is skipped when that share falls under `_MIN_SOURCE_EXCERPT`
(900) — below which the first few hundred characters of a paper are title and
boilerplate, while the extracted description is dense and already about what
matched. Measured over the same 31 questions, budget unchanged:

| | before | after |
|---|---|---|
| nodes per bundle | 1 in 31/31 | min 5, **median 7**, max 14 |
| bundles adding a document the fusion top-10 missed | 3/31 (9.7%) | **19/31 (61.3%)** |
| chars used | ~1,800 | 1,698 (cap 1,800) |

A 6.3x rise in novel-document contribution at zero prompt cost.

**A floor was required, and finding it was the useful part.** The first version
capped every body unconditionally, which broke 10 multi-pool reservation tests.
Those tests run at `budget=400`, where a five-way split leaves 80 characters per
node — a sentence opening, not evidence. The tests were not stale: they were
right, and the change was wrong for tight budgets. Below `_MIN_NODE_SHARE`
(300) the original first-body-takes-what-it-needs behaviour stands. That keeps
`budget=400` byte-identical, leaves the default 32,000 path untouched (its share
exceeds `SOURCE_EXCERPT_CHARS`, so nothing is capped), and redistributes only in
the band the ask path actually occupies.

Does not license: any claim about answer quality. This measures what reaches the
prompt, not what the model does with it. Whether seven documents at 360
characters beat one at 1,800 for token F1 — or for fabrication, where more
distinct near-miss material is exactly the mechanism §12 blames — is unrun.
Reproduce with `~/.blackhole/Tesserae/2026-08-21/bundle_nodes.py`.
