# LoCoMo — the benchmark everyone quotes, measured with its denominators showing

LoCoMo is the most-quoted benchmark in agent memory. The numbers attached to it
do not agree with each other, and the reasons they disagree are almost never
printed beside them:

| claim | protocol | denominator |
| --- | --- | --- |
| Mem0 92.5 (self-reported) | `gpt-5` answers and judges, top-k 200, partial credit, 14-day date tolerance | 1,540 |
| Letta ~83 (highest independently verified) | LLM judge | 1,540 |
| Zep 94.7 | a different model and setup | not comparable to either |
| the paper's own table | token F1, no LLM judge at all | 1,986 |

Three of those four numbers exclude the same 446 questions — the adversarial
category — because 444 of them ship with no gold answer. That is 22.5% of the
benchmark, and it is not a random 22.5%: it is the class where the paper's own
Table 2 shows long-context models scoring worst. **A LoCoMo number without its
denominator is not a result.** This package's job is to make it impossible to
print one from here.

Everything below that is a count was measured this build, by the code in this
package, against `snap-research/locomo` at `data/locomo10.json`.


## The data, measured

```
10 conversations   272 sessions   5,882 turns   1,986 QA pairs
categories         {1: 282, 2: 321, 3: 96, 4: 841, 5: 446}
per conversation   19-32 sessions, 369-689 turns, 105-260 questions
turns with a BLIP caption                       1,226 of 5,882
evidence elements                               2,815
  malformed (not exactly one D<n>:<t>)          6
  of those, unreadable at all                   2
  ids naming a turn that does not exist         2
questions with empty evidence                   4
questions with no gold session resolved         4
questions whose gold spans >1 session           332
```

The category integers are 1 = multi-hop, 2 = temporal, 3 = open-domain,
4 = single-hop, 5 = adversarial. That map is read off the reference harness's
own branch comments in `task_eval/evaluation.py`, not inferred from the counts —
two categories of similar size would otherwise be interchangeable.


## Four things that are decided here, once, and recorded on every artifact

**The corpus is dialogue text plus every BLIP caption, and nothing else.** The
reference code contains two paths that disagree: one gates captions on a key
this release of the data does not carry and counts zero, the other counts all
1,226. `img_url`, `query`, `observation`, `session_summary` and `event_summary`
are never ingested — `query` in particular is the annotator's image search
string, and a gold answer reachable only through it is a gold answer no memory
system can legitimately retrieve.

**One project per conversation.** Speaker names repeat across the ten
conversations. A pooled corpus lets a question about one conversation retrieve
another's turns about a different person of the same name, and nothing in a
reported number would show it.

**The staging unit is the session**, one markdown document each — measured, a
staged document is 3,553 characters on average and 7,275 at most, against
57,807-116,077 for a whole conversation staged as one file — **all ten** over
the compiler's 48,000-character chunk budget, which would split them and destroy
the `source_path` provenance every retrieval score here is computed from.

**K is a reported SET, fixed in code before any result existed:** 1, 2, 3, 5,
10. A conversation holds 19 to 32 sessions, so K=10 is more than half the corpus
on the smallest of them — a uniformly random ranker scores recall@10 ≈ 0.53
there. Every row prints its random floor beside it, and MRR is the headline
because it is the metric a large K cannot inflate.


## The judge is a boundary

`evals/locomo/judge.py` defines one interface with two implementations:

* `DeterministicJudge` — exact match and token F1 through `evals/qa/scorer.py`,
  plus the abstention rule for the adversarial category. No model, no network,
  no cost, exact reproduction. This is what runs today.
* `LLMJudge(model)` — the published Protocol-B grader prompt, verbatim, against
  any named model at temperature 0.

Adding `gpt-4o-mini` when there is credit is `--judge llm:gpt-4o-mini`. It is a
flag, not a rewrite, and nothing above the boundary names a grader.

Both judges have a **canary** that grades a right answer and a wrong one and
refuses if either comes back wrong. A judge stuck on one label produces a
complete, plausible, meaningless report.


## The backbone canary is mandatory, and here is why

