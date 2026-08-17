# A headroom-bearing instrument for the self-improvement claim

**Date:** 2026-08-18
**Status:** scope, not yet built
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
