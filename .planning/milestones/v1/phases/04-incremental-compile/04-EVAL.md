# Evaluation Plan: Phase 4 — Incremental / Streaming Compile

**Designed:** 2026-06-03
**Designer:** Claude (grd-eval-planner)
**Method(s) evaluated:** Provenance-driven incremental differ; node_provenance SQLite sidecar; persistent async runtime (url_resolver); feature-flagged incremental path; delete_nodes_by_source tombstone logic
**Reference:** 04-RESEARCH.md (direct codebase inspection); 04-01 through 04-04 PLANs

---

## Evaluation Overview

Phase 4 is the highest-risk phase in the milestone: it replaces the fragile `changed_only` evict-by-source-path merge with a designed incremental layer backed by a `node_provenance` SQLite sidecar. Correctness and determinism completely dominate performance as evaluation axes. A partial failure (incremental compiles successfully but silently drops cross-file nodes) is worse than an outright crash.

The canonical JSON artifact (`graph.json`) stays the source of truth; byte-idempotence is a hard invariant. Provenance timestamps (`first_seen_at`/`last_updated_at`) live exclusively in the SQLite sidecar — never in `graph.json`. The evaluation therefore centers on two guarantees: (1) the incremental path produces a graph byte-identical to a full compile of the same final corpus, and (2) the 2400→1700 anti-collapse: a cross-file concept node owned by an unchanged file survives a changed-file re-extract.

There is no meaningful external paper to reference for metric selection; metrics are derived from the documented footgun, the existing `test_idempotence.py` golden test, and the CMP-01 through CMP-04 requirements.

All tests run deterministically with no pytest-asyncio, no real sleeps, no wall-clock calls. The SQLite sidecar `.db` file is excluded from byte-hash comparisons (it is already excluded for `.jsonl` files; `.db` must join that exclude set).

### Metric Sources

| Metric | Source | Why This Metric |
|--------|--------|----------------|
| Byte-identical graph.json (incremental == full) | CMP-03; 04-04-PLAN golden parity test | Defines correctness of incremental path absolutely |
| Node count after K-file edit (no collapse) | Documented 2400→1700 footgun; BENCHMARKS.md | Primary failure mode of the old path |
| Cross-file concept survival | 04-RESEARCH.md Pitfall 2 | The exact bug that motivated this phase |
| Byte-idempotence of full compile (existing) | tests/test_idempotence.py | Must not regress |
| node_provenance non-empty after full compile | CMP-02; 04-01-PLAN | Seed guarantee for all future incremental runs |
| delete_nodes_by_source tombstones empty-provenance only | 04-01-PLAN; 04-RESEARCH.md code example | Core correctness of delete logic |
| first_seen_at/last_updated_at deterministic (no now()) | 04-RESEARCH.md Pitfall 1 | byte-idempotence constraint |
| url_resolver: no asyncio.run per call | CMP-04; 04-02-PLAN | Pathological per-call overhead |
| Feature flag present, default OFF | 04-03-PLAN; roadmap | Safe rollout gate |

### Verification Level Summary

| Level | Count | Purpose |
|-------|-------|---------|
| Sanity (L1) | 9 | Port protocol compliance, schema creation, API surface, grep-based static checks |
| Proxy (L2) | 7 | Golden parity (K=1/5/21), anti-collapse, byte-idempotence regression, provenance seed, tombstone unit, flag fallback |
| Deferred (L3) | 2 | Real-corpus parity before flag goes default-ON; incremental wall-clock vs full |

---

## Level 1: Sanity Checks

**Purpose:** Verify basic functionality. ALL must pass before proxy evaluation begins.

### S1: GraphStore protocol exposes delete operations

- **What:** `GraphStore` protocol defines `delete_node(node_id: str)` and `delete_nodes_by_source(source_paths: Set[str]) -> Set[str]`
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "from tesserae.ports.graph_store import GraphStore; import inspect; src = inspect.getsource(GraphStore); assert 'delete_node' in src and 'delete_nodes_by_source' in src; print('OK')"`
- **Expected:** `OK`
- **Failure means:** 04-01 plan not implemented; port protocol incomplete

### S2: SqliteGraphStore is protocol-compliant (isinstance check)

- **What:** After adding delete methods, `SqliteGraphStore` still satisfies the `@runtime_checkable` `GraphStore` protocol
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "from tesserae.graph_stores.sqlite import SqliteGraphStore; from tesserae.ports.graph_store import GraphStore; s = SqliteGraphStore.__new__(SqliteGraphStore); assert isinstance(s, GraphStore), 'Protocol mismatch'; print('OK')"`
- **Expected:** `OK`
- **Failure means:** A required protocol method is missing from `SqliteGraphStore`