A provider chain handed a model it does not have returns `None`. The runner
turns `None` into `""`. `is_refusal("")` is `True`. So a wholly dead backbone
prints `refusal_rate 1.000` with `error_rate 0.000` — a system that reads as
cautious rather than broken.

On LoCoMo that is worse than elsewhere: **on the 446 adversarial questions,
declining is the gold answer**, so a dead backbone scores 446 of 446 there and
produces this project's best-looking headline. `run.py` answers one planted
question before any measured pass and aborts with exit code 2 if the answer does
not contain the planted token.


## Three numbers or none

Every report prints all three of

1. every scorable question, with a refusal scoring zero — the number the field
   quotes;
2. the subset **every** arm answered — the like-for-like comparison;
3. each arm's refusal and error counts — the thing that separates the two;

or it prints none of them and names the one it could not compute. This is not
caution for its own sake. Measured elsewhere in this repository: a +0.077
headline gap was 72% one arm refusing and scoring zero, and its like-for-like
gap was +0.021; a +0.0906 gap on another corpus decomposed 99.3% answer-rate and
0.7% quality. Neither was visible in the number that got quoted.

The adversarial category is scored in its own section, never merged into a
refusal rate, and always beside the answerable result.


## Replicates, and what needs them

Retrieval scoring needs none: it reads no model and reproduces byte for byte —
re-running the BM25 arm on conv-26 produces an identical report. Answer scoring
needs them: an identical generative configuration in this repository has swung
0.043 token F1 between two runs. `--replicates 3` matches the published
protocol, which grades three times and reports the mean and the population
standard deviation across whole-run accuracies.

A single replicate reports **no** spread rather than `0.0`. The spread of one
number is unmeasured, and printing zero claims a reproducibility the run did not
observe.


## Running it

```bash
# what would it cost? counts documents, calls and questions — never dollars
uv run python -m evals.locomo.run --conversations conv-26

# stage the corpus and stop: no compile, no LLM, no network
uv run python -m evals.locomo.run --conversations conv-26 --stage-only \
    --work ~/.blackhole/Tesserae/locomo/work

# the free half: recall@{1,2,3,5,10} and MRR against the random floor
uv run python -m evals.locomo.run --conversations conv-26 \
    --arms bm25,dense --retrieval-only

# re-grade saved answers with a different judge — offline for the deterministic one
uv run python -m evals.locomo.run --score answers.json --judge llm:gpt-4o-mini
```

Never inside the repository: `guard_work_dir` refuses any path under this
checkout or holding a `pyproject.toml`, because a compile there would overwrite
this project's own `.tesserae/graph.json`.

`--reuse-compile` measures against a graph an earlier run built. It verifies
that every document this conversation would stage is already on disk byte for
byte AND that the compiled graph indexes them, then writes nothing. Both halves
are needed: `ingest` restages before compiling, so a directory can hold one
conversation's fresh documents beside another's graph.


## The controls are checked, and today they fail

`adapter.PROTOCOL_CONTROLS` declares six controls onto every artifact — backbone,
judge, judge runs, dataset revision, embedder, evidence budget — and
`protocol_blockers` refuses a published-comparable table when any is unmet.

Two of them are marked `UNFIXED`: the published protocols let every compared
system bring its own retriever and its own evidence budget (one of them
retrieves 200 memories per question), so there is no value to match. They must
still be **declared** — a run nobody wrote down is a run nobody can reproduce —
but they cannot be compared against a constant, and inventing one would invent a
control the field never agreed on.

Declarations are claims, so evidence is required on top: a run must record
`answer_calls`, `llm_judge_calls` and `canary_calls` greater than zero. A
deterministic run records `llm_judge_calls: 0` and is blocked, which is correct —
declaring a judge model is not judging.

**As of this build, `judge`, `judge_runs`, `llm_model`, `llm_judge_calls`,
`answer_calls` and `canary_calls` are all unmet on this machine, so §7 of every
report is withheld.** That refusal is the feature. No published baseline's own
number is reproduced anywhere in this repository, and none of the figures in the
table at the top of this file was measured here.
