# Tesserae against the Neo4j agent-memory TCK

Neo4j publishes no benchmark scores for `neo4j-labs/agent-memory`. What it
publishes is a Technology Compliance Kit: [`neo4j-labs/agent-memory-tck`][tck],
189 scenarios across four tiers, backend-agnostic, and free of model calls. That
made it the one Neo4j comparison reachable on this machine, so we ran it.

**Tesserae fails it, and the failures are the useful part.** 59 of 93 Bronze
scenarios pass. The 34 that fail all trace to four properties of the engine, and
none of the four is a bug.

[tck]: https://github.com/neo4j-labs/agent-memory-tck

## The measured result

Adapter: `evals/tck/adapter.py` over `evals/tck/memory.py`. Kit pinned at
`4603b91f4fc831f19901b4f68d96f8dc039e9a38` (TCK 1.0.0; the kit publishes no
tags, so a commit is the only pin available).

| Tier | Passed | Failed | Skipped | Total |
| --- | ---: | ---: | ---: | ---: |
| Bronze | **59** | 34 | 0 | 93 |
| Silver | 6 | 61 | 0 | 67 |
| Gold | 0 | 16 | 2 | 18 |
| Platinum | 0 | 0 | 11 | 11 |
| **All collected** | **65** | **111** | **13** | **189** |

The whole run takes about two seconds, offline, with no model and no compile.

Certification thresholds ([`docs/how-to/certification.adoc`][cert]) are 100% for
Bronze, 100%+100% for Silver, 100/100/80% for Gold. Fed these counts, the kit's
own `determine_achieved_tier` returns `None`: **Tesserae reaches no tier**, and
nothing here has been submitted anywhere.

[cert]: https://github.com/neo4j-labs/agent-memory-tck/blob/main/docs/how-to/certification.adoc

## Why the 34 Bronze scenarios fail

Every one raises `evals.tck.memory.Unsupported` naming the property responsible,
except the last, which fails on an assertion.

| Failures | Blocked by |
| ---: | --- |
| 13 | **`delete_message`.** `SessionChunksDB` exposes `record_turns`, `turns_for_day`, `mark_coverage`, `covered_days`, `coverage_rows` — no delete, no update — and its `turns` table has no id column for one to address. The agent-write overlay is append-only on purpose: a retraction is a `retracts` edge, which adds a row rather than removing one. |
| 7 | **`search_messages`.** Tesserae's retrieval is real — BM25, embeddings, personalised PageRank — and all of it indexes `graph.json`, which a compile produces. The live turns table is indexed on `day` and its uniqueness key, nothing else. |
| 6 | **`clear_session`.** Same append-only store. Turns are bucketed by KST day rather than owned by a session, so there is no per-session extent to remove even if a delete existed. |
| 3 | **`add_entity` for LOCATION, OBJECT, EVENT.** `ResearchNodeType`'s 76 members are research-domain shaped (Paper, Dataset, Benchmark, Metric); LOCATION and OBJECT are simply absent. EVENT exists but sits in `agent_write.DENIED_NODE_TYPES`, reserved for the session-graph producer that re-derives Event nodes from transcripts every compile. |
| 2 | **`add_preference`.** No Preference node type in 76, no `has_preference` edge in the vocabulary. Tesserae models what a project knows, not what a user likes. |
| 2 | **`add_fact`.** `ALLOWED_EDGE_TYPES` is closed (`uses_dataset`, `evaluated_on`, `achieves_score`, …) and `agent_write` refuses an unknown edge type rather than coercing it, so a free predicate like `WORKS_AT` has nowhere to go. |
| 1 | **Metadata round-trip.** `record_turns` writes the `meta` column as `{"name": …}` or `{}` and carries nothing else, so metadata survives the write and is gone from every read. |

Silver's 6 passes are all `add_entity`, the two types
`evals/tck/memory.py:ENTITY_TYPE_MAP` covers. Its 61 failures are one property:
an agent write is durable in `.tesserae/agent-writes.jsonl` and enters the graph
only when `ProjectWiki.compile` replays it, while every read path loads
`graph.json` off disk and nothing merges the overlay at read time. There is no
read-after-write without a compile, so nothing written can be searched back.

Gold presupposes Bronze and Silver writes underneath it and fails on their
absence. Platinum self-skips on `NotImplementedError`, as the kit intends.

## What this result licenses, and what it does not

**Defensible:**

> Tesserae ships an adapter implementing the `neo4j-labs/agent-memory` TCK 1.0.0
> interface over its compile-free write paths, and passes 59 of 93 Bronze
> scenarios. It reaches no certification tier. The 34 failures are architectural:
> Tesserae is a compile-and-project context engine, not a mutable memory service.

**Not defensible, and the reasons matter more than the list:**

- *Anything comparative on quality, latency or scale.* The kit measures none of
  them. Its embedder is `MockEmbedder`, SHA256 hex pairs scaled to 1536 floats
  (`tck/fixtures/mocks.py`), which has no semantic geometry at all: similar
  sentences get unrelated vectors. Neo4j's own reference adapter uses it. No
  scenario can be asserting retrieval quality, because the kit deliberately
  removes the ability to measure it.
