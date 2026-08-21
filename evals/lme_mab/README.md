# LongMemEval-MAB — comparing the memory system, not the model

The point of this harness is a number that can sit in a table next to numbers
somebody else published. That only works if everything except the memory system
is held still, so the protocol below is copied from the paper we would be
compared against, not chosen by us.

**Reference protocol.** *Temporal Order Matters for Agentic Memory: Segment
Trees for Memory Construction and Retrieval* — arXiv:2606.04555, 3 Jun 2026
(Vector Institute / University of Toronto), §5.2–5.3.

| Control | Value it fixes | Why it cannot drift |
|---|---|---|
| Backbone | `gpt-5.4-mini` for memory construction **and** answer generation | A different model measures the model |
| Embeddings | `text-embedding-3-small`, all retrieval and similarity | A different embedder makes recall gaps unattributable to the architecture |
| Judge | `gpt-4o-mini` | A different judge rescales every score |
| Evidence budget | **K = 10**, identical for all methods | More context is a bigger answer, not a better memory |
| Metrics | LLM-judge accuracy, token-level F1 | — |

**Baselines already published under exactly that protocol**, so we run only our
own side: BM25, Dense, RAPTOR, MemTree, A-MEM, Mem0, HippoRAG, and the paper's
own SegTreeMem (best, ~20% over the strongest external baseline).

This repo holds **none** of those published numbers. The BM25 and Dense arms in
§6 below are ours, measured under our own protocol; they share a name with two
of the published baselines and nothing else.

## The data, measured rather than assumed

`ai-hyz/MemoryAgentBench`, split `Accurate_Retrieval`, records whose
`metadata.source` is `longmemeval_s*`:

| group | context | questions |
|---|---|---|
| 0 | 1,600,183 chars (~400k tok) | 60 |
| 1 | 1,589,693 (~397k) | 60 |
| 2 | 1,715,268 (~429k) | 60 |
| 3 | 1,588,305 (~397k) | 60 |
| 4 | 1,646,919 (~412k) | 60 |
| **total** | **8,140,368 chars ≈ 2.04M tokens** | **300** |

MemoryAgentBench builds memory ONCE per group and queries it repeatedly. Note
the group size: LongMemEval_S is ~115k tokens per *instance*, but MAB
concatenates instances into shared haystacks, so a group is ~400k. Budgeting off
the per-instance figure under-counts the ingest by about 3.5x — this was
measured off the parquet after an estimate got it wrong in exactly that way.

## What it costs us

Only our own side runs. Roughly, at ~4k tokens of dialogue per extraction call:

| phase | via codex CLI | via OpenAI API |
|---|---|---|
| Ingest 2.04M tok (~509 calls) | ~10.2M tok | ~2.5M tok |
| 300 queries | ~5.4M tok | ~0.9M tok |
| **total** | **~15.6M tokens** | **~3.4M tokens** |

The gap is not the data, it is the harness: a trivial `codex exec` call was
measured at **15,090 tokens** with a stripped `CODEX_HOME` (16,969 with the
normal profile — so skills and `AGENTS.md` account for under 2k of it). At 809
calls that fixed overhead is ~78% of the codex column. It changes cost and
latency, not answers, and this protocol scores accuracy and F1 rather than
latency — so it is a quota decision, not a validity one. That would NOT hold on
LongMemEval-V2, whose LAFS metric scores latency directly.

## Blockers before any number is quotable

Two of the four controls cannot be met on this machine today:

1. **`text-embedding-3-small` needs an OpenAI API key.** Tesserae's default is
   model2vec `potion-base-8M` (256d) against the protocol's 1536d. Running ours
   varies the embedder and the memory architecture at once.
   `OpenAIEmbeddingBackend` (`prefer="openai"`) exists for this and is
   deliberately unreachable from the `auto` path, since it bills per call.
2. **`gpt-4o-mini` needs an OpenAI API key.** codex refuses it outright: *"The
   'gpt-4o-mini' model is not supported when using Codex with a ChatGPT
   account."*

`gpt-5.4-mini` — the expensive control, and the one that actually decides the
answers — **does** work through codex, confirmed. So the split is favourable:
generation runs on the subscription, and the two blocked controls are the cheap
models.

