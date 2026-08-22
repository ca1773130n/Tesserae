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


## Where this arm stands against the published numbers, and why that is not a ranking

Measured 2026-08-23 on **conv-26 only** — 152 gradeable questions, 2 replicates,
`gpt-5.6-luna` answering, `sonnet` judging. The competitor column is the
LLM-as-a-Judge (`J`) table from the Mem0 paper (arXiv:2504.19413), whose runs
answer with `gpt-4o-mini` and judge with `gpt-4o-mini` over all 1,540 gradeable
questions.

| category | this arm | Mem0 | Mem0-graph | Zep | best RAG |
| --- | --- | --- | --- | --- | --- |
| single-hop (4) | 89.3 | 67.1 | ~65.7 | ~8% under Mem0 | |
| open-domain (3) | 76.9 | 72.9 | 75.7 | 76.6 | |
| temporal (2) | 62.2 | 55.5 | 58.1 | | |
| multi-hop (1) | 42.2 | 51.2 | under Mem0 | | |
| overall | 71.7 | 66.9 | 68.4 | | ~61 |

Full-context — every token of the conversation in the prompt, no memory system
at all — scores ~73 in the same table, above every memory system including this
one.

**The overall column is not a result and must not be quoted as one.** Three
things differ before the architectures do, and the first is almost certainly
worth more than all the rest together:

1. **The backbone.** This arm answers with `gpt-5.6-luna`; every published
   number above answers with `gpt-4o-mini`. A stronger answering model lifts
   every category, and nothing here separates that lift from the memory's
   contribution. The +4.8 overall could be entirely this.
2. **The denominator.** 152 questions from one conversation against 1,540 from
   ten. conv-26's category mix is slightly HARDER than the full set (more
   multi-hop and temporal, less single-hop); applying these same per-category
   accuracies to the full-dataset mix gives 74.2, so the denominator is not
   what is flattering this arm.
3. **The judge.** `sonnet` against `gpt-4o-mini`, and the published runs average
   10 replicates where this one averages 2 (spread 0.013).

**What the table does support is a statement about SHAPE, and it is not
flattering.** This is the most lopsided profile in it: best single-hop by 22
points, worst multi-hop by 9. A system whose thesis is a typed graph that
reasons across sessions is losing on exactly the questions that require
reasoning across sessions, and winning on the ones an inverted index wins.
Single-hop is where raw source text in the lexical lanes helped most, and
multi-hop is where the graph was supposed to. Only one of those happened.

That is the number to move, and reranking will not move it: a cross-encoder
reorders candidates for ONE query, and a multi-hop question needs two documents
that no single query ranks together.


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

A provider chain handed a model it does not have returns `None`. The runner used
to turn `None` into `""`, and `is_refusal("")` is `True`, so a wholly dead
backbone printed `refusal_rate 1.000` with `error_rate 0.000` — a system that
read as cautious rather than broken.

`answer_conversation` now asks an empty reply again once, and records the second
one as `Error:` (`_EMPTY_ANSWER`), so a wholly dead backbone reads as
`error_rate 1.000` instead. **That is a second line of defence and not a
replacement for the canary.** It was added for the partial case rather than the
total one: `gpt-5.6-luna` returned the empty string on 66 of 398 answering calls
of the 2026-08-22 conv-26 run (16.6%, against 5.5% on 2026-08-21 at a prompt
two-thirds the size), and every one was counted as the memory choosing to
abstain. A lost call is not a decision. The canary is still what stops a run
before it spends, because a backbone can also be confidently wrong.

Every row persists its own `empty_replies`, and the run's meta sums them into
`evidence.empty_replies` beside an `answer_calls` that counts retries. The retry
bounds the provider's flakiness; it does not hide it.

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

# reorder the retrieved candidates with a cross-encoder before answering
uv sync --extra rerank    # torch + transformers, 558 MB, not a normal install
uv run python -m evals.locomo.run --conversations conv-26 --reuse-compile \
    --rerank Qwen/Qwen3-Reranker-0.6B