- *That a higher score would have meant something.* Swap our adapter for a
  150-line dict-backed shim with no graph, no persistence, and `substring in
  content` standing in for semantic search. The same runner reports **178 passed,
  11 skipped**, which `determine_achieved_tier` scores **Gold**. That outranks the
  only entry in `certifications/registry.json`, Neo4j's own `neo4j-agent-memory`
  0.0.5 at Bronze. Passing this kit is evidence about API surface. It is not
  evidence about a memory system.
- *"Certified".* Certification is a maintainer-reviewed PR of a self-reported
  JSON containing test outcomes and no adapter source, no fingerprint, and no
  attestation; Neo4j re-runs nothing. The badges are Neo4j-logo shields, and
  Apache-2.0 §6 grants no trademark rights. "Listed in the TCK certification
  registry" would be the accurate phrasing if we ever submitted, which we have
  not.
- *That the kit answers the question that prompted it.* A TCK pass answers "can
  Tesserae speak this API?" The question asked was "is Tesserae a competitor to
  Neo4j's agent memory?", and no conformance kit can answer that.

One more caution for reading anyone else's result, including a future one of
ours: `tck/report/compliance_report.py`'s `determine_achieved_tier` computes
`testable = total - skipped` and `continue`s past a tier when `testable == 0`,
so an implementation that skips all 189 scenarios is scored **Gold**. Called
directly with an all-skipped tally, it returns `gold`. Our runner deliberately
does not call it and reports pytest's own counts instead.

## Running it

The kit is not on PyPI: `uv pip install neo4j-agent-memory-tck` fails with "not
found in the package registry", so a clone is the only install path. It lands
under `evals/`, gitignored beside the cognee and MegaMem checkouts.

```bash
git clone https://github.com/neo4j-labs/agent-memory-tck.git evals/agent-memory-tck
git -C evals/agent-memory-tck checkout 4603b91f4fc831f19901b4f68d96f8dc039e9a38

# The kit brings pytest-asyncio and pydantic, which Tesserae does not depend on.
# Keep it out of the project venv so `uv run pytest` stays a clean measurement.
uv venv --python 3.11 ~/.blackhole/Tesserae/tck-venv
VIRTUAL_ENV=~/.blackhole/Tesserae/tck-venv uv pip install -e . -e evals/agent-memory-tck

~/.blackhole/Tesserae/tck-venv/bin/python -m evals.tck.run_tck --tier bronze
```

`--keep-tree` leaves the assembled run tree in place to inspect. Omit `--tier` to
run everything the kit collects.

The runner copies the kit's `tck/tests/` into a scratch directory and writes
`evals/tck/conftest.py` over `tests/v1/conftest.py` — the path whose upstream
contents wire Neo4j's `ReferenceAdapter`, and the nearest conftest to the
scenarios, so it is the one that wins. **The clone is never modified.** A mutated
checkout looks identical to upstream at a glance, and the next reader cannot tell
which result came from which code.

The adapter writes to a scratch project root: `$TESSERAE_TCK_ROOT` if set, else a
fresh temporary directory. It must never point at a real project's `.tesserae/`.

`CI` set in the environment makes the runner print SKIP and exit 0. The kit is a
gitignored clone pinned to a commit; a green CI job that silently skipped is
worse than no job. `tests/test_tck_adapter.py` is what runs in the suite — it
exercises `evals/tck/memory.py` directly, offline, and needs no clone.

## Adapter decisions that shape the number

Three, all disclosed because each moves the count:

1. `clear_all_data` deletes the SQLite file and the overlay journal. The kit
   calls it before each of its 189 scenarios purely for isolation and asserts
   nothing about it, so this is harness lifecycle rather than a contract
   operation. It is *not* evidence that Tesserae can delete anything — the
   operations the kit does assert on, `delete_message` and `clear_session`, are
   refusals.
2. Message timestamps are strictly increasing. The turns table's uniqueness
   key is `(session_path, ts, role, text_hash)`, so two identical messages
   written inside the same microsecond collapse into one and `record_turns`
   returns 0 without raising. Stamping strictly increasing timestamps is the
   adapter's job and makes ordering total. The collapse is still real and still
   reachable, `tests/test_tck_adapter.py` pins it, and the adapter turns any
   zero-row write into a loud `RuntimeError` rather than storing nothing
   silently.
3. Reads sweep three KST day labels. `turns_for_day` is the store's only
   public read and it takes one day; nothing exposes "which days does this
   session span". Sweeping yesterday, today and tomorrow uses only the public
   API and covers a run that straddles KST midnight.

What the adapter deliberately does **not** do is keep a private dict. A fallback
store would pass every search scenario in the kit — they pass `threshold=0.0` and
assert little more than `len(results) > 0`, and one asserts only
`isinstance(results, list)` — while measuring nothing about Tesserae. The
refusals are the measurement.

## Licence

The kit is Apache-2.0, © 2026 Neo4j Labs, and its README states it is
"maintained by Neo4j Labs as an experimental, community-supported project. It is
not officially supported by Neo4j." Running it and publishing the results is
permitted; the trademarks and badges are not (§6).
