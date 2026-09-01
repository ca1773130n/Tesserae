# Handoff — Mem0 benchmarking, the LLM provider rewrite, and where the edge actually is

Written 2026-09-02, at the end of a long session that shipped three releases and
ran six head-to-head benchmarks against Mem0. Read the first two sections before
touching anything; the rest is reference.

---

## 1. Read this first

**Every retrieval, packing and answer-quality comparison against Mem0 came back
parity.** Six benchmarks, three corpora, two judges. Not one produced a
significant win. If you are about to optimise retrieval or packing to beat Mem0,
stop and read §4 — that ground is measured out.

**The user's standing goal** is to beat Mem0 *by far* on answering quality,
provenance and hallucination, with answer rate treated as evidence that the
memory relates concepts rather than merely retrieves them. Parity is not
acceptable to them, and they are right that it is not a differentiator.

**The user's standing constraints:**

- No paid API runs until Tesserae can be shown *way* better. Broken once,
  deliberately, on 2026-09-02: they authorised spend for question generation.
  There are **no API keys on this machine** — the Claude and Codex CLIs are
  authenticated and run on subscription quota instead.
- Never compile without explicit say-so. The 2026-09-01 corpus compile was
  authorised.
- Never signal a process this session did not start. Never `pkill`/`killall`.
  (Violated once with `pkill -P` — do not repeat.)
- `evals/` holds 877 MB of gitignored clones with unrecoverable local work.
  Never clean it.

---

## 2. What shipped

Three releases, all live on PyPI and npm, all verified from a clean install.

| version | what |
| --- | --- |
| **v0.37.0** | `TESSERAE_EMBEDDING_PREFER` (`model2vec`/`st`/`openai`/`hash`) and `TESSERAE_ST_MODEL` (default `BAAI/bge-base-en-v1.5`). Auto ladder unchanged, so `pip install tesserae` stays torch-free. |
| **v0.37.1** | Custom LLM providers work at all. `llm_api_style` (`anthropic`\|`openai`) names the WIRE, so an OpenAI-compatible endpoint is reachable; `llm_auth_token` is the bearer credential; an explicit endpoint provider is a contract that fails loudly instead of degrading; one documented precedence for every `llm_*` key; `project_llm_settings()` so `ask`/`query`/lint/MCP/daemon see the project config. |
| **v0.37.2** | Three call sites that built their own Anthropic SDK client with no `base_url` (`query.py`, `llm_synthesis.py`, `synthesis.py`); the liveness message now names the classified cause; `config status` reports the provider's true source layer; `setup` stopped reading two config keys nothing writes. |

PRs #250–#261. The provider work came from a 30-agent audit that confirmed **23
defects**; the three worst are worth remembering because they were invisible from
the outside:

1. `provider="custom"` always built the *Anthropic* client, so every
   OpenAI-compatible server (vLLM, LiteLLM, OpenRouter, Ollama, LM Studio) got a
   404. The one OpenAI-protocol client in the tree was constructed by nothing and
   hardcoded `api.openai.com`.
2. The configured model was scoped against whichever config *layer* supplied the
   provider, so setting provider by flag and model in a file silently discarded
   the model — then the chain fell through to the Claude CLI, spawned with
   `--model sonnet` against the user's own base URL. That is the "wrong model"
   error the user originally reported, and nothing named the real cause.
3. One `llm_api_key` was sent as `X-Api-Key` by the SDK and as a bearer token by
   the CLI, and passing it suppressed the SDK's own auth-token resolution — so a
   bearer-only gateway was unreachable.

---

## 3. Frozen configuration — do not re-tune

The user froze this on 2026-08-31. Treat it as the baseline any change must not
regress; see `best-configuration-frozen` in agent memory.

| area | setting |
| --- | --- |
| session corpora (LoCoMo, LongMemEval) | `document_first=True` + fan-out, shipped `model2vec` |
| large document corpora | fused 1/1/1 + document dedupe + `TESSERAE_EMBEDDING_PREFER=st`; **not** `document_first` |
| packing | graph-selected documents as SOURCE PROSE, ≥2,500 chars/document (`_MIN_SOURCE_EXCERPT`) |
| answer review | `TESSERAE_VERIFY_BAND=on` |
| reranker | off — +3 MRR, −1.4 recall, ~600× the cost of fan-out |

---

## 4. Every benchmark result, with the number that matters

### 4.1 Document recall vs Mem0 — parity once the encoder is equal

148 papers, 57 questions, distinct-document gold recall.

