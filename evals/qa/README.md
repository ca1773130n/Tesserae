# QA benchmark

Three times now this repo has stood a competitor system up and driven it end to
end. Three times it produced no number. The runs worked fine; what was missing
every time was the other half, the part that checks whether the right answer came
back. This directory is that half.

The scorer is the artifact. Running it is a separate decision, and one that has
not been made yet: **no report has been generated, and none is committed here.**
`evals/federation/report.md` exists because that eval was run. This one ships a
`--score` command instead of a result.

## What it measures

Per system, and per stratum within each system:

| metric | what it catches |
|---|---|
| **exact match** | the answer, normalized (casefold, drop articles and punctuation) |
| **token F1, macro** | partial credit, averaged per question — the HotpotQA convention |
| **token F1, micro** | the same arithmetic over pooled token counts; diverges from macro when a system fails selectively on short or long answers |
| **refusal rate on answerable questions** | over-refusal |
| **hallucination rate on unanswerable questions** | a fluent answer to a question with no answer |
| **error rate** | queries that crashed, kept separate from both of the above |

Token F1 comes from `evals/metrics.py::prf1`, the same function
`evals/federation/run_eval.py` scores cross-project links with. That is
deliberate. Two evals in one repo, each with its own idea of what F1 means when
a denominator is zero, cannot be read against each other.

### The two rates only work as a pair

A system that answers "I don't know" to everything scores a perfect 0.0
hallucination rate. That number is worthless on its own, so `refusal_rate` on the
answerable half is always printed beside it, and neither is ever reported alone.
`tests/test_qa_scorer.py::test_a_system_that_refuses_everything_looks_perfect_on_hallucination_alone`
pins it.

### The null model is not optional

`evals/growth/probe_anchors.py` records what happens without one: three
candidate anchor matchers all scored 15/15 with both controls silent — and so
did a deliberately crude null model. Nothing in the eval could tell the graph
apart from vocabulary overlap.

The trap is worse here. HotpotQA is built from Wikipedia, which every frontier
model has memorised. "Tesserae answered 18 of 24" means nothing until you know
what the bare model answers with no corpus at all. So `evals/qa/null_model.py` is
a first-class system in the comparison, not a footnote to it.

It cannot see the corpus by construction: `insert_document` takes the document
and drops it, and there is no code path from a document to a prompt. A test
proves that with a marker token instead of by reading the code.

## What it does NOT measure

**Latency. Not at all, and no wall-clock number from this harness means
anything.**

The committed LightRAG store at `examples/demo-corpus/raganything-store/` ships
with `kv_store_llm_response_cache.json`, holding 307 cached responses. A fresh
Tesserae ingest carries none. Timing the two against each other measures how much
of one side's work was already paid for — a fact about this repository's git
history, not about either system. The report says so at the top. Do not delete
that line to make a slide.

Also not measured: ingest cost, memory, graph quality, multi-hop reasoning
depth, or anything about answers to questions outside the question set.
`evals/growth/` is where multi-hop connectivity is measured, and it is a
different instrument with different failure modes.

## Fairness constraints — all of these must hold before any number is published

The runner encodes these as `fairness_blockers()` and prints a refusal in §4 of
the report rather than a caveat. A blocked report is not a report with an
asterisk; it is a report you may not quote.

1. **The answering model must be identical across systems.** It currently is
   not. The committed LightRAG store was built on `gpt-5.4`
   (`examples/demo-corpus/scripts/seed_raganything_store.py:63`) while Tesserae
   defaults to `gpt-5.6-luna` (`tesserae/llm_json.py::CODEX_DEFAULT_MODEL`).
   Publishing across that gap compares two models and attributes the difference
   to retrieval. Re-seed the store on the same model first, or do not publish.

2. **The embedding backend must be identical, or the difference must be the
   thing under test.** It currently is not: the raganything store uses
   `all-MiniLM-L6-v2` at 384 dimensions
   (`examples/demo-corpus/scripts/seed_raganything_store.py:64-68`), Tesserae
   resolves to model2vec `minishlab/potion-base-8M` at 256
   (`tesserae/retrieval/hybrid.py::Model2VecBackend`). Recall differences across
   that gap are not attributable to the graph.

3. **Same corpus, same questions, same answer-shape instruction.** Token F1
   against a one-word gold answer collapses if one system replies in prose, so
   every system must be asked for the same answer shape.
   `evals/qa/null_model.py::NULL_SYSTEM_PROMPT` is the reference wording.

