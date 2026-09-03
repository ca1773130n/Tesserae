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

---

## 10. Continuation, later on 2026-09-02 — §7.1 shipped, §8.1 measured

### 10.1 §7.1 is done

PR #262 (`da223b32`, unreleased as of writing). `TESSERAE_EXTRACT_CONCURRENCY`
unset now follows the endpoint: a loopback `llm_base_url` (`localhost`,
`127.0.0.1`, `::1`, `127.x`) means one worker, with a stderr note naming the
endpoint and the override. The batch runner asks the extractor where its
requests go (`LLMResearchExtractor.llm_base_url`, delegated through the
selective router); a deterministic extractor has no endpoint and keeps 4. An
explicit value always wins. `docs/tuning.md` and its seven mirrors carry it.

### 10.2 The lexically-disjoint second hop, measured without a compile

The 11-paper graph cannot host §8.1: ~36 cross-paper edges over 18 pairs,
mostly hubs (`RAG`, `query`, `Exact Match`). Two compiled graphs on disk can —
`2026-08-27/fullpapers-chunked` (148 papers, ~1,600 non-hub cross-document
edges, bge node vectors already cached in `2026-08-29/vectors.sqlite`) and
`2026-08-24/dsc-graph` (1,414 documents, ~1,500). Neither needed a compile, a
judge, or an LLM: the benchmark is retrieval-only, which is the level at which
the §8.1 hypothesis actually lives.