### S3: node_provenance table auto-creates on _ensure_schema

- **What:** A fresh `SqliteGraphStore` (no pre-existing `.db`) creates the `node_provenance` table automatically
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import tempfile, os; from tesserae.graph_stores.sqlite import SqliteGraphStore; db = os.path.join(tempfile.mkdtemp(), 'g.db'); s = SqliteGraphStore(db); import sqlite3; con = sqlite3.connect(db); tables = {r[0] for r in con.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()}; assert 'node_provenance' in tables, tables; print('OK')"`
- **Expected:** `OK`
- **Failure means:** `_ensure_schema` not updated; sidecar table missing

### S4: compile() / ingest() accept changed_paths parameter

- **What:** `ProjectWiki.compile` (and/or `BatchIngestRunner.run`) accepts `changed_paths: Optional[List[Path]]` without raising TypeError
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import inspect; from tesserae.project import ProjectWiki; sig = inspect.signature(ProjectWiki.compile); assert 'changed_paths' in sig.parameters, list(sig.parameters.keys()); print('OK')"`
- **Expected:** `OK`
- **Failure means:** 04-03 API change not landed; daemon cannot thread paths into compile

### S5: daemon threads changed_paths into compile call

- **What:** `daemon._run_pipeline` passes `changed_paths=` to `wiki.compile` (not just `changed_only=bool(paths)`)
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import inspect; from tesserae.engine.daemon import WikiDaemon; src = inspect.getsource(WikiDaemon._run_pipeline); assert 'changed_paths' in src, 'changed_paths not found in _run_pipeline source'; print('OK')"`
- **Expected:** `OK`
- **Failure means:** 04-03 daemon wiring incomplete; changed_paths lost at call boundary

### S6: url_resolver has no asyncio.run per-call (static grep)

- **What:** `url_resolver.py` does not contain `asyncio.run(` in any method body (only the module-level background loop setup is permitted)
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import ast, pathlib; src = pathlib.Path('tesserae/graph_stores/url_resolver.py').read_text(); tree = ast.parse(src); calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'run' and isinstance(n.func.value, ast.Name) and n.func.value.id == 'asyncio']; assert len(calls) == 0, f'{len(calls)} asyncio.run calls found at lines {[c.lineno for c in calls]}'; print('OK')"`
- **Expected:** `OK`
- **Failure means:** 04-02 not implemented; per-call asyncio.run pathology remains

### S7: url_resolver imports cleanly and module-level loop starts

- **What:** `import tesserae.graph_stores.url_resolver` succeeds and the module-level background thread is alive
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import tesserae.graph_stores.url_resolver as ur; assert hasattr(ur, '_loop') or hasattr(ur, '_bg_thread'), 'No background loop found'; print('OK')"`
- **Expected:** `OK`
- **Failure means:** 04-02 broken import or loop not initialised at module level

### S8: Feature flag present, defaults to full-compile (OFF)

- **What:** A config key or constant `incremental_compile` exists and defaults to `False`
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "from tesserae.project import ProjectWiki; import inspect; src = inspect.getsource(ProjectWiki.compile); assert 'incremental_compile' in src or 'INCREMENTAL_COMPILE' in src, 'feature flag not found'; print('OK')"`
- **Expected:** `OK`
- **Failure means:** 04-03 feature flag not implemented; incremental path not safely gated

### S9: Provenance timestamps absent from graph.json schema

- **What:** `ResearchNode` serialisation (`to_json` / `model_dump`) contains no `first_seen_at` or `last_updated_at` fields
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "from tesserae.research_graph import ResearchNode, ResearchNodeType; n = ResearchNode(id='x', name='X', type=ResearchNodeType.CONCEPT, aliases=[]); import json; d = json.dumps(n.__dict__ if hasattr(n, '__dict__') else vars(n), default=str); assert 'first_seen_at' not in d and 'last_updated_at' not in d, d; print('OK')"`
- **Expected:** `OK`
- **Failure means:** Timestamps leaked into ResearchNode fields; byte-idempotence broken

**Sanity gate:** ALL 9 sanity checks must pass. Any failure blocks proxy evaluation and must be fixed first.

---

## Level 2: Proxy Metrics