```

`--rerank` is a SECOND ranking stage, not a different retriever. The lanes still
run over the whole graph and still decide what is a candidate; the cross-encoder
reads each candidate together with the question and reorders it. So it can only
move a document the lanes already found — **a reranked run's recall ceiling is
the recall of `--rerank-overfetch` times the budget**, and if the answering
session is not in that set, reranking cannot put it there.

It exists because rank-1 is where this benchmark is lost: measured on conv-26 the
fused ranking is 0.051 MRR behind a BM25-over-whole-documents reference by rank
10 but 0.107 behind at rank 1 — good recall, bad ordering, which is the one
shape a cross-encoder fixes. Every run records `rerank_model` and
`rerank_overfetch` in its meta, and `""`/`0` means the shipped fused ranking
answered.

Never inside the repository: `guard_work_dir` refuses any path under this
checkout or holding a `pyproject.toml`, because a compile there would overwrite
this project's own `.tesserae/graph.json`.

`--reuse-compile` measures against a graph an earlier run built. It verifies
that every document this conversation would stage is already on disk byte for
byte AND that the compiled graph indexes them, then writes nothing. Both halves
are needed: `ingest` restages before compiling, so a directory can hold one
conversation's fresh documents beside another's graph.


## The other axis: tokens to a correct answer

`run.py` measures rank. `run_context.py` measures **tokens**, and the two are
different questions. At the scale this project is aimed at, retrieving every
detail is not an option and pasting documents into a prompt is not an option, so
the binding constraint is not "did you rank the right document first" but "can
you hand a model enough understanding, in few enough tokens, to answer".

**BM25 is the retriever, not the rival.** It ranks in two of the three arms;
there is no recall@k table between it and Tesserae in that report, deliberately.

| arm | what fills the budget |
| --- | --- |
| `bm25_docs` | BM25 ranks; whole session documents, best first, until the budget is spent. The incumbent. |
| `bm25_compiled` | BM25 finds the region; its sessions seed `compile_context`, which compiles background from the graph. |
| `graph_only` | no document retrieval at all — `compile_context` from the question alone. |
| `closed_book` | no evidence. The floor, and the meter for the refusal free lunch. |
| `whole_corpus` | every staged session, unbudgeted. The ceiling. |

Every arm is handed the same token budget and fits its own context into it with
its own knob — documents for the first, `compile_context`'s character budget
scanned over a declared grid for the next two. Truncation is a counted, printed
column, because a fixed budget measures truncation skill unless it is visible.

Tokens are counted over the **complete serialized request** — system prompt,
JSON contract, schema name and user turn, exactly the string
`llm_json._stitch_json_prompt` sends — with a digest-pinned Qwen3 BPE. Counting
only the evidence would leave instructions and few-shot examples free to move
into the system half. Character budgets are not a substitute: chars-per-token
varies about 35% across this corpus's own artifact families.

**Every prompt is built, counted and written to disk before any of them is
sent.** `--score` then re-derives the whole report from that file offline, which
is what makes a token claim auditable rather than a number that has to be
re-spent to check.

```bash
# FREE. Builds every request at every rung, counts it, writes prompts.jsonl.
uv run python -m evals.locomo.run_context --dry-run \
    --work ~/.blackhole/Tesserae/2026-08-21/locomo-run/work \
    --conversations conv-26 --budgets 512,2048,8192

# the measurement: one canary, then one call per prompt per replicate
uv run python -m evals.locomo.run_context \
    --work ~/.blackhole/Tesserae/2026-08-21/locomo-run/work \
    --conversations conv-26 --budgets 512,2048,8192 \
    --arms bm25_docs,bm25_compiled,graph_only,closed_book,whole_corpus \
    --backbone gpt-5.6-luna --replicates 3 --i-know-this-costs-money --yes

# re-score without re-spending
uv run python -m evals.locomo.run_context --score answers-context.json
```

`run_context.py` **never compiles**. It reads a corpus and a graph an earlier
run staged, and refuses when either is missing.

**What conv-26 cannot show.** Its whole staged corpus serialises to 19,906
tokens under this tokenizer, measured — it fits in any current context window.
That makes `whole_corpus` a legal arm and makes conv-26 able to calibrate the
instrument but not to demonstrate the at-scale claim. If the ceiling arm
dominates the ladder, that falsifies this corpus as evidence, not the claim, and
the report prints that sentence whether or not it is convenient.


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
