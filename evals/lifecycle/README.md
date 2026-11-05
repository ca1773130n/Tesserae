# Lifecycle eval — extraction, update and end to end, scored apart

Roadmap item R1. The hallucination metric this replaces agreed with itself 35%
of the time on re-run, so nothing it said could gate a change. This harness
scores the three places a memory can go wrong separately, on public gold data,
and re-runs every stage to find out whether its number is a measurement.

| Stage | System under test | Gold | Headline numbers |
|---|---|---|---|
| `extraction` | `session_graph_llm.extract_with_llm` over one HaluMem session | HaluMem memory points | recall / omission, interference, ungrounded and fabrication rates, false-memory resistance |
| `update` | `memory/supersede.run_supersede_pass` (+ what `memory/contradiction` detects) over gold memories | HaluMem `is_update` / `original_memories`; FactConsolidation (silver pairs) | correct, hallucination (reversed), omission, candidate recall, spurious edges, wrongly hidden nodes |
| `e2e` | extract → supersede → answer, graded by LongMemEval's rubric | LongMemEval knowledge-update + abstention (102 questions) | accuracy per subset, `lifecycle` arm beside the `raw` control |
| `fc-qa` | the supersede pass as a filter before answering | FactConsolidation answers | substring-match accuracy, `lifecycle` beside `raw` |

Module docstrings carry each stage's exact definitions; read them before quoting
a number.

## Test-retest: the acceptance rule

Every invocation runs its stage `--runs` times (default 3) with the CLI response
cache off and a fresh supersede verdict cache, so a re-run reaches the model
again rather than replaying the first run. Per-item labels across runs give
Cohen's kappa for every pair of runs; per-unit rates give Spearman's rho.

- **min pairwise kappa ≥ 0.8** — the stage may serve as a gate.
- **min pairwise kappa < 0.6** — rejected as a gate.
- in between — diagnostic only. One run — `insufficient-runs`.

A deterministic arm (the update stage without `--llm`) agrees with itself by
construction, and the result file says so; its kappa checks the harness, not a
judge.

Failed LLM calls push kappa the wrong way: an update verdict that fails falls
back to the deterministic arbiter, which agrees with itself. So when more than
2% of an invocation's LLM calls fail or are refused over budget, the result's
`verdict` is `contaminated` whatever the kappa says, and every failure is
counted in `notes`. Extraction sessions and e2e questions with any failed
extraction call are excluded and listed rather than scored: missing memory reads
as omission, and on an abstention question as a correct "I don't know".

## Data

Pinned to a Hugging Face revision and the file's LFS sha256 (see
`datasets.SOURCES`), cached outside the repository at
`~/.cache/tesserae/evals/lifecycle` (`TESSERAE_EVAL_CACHE` overrides). A cache,
work or results directory inside the repo is refused.

| Name | Upstream | Size |
|---|---|---|
| `halumem-medium` | `IAAR-Shanghai/HaluMem` `HaluMem-Medium.jsonl` | 33.5 MB |
| `halumem-long` | `IAAR-Shanghai/HaluMem` `HaluMem-Long.jsonl` | 106.5 MB |
| `factconsolidation` | `ai-hyz/MemoryAgentBench` `Conflict_Resolution` parquet | 1.5 MB |
| `lme-oracle` | `xiaowu0162/longmemeval-cleaned` `longmemeval_oracle.json` | 15.4 MB |
| `lme-s` | `xiaowu0162/longmemeval-cleaned` `longmemeval_s_cleaned.json` | 277 MB (loading it takes a few GB of RAM) |

HaluMem is CC-BY-NC-ND-4.0: fine to evaluate against, not to redistribute.
FactConsolidation reads parquet, so those commands need `uv run --with pyarrow`
(the same optional dependency `evals/lme_mab` uses).