**Purpose:** Correctness and determinism evaluation of the incremental path against the ground truth (full compile of the same corpus). These are the heart of the evaluation for this phase.

**IMPORTANT:** These proxy metrics use the `tests/fixtures/wiki_corpus/` fixture with a deterministic (non-LLM) extractor. Results are reproducible and version-controlled. `validated: false` until deferred real-corpus validation confirms.

### P1: Golden parity K=1 — incremental byte-identical to full compile

- **What:** After changing 1 file in the fixture corpus, incremental compile produces a `graph.json` byte-identical to a fresh full compile of the modified corpus
- **How:** Run the golden parity test for K=1 condition from 04-04-PLAN
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_incremental_parity.py::test_parity_k1 -v`
- **Target:** PASS (byte-hash equality; zero diff between incremental and full graph.json)
- **Evidence:** 04-04-PLAN design; byte-idempotence discipline already proven by test_idempotence.py
- **Correlation with full metric:** HIGH — directly measures the correctness guarantee for the simplest case
- **Blind spots:** Fixture corpus is small; real cross-file alias patterns may differ
- **Validated:** No — awaiting deferred real-corpus parity (D1)

### P2: Golden parity K=5 — incremental byte-identical to full compile

- **What:** After changing 5 files, incremental compile produces byte-identical graph.json to full compile
- **How:** Run the golden parity test for K=5 condition from 04-04-PLAN
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_incremental_parity.py::test_parity_k5 -v`
- **Target:** PASS
- **Evidence:** Same as P1; K=5 exercises the multi-file eviction path more thoroughly
- **Correlation with full metric:** HIGH
- **Blind spots:** Fixture cross-file concept density may be lower than production
- **Validated:** No — awaiting D1

### P3: Golden parity K=21 — the documented collapse scenario

- **What:** After changing 21 files (the exact scenario that triggers the 2400→1700 collapse in the old path), incremental compile produces byte-identical graph.json to full compile. This is the direct regression test for the footgun.
- **How:** Run the golden parity test for K=21 condition from 04-04-PLAN
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_incremental_parity.py::test_parity_k21 -v`
- **Target:** PASS
- **Evidence:** 04-04-PLAN; 04-RESEARCH.md "The 2400→1700 collapse mechanism"
- **Correlation with full metric:** HIGH — directly exercises the failure mode this phase eliminates
- **Blind spots:** Fixture may not have 21 files; test may need to repeat-edit fewer files to reach K=21 changes
- **Validated:** No — awaiting D1

### P4: Anti-collapse — cross-file concept survives changed-file re-extract

- **What:** A concept node that appears in BOTH a changed file and an unchanged file is NOT tombstoned after an incremental compile of only the changed file
- **How:** Run the anti-collapse unit test from 04-04-PLAN (explicit fixture with a shared concept node)
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_incremental_parity.py::test_cross_file_concept_survives -v`
- **Target:** PASS; shared concept node present in output graph with correct provenance rows for both source files
- **Evidence:** 04-RESEARCH.md Pitfall 2 — the exact anti-collapse guarantee required by CMP-03
- **Correlation with full metric:** HIGH — directly tests the cross-file provenance correctness invariant
- **Blind spots:** Only tests a single shared concept; production alias networks are more complex
- **Validated:** No — awaiting D1

### P5: delete_nodes_by_source tombstones ONLY empty-provenance nodes (unit)

- **What:** `delete_nodes_by_source({'file_a.md'})` deletes nodes whose ONLY provenance is `file_a.md`; nodes also attributed to `file_b.md` are retained with their `file_b.md` provenance row intact
- **How:** Unit test with an in-memory fixture SqliteGraphStore populated with known provenance rows
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_graph_store.py::test_delete_nodes_by_source_tombstones_empty_provenance -v`
- **Target:** PASS; deleted count equals only the nodes with single-source provenance in the changed set
- **Evidence:** 04-01-PLAN; 04-RESEARCH.md code example for delete_nodes_by_source
- **Correlation with full metric:** HIGH — tests the core deletion predicate in isolation
- **Blind spots:** Does not test the full incremental path end-to-end; only the SQL predicate
- **Validated:** No

### P6: Byte-idempotence of full compile must not regress

- **What:** The existing `test_compile_is_byte_idempotent` golden test remains green after Phase 4 changes
- **How:** Run existing test unchanged
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_idempotence.py::test_compile_is_byte_idempotent -v`
- **Target:** PASS (pre-existing green; any failure is a Phase 4 regression)
- **Evidence:** Already proven; this is a non-regression check
- **Correlation with full metric:** HIGH — directly verifies the byte-idempotence invariant that incremental must preserve
- **Blind spots:** Does not test incremental path idempotence; only full compile
- **Validated:** No — regression status only