| arm | @10 | @50 |
| --- | --- | --- |
| Mem0 OSS raw chunks, nomic-embed-text | 0.784 | 0.942 |
| Mem0 OSS raw chunks, **same bge-base** | 0.775 | 0.944 |
| Tesserae, shipped model2vec | 0.754 | 0.914 |
| **Tesserae, bge-base in the dense lane** | **0.791** | **0.962** |

Paired with the equal encoder: 8 wins, 8 losses, 41 ties at ten documents (sign
p=1.0); 6/3/48 at fifty (p=0.51). **The embedder was the whole gap. The graph
adds nothing measurable to document recall.**

### 4.2 The +28.9% source-prose result — judge-sensitive, and it reverses

The one historical win (graph-selected documents packed as source prose beating a
BM25 passage control, gpt-4o-mini judge, 8/8 replicates, p=0.0078) does not
survive scrutiny:

- Re-run with a local qwen2.5:7b judge: **+7%, p=0.56, not significant**.
- On a second corpus (this project's own docs, 24 hand-written questions):
  **loses by 17–26%**.

The README now carries all three numbers. Quote the +28.9% only with "gpt-4o-mini
judge; +7% n.s. under a local 7B judge; loses on a second corpus."

### 4.3 Document QA head-to-head — parity at equal budget

100 questions, 13,000-char budget, local judge, Mem0 on the same bge encoder.

| arm | hallucinated | over-refused |
| --- | --- | --- |
| Mem0 | 0.133 | 0.500 |
| Tesserae, claim-anchored packing | 0.117 | 0.675 |

45 of 57 questions tie in an earlier variant of this run. The instrument
saturates: a 7B answerer writes the same ~2,600-character answer from better
evidence.

### 4.4 Answered-rate — we lose, and the metric had to be fixed twice

| metric | Mem0 | Tesserae |
| --- | --- | --- |
| asserted a figure | 20/40 | 13/40 |
| figure in a record naming the BENCHMARK | 8 | 7 |
| figure in a record naming BENCHMARK **and SYSTEM** | **5** | **3** |

Precision 0.25 vs 0.23. **We are behind on correct answers.** Every apparent lead
vanished as the metric tightened. Ceiling is 26/40 — 14 questions have no record
anywhere attributing a figure on that benchmark to that system.

Mem0's higher answer rate is not better memory: its evidence carries **2.3× more
numeric records** (51.7 vs 22.5 per question), so the model always finds
something number-shaped to assert, and 75% of what it asserts is wrong. Ours is
wrong 77% of the time it asserts. **Assertion rate tracks the density of
grabbable numbers, not the presence of the right one.**

### 4.5 The verifier does not catch misattribution

`check_against_evidence` flagged **0 of 3** hallucinations (0 false alarms). Not
broken — answering the wrong question. It asks "is this text grounded in the
evidence"; a confabulated figure is a REAL figure lifted from another system's
row, so it is grounded and passes. It detects fabrication-from-nothing, never
misattribution-from-real-evidence, which is the failure mode that actually
occurs.

### 4.6 The misattribution checker — the one thing that works

Built 2026-09-01, at `~/.blackhole/Tesserae/2026-08-31/hallucination/agents/misattribution.py`.
Textual co-location at RECORD granularity: pull figures from the answer, find
where each occurs in the evidence, check whose record it sits in.

- **14/15 hallucinations caught across both arms** (8/8 on *Mem0's* answers,
  6/7 on ours), 4 false alarms in 33 answered-true.
- Incumbent coverage check on the same rows: 0/7.
- Audited: verdicts are byte-identical with the graph absent, and a subject swap
  with answer and evidence frozen flips the verdict in both directions.

**Not shipped.** Unresolved: it is a *general* RAG checker — it improves Mem0's
answers too, so it is not by itself a Tesserae differentiator.

**Discarded deliberately:** `verify_claim("evaluated_on")` separated true from
false premises 60/60 and 0/40 — perfect, and perfectly circular, because the
question set was built from those very edges. Never claim it.

### 4.7 The contamination-free reasoning benchmark — parity

The benchmark the user actually asked for. Harness:
`~/.blackhole/Tesserae/2026-09-01/recent/`.

- **11 arXiv papers submitted 2026-08-22..26** — eight days old, past every
  model's cutoff, so memorisation cannot produce a correct answer.
- **12 questions**, each kept only after a **leave-one-paper-out ablation**:
  best single paper **0.444**, pair **1.000**. 13 candidates discarded precisely
  because one paper sufficed.
- Both arms answered and judged by the Claude CLI; Mem0 on raw chunks with
  bge-base; identical 13,000-char budget.

| metric | Mem0 | Tesserae | paired |
| --- | --- | --- | --- |
| answered | 0.833 | **1.000** | 12/12 vs 10/12 |
| quality | 0.722 | 0.750 | +0.028, 5W/3L/4T, **p=0.73** |
| provenance | **0.750** | 0.708 | −0.042, 3W/2L/7T, **p=1.00** |
| unsupported claims/answer | **2.08** | 2.33 | +0.250 worse, 3W/7L/2T, **p=0.34** |

**Parity on every metric.** The finding underneath the ranking: **both systems
average ~2 unsupported claims per answer.** Neither is reliable at cross-paper
reasoning on unseen papers.

---

## 5. Measurement traps — every one of these bit during this session

Read these before designing a benchmark. Each cost hours.

1. **A significant result that is worthless.** First hallucination run:
   Tesserae 0.000 vs Mem0 0.133, **p=0.008** — produced by refusing 39 of 40
   answerable questions. *Never report a hallucination rate without an
   over-refusal control on the same question set.*
2. **Scoring that shares the blind spot you are testing for.** A "grounded"
   scorer that accepted a figure from any record naming the benchmark showed
   7-vs-8; requiring the record to also name the SYSTEM turned it into 3-vs-5.
3. **Optimising a proxy that is not the target.** Benchmark-NAME coverage was
   raised 29→36 of 40 and the answered rate *fell* 13→10, because mentions crowd
   out the passages carrying numbers. The right proxy was name **and figure in the
   same record** (25→33 of 40).
4. **A lexical checker cannot score a packing strategy.** `check_against_evidence`
   coverage favours BM25 by construction; it rewards wording overlap, which is
   what BM25 optimises.
5. **Circular ground truth.** Questions built from `evaluated_on` edges make
   `verify_claim("evaluated_on")` a perfect oracle. Ground truth must come from
   raw source text, never from the graph the arms retrieve from.
6. **Question sets need mechanical validation before use.** Four filtering rounds
   were needed on one set (named benchmarks only; systems mentioned ≥5× in their
   own paper; balanced brackets; corpus-wide co-occurrence).
7. **A saturated judge cannot resolve anything.** A local 7B tied 45 of 57
   questions. Use a judge that discriminates, or the run is wasted.

---

## 6. Operational gotchas — local model servers and this machine

The machine is a 16 GB Mac that runs with ~13 GB of swap in use, often from
processes that are not ours.

- **`TESSERAE_EXTRACT_CONCURRENCY=1` is mandatory for a single-slot local server.**
  The default is 4; Ollama serves one at a time, so three requests queue behind
  each call, and a dropped queued request blocks the client for the entire
  `TESSERAE_EXTRACT_TIMEOUT` (which was 7200s in this environment). This cost
  hours and looked exactly like a memory problem. **This is a real product gap —
  see §7.**
- **A fraction of chunks make a local model emit nothing, at any timeout.** The
  timeout therefore sets the *price of each hang*, not quality. 300s produced 11
  documents in ~5 h; 1800s produced one document per hour. Short caps win.
- **`llama3.2:3b` is non-terminating on ~25% of chunks** with Tesserae's
  extraction schema — not slow, stuck. Not a usable extractor.
- **The LLM cache is keyed by (prompt, model)**, so switching models sets work
  aside rather than destroying it; switching back recovers it.
- **Check `memory_pressure` before starting anything heavy**, and run one job at
  a time. Two concurrent corpus builds took the machine to 6% free and the OS
  killed both.
- Progress signals that mislead: "documents touched" in the log only counts
  documents that emitted a notice; cache-file counts are lumpy under concurrency;
  a queued request's reported latency is mostly queue wait. **Use Tesserae's own
  `N/M` document counter.**

---

## 7. Open product gaps

1. **Extraction concurrency vs single-slot servers.** Default 4 is right for a
   rate-limited cloud API and wrong for Ollama or a single-GPU vLLM — exactly the
   configuration v0.37.1/v0.37.2 made work. Detect a local `base_url`, or default
   to 1 when `llm_api_style=openai` and the host is loopback, or document it
   loudly. **Highest-value small fix in the repo right now.**
2. **`within_doc_pack` / figure-record packing** (in
   `~/.blackhole/Tesserae/2026-08-30/packing/anchored.py`) raises figure-in-record
   coverage 25→33 of 40 against a ceiling of 34, in less text than the shipped
   packer. Unshipped because it did not convert to a significant answer gain.
3. **The misattribution checker** (§4.6). Works, audited, unshipped.
4. `context_compiler.py:1185` still packs `_raw[:cap]` — the top of each document
   — ignoring where the matched node actually sits.

---

## 8. Next direction — where an edge could actually exist

Everything measured so far is parity because **both systems hand ~13,000
characters of text to the same model, and the model is the bottleneck.** Any
design that ends in "we assemble a slightly better context window" will keep
returning p≈0.5. Three directions have a structural reason to differ.

### 8.1 The lexically-disjoint second hop (highest value)

**The hypothesis.** A vector store can only retrieve what is similar to the
query. A graph can retrieve what is *connected* to what is similar to the query.
Every benchmark run so far accidentally avoided testing this: the 12 cross-paper
questions used papers that share technical vocabulary, so Mem0's embeddings found
both hops anyway — which is exactly why provenance came out 0.750 vs 0.708.

**The design.** Build questions where the second document is reachable **only via
a graph edge**, not via query similarity:

- Pick a fact in document A that the question asks about.
- Require a fact from document B that is needed for the answer but shares
  **no distinctive vocabulary with the question** — the bridge is an entity or
  claim that A mentions and B elaborates.
- Verify mechanically: BM25 and dense retrieval over the query must both fail to
  return B in the top-k. If either finds B, discard the question — the same
  discipline as the leave-one-paper-out ablation.

**Why it should separate.** Mem0 has no path to B. Tesserae's
`evaluated_on`/`addresses`/`instantiates` edges do. If it does not separate, the
graph's edges genuinely add nothing to retrieval and that is decisive knowledge.

**Caution from memory:** `graph-expansion-is-a-measured-null` records that hubs
make depth-2 reach everything, so a BFS-reachability arm needs a shuffle control
and a budget-matched baseline. Build both in from the start.

### 8.2 Scale, because corpus size is the known discriminator

`graph-wins-only-at-scale` records that the graph beat BM25 on 148 papers and
lost on 19 LoCoMo sessions. Every recent benchmark ran on **11 papers** because
the machine could not compile more. At 11 documents a vector store has nothing to
get confused by.

Get a corpus of 500–1,000 documents compiled on a machine that can do it, then
re-run §8.1. This is the single largest confound in every number in §4.

### 8.3 Verification with provenance, made non-general

The misattribution checker works but helps Mem0 too. The version Mem0 *cannot*
have is one that answers **"which source supports this, and is that source
authoritative for this claim?"** — which needs typed edges and document identity,
not just text co-location. Concretely: a claim-level verdict that cites the node
and the document, refuses when the graph has no path, and is deterministic. That
is `verify_claim` plus the misattribution insight, and it is the only asset in
this repo with no competitor equivalent.

Benchmark it on a corpus containing **contradictions** — papers that disagree —
where the correct behaviour is to report the disagreement with sources rather
than pick one. A chunk store cannot do this; it has no notion of a claim or a
source's standing.

### 8.4 What not to do again

- Do not tune retrieval or packing for document QA against Mem0 (§4.1, §4.3, §4.4).
- Do not build a benchmark on a corpus old enough to be in pretraining.
- Do not use "answered" or "asserted" as a quality metric; it rewards guessing.
- Do not ship a checker or packer on a p≈0.3 result.

---

## 9. Where everything lives

| what | path |
| --- | --- |
| recent-papers benchmark (corpus, questions, graph, results) | `~/.blackhole/Tesserae/2026-09-01/recent/` |
| 45 fetched papers, full text | `~/.blackhole/Tesserae/2026-09-01/recent/corpus_all/` |
| packers: within-document, claim-anchored, figure-record | `~/.blackhole/Tesserae/2026-08-30/packing/anchored.py` |
| misattribution checker + audit | `~/.blackhole/Tesserae/2026-08-31/hallucination/agents/` |
| false-premise question set and results | `~/.blackhole/Tesserae/2026-08-31/hallucination/` |
| Mem0 harnesses (raw-chunk store, evidence export) | `~/.blackhole/Tesserae/2026-08-30/mem0-h2h/` |
| node-vector cache (479 MB, ~19 h to rebuild — keep) | `~/.blackhole/Tesserae/2026-08-29/vectors.sqlite` |

Agent memory notes to read before starting: `benchmark-standings`,
`mem0-head-to-head-verdict`, `answered-rate-vs-mem0-not-beaten`,
`verifier-does-not-catch-misattribution`, `recent-papers-reasoning-benchmark`,
`best-configuration-frozen`, `no-spend-way-better-2026-08-30`,
`graph-wins-only-at-scale`, `check-the-benchmark-before-optimising`.