4. **Every declaration must be recorded.** A missing declaration is treated as a
   blocker, not as agreement — "we did not write down which model answered" and
   "the models matched" are different facts, and only one of them is checkable.
   The baseline is exempt from the embedding checks (having no retrieval is what
   it is for) and stays subject to the model check.

## What the shipped question set actually contains

Worth knowing before reading a stratum table:

- `evals/cognee/evals/src/hotpot_qa_24_qa_pairs.json` is **24 questions, every
  one labelled `level: "hard"`**. There is exactly one stratum. Per-stratum
  reporting is built and tested, but on this set it has nothing to separate.
- The set contains **no unanswerable questions**, so the refusal and
  hallucination metrics cannot be exercised by it at all. `evals/qa/unanswerable.json`
  adds 12 probes in two kinds:
  - `unanswerable-absent` — real entities (Bhutan, Chernobyl, CRISPR) with zero
    occurrences in the 240-document corpus. A grounded system should say so; a
    base model will answer from memory. That asymmetry is the point, and it is
    why the null model's hallucination rate on this stratum is expected to be
    high without that being a defect.
  - `unanswerable-fictitious` — invented entities with no correct answer
    anywhere. Every system, baseline included, should refuse.
  A test re-verifies each probe's absence against the corpus rather than
  trusting the claim.
- 24 questions is a small set. Two systems whose macro F1 rounds the same at
  four decimals are reported as tied, because they are.

## Running it

Nothing here runs by itself, and nothing here ingests. Three guards, in order:

1. `CI` set in the environment → `SKIP`, exit 0, before any file is read.
2. A missing prerequisite → `SKIP: <what>` plus the command that fixes it, exit
   0. The model is `evals/growth/probe_anchors.py`: an eval that exits non-zero
   on a missing optional input gets wired into CI by someone making the build
   green, and then it runs.
3. `--answer` refuses without `--i-know-this-costs-money`. No default path
   reaches an LLM.

```bash
# Score answers that already exist. No LLM, no network, no corpus.
uv run python -m evals.qa.run_qa_eval --score answers/*.json --out evals/qa/report.md

# Phase 1 — stage the corpus. Writes files. Compiles nothing.
uv run python -m evals.qa.run_qa_eval --system tesserae --stage-only \
    --project ~/.blackhole/Tesserae/$(date +%F)/qa-run

# Phase 2 — after YOU have compiled. Costs LLM quota.
uv run python -m evals.qa.run_qa_eval --system tesserae --answer \
    --project ~/.blackhole/Tesserae/$(date +%F)/qa-run \
    --answers-out answers/tesserae.json --i-know-this-costs-money

# The baseline, on the same questions, with no corpus.
uv run python -m evals.qa.run_qa_eval --system null \
    --answer --answers-out answers/null.json --i-know-this-costs-money
```

The compile between phase 1 and phase 2 is a command you type. For the
competitors, `insert_document` *is* ingestion — one call, one document, in
process. For Tesserae it is an LLM extraction pass over the whole corpus that
takes hours and rewrites `graph.json`, so the harness stages the documents and
stops. Never point `--project` at the repository root: a compile there
overwrites the project's real graph with the benchmark's, the same footgun
`evals/growth/run.py` warns about.

## Layout

| file | what it is |
|---|---|
| `scorer.py` | the metrics. No dependency on `tesserae`, on the vendored clone, or on a network — it has to be testable or it ships unmeasured |
| `run_qa_eval.py` | staging, answering, scoring, and the report |
| `benchmark_tesserae.py` | the `QABenchmarkRAG` subclass — thin on purpose |
| `null_model.py` | the base model with no corpus |
| `vendor_base.py` | loads the ABC out of the gitignored cognee clone by file path |
| `unanswerable.json` | 12 refusal probes, each verified absent from the corpus |
| `../metrics.py` | `prf1`, shared with the federation eval |

The ABC comes from `evals/cognee/evals/src/qa/qa_benchmark_base.py`, which
already drives cognee, graphiti, mem0 and lightrag and assumes none of them.
That clone is gitignored, 877 MB, and carries uncommitted local work — read it,
never modify it. Everything here degrades to `SKIP` when it is absent, and the
scorer's tests run either way.