### P7: Feature flag fallback — full compile runs when flag is OFF

- **What:** With `incremental_compile: false` (default), `wiki.compile(changed_paths=[...])` executes a full compile identical to a compile with no `changed_paths` argument
- **How:** Run regression test asserting full compile path taken when flag is OFF
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_incremental_parity.py::test_flag_off_runs_full_compile -v`
- **Target:** PASS; output graph byte-identical between `changed_paths=None` and `changed_paths=[...]` when flag is OFF
- **Evidence:** 04-03-PLAN feature flag requirement; roadmap "full-compile fallback"
- **Correlation with full metric:** MEDIUM — verifies safe default, not incremental correctness
- **Blind spots:** Does not test flag-ON behavior; that is P1–P4
- **Validated:** No

---

## Level 3: Deferred Validations

**Purpose:** Full validation requiring the real project corpus (~2620 nodes / 8529 edges) or integration context not available during fixture-based testing.

### D1: Real-corpus incremental-vs-full parity — DEFER-04-01

- **What:** Incremental compile of K changed files on the actual `.tesserae/` project corpus produces a `graph.json` byte-identical to a full compile of the same modified corpus (K=1, K=5, K=21)
- **How:** Run the parity test suite (same as P1–P3) but against a checkout of the real corpus with deterministic extractor
- **Why deferred:** Real corpus requires the actual project data (~2620 nodes) not present in `tests/fixtures/wiki_corpus/`; also requires the feature flag to be turned ON by a human reviewer after P1–P4 pass
- **Validates at:** phase-04-flag-enable (manual gate: reviewer enables `incremental_compile: true` after proxy tests pass in CI)
- **Depends on:** P1, P2, P3, P4 all passing; real corpus available; feature flag enabled
- **Target:** Byte-identical `graph.json` (zero diff); node count matches full compile exactly
- **Risk if unmet:** Incremental path ships with unknown behaviour on production data; flag must stay OFF until resolved
- **Fallback:** Keep flag OFF indefinitely; incremental path is never exercised in production

### D2: Incremental wall-clock < full compile on real corpus — DEFER-04-02

- **What:** Incremental compile of K=5 changed files on the real corpus completes faster than a full compile of the same corpus
- **How:** Timed run comparison (`time wiki.compile(changed_paths=[5 paths])` vs `time wiki.compile()`) on the real corpus
- **Why deferred:** Performance benchmark requires real corpus scale; fixture corpus is too small to show meaningful speedup; also deferred until D1 confirms correctness
- **Validates at:** phase-04-flag-enable (same gate as D1)
- **Depends on:** D1 passing
- **Target:** Incremental wall-clock < 50% of full compile wall-clock at K=5 on a ~2620-node corpus
- **Risk if unmet:** Incremental path is correct but not faster; acceptable for correctness but undermines the latency motivation (CMP-01)
- **Fallback:** Accept incremental for correctness benefit alone; investigate bulk-upsert bottleneck

---

## Ablation Plan

### A1: Remove node_provenance table — verify incremental falls back to full compile

- **Condition:** Delete `node_provenance` table from an existing `.db` before running incremental compile
- **Expected impact:** Compile detects missing table, emits warning, runs full compile (not a silent partial compile)
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_incremental_parity.py::test_missing_provenance_table_falls_back -v`
- **Evidence:** 04-RESEARCH.md "Silent downgrade on missing provenance table" pitfall — failure mode explicitly documented

### A2: Verify old changed_only path is unreachable when incremental flag is ON

