# Proving the architecture: lifelong learning and self-improving memory

**Date:** 2026-08-17
**Status:** design, not yet run
**Goal:** evidence that Tesserae's architecture is better than alternatives at
*accumulating domain knowledge over time* and at *automatically self-improving
each expert memory* — not that it retrieves well once.

---

## 1. The claim, stated so it can fail

> A Tesserae memory improves **without new input**, and improves **faster with
> new input**, than memories that only store and retrieve.

Two halves, and they need different instruments:

| half | claim | falsified by |
|---|---|---|
| **Self-improvement** | On a FROZEN corpus, quality rises across consolidation cycles | Δ ≈ 0 across cycles |
| **Accumulation** | As documents arrive, quality rises faster and plateaus higher than baselines | Baseline curve matches or beats ours |

The first is the load-bearing one. Any system with more documents answers more
questions, so an accumulation curve alone proves nothing about architecture. A
curve that rises **while the corpus stands still** is a property only a memory
with a consolidation loop can have.

## 2. Why the instrument we have cannot test it

`evals/lme_mab/` (`Accurate_Retrieval`) is **static**: one fixed haystack, one
shot, no time axis. A system that consolidates scores identically to one that
dumps text. It measures retrieval, which is a *precondition* of the claim, not
the claim. Its BM25 0.843 / Dense 0.473 result stands on its own terms and says
nothing about lifelong learning.

`evals/growth/` has the right shape — cumulative chronological slices, 15
multi-hop questions, controls that must stay 0, grounding checked separately
from node existence so hollow nodes cannot inflate the curve — but **no
baselines**. It shows our curve rises. It cannot show it rises *more*.

## 3. Experiment 1 — the self-improvement curve (build first)

**Design.** Compile a corpus once. Freeze it. Measure. Run consolidation
cycles that ingest **zero** new documents. Measure again, same questions.

```
   compile ──► T0 ──► [consolidate] ──► T1 ──► [consolidate] ──► T2 ...
                              no new documents at any point
```

**Arms**

| arm | mechanism available on a frozen corpus | expected |
|---|---|---|
| Tesserae | distill, LRU decay, **associate**, summarize, brief | rises |
| BM25 | none | **flat by construction** |
| Dense (model2vec) | none | **flat by construction** |

The baselines being flat is the *result*, not a weakness of the comparison. They
have no mechanism to improve without new input; that asymmetry is the
architecture claim made visible.

**Headline metric (free, repeatable).** Reuse `evals/growth/run.py::evaluate`'s
`answerable`: both anchors resolve to *grounded* nodes AND a path links them AND
the path is evidence-supported. `associate` (op 3) discovers new edges, which is
exactly what moves this number on a frozen corpus.

For the baselines, which have no notion of a path, score the same questions as
*joint evidence coverage*: does the top-K contain documents supporting both
anchors? Same K for every arm.

**Secondary metric (costed, run twice).** `evals/qa/`'s EM + token-F1 at T0 and
at the final cycle only, to confirm retrieval gains translate into answer gains
rather than merely reshuffling the index.

**Report.** Δ per cycle per arm, with LLM calls spent per cycle beside it, so
the gain is priced rather than asserted.

### ⚠ The trap that would fake a null result

`associate` writes discovered edges to a **sidecar overlay** under `.tesserae`,
*never* into `graph.json` (`docs/engine-consolidation.md` §3). The merge happens
in memory at read time, via `tesserae/memory/associate.py::load_overlay_edges` /
`apply_overlay`.

`evals/growth/run.py::load_graph` reads `graph.json` directly.

**Run unmodified, this experiment measures exactly zero improvement and appears
to disprove the central claim.** Any harness here must load the graph *with the
overlay merged*, and a test must assert that a graph with overlay edges differs
from one without — otherwise a silent regression in overlay loading looks like
"consolidation does nothing."

## 4. Experiment 2 — the accumulation curve, with baselines

Extend `evals/growth/` rather than duplicating it: it already owns the slice
machinery, the questions, the anchors, and the controls.

Add BM25 and Dense arms scored at **each slice** on the same joint-coverage
metric. Report all three curves on one axis. The claim is shape, not level:
ours should rise faster per document and plateau higher.

Keep the existing invariant — **`controls fired` must stay 0**. The controls ask
questions the corpus cannot answer; if a path appears, the checker is finding
spurious connections and every number is suspect. That guard applies to the
baseline arms too.

## 5. Experiment 3 — external comparability

`ai-hyz/MemoryAgentBench` has four competencies. We downloaded one.

| split | measures | have it? |
|---|---|---|
| `Accurate_Retrieval` | static retrieval | ✅ used by `evals/lme_mab/` |
| **`Test_Time_Learning`** | **learning from experience** | ❌ **the one that matches this claim** |
| `Long_Range_Understanding` | synthesis across a long history | ❌ |
| `Conflict_Resolution` | superseding stale knowledge | ❌ — maps onto our `merged_into` / supersede path |

`Test_Time_Learning` is the published instrument for the user's claim, so a
number on it is comparable to what others report. `Conflict_Resolution` is the
second-best fit and exercises machinery we already shipped (#141–#152).

Both still inherit the blocked controls documented in
`evals/lme_mab/README.md`: the protocol fixes `text-embedding-3-small` and
`gpt-4o-mini`, neither reachable on codex OAuth. So these produce *our* numbers
under a self-consistent local protocol, quotable as ours and not beside anyone
else's published table — the same honesty constraint `fairness_blockers()` and
`NOT_COMPARABLE` already enforce.

## 6. Sequencing and cost

| # | experiment | spend | why this order |
|---|---|---|---|
| 1 | self-improvement curve | one scratch compile (~90 min on demo-corpus, less with a warm `~/.tesserae/llm_cache`) + N consolidation cycles | cheapest, strongest mechanism claim, no competitor can draw the chart |
| 2 | growth + baselines | reuses experiment 1's compiles across slices | the accumulation half, on machinery that exists |
| 3 | MAB `Test_Time_Learning` | Tesserae arm compiles per group (~102 extraction calls/group, ~509 for five) | external comparability, most expensive |

Every compile lands in a scratch project **outside the repo**; `guard_work_dir`
refuses anything else. **No experiment here compiles this project.**

## 7. What may NOT be claimed from any of this

- Anything about latency. None of these metrics score it, and the codex
  per-call overhead (15,090 tokens on a trivial `exec`) would dominate if they
  did.
- Any comparison to Mem0's, HippoRAG's or SegTreeMem's published figures. The
  protocol's judge and embedder are unavailable here; see
  `evals/lme_mab/README.md` and `tests/test_docs_comparative_claims.py`, which
  fails the build on exactly this class of claim.
- That Tesserae passes Neo4j's TCK. It reaches **no tier** (57/93 Bronze), for
  deliberate reasons documented in `evals/tck/README.md`.

What may be claimed, if the numbers land: that a Tesserae memory measurably
improves on a corpus that is not changing, and that two standard retrieval
baselines provably cannot.