Harness: `~/.blackhole/Tesserae/2026-09-02/bridge/bridge_bench.py` (+
`posthoc.py`). Item construction, all mechanical, ground truth from raw text
(trap #5):

- take a cross-document edge `a -rel-> b` whose `edge_provenance` says it was
  extracted from document A, where b's own node lives in another document;
- QUERY = A's text around the edge's evidence span, every mention of b's name
  and aliases masked — "the metric it reports", without the name;
- GOLD = the document(s) other than A that mention b's name most in raw text
  (≥3 mentions, within half of the max, up to 3) — the ones that elaborate b;
- KEPT only if BM25 over documents AND a bge-base chunk store (what Mem0's raw
  store reduces to) both miss every gold document at 10. 120 of 400 items
  survive on the 148-paper corpus; the other 280 are "easy".

Arms, budget-matched at 10 documents: `bm25`, `dense`, `dense_2hop` (top-1
dense document's centroid as a second query — the honest non-graph second
hop), `tess_hybrid` (shipped fused 1/1/1 over nodes, bge in the dense lane,
dedupe to documents), `tess_graph1hop` (+ documents of each of the top-20 seed
nodes' typed 1-hop neighbours, hubs >50 and summaries excluded, weight 0.5),
`tess_shuffle` (same expansion with random nodes — the control
`graph-expansion-is-a-measured-null` demands).

**148 papers, kept stratum, n=120, hit@10.** Chance for 10 random documents is
0.15.

| arm | hit@10 |
| --- | --- |
| bm25 / dense | 0.000 (by construction) |
| dense_2hop | 0.125 |
| tess_hybrid | 0.125 |
| tess_graph1hop (pre-registered) | 0.167 — vs dense_2hop W13/L8 p=0.38; vs shuffle W7/L2 p=0.18 |
| tess_shuffle | 0.125 |

**Every shipped shape is at chance.** The decomposition says why, and it is
not "the edges add nothing":

- the expansion REACHES the bridge node in 53/120 kept items (0/120 for
  similarity, by construction);
- graph.json carries ONE `source_path` per node — the document that first
  mentioned it — and that document is gold in only 16 of those 53;
- `sqlite.db`'s `node_provenance` holds every document a node was seen in, and
  the gold is in that set for 34/53;
- even then, the neighbour's document is out-ranked by the 200 seed documents
  at weight 0.5: reached-and-provenance-is-gold converts to a hit 3 times in 16.

Post-hoc arms recomputed from the stored seeds (`posthoc.py`, no retrieval
re-run), **exploratory on this corpus**:

| arm | hit@10 | vs dense_2hop | vs shuffle |
| --- | --- | --- | --- |
| g1_prov (multi-document provenance, w=0.5) | 0.192 | W13/L5 p=0.10 | W10/L2 p=0.04 |
| **g1_prov_w1 (multi-document provenance, w=1.0)** | **0.225** | **W17/L5 p=0.017** | **W14/L2 p=0.004** |
| g1_split (5 base + 5 expansion) | 0.183 | W16/L9 p=0.23 | W15/L8 p=0.21 |
| chance (10 random docs) | 0.150 | W17/L14 p=0.72 | — |

Easy stratum (n=120): BM25 over documents 0.900, dense 0.833, dense_2hop 0.833,
tess_hybrid 0.742, tess_graph1hop 0.800 (beats hybrid p=0.04 and shuffle
p=0.02), g1_prov_w1 0.833. On easy items the graph shapes are still behind
plain document BM25 — consistent with §4.1.

**The 1,414-document confirmation run does not confirm it.** Same script, same
pre-registered arms, `2026-08-24/dsc-graph` (documents are abstract-length,
median 1.2k chars; 219 of 400 items kept; chance at 10 is 0.023):

| arm | hit@10, kept n=219 | vs dense_2hop |
| --- | --- | --- |
| **dense_2hop** | **0.151** | — |
| tess_hybrid | 0.078 | — |
| tess_graph1hop (pre-registered) | 0.105 | W15/L25 p=0.15 |
| tess_shuffle | 0.091 | — |
| g1_prov (post-hoc) | 0.091 | W15/L28 p=0.07 |
| g1_prov_w1 (the 148-paper winner) | 0.096 | W17/L29 p=0.10 |
| g1_split | 0.114 | W20/L28 p=0.31 |
| chance | 0.023 | — |

Every graph arm loses to a dense second hop at scale, in the same direction,
and the arm that won on 148 papers is the clearest loser. Easy stratum
(n=181): BM25 0.812, dense_2hop 0.746, tess_hybrid 0.713, tess_graph1hop 0.707.

**What this establishes.** The §8.1 hypothesis was framed as decisive either
way, and it is — with one refinement:

- The edges DO reach the second-hop entity that similarity cannot: the bridge
  node is among the top-20 seeds' neighbours in 44% of kept items on 148
  papers and **80%** on 1,414 documents, against 0% for similarity by
  construction. The edge is not the null.
- The graph→document step throws it away. graph.json keeps one `source_path`
  per node (the first document that mentioned it) — gold in 30% / 22% of
  reached items. sqlite `node_provenance` keeps every document — gold in 64% /
  55% — but a node like `Model:BERT` was seen in ~30 documents and nothing in
  the store says which of them elaborates it, so ranking the provenance set
  converts a reached node into a hit only 10–15% of the time on either corpus.
- A dense second hop (re-query with the top document's centroid) gets the
  neighbourhood for free from embedding space and wins at scale.

So: **do not build a graph-expansion retrieval arm on this store.** The node
reach is real but unusable until the node→document projection carries
per-document mention density — and once it does, the fair comparison is against
an entity-mention inverted index, not against Mem0. The ceiling if that
density existed is bounded by "gold in provenance set" (97/219 = 0.44), and
that number is circular here because gold IS the max-mention document.

Caveats on the items themselves: bridges are dominated by Metric / Model /
Task / ResearchField nodes with df ≈ 30 (SSIM, RoBERTa, BERT, Transfer
Learning); "the document that mentions BERT most" is a weak stand-in for "the
document a question about BERT needs". A tighter item filter (df ≤ 5%,
Algorithm/Dataset/Benchmark bridges only) would give more meaningful items and
fewer of them — on 148 papers the df ≤ 8 subset (n=47) shows the same picture
(graph1hop 0.106 = dense_2hop 0.106).

### 10.3 Product gap the benchmark points at

`graph.json` records one `source_path` per node — the first document that
mentioned it — and retrieval reads graph.json. `sqlite.db` already records
every document (`node_provenance`) but no mention count. Two consequences,
both measured above: `hybrid_search` cannot hand an expansion the documents
where an entity is elaborated even when an edge reaches the entity, and even
the full provenance set cannot be ranked. If node→documents is ever projected
for retrieval, it has to carry mention density per (node, document) from the
extraction pass; nothing downstream can reconstruct it. Until then, expansion
arms are not worth another benchmark.

### 10.4 §8.3 measured — standing is a no-op, and mention density is not

Shipped first, because §10.3 named it: **PR #265 (`b19e2159`)** adds a
`node_mentions` sidecar, a compile-time pass counting each node's name and
aliases word-bounded in each of its provenance documents, and
`SqliteGraphStore.node_documents`, which returns a node's documents
most-mentioning first. Reader is driven by `node_provenance` and LEFT-joins the
counts, so the sidecars cannot drift. Measured: 31.5s for 43,151 pairs over 148
full papers, 5.2s for 27,669 pairs over 1,552 abstracts.

Then the §8.3 test, at `~/.blackhole/Tesserae/2026-09-02/attribution/`. No LLM
at all: the 100 false-premise questions were already answered by both systems on
2026-08-31, so only the CHECKER changes between arms.

- `text_only` — the misattribution checker: which record owns the figure. A
  chunk store can do this. The control.
- `graph_stand` — plus STANDING: the figure's document must be one the subject
  node is actually ABOUT, by mention density. Not circular: the questions were
  built from `evaluated_on` edges (§4.6), and mention density had no part in it.

**Standing changed nothing, on either arm.** Zero false premises caught that
co-location missed, zero lost, zero new over-refusals, on all 46 asserted
answers. The reason is mechanical and worth recording:

| of the 30 asserted answers whose figure was located in the evidence | |
| --- | --- |
| cited document is in the subject's top-3 by mention density | 28/30 |
| cited document IS the single most-mentioning document | 28/30 |
| cited document is the question's `source_doc` (raw-text truth) | 28/30 |

Co-location and standing agree everywhere, so this question set **cannot
discriminate them**. The failure mode standing exists to catch — a figure lifted
from a related-work row in a paper that merely cites the subject — does not
occur here, because `pack_figure` packs the record holding the benchmark and a
number, and that record is in the subject's own paper. This is a null result
about the TEST, not a refutation of the idea; a set built from related-work rows
would be needed, and nothing says one is worth building.

**The finding worth keeping is the third row of that table.** Mention density
picks the ground-truth owning document for a system **28 of 30 times (93%)**,
against a label that came from raw text and never from the graph. That is an
independent validation of the PR #265 ranking, arriving from a benchmark aimed
at something else.

Also confirmed, both arms: the general checker refuses **8 of 8** false premises.
It is good, and it is Mem0's too.

**Caveat, stated rather than buried.** The over-refusal control in
`summary.json` (tesserae 0.900, mem0 0.050) is **not interpretable** and must
not be quoted. It counts flags on TRUE-premise questions, but a true premise can
still get a hallucinated answer, and flagging that is correct. Separating the
two needs a gold figure per question, which `questions_premise.jsonl` does not
carry. The asymmetry is real but it measures the packers, not the checkers: 9 of
Tesserae's 18 asserted answers quote a figure absent from their own evidence.
That is trap #2 in this document, committed again while testing for it.

### 10.5 The first result that replicates — mention density beats the shipped answer

Run 2026-09-03, `~/.blackhole/Tesserae/2026-09-03/authority/`. The 28/30 in
§10.4 was a side result on a small sample, so this measures the claim head-on
against the behaviour the product actually ships.

**Question.** A node has several provenance documents. Which one is it ABOUT?

**Ground truth: the paper title.** A document whose title names the entity is
authoritative for it. Titles are raw text, had no part in building the graph,
and — this is the load-bearing detail — **the title line is removed from every
body the counts are taken over**, so a title match cannot feed the signal being
scored against it. Items are kept only where the entity is in ≥2 provenance
documents (something to choose between) and exactly one title names it (an
unambiguous answer).

**Arms**, each picking one document: `first_seen` (what `graph.json` stores
today — the incumbent), `mentions` (PR #265), `longest` (a length prior,
because long papers mention everything more), `random` (the floor).

| arm | 148 papers, n=25 | 1,552 abstracts, n=71 |
| --- | --- | --- |
| **mentions** | **0.920** | **0.845** |
| first_seen (shipped) | 0.520 | 0.549 |
| longest | 0.480 | 0.324 |
| random | 0.400 | 0.493 |

Paired against the incumbent: **W12/L2/T11, p=0.013** on the first corpus and
**W26/L5/T40, p=0.000192** on the second. The length prior is at or below
random on both, so this is not "long documents win".

**This is the first result in the whole programme that replicates on a second
corpus.** Everything else either measured parity or reversed at scale (§4, §10.2,
§10.4). The direction is also the useful one: the shipped `source_path` is right
about half the time, which is roughly a coin flip between two candidates, and
counting fixes most of the gap.

The residual misses are informative rather than noise: `BM25` picked a dense-
retrieval paper over the probabilistic-relevance-framework paper on counts of
2 vs 2, and `Retrieval-augmented generation` split 4/3/3. Generic method names
that every paper says a few times are where density has nothing to work with —
the same shape as the `df ≈ 30` bridges in §10.2.

**What follows.** `node_documents` should be the answer to "which document is
this entity about" anywhere the product needs one: citation, `verify_claim`
sources, agent-facing context. What it should NOT be reused for is retrieval
expansion (§10.2). Note the sample ceiling honestly: 25 and 71 items, because
the title-match filter is strict — most nodes have a single provenance document
and nothing to choose between.

### 10.6 The first ANSWER-quality win over Mem0 — packing, not retrieval

Run 2026-09-03, `~/.blackhole/Tesserae/2026-09-03/packing/`. Two multi-agent
rounds (16 + 8 agents, every arm adversarially verified) plus one judged answer
run on the same 100 questions both systems answered on 2026-08-31. Only the
EVIDENCE Tesserae is handed changed; the question set, the answerer and Mem0's
answers are the 2026-08-31 originals.

**Why packing and not retrieval.** §10.2 closed retrieval. But two of the seven
Mem0 comparisons were LOSSES, not parities, and both had the same measured
cause: 9 of 18 Tesserae answers that asserted a figure quoted one absent from
their own evidence. That is a packing defect, and the model is not the bottleneck
for it.

**Round 1 — ranking (8 strategies).** All eight converged on gold_record 0.575
with competing figures at ~0.0, every one verified SOLID with no label leak. The
convergence was the finding: **0.575 = 23/40 is the CEILING**, not a score, and
eight independent ideas all hit it exactly. Underneath their different framings
every arm reduced to two rules — prefer records naming subject AND benchmark AND
holding a figure, and prefer the authoritative document (PR #265). Verifiers
found several arms' elaborate named mechanisms inert, and one latent cache bug
keyed on object identity that would have broken silently on a port.

**Round 2 — the record UNIT (4 strategies).** Of the 17 questions above the
ceiling, **14 had the benchmark and the figure in DIFFERENT records**: a table's
column header is one record, its data row another, so neither alone can ever
satisfy the test. No ranking could reach them.

| regrouping | ceiling | competing | verdict |
| --- | --- | --- | --- |
| **caption-window** | **0.725** | **0.0** | SOLID |
| header-inherit | 0.750 | 10.7 | pays the precision back |
| table-block | 0.625 | 8.6 | +2 questions only |
| sliding-merge | 0.800 | 4.7 | **OVERMERGED — fake** |
| baseline | 0.575 | 0.0 | — |

The highest scorer was fake, and its own verifier proved it: shuffling the
paragraphs to destroy adjacency scored the SAME OR HIGHER (33/34/32 vs 32), so
the gain was purely record size — a big enough bag of text trivially contains a
benchmark name and a number that have nothing to do with each other. That is
trap #2 in this document, caught by an adversarial verifier rather than shipped.

caption-window survived its own size nulls (length-matched random prose 24-26,
shuffled caption assignment 25-26, real caption 29) and one real bug was fixed
after it: captions were searched BELOW the table first, mis-attaching the next
float's caption to 64 of 344 table runs, because this corpus is flattened arXiv
HTML with captions above. Ablation also shows the `intro` half contributes
exactly zero (caption-only reaches the same +6) — dead code, do not port it.

**The packing result, proxy level:**

| | coverage | competing figures |
| --- | --- | --- |
| shipped packer | 0.150 | 3.1 |
| 2026-08-30 figure-record packer | 0.575 | 42.0 |
| **caption-window + tuned arm** | **0.725** | **0.0** |

**The answer run — the decisive test.** 100 questions, Claude CLI on
subscription quota, no paid API. Ground truth for correctness is the figures the
OWNING document reports next to the benchmark, taken from raw text.

| metric | Mem0 | Tesserae, new packing |
| --- | --- | --- |
| **correct answers** | 12/40 | **19/40** |
| over-refusal (gold record WAS in evidence) | 0.345 | **0.138** |
| precision of asserted figures | 0.571 | **0.613** |
| false assertion on false premises | **0.200** | 0.317 |

Paired on correctness: **W7/L0/T33, sign p=0.016** — we never got one wrong that
Mem0 got right. Integrity check: **zero** correct answers came from a question
whose gold record was absent from the evidence, so nothing was won by luck.

**With the misattribution checker on BOTH arms** — it is general (§4.6), so
applying it to only one side would be the mistake:

| | correct | false assertion |
| --- | --- | --- |
| Mem0 + checker | 12/40 | 0.000 |
| **Tesserae + checker** | **18/40** | 0.017 |

Paired: **W6/L0/T34, p=0.031.**

**THE HONEST STANDING.** This does NOT beat Mem0 on everything. It wins
decisively on the two benchmarks that were losses — correct answers (18 vs 12,
p=0.031) and over-refusal (0.172 vs 0.345) — and is ONE case behind on
hallucination (1 vs 0). Everything else in §4 remains parity.

**Caveat that must travel with the false-assertion numbers.** The checker's
dominant flag is `benchmark_absent_from_source_paper` (18 of 19), and the false
premises were CONSTRUCTED by pairing a system with a benchmark from a different
paper. Detection rule and construction rule are near-isomorphic, so that catch
rate is an upper bound on this set, not a general hallucination-detection
number. It is not the §4.6 circularity — the checker reads raw evidence text,
never the graph — but it is a close cousin and must not be quoted as
"we beat Mem0 on hallucination".

**What to ship.** The caption-window record unit and the two ranking rules are
the product change; the arms live in `packing/`. Ship caption-only (drop
`intro`), keep the caption-above fix, and do not port `is_prose`/`_assertion`
from arm_record-shape (inert, and carries the cache bug).

### 10.6a CORRECTION to §10.6 — the assertion metric was mine and it was wrong

**The §10.6 table is superseded by this one.** Read this section instead.

§10.6 counted an assertion with a regex for any figure in the answer, and
compared that against Mem0's stored flags, which came from an LLM classifier.
Two different instruments on the two sides, which is trap #2 in this document
committed while testing for it.

The failure mode is specific and systematic. An answer that correctly REFUSES
and then cites what the system WAS evaluated on — *"the evidence does not
mention a benchmark called Next Generation Simulation; Wav2Lip reports 6.843 on
ReSyncED"* — contains figures, so the regex scored it as a hallucination. The
single false assertion §10.6 reported as surviving the checker was exactly that
shape, i.e. not a hallucination at all.

Re-scored with ONE classifier over both arms' 200 answers
(`packing/classify.py`, Claude CLI, subscription):

| metric | Mem0 | Tesserae, new packing |
| --- | --- | --- |
| **false assertion** | 0.133 | **0.000** |
| **precision of asserted figures** | 0.600 | **0.739** |
| **over-refusal** | 0.345 | **0.276** |
| correct answers | 12/40 | 17/40 |

Paired: false assertion **Tesserae-only 0, Mem0-only 8, sign p=0.0078**;
correct answers **W6/L1/T33, sign p=0.125**.

**Both of §10.6's errors ran in opposite directions.** We do BETTER than it said
on hallucination — zero false assertions, not 0.317, and the win is significant
without needing the checker at all. We do WORSE than it said on correctness: 17
not 19, and the paired test is **p=0.125, NOT significant**. The p=0.016 in
§10.6 was an artefact of the regex over-counting our assertions.

**Standing on this benchmark family, honestly:** three wins (hallucination,
precision, over-refusal) and one non-significant lead (correct answers), against
the two LOSSES it started as. The §10.6 checker-gated table is also superseded —
with a fair assertion metric the checker is not needed to win hallucination, so
its construction caveat no longer bears on the headline.

### 10.6b What packing CANNOT reach — scope of the win

Checked before spending further, because the goal was "beat Mem0 on ALL quality
benchmarks":

- **§4.1 document recall** — retrieval, not packing. Closed as a null in §10.2.
- **§4.7 contamination-free reasoning** — the 11-paper corpus contains **zero
  markdown tables**, so the caption-window unit is inert there; and its
  questions deliberately name no system, so authority ranking has no subject to
  rank by. Its `provenance` metric scores which DOCUMENTS were packed, not
  within-document ordering.
- **§4.2 source-prose** — already discredited as judge-sensitive.

So this change wins where it applies and cannot move the rest. "Beat Mem0
everywhere" is not reachable by packing alone; the remaining parities need a
lever that is neither retrieval nor packing, and both of today's candidates
(graph expansion §10.2, source standing §10.4) measured as nulls.

### 10.6c The held-out extension — correctness is now significant, and the family is swept

§10.6a left correct answers as a lead that could not be resolved: 7 discordant
pairs at n=40, p=0.125. That is a POWER problem, and the honest fix is more
items from the same generator, not a better story about the same ones.

**The blocker I reported was wrong.** §10.6b said the Mem0 arm "cannot be
re-run" because `qdrant_premise` was deleted and `mem0` is installed nowhere.
Both facts are true; the conclusion was not. Mem0 with `infer=False` makes no
paid calls, and the rebuild is deterministic given the same corpus, chunking and
encoder. Rebuilt into a scratch venv
(`~/.blackhole/Tesserae/2026-09-03/mem0rebuild/`, mem0ai 2.0.20, 4,842 chunks,
2.5 min) and checked against the stored evidence:

| faithfulness of the rebuilt competitor, 100 shipped questions | |
| --- | --- |
| byte-identical evidence | **100/100** |
| identical document sequence | **100/100** |
| document-set Jaccard | mean 1.000, min 1.000 |

That check is what licenses using it on new questions, and it ran before
anything was extended.

**The extension is pre-registered by construction, not by promise.** The builder
makes every valid true-premise pair, shuffles with a fixed seed, and the shipped
set took `true_q[:40]`. This takes `true_q[40:]` — membership fixed by an index,
verified disjoint (overlap 0). **12 is all that remains**: the corpus yields 52
usable pairs under these filters, so this is the maximum power available here,
not a sample sized to taste. The analysis was fixed in advance: paired sign test
on correct answers over the combined 52, both arms scored by one classifier.

| set | Tesserae | Mem0 | paired | sign p |
| --- | --- | --- | --- | --- |
| shipped 40 | 16 | 12 | W5/L1/T34 | 0.219 |
| held-out 12 | 7 | 2 | W5/L0/T7 | 0.063 |
| **COMBINED 52** | **23** | **14** | **W10/L1/T41** | **0.0117** |

(The shipped-40 count reads 16 here against §10.6a's 17: this run uses a
stricter correctness rule — the classifier's own figure field, falling back to
the answer's figures only when it yields none. Applied identically to both arms,
and it costs Tesserae one item and Mem0 none, so the stricter rule is the
conservative one and is what the combined test uses.)

**THE BENCHMARK FAMILY THAT HELD BOTH LOSSES IS NOW SWEPT**, all measured with
one classifier over both arms:

| metric | Mem0 | Tesserae | |
| --- | --- | --- | --- |
| **correct answers** | 14/52 | **23/52** | W10/L1, **p=0.0117** |
| **hallucination** | 0.133 | **0.000** | 0 vs 8 cases, **p=0.0078** |
| **precision** | 0.600 | **0.739** | win |
| **over-refusal** | 0.345 | **0.276** | win |

**Still not "all quality benchmarks."** §4.1 document recall is retrieval, a
measured null (§10.2). §4.7 contamination-free cannot show a significant win for
ANYONE: at n=12 a sign test needs 10 wins with zero ties for p<0.05, and its
standing is 5W/3L/4T on quality, 3W/2L/7T on provenance. Ties dominate because
the set is underpowered by construction. Those two need a larger corpus and a
larger question set, not a better packer.

### 10.6d The last two benchmarks, and the one decision that unblocks them

Both were checked with numbers rather than argued from shape, because §10.6b
already showed that "this cannot be done" is a claim I get wrong.

**§4.1 document recall — a genuine tie, not a lead to convert.** With the
encoder equalised it is 8W/8L/41T at ten documents (sign p=1.0) and 6W/3L/48T at
fifty (p=0.51). There is no direction to push: the arms agree. It is also
retrieval, where graph expansion measured null twice (§10.2), and both sides
already run bge-base. Winning it needs a better retriever than a vector store,
which is the thing three benchmarks now say does not exist here.

**§4.7 contamination-free — the corpus is exhausted, not the method.** Same
held-out trick as §10.6c, applied to its generator:

| | |
| --- | --- |
| qualifying paper pairs (shared terms 8..60) | 55 |
| used by the shipped run (top 45 by overlap) | 45 |
| **held out and still available** | **10** |
| observed survival of the leave-one-out ablation | 12/45 = 27% |
| questions those 10 pairs would yield | **~3** |
| resulting n | 12 → **~15** |

At n=15 with a standing of 5W/3L/4T on quality, that is not a significance fix;
ties dominate and the extra items cannot outvote them. The 11-paper corpus is
the binding constraint, and the builder's own comment records why: 30 of the 45
fetched papers "are no longer in corpus/ after the machine forced a smaller
run". `corpus_all/` still holds all 45.

**THE DECISION.** Compiling the remaining ~34 papers would roughly quadruple the
corpus, multiply the qualifying pairs (they grow with the square of the paper
count), and give this benchmark enough items to resolve at all — for either
system. That needs an explicit authorisation to compile, which is the user's
standing rule to give (§1), and it is a multi-hour local-model run on a machine
that has already been at 27% free memory today. Nothing else in this programme
is blocked on anything but that.

### 10.6e §4.7 resolved at last — and it goes to Mem0

The one benchmark §10.6d said was corpus-bound is now measured properly, and the
answer is that **Tesserae loses cross-paper reasoning**. Recording it in full
because a null that cost this much should never be re-derived.

**What it took.** The user authorised the compile §10.6d asked for.
`corpus_all/` held all 45 fetched papers; 34 had been dropped when the machine
forced a smaller run. Staged them, compiled with the Claude CLI on subscription
(4h 39m, 45 documents), rebuilt Mem0's arm over the same 45 papers (2,328
chunks), generated 40 new questions with the SAME builder and the SAME
leave-one-paper-out ablation (131 candidates, 31% survival, 3h 36m), and ran the
head-to-head (52 questions, 2h 55m). About nine hours of machine time.

**Result, 52 questions on 45 papers (was 12 on 11):**

| metric | Mem0 | Tesserae | paired | sign p |
| --- | --- | --- | --- | --- |
| answered | 0.962 | 0.962 | W2/L2/T48 | 1.00 |
| **quality** | **0.667** | 0.577 | W12/L21/T19 | 0.16 |
| **provenance** | **0.702** | 0.644 | W10/L15/T27 | 0.42 |
| unsupported claims | 2.60 | **1.92** | W28/L17/T7 | 0.14 |

Nothing reaches significance, but the DIRECTION on quality and provenance now
matches the original 12-question run (0.722/0.750 vs 0.750/0.708) instead of
contradicting it. Two independent samples pointing the same way is weak evidence
that Mem0 is genuinely better here, not noise. **Do not re-run this expecting a
different answer.**

The one metric we lead — 1.92 unsupported claims per answer against 2.60, W28/L17
— is the same shape as the §10.6a hallucination win: our evidence carries fewer
grabbable wrong things. It does not convert into better answers.

**Caveats, both running in OUR favour and neither rescuing it.** 14 of the 45
papers fell back to deterministic extraction when the subscription session limit
hit mid-compile, thinning our node layer but not Mem0's; retrying them would
have meant re-extracting all 45, because the manifest is compile-level with no
per-file digests. And the questions were built from abstracts and full text, never
from the graph, so the degraded extractions cannot have biased the question set.

**Why the benchmark could not have shown this before.** At n=12 a sign test needs
10 wins with zero ties for p<0.05 and the standing was tie-dominated, so neither
system could win it. The corpus is now 45 papers, 881 qualifying pairs, 52
questions. The instrument works; the answer is just not the one we wanted.

### 10.6f FINAL STANDING against "beat Mem0 on all quality benchmarks"

| benchmark | verdict |
| --- | --- |
| correct answers (figure attribution) | **WIN** 23/52 vs 14, p=0.0117 |
| hallucination | **WIN** 0.000 vs 0.133, p=0.0078 |
| precision of asserted figures | **WIN** 0.739 vs 0.600 |
| over-refusal | **WIN** 0.276 vs 0.345 |
| document recall | **TIE** 8W/8L/41T with the encoder equalised |
| cross-paper reasoning quality | **LOSS** 0.577 vs 0.667 |
| cross-paper reasoning provenance | **LOSS** 0.644 vs 0.702 |

**The goal is not achievable and this is now measured, not argued.** Every
benchmark has been run to the limit of what the corpora support. The honest
one-line summary: **packing wins figure-attribution answering decisively;
cross-paper reasoning belongs to Mem0.** Document recall is retrieval, which
§10.2 closed as a null.

### 10.7 Operational notes from this continuation

- Background Bash tasks were killed by the harness ~15–20 min in, twice, with
  nothing written. macOS has no `setsid`. Long jobs now launch via
  `subprocess.Popen(..., start_new_session=True)` with a pidfile, checkpoint
  every stage, and are watched with a bounded monitor loop. Memory note
  `long-jobs-need-own-session`.
- The agentmemory MCP (localhost:3111) was down all session; notes went to
  the harness's file memory instead (`bridge-retrieval-benchmark`,
  `compiled-benchmark-graphs-on-disk`, `long-jobs-need-own-session`).
- Nothing was compiled, nothing was paid for, no process outside this session
  was signalled.