## Commands

    uv run python -m evals.lifecycle.run fetch halumem-medium factconsolidation lme-oracle

    # free: deterministic supersede
    uv run python -m evals.lifecycle.run update --dataset halumem-medium
    uv run --with pyarrow python -m evals.lifecycle.run update --dataset factconsolidation \
        --fc-source factconsolidation_sh_6k --fc-source factconsolidation_sh_32k

    # spends — every LLM stage needs --i-know-this-costs-money; --max-llm-calls caps all roles
    uv run python -m evals.lifecycle.run update --dataset halumem-medium --llm haiku --users 2 \
        --max-llm-calls 500 --i-know-this-costs-money
    uv run python -m evals.lifecycle.run extraction --llm haiku --judge llm:sonnet --sample 50 \
        --runs 3 --i-know-this-costs-money
    uv run python -m evals.lifecycle.run e2e --backbone haiku --judge sonnet --memory raw \
        --runs 3 --i-know-this-costs-money
    uv run python -m evals.lifecycle.run e2e --backbone haiku --judge sonnet --memory lifecycle \
        --runs 3 --i-know-this-costs-money
    uv run --with pyarrow python -m evals.lifecycle.run fc-qa --backbone haiku \
        --fc-source factconsolidation_mh_6k --runs 3 --i-know-this-costs-money

Cost, roughly: extraction is `runs × sessions × (chunks + 1)` calls (a median
HaluMem session is 44 turns, 2 chunks); e2e on `oracle` is `runs × 102 × (2 +
sessions)` for `lifecycle` and `runs × 102 × 2` for `raw`; fc-qa is `runs × 100`.
An LLM-arbitrated update pass asks once per candidate pair, and HaluMem users
have hundreds — always pass `--max-llm-calls`. A run that hits the cap says so
in `notes`: refused calls are failures (extraction, e2e) or deterministic
fallbacks (update), so that result mixes arms and measures neither. Runs longer
than ~15 minutes belong in a detached session (`Popen(start_new_session=True)`),
not a background shell job.

`CI` set → every verb prints SKIP and exits 0. CI runs `tests/test_lifecycle_eval.py`.

## Output

One JSON per invocation in `~/.tesserae/evals/lifecycle/`
(`TESSERAE_LIFECYCLE_RESULTS_DIR` overrides), named
`<UTC stamp>-lifecycle-<stage>-<dataset>-<arm>.<token>.json`, written atomically
and never over an existing file. It carries the dataset revision and sha256, the
protocol (models, client identity, rubric source, sample), the per-run metrics
and labels, the mean summary, the agreement verdict, the overall `verdict` and
the LLM call tally. The MCP `doctor_report` tool returns the newest result per
stage, dataset and arm as `lifecycle_eval`, with its notes and failure tally,
parsing only the newest file of each series.

## What these numbers are not

- HaluMem's own extraction metrics are judged against the dialogue as well as
  the gold list. Here `fabrication_rate` counts any finding that matches no gold
  point, so it is an upper bound — a true but unlisted fact counts against the
  extractor. Do not set it beside MemOS's self-reported numbers without saying so.
- FactConsolidation ships no conflict pairs. `datasets.conflict_pairs` derives
  them (161 in the 6k context); they are silver labels.
- HaluMem gold pairs whose "newer" point is not later in session order (an
  update that restates an earlier point) are set aside as
  `inverted_gold_set_aside`, not scored: no recency rule can order them. 6 of
  HaluMem-Medium's 3,775.
- The update stage feeds gold memories, not extracted ones, so it measures the
  supersede pass alone. The e2e stage is where extraction and update errors
  compound.
- HaluMem is persona chat and LongMemEval is assistant chat. Neither is a coding
  or research session, which is what Tesserae ingests. A gain here is evidence,
  not proof, for the sessions Tesserae actually sees.

## Self-capture

The harness calls the LLM through `system=` prompts whose openings are listed in
`tesserae.harness_sessions._TESSERAE_PROMPT_SIGNATURES` (asserted by
`tests/test_lifecycle_eval.py`), and moves its working directory to
`--work-dir` (outside the repo) before the first call. A CLI transcript of an
eval call is therefore filed under a directory no project owns and, if it is
found anyway, dropped by signature.
