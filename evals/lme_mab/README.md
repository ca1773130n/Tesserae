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

## Not built yet

The ingest/query adapter. Ingest must land a group's dialogue in a scratch
project OUTSIDE this repo and compile there — `evals/growth/run.py` already
enforces that boundary (`refusing to compile inside the repo — that overwrites
.tesserae/graph.json`) and this must do the same. Query must return exactly
K=10 evidence items via `hybrid_search`.