Until both are available, a run here produces an internal number and **not** one
comparable to the published baselines. Say that plainly if anyone quotes it.

## The adapter

`adapter.py` is the memory system under test; `run.py` drives it.

    # what would it cost? prints the estimate and stops
    uv run python -m evals.lme_mab.run --parquet <Accurate_Retrieval.parquet>

    # ONE group first — 60 questions, a fifth of the bill. The intended first run.
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 \
        --work ~/.blackhole/Tesserae/lme-mab/work --i-know-this-costs-money --yes

    # write the session documents and stop: no compile, no LLM, no network
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 --stage-only

    # the two baselines only: recall@10 and MRR of the gold session.
    # No cost banner, no consent flag, no API key, no LLM, no money — and no
    # network: the dense arm loads its local model with the Hugging Face hub
    # switched off (`HF_HUB_OFFLINE`), because `StaticModel.from_pretrained`
    # otherwise contacts huggingface.co on every construction even when every
    # file is already cached — measured with a spy on `socket.getaddrinfo`.
    # A cold cache is a refusal naming the one-time warm-up command, never a
    # download in the middle of a benchmark.
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 \
        --arms bm25,dense --retrieval-only

    # re-measure all three arms against a group ALREADY compiled in --work.
    # A compile is ~an hour of 13 concurrent workers per group, so a retrieval
    # change is measured this way rather than by paying it again. The flag
    # writes nothing: it proves every document this group would stage is
    # already there byte for byte and refuses otherwise, because a graph
    # compiled from other text answers about a haystack these questions were
    # never asked about — and would print a number while doing it.
    uv run python -m evals.lme_mab.run --parquet <p> --groups 0 \
        --work <already-compiled> --arms tesserae,bm25,dense \
        --retrieval-only --reuse-compile --i-know-this-costs-money --yes

`--arms` takes a comma list of `tesserae`, `bm25`, `dense` and defaults to
`tesserae`. One predicate — is `tesserae` among them — gates every money layer:
the cost banner, the consent flag, the typed confirmation and the answering
backbone. A list without it reads the parquet, ranks `Session.render()` in
memory and writes the report. The CI guard has **no** carve-out: `CI` set skips
every arm, because a benchmark that runs in CI under one set of flags is one
edit away from running under all of them.

The `OPENAI_API_KEY` check is gated on something narrower — `--embedding-prefer
openai`, the one value that bills. That flag defaults to the local backend so §6
can hold ONE embedder still across all three arms, and a gate keyed to the arm
rather than to the embedder demanded a credential the default run never touches,
refusing exactly the self-consistent local comparison §6 exists to print.

Ingest lands a group's dialogue in a scratch project OUTSIDE this repo and
compiles there. `guard_work_dir` refuses anything else, using the sentence
`evals/growth/run.py` already uses: `refusing to compile inside the repo — that
overwrites .tesserae/graph.json`. It raises where growth calls `sys.exit`, which
is the only reason a test can execute it at all.

Query returns exactly K=10 evidence items via `hybrid_search`. When the graph
holds fewer it records a shortfall and returns short. It never pads.

**An evidence item is not the same size on the two paths, and that is
deliberate.** `MabHit.text` — what retrieval scoring and `--retrieval-only`
see — is the node's own `name — description — source: path`, measured at mean
234 characters over group 0's 600 hits. Answering reads
`MabMemory.answer_evidence` instead, which appends the first
`EVIDENCE_SOURCE_CHARS` (2,400) of a hit's own session file when — and only
when — the hit is the node that *is* that session. Before that, the backbone
read 1.7% of the text the retriever had already scored to rank it, which is the
substitution arXiv:2410.10813 §5.2 measures as a loss. Two gates, both
load-bearing: node **type** cannot tell an anchor from the 103 nodes that merely
inherited a transcript's path, so identity with the document's own H1 does it;
and a session's text goes to the first hit that stands for it, because 17
sessions carry two such nodes and 11 of the 60 questions retrieve both. The cap
is *not* `hybrid.SOURCE_LEXICAL_CHARS` — ranking and answering are different
questions and `adapter.EVIDENCE_SOURCE_CHARS` derives its own. Retrieval is
untouched by any of it: `recall@10 0.8197 / MRR 0.7068` on group 0 before and
after, to four decimals, over identical rankings.