- **Condition:** With `incremental_compile: true`, confirm the `changed_only` evict-by-source-path code block (project.py lines 489–512) is NOT executed
- **Expected impact:** The old merge block is either deleted or guarded behind `not config.incremental_compile`; a counter or log statement in the old block never fires
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_incremental_parity.py::test_old_changed_only_not_executed -v`
- **Evidence:** 04-03-PLAN; the old path being active would mean two incremental strategies run simultaneously

---

## WebMCP Tool Definitions

WebMCP tool definitions skipped — phase does not modify frontend views.

---

## Baselines

| Baseline | Description | Expected Score | Source |
|----------|-------------|----------------|--------|
| Full compile node count | ~2620 nodes / 8529 edges | 2620 nodes, 8529 edges | BENCHMARKS.md (last compile 2026-06-01) |
| Byte-idempotence (existing) | test_compile_is_byte_idempotent | PASS (pre-existing) | tests/test_idempotence.py |
| Incremental collapse baseline (old path) | 21-file edit on old path → ~1700 nodes | 1700 nodes (the footgun) | 04-RESEARCH.md documented footgun |

---

## Evaluation Scripts

**Location of evaluation code:**
```
tests/test_incremental_parity.py     — to be created in 04-04 plan execution
tests/test_graph_store.py            — existing; add delete_nodes_by_source test
tests/test_idempotence.py            — existing; must stay green (P6)
```

**How to run full proxy evaluation:**
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_incremental_parity.py tests/test_graph_store.py::test_delete_nodes_by_source_tombstones_empty_provenance tests/test_idempotence.py::test_compile_is_byte_idempotent -v
```

---

## Results Template

*To be filled by grd-eval-reporter after phase execution.*

### Sanity Results

| Check | Status | Output | Notes |
|-------|--------|--------|-------|
| S1: delete ops in protocol | | | |
| S2: isinstance protocol check | | | |
| S3: node_provenance auto-create | | | |
| S4: changed_paths in compile() | | | |
| S5: daemon threads changed_paths | | | |
| S6: no asyncio.run per call (grep) | | | |
| S7: url_resolver imports cleanly | | | |
| S8: feature flag present, default OFF | | | |
| S9: timestamps absent from graph.json | | | |

### Proxy Results

| Metric | Target | Actual | Status | Notes |
|--------|--------|--------|--------|-------|
| P1: parity K=1 | PASS | | | |
| P2: parity K=5 | PASS | | | |
| P3: parity K=21 (anti-collapse) | PASS | | | |
| P4: cross-file concept survives | PASS | | | |
| P5: tombstone empty-provenance only | PASS | | | |
| P6: byte-idempotence regression | PASS | | | |
| P7: flag-OFF full compile fallback | PASS | | | |

### Ablation Results

| Condition | Expected | Actual | Conclusion |
|-----------|----------|--------|------------|
| A1: missing provenance table | Falls back, warns | | |
| A2: old changed_only unreachable | Not executed | | |

### Deferred Status

| ID | Metric | Status | Validates At |
|----|--------|--------|-------------|
| DEFER-04-01 | Real-corpus incremental-vs-full parity | PENDING | phase-04-flag-enable |
| DEFER-04-02 | Incremental wall-clock < full compile | PENDING | phase-04-flag-enable |

---

## Evaluation Confidence

**Overall confidence in evaluation design:** HIGH

**Justification:**
- Sanity checks: adequate — 9 checks covering every new API surface; all static or in-process, no fixture required
- Proxy metrics: well-evidenced — P1–P4 directly test the documented failure mode; P5 tests the deletion predicate in isolation; P6 is a pre-existing golden test; evidence sourced from 04-RESEARCH.md direct code inspection
- Deferred coverage: complete for correctness and performance on real corpus; gated correctly on flag-enable milestone

**What this evaluation CAN tell us:**
- Whether the new port protocol and SQLite schema are correctly wired (S1–S9)
- Whether the incremental differ produces byte-identical results on the fixture corpus for all three K values (P1–P3)
- Whether cross-file concept nodes are protected from tombstoning (P4)
- Whether the deletion predicate is correct in isolation (P5)
- Whether Phase 4 regresses the existing byte-idempotence golden test (P6)
- Whether the feature flag safely defaults to full compile (P7)

**What this evaluation CANNOT tell us:**
- Whether incremental parity holds on the real ~2620-node corpus — deferred to DEFER-04-01 (requires flag-enable milestone)
- Whether the incremental path is actually faster than full compile on production-scale data — deferred to DEFER-04-02
- Whether the persistent async loop in url_resolver.py is correct under concurrent access (out of scope for this phase; Phase 7 daemon/serve unification)

---

## EVAL COMPLETE

**Tier summary:** 9 sanity checks (static/in-process, zero external deps) | 7 proxy metrics (golden parity K=1/5/21, anti-collapse, tombstone unit, byte-idempotence regression, flag fallback) | 2 deferred validations (real-corpus parity + perf, both gated on flag-enable milestone).

---

*Evaluation plan by: Claude (grd-eval-planner)*
*Design date: 2026-06-03*
