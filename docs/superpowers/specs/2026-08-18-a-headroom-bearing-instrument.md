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