**The retrieval unit is a session, not a turn window.** ~112 documents per
group rather than ~1,290. K=10 is a fixed control, so the unit size is one too:
ten sessions and ten turns are different amounts of evidence, and picking the
smaller unit would move the score for a reason that is not the memory
architecture. The full argument is in `adapter.py`'s module docstring.

## §6, the retrieval comparison — ours, not the paper's

The report's §6 puts Tesserae, BM25 and Dense in one table on **recall@K and MRR
of the gold session**. Dropping answer accuracy for retrieval accuracy drops the
LLM judge, which is the one control this machine cannot meet at all, so the
three rows can be measured here honestly. What it does not do is make them
quotable next to anybody's published LongMemEval numbers, and the report says so
in a paragraph printed **above** the table rather than below it — a screenshot of
a table has to carry its own caveat. That string is
`retrieval.NOT_COMPARABLE`; every consumer imports it and nobody restates it.

What is held still across the three rows:

| held still | value |
|---|---|
| corpus | one list of `Session.render()` documents — the exact bytes a Tesserae run stages |
| budget | K = 10 for every arm |
| embedder | the repo's own local `model2vec:minishlab/potion-base-8M`, asked for **by name**, by every arm |
| gold | one `align_gold` result per group, shared by all three arms |

`prefer="model2vec"` and never `"auto"`: `auto` degrades to a hash-bucket stub
on a `UserWarning` nobody sees in a benchmark run, and the stub produces
plausible-looking numbers that measure a hash function. The dense arm checks the
resolved backend's `name` on every use and refuses otherwise.

**The embedder row is enforced, not asserted.** `--embedding-prefer` used to
default to `openai`, so the Tesserae arm resolved `text-embedding-3-small` while
the dense arm resolved model2vec, and the table printed two embedders directly
under a sentence claiming one — a sentence whose stated reason for not being
quotable was that the embedder in the table *is not* `text-embedding-3-small`,
which the same table then printed. The flag now defaults to the local embedder,
and `retrieval.embedder_refusal` withholds §6 entirely — naming both arms and
what each resolved — when the rows do not share it. A caveat that is true
because the code enforces it beats a caveat that is true because it was
reworded.

An arm that refuses is not the end of the run. The dense arm's model may not be
in this machine's cache; BM25 has by then scored every question, and those
numbers are kept. §6 names the missing arm **above** its table, for the same
reason the caveat goes there: a crop showing two rows where three arms were
asked for reads as a comparison that ran.

Neither baseline returns a document its lane scored at or below zero — a BM25
zero is no shared term, a non-positive cosine is no similarity — so an arm that
matched nothing comes back empty rather than filling K on tie-break order. The
shortfall is recorded and the metric never pads it.

Tesserae's row says **lower bound** in its own method cell, not only in the
footnote under the table — the cell is the part that travels with the number
into a screenshot or a paste, and the footnote is the part that gets cropped
off. It is a lower bound twice over: a retrieved node carries one
`source_path`, and `canonicalization.merge_node_group` keeps the canonical
node's when it collapses a concept extracted from many sessions, so some gold
sessions are unreachable through provenance however well the memory retrieved;
and K hits de-duplicate to fewer than K documents when several nodes come from
one session. Topping that list back up to ten distinct sessions would hand this
arm a bigger budget than the baselines got, so it is left short and the footnote
says why.

Which is why the §6 footnote counts questions that returned fewer than K
**distinct documents** and does not call them shortfalls. That count is not
`MabMemory.shortfalls`: the arm's own record counts a *search* that matched
fewer than K nodes, while §6 counts documents after de-duplication, and for
Tesserae that fires on nearly every question with nothing wrong. A lane that
matched nothing lands in the same count, so it is read beside §5 and never on
its own.

`--k` is checked before anything is read. `K < 1` is refused by name —
`min(|G|, 0)` is a division by zero and `[:-1]` quietly drops the last document
off every ranking and prints a negative rate — and the refusal arrives as a
`SKIP` on the flag rather than as a traceback after the 20MB parquet load.

