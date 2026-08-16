# Handoff — CHARTER shipped, comparison claims retracted, benchmarks scaffolded

**Date:** 2026-08-16
**`main` at:** `5355170b`
**Covers:** 2026-08-15 → 2026-08-16, 23 merged PRs (#162–#184)

Everything below is merged. The repo is clean: no open PRs, no worktrees, no
unmerged branches but `gh-pages`.

---

## 1. What shipped

### CHARTER re-scope — all 7 steps (#162–#170, #163)

The headline, measured on the live 47,132-node graph: **`graph_map`'s root went
from 1,852 cards / 32.5 s to 7 named cards / 1.4 s.**

| PR | What |
|---|---|
| #162 | a compile derives the charter; anchors are nameable |
| #166 | a domain brief is a community summary keyed on a slug that survives ingest |
| #169 | a domain is dated by its sources, and reports what it cannot date (48/780 degrade to `undated` rather than raising) |
| #165 | `graph_map`'s entry point is a directory of names, not a size rank |
| #167 | `TESSERAE.md` names divisions instead of twelve type-sorted nodes |
| #168 | `charter_route` places a task in ~185 ms over 780 rows, or names nothing |
| #170 | `CHARTER_PARTITION` + `CHARTER_FALLBACK` lint probes |

**The lesson from this wave is not in any single PR.** Every confirmed defect
was a SEAM between steps, not a bug inside one. Four separate PRs invented four
different filenames for the same artifact, and each was green in isolation
because nothing existed to disagree with it. Single-PR review found none of
them; a cross-PR reviewer told to diff overlapping files across ALL branches
found all four, plus a merge order that would have landed green while silently
reporting nothing.

**If you fan a milestone into parallel PRs that share an artifact:** one PR must
own the artifact's key/path and land FIRST, and the others must call its helper
rather than restate the convention. Run a cross-PR review before merging any.

### The LLM cache was serving cross-contaminated answers (#171)

`~/.tesserae/llm_cache` was addressed by `sha256(cache_key + model + extra)`
where `cache_key` came from the caller. The module contract said "the key is a
content digest" — and **eleven of twelve callers passed a constant label.**

Measured before the fix (5,450 entries, 66 MB):

| key family | distinct files | meaning |
|---|---|---|
| `community-summary-v1::<count>` | 40 | one summary per member-COUNT bucket |
| `agent-distill-v1::agent_distill_map` | 1 | one answer for EVERY document |
| `sessions-v3` | 2 | one findings set for every chunk of every session |
| `supersede-v1` | 1 | one verdict for every supersede judgement |

Fixed centrally: `_cli_cache_path/get/put/drop` now take a keyword-required
`prompt` and hash the assembled text, so a client cannot address the cache
without the bytes it is about to send. `cache_key` is now a namespace.

**Never hand-build a content digest for a new LLM call — pass a readable
namespace and let the layer digest the prompt. To skip caching entirely, pass
`cache_key=None`.** The one caller that must never cache is `config status`'s
liveness probe: its prompt is constant, so a hit would report `✓ OK` forever
through a rate limit or an expired login.

### The daemon warms briefs; a tick yields to a waiting pipeline (#172, #173)

`materialize_domain_brief` had no production caller — steps 2/5/7 were readers
of an artifact nothing wrote. The daemon now warms briefs on its sleep cycle,
budgeted (`--brief-budget`, default 8, `0` disables), separate from
`--summarize-budget`.

Operating envelope, documented in `docs/engine-consolidation.md`:
**25 + 8 = 33 LLM calls per tick × 12 ticks/hour at `--consolidate-idle 300` =
396 calls/hour ceiling**, reached only while cold, decaying to zero.
Cold-start convergence over 780 domains ≈ 98 ticks ≈ 8.2 h.

Two traps found in review, both fixed:
- **Head-of-line starvation** froze the pass forever (deterministic rank + a
  `break` at budget = candidate #9 unreachable), burning 96 calls/hour warming
  nothing. Root cause: `summarize_community` returns `None` on citation-lint
  rejection WITHOUT caching and WITHOUT `forget_cached_answer`, and all tier-1
  divisions are routers, so the lint hits exactly the population the budget
  targets. Fixed with a zero-call pre-filter plus `2**strikes` back-off.
- The ordering is **breadth-first, not a demand rank** — `domain_member_ids` is
  the whole subtree, so a parent's demand always dominates its children's. That
  is deliberate; the docstring now says so.

#173 makes a tick abandon its remaining budget when a pipeline run is blocked on
the compile gate: **1260.6 ms → 55.3 ms** wait, measured. The gate is
deliberately NOT yielded — it exists so a tick reads one consistent
`graph.json`.

### A compile narrates itself (#176, #177)

A compile ran 3h35m printing nothing, was read as a hang, and was killed. It was
~60% through a full re-extract that the `graphed` completeness gate had
correctly forced.

Two faults, both fixed. `make_compile_progress()` returned a no-op reporter
whenever stderr was not a TTY — i.e. it silently disabled itself on exactly the
redirected and detached runs that need it. And `progress=None` meant SILENCE, so
the five callers that passed nothing (`refresh`, `watch`, MCP, ingest
orchestrator, engine daemon) ran completely mute.

**A silent compile is not a stalled one.** Before concluding otherwise: count
`graphed` entries in `.tesserae/manifest.json` (NOT sha256 — that never moves
during a full re-extract), check the process has live LLM children turning over,
and time one `codex exec` call to price the remainder.

**A killed compile is cheap to resume** — completed extractions replay from
`~/.tesserae/llm_cache` at no cost, for the codex/claude CLI clients only.
`AnthropicLLMJsonClient` never touches that cache, and clearing the directory
converts a cheap resume into a full-price one.

### Documentation: a ratchet, a glossary, three retractions (#174, #175, #178, #180)

`tests/test_docs_i18n.py` now enforces mechanical parity across **553 doc pairs**
against `tests/fixtures/docs_i18n_parity_baseline.json`. Baseline drift when
first measured: **313 of 553 pairs**, 622 identifiers absent from a translation,
307 structure mismatches, 281 one-off English words in CJK prose.

It cannot see a wrong translation, only a missing one. That is what
`docs/i18n/GLOSSARY.md` is for — the house rendering of terms mistranslated more
than once, derived from the corrected mirrors rather than invented.

**#178 retracted three comparative claims nothing measured**, including a
hardcoded competitive report written to `.tesserae/competitive_report.md` on
every compile naming MegaMem, Graphiti/Zep and Qdrant with zero measurements.
`tests/test_docs_comparative_claims.py` now catches 14/14 crafted claim shapes
at 0 false positives across 218 first-party docs.

**#180 re-verified the Neo4j edition claims** against the Cypher Manual
(Version 25, fetched 2026-08-16) and corrected one AGAINST our interest: the
spec credited Community Edition with property *type* constraints, which are
Enterprise. CE has exactly one constraint family — uniqueness. The finding also
gained a second leg: graph types, Neo4j's newest schema mechanism, is
Enterprise-only too, so "CE cannot make a property mandatory" holds through both
the old syntax and the new.

### Benchmarks scaffolded (#179, #181, #184)

- **#179** — a QA scorer (EM, token-F1, refusal/hallucination pairs, gold
  coverage, a first-class null model). Three refusal layers: `CI` set → SKIP;
  missing prerequisite → SKIP + the exact command; reaching an LLM requires
  `--i-know-this-costs-money`.
- **#181** — `OpenAIEmbeddingBackend` (explicit-only, never on the `auto` path)
  plus the LongMemEval-MAB loader.
- **#184** — the Neo4j TCK adapter. **Detail in §3.**

### CI (#182, #183)

Per-PR compute **~18 min → ~6.5 min (64%)**: all three matrix legs still report,
but on a PR only 3.11 runs the suite; 3.10/3.12 land as ~7 s no-ops. `main`,
tags and dispatch run all three in full.

---

## 2. Live state of THIS project — everything above is latent

| | |
|---|---|
| `.tesserae/graph.json` | **2026-08-09**, 47,132 nodes / 104,677 edges |
| `.tesserae/charter/` | **ABSENT** — no compile since #162 |
| domain briefs on disk | **0** |
| manifest | **1,592 / 2,524 graphed** (932 not) |
| `Event` / `Runbook` / `Gotcha` | 226 / 47 / 103 (from the older compile) |
| engine daemon | not running (stale pidfile from 2026-08-02) |

**Consequence:** `graph_map` still returns the 1,852-card root on this project.
Every charter reader correctly reads an empty set. Nothing is broken; the work
is simply not materialised.

**The 932 ungraphed entries mean the next `--changed-only` compile will refuse
its no-op and re-extract all 2,524 documents** (`project.py:898` requires every
entry to be `graphed`). That is the repair path working as designed, not a bug —
budget ~6 hours, or less where `~/.tesserae/llm_cache` still has entries.

⚠️ **HARD RULE, restated by the user 2026-08-15: never compile this project to
test, verify, demo or "make the work real".** Only the user decides a compile
happens. If something needs a compiled graph to be observable, say so and stop.

---

## 3. The competitor question, answered

**Can we say Tesserae is a competitor to Neo4j's agent memory?**

**On capabilities — yes, and it is sourced.** The research compared against
`neo4j-labs/agent-memory` v0.5.0 by reading its source and found exactly three
things it had that Tesserae lacked: a persisted vector index, a soft-merge
tombstone, and a transaction-time clock. All three shipped (#141–#152); all
three verified present today (`vector_cache.py`, `merged_into`,
`temporal_observed.py`).

**On their conformance kit — no.** #184 built a real adapter and ran it:

| Tier | Passed | Failed | Skipped | Total |
|---|---:|---:|---:|---:|
| Bronze | **57** | 36 | 0 | 93 |
| Silver | 6 | 61 | 0 | 67 |
| Gold | 0 | 16 | 2 | 18 |
| Platinum | 0 | 0 | 11 | 11 |

The kit's own `determine_achieved_tier` returns `None`. **Tesserae reaches no
tier.** The 36 Bronze failures trace to deliberate properties: append-only
stores (13 `delete_message`, 6 `clear_session`), retrieval indexing a compiled
artifact rather than a live table (7 `search_messages`), a closed type
vocabulary (5), no metadata column (1).

**Two facts about the TCK that matter more than our score:** a plain dict shim
scores **178 passed and grades `gold`**, and `determine_achieved_tier` on an
all-skipped tally ALSO returns `gold`. Passing it demonstrates very little.

⚠️ The squash commit for #184 says *"fails 34 of 93"* — **that is wrong; it is
36 failures / 57 passes.** GitHub used the PR title, written before the
metadata-echo fix. `evals/tck/README.md` on `main` is correct and authoritative.

**Not sayable, on any evidence we have:** anything about being faster, more
accurate, or better at retrieval than any named system. Never measured.

**Also do not conflate** `neo4j-labs/agent-memory` with `mcp-neo4j-memory` or
the third-party `@knowall-ai` server. The harshest findings in the research
(name-string identity, non-atomic writes, `DETACH DELETE`) are about the latter
two.

---

## 4. Next things to do

### 4.1 — Decide whether to compile (USER'S CALL, blocks §4.2)

Everything in §1 is latent until a compile derives the charter. Cost: a full
re-extract of 2,524 documents (~6 h, less where the LLM cache still holds
entries), because 932 entries lack `graphed`.

The compile now narrates itself, so progress is visible: each document prints as
`cache` or `LLM` with a running rate.

**Do not start this without being asked.**

### 4.2 — Warm the briefs, then check the readers (after 4.1)

Once a charter exists, start the daemon and confirm the three readers go warm:
`graph_map`'s domain card reports `quality: "llm"`, `charter_route` reports
`warm_rows ≥ 1`, and `CHARTER_FALLBACK` stops counting the domain cold.

Budget 8 briefs/tick, ~98 ticks to converge. Watch for the citation-lint
back-off: a router whose prose cites no child is deferred `2**strikes` ticks.

### 4.3 — The benchmark, blocked on a decision the user has already made

`evals/lme_mab/` is built and merged. It cannot produce a **comparable** number,
and the reason is settled: the published protocol we would be compared against
(arXiv:2606.04555 §5.2-5.3) fixes `text-embedding-3-small` and `gpt-4o-mini`,
and **the user does not use an OpenAI API key — codex OAuth only.** codex
rejects `gpt-4o-mini` outright ("not supported when using Codex with a ChatGPT
account") and exposes no embedding endpoint.

`gpt-5.4-mini` — the control that decides the answers — **does** work via codex.

Measured cost if it ever runs (5 groups, 2.04M tokens of dialogue, 300
questions): **~15.6M tokens via codex, ~3.4M via API.** 78% of the codex column
is fixed per-call overhead (a trivial `codex exec` costs **15,090 tokens** even
with a stripped `CODEX_HOME`).

**The achievable version, if wanted:** Tesserae vs BM25 vs Dense retrieval —
the two baselines needing no LLM for memory construction — under one
self-consistent local protocol (codex backbone, one local embedder via ollama,
K=10 for all). Honest and quotable as *our* measurement; not comparable to
Mem0's or HippoRAG's published numbers, and `fairness_blockers()` will say so.

### 4.4 — Point branch protection at one context (USER'S CALL, 1 click)

Protection requires `pytest (py3.10)`, `pytest (py3.11)`, `pytest (py3.12)` BY
NAME. An aggregating **`tests`** job now exists that always reports. Requiring
`tests` and nothing else frees the matrix to change without another deadlock.

This is a repo setting and was deliberately left to a human.

### 4.5 — Smaller, genuinely optional

- **Pre-existing mirror defects** the verifiers ruled out of scope: German uses
  `Diagramm` (chart) for *graph* and `Brief` (letter) for *brief*; a French typo
  `Le passage entier est enveloppe`. Own sweep.
- **`~/.blackhole/Tesserae`** holds ~746 MB across earlier dates, none from this
  session — likely abandoned worktrees with their own venvs.
- **The `agentmemory` MCP** at `localhost:3111` was unreachable all session.

---

## 5. Traps that will waste your time if you do not know them

1. **`test_packaging_install.py::test_editable_install_exposes_tesserae_console_command`
   ALWAYS fails in a worktree** — `ensurepip` SIGABRTs under the uv-managed
   CPython. It passes on the main checkout and in CI. Ignore that one failure;
   every other one is real.
2. **Fresh worktrees need `uv sync --python 3.11 --all-extras`** or 38 tests
   fail on missing extras.
3. **Check `gh api repos/:owner/:repo/actions/permissions` BEFORE suspecting a
   workflow file.** `allowed_actions: local_only` blocks `actions/checkout` and
   everything else, and GitHub reports it as `startup_failure` with no
   annotation, naming the run after the workflow file — indistinguishable from a
   YAML error. The tell is that runs fail on EVERY branch at once.
4. **Lint workflows with `actionlint`.** PyYAML silently accepts duplicate
   mapping keys (last wins); GitHub rejects the file. A duplicate `if:` cost
   several pushes to find. Binary at
   `~/.blackhole/Tesserae/2026-08-16/tools/actionlint`.
5. **Never pair `paths-ignore` with required checks.** A skipped workflow posts
   no checks, so a docs-only PR blocks forever. Removed in #183 for exactly this.
6. **The repo is PUBLIC, so Actions bills $0.** The billing page's "Usage by
   repository" panel shows GROSS (what it would cost on a private repo); the
   "Billed amount" column is the money and reads $0. Verify with
   `gh api repos/:owner/:repo/actions/runs/<id>/timing` → `billable.UBUNTU.total_ms`.
7. **`evals/` holds 877 MB of gitignored upstream clones** (cognee, MegaMem)
   carrying local work that exists in no git repository. Never clean it.
8. **The roadmap specs have been wrong 11 times** across two milestones, twice in
   ways that would have shipped silent corruption. Every one was caught because
   the implementer re-verified instead of following. Read each step's
   "Correction, as implemented" block before trusting spec text.

---

## 6. Where the artifacts are

| What | Where |
|---|---|
| TCK clone + venv | `~/.blackhole/Tesserae/2026-08-16/tck/`, `tck-venv/` |
| LongMemEval-MAB parquet (20 MB) | `~/.blackhole/Tesserae/2026-08-16/lme-mab/` |
| `actionlint` | `~/.blackhole/Tesserae/2026-08-16/tools/` |
| Neo4j research + corrections | `docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md` |
| CHARTER roadmap | `docs/superpowers/specs/2026-08-14-charter-rescope-roadmap.md` |
| Scale measurement | `docs/superpowers/specs/2026-08-14-scale-measurement.md` |