### What the parquet actually holds — measured, and not what was assumed

The dialogue is in the file **twice**, and the two copies disagree:

| view | shape | dates? |
|---|---|---|
| `context` | `repr` of a flat list alternating `'Chat Time: 2022/11/17 (Thu) 12:04'` with a list of turn dicts | **yes** |
| `metadata.haystack_sessions` | already parsed: per question → sessions → `{role, content, has_answer}` | **no** |

The two views agree on the dialogue — the haystack's turn text is 96.7% of the
context's characters, the rest being the `repr` scaffolding and the date headers
— and the adapter splits on `context`, because there is no `haystack_dates`
field anywhere in this parquet and `temporal-reasoning` is one of the question
types being scored.

#### Correction: they do not align positionally, and this file used to say they did

An earlier version of this section said the flattened `haystack_sessions` holds
exactly as many sessions as `context` has `Chat Time:` markers, "111 / 107 / 116
/ 112 / 113 for groups 0-4". Both halves of that are wrong, measured through
`adapter.split_sessions` and `retrieval.session_signature` on the real parquet:

| group | `split_sessions(context)` | flattened haystack | dup. signatures | haystack matched | gold sessions | gold matched | questions with ≥1 gold |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | 111 | 111 | 0 | 111 | 105 | **105** | 60/60 |
| 1 | 107 | 107 | 0 | 107 | 105 | **105** | 59/60 |
| 2 | 116 | 116 | 0 | 116 | 109 | **109** | 58/60 |
| 3 | **111** | **112** | 0 | 112 | 102 | **102** | 57/60 |
| 4 | **110** | **113** | 0 | 112 | 93 | **93** | 56/60 |

1. **The counts disagree for groups 3 and 4.** `haystack_sessions` is stored per
   *question* — 1-6 sessions each — and a session that answers two questions is
   listed twice, so flattening counts occurrences and not sessions.
2. **The order disagrees for every group, including the three where the counts
   match.** `flatten(haystack)[0]` and `split_sessions(context)[0]` are
   different conversations in group 0: a Delta SkyMiles redemption against a
   résumé rewrite. Any offset or cumulative-index bridge (`sessions[off + s]`)
   therefore mis-attributes gold in **every** group, silently, while printing a
   number that looks right.

So the bridge is the turn text itself. `retrieval.session_signature` is
`sha1("|".join(whitespace-normalised turn contents))`, and matching on it
resolves **514 of 514** gold sessions across all five groups with **zero**
duplicate signatures. Exactly one non-gold haystack occurrence, in group 4,
matches nothing; it is counted as `n_unmatched`, never guessed at. An unmatched
occurrence that is *gold* refuses instead of being counted, because that one
moves the answer key: a question with two golds, one of them unfindable, would
print recall 1.000 where the truth is 0.500, and a question whose only gold went
missing would fall into `n_no_gold` and leave the mean — dropping exactly the
question the arms were most likely to have missed. On the measured data the
refusal never fires. Ten of the
300 questions carry no gold session at all (0/1/2/3/4 per group) and are
excluded from recall and MRR rather than scored zero — you cannot score
retrieval of a gold that does not exist.

`metadata` also carries `question_types` — `multi-session`,
`knowledge-update`, `temporal-reasoning`, `single-session-user`,
`single-session-assistant`, `single-session-preference` — which the report uses
as strata. An aggregate that hides which KIND of question failed says very
little about a memory system.

`has_answer` marks the gold evidence turn. It is staged into no document and
read on no retrieval path; a corpus carrying it would let retrieval key on
"this is the answer" and score the leak.

### The controls are checked, and today they fail

`adapter.protocol_blockers` compares the run's declarations against the four
fixed values above. `evals/qa/scorer.py::fairness_blockers` does not fit — it
asks whether two runs *in this repo* declared the same thing, and the question
here is whether ONE run matches a constant from somebody else's paper, with no
second report to diff against. Until the two blocked controls are available, §3
of the report withholds its quotable table and names the control that is
missing. Today that is the judge, every time.
