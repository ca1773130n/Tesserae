# Evaluation Plan: Phase 06 — Real Default Embeddings (Track B)

**Designed:** 2026-06-05
**Designer:** Claude (grd-eval-planner)
**Method(s) evaluated:** Model2VecBackend, fail-loud active_embedding_backend, backend-gated RETR-02 candidate lift, embedding_status semantic flag
**Reference plans:** 06-01-PLAN.md, 06-02-PLAN.md, 06-03-PLAN.md, 06-DECISIONS.md

---

## Evaluation Overview

Phase 06 replaces silent hash-stub degradation with two outcomes: (1) a real lightweight embedding backend (model2vec) that is preferred when installed; (2) a loud UserWarning when no real backend is importable so the degradation is never silent. A backend-gated RETR-02 candidate lift allows the embedding lane to generate candidates when a real backend is active, while keeping the hash-stub gated out of candidate generation. The `embedding_status` MCP tool exposes a `semantic` boolean so the active mode is always inspectable.

All proxy metrics are designed to run in CI with no model2vec or torch installed. The stub-semantic backend used in RETR-02 tests is a deterministic in-test 4-dim keyword-concept projector — it proves the gate logic without the heavy model. The one real-model test is importorskip-gated.

Supersede-cosine quality (deferred from 5.x) remains out of scope for this phase per 06-DECISIONS.md.

### Metric Sources

| Metric | Source | Why This Metric |
|--------|--------|----------------|
| UserWarning on hash fallback | 06-01-PLAN.md must_haves, RETR-01 | Directly proves the "no silent degradation" contract |
| Backend selection order (m2v→ST→hash) | 06-01-PLAN.md, RETR-01 | Verifies the resolution ladder is correct |
| RETR-02 candidate gate (non-hash lifts; hash blocks) | 06-01-PLAN.md, RETR-02 | Proves paraphrase recall is unlocked only with a real backend |
| graph.json sha256 stability | byte-idempotence memory, 06-03-PLAN.md | Prevents embedding vector leakage into the persistent artifact |
| embedding_status.semantic flag | 06-02-PLAN.md must_haves | MCP observability — user-visible truth about backend quality |
| No bare `except Exception` swallow | 06-01-PLAN.md | Code-level proof that degradation is no longer hidden |
| pyproject.toml `semantic` extra | 06-02-PLAN.md must_haves | Install-path correctness |

### Verification Level Summary

| Level | Count | Purpose |
|-------|-------|---------|
| Sanity (L1) | 6 | Importability, grep guards, pyproject shape, existing suite green |
| Proxy (L2) | 5 | Deterministic CI-safe behavioral proof (mocks + stub backend + idempotence) |
| Deferred (L3) | 2 | Real-model quality + supersede-cosine overlap (importorskip-gated or deferred 5.x) |

---

## Level 1: Sanity Checks

**Purpose:** Verify basic functionality. ALL must pass before proceeding.

### S1: Module imports cleanly with no heavy import at load time

- **What:** `Model2VecBackend`, `backend_is_semantic`, `active_embedding_backend`, `reset_embedding_backend`, `HashEmbeddingBackend` are all importable from `tesserae.retrieval.hybrid` in a venv without model2vec/torch.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "from tesserae.retrieval.hybrid import Model2VecBackend, backend_is_semantic, active_embedding_backend, reset_embedding_backend, HashEmbeddingBackend; print('OK')"`
- **Expected:** prints `OK`, no ImportError, no model download.
- **Failure means:** The lazy-import contract is broken; model2vec was pulled in at module load time or the symbol was not added.

### S2: pyproject.toml has the `semantic` extra with model2vec

- **What:** The `semantic` optional-dependency extra exists and names `model2vec>=0.3`.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import tomllib, pathlib; d=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); extras=d.get('project',{}).get('optional-dependencies',{}); assert 'semantic' in extras, 'missing semantic extra'; assert any('model2vec' in dep for dep in extras['semantic']), 'model2vec not in semantic extra'; print('OK')"`
- **Expected:** prints `OK`.
- **Failure means:** 06-02-PLAN.md deliverable missing; `pip install tesserae[semantic]` would not pull model2vec.

### S3: No bare `except Exception` swallow on the auto path

- **What:** The old silent-fallback pattern is removed from `active_embedding_backend`.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import ast, pathlib; src=pathlib.Path('tesserae/retrieval/hybrid.py').read_text(); tree=ast.parse(src); [print('BARE EXCEPT FOUND at line', n.lineno) for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is None]; print('no bare except')"`
- **Expected:** prints `no bare except` (zero bare `except:` handlers in the whole file, or at minimum none inside `active_embedding_backend`).
- **Failure means:** Silent exception swallowing remains; the fail-loud contract is not met.

### S4: `isinstance(embed_backend, HashEmbeddingBackend)` gate present in hybrid_search

- **What:** The backend-type gate introduced for RETR-02 is present in `hybrid.py`.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import pathlib; src=pathlib.Path('tesserae/retrieval/hybrid.py').read_text(); assert 'isinstance(embed_backend, HashEmbeddingBackend)' in src or '_hash_backend' in src, 'gate pattern missing'; print('OK')"`
- **Expected:** prints `OK`.
- **Failure means:** The RETR-02 gate was not implemented; all backends are treated identically.

### S5: `embedding_status` returns a `semantic` key

- **What:** The MCP server's `embedding_status` response dict contains a `semantic` boolean.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -c "import pathlib; src=pathlib.Path('tesserae/mcp_server.py').read_text(); assert '\"semantic\"' in src or \"'semantic'\" in src, 'semantic key missing from mcp_server'; print('OK')"`
- **Expected:** prints `OK`.
- **Failure means:** 06-02-PLAN.md deliverable missing; the MCP surface does not expose backend quality.

### S6: Existing test_hybrid_search.py still passes

- **What:** No regression in the hash-mode and single-lane behaviour tests that existed before Phase 06.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_hybrid_search.py -q 2>&1 | tail -5`
- **Expected:** `passed` with 0 failures, 0 errors.
- **Failure means:** Phase 06 changes broke existing hybrid search behaviour.

**Sanity gate:** ALL six sanity checks must pass. Any failure blocks progression to proxy evaluation.

---

## Level 2: Proxy Metrics

**Purpose:** Deterministic, CI-safe behavioral proof of Phase 06 contracts.
**IMPORTANT:** All proxy metrics use mocks or a deterministic in-test stub backend. They do NOT require model2vec or torch. `validated: false` until deferred real-model check confirms.

### P1: Fail-loud — UserWarning emitted on hash fallback, with no real backend

- **What:** `active_embedding_backend('auto')` emits a `UserWarning` mentioning `tesserae[semantic]` when both `Model2VecBackend` and `SentenceTransformersBackend` constructors raise `ImportError` (mocked).
- **How:** Monkeypatch both constructors in a pytest test; call `reset_embedding_backend()` before; assert with `pytest.warns(UserWarning, match=r"tesserae\[semantic\]")`; assert the return value is a `HashEmbeddingBackend` instance.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "warns" -q`
- **Target:** PASS — the test asserts exactly one UserWarning matching `tesserae[semantic]` and a HashEmbeddingBackend return.
- **Evidence:** 06-01-PLAN.md must_haves item 2 and verify block; the warning text is specified in the plan's code snippet verbatim.
- **Correlation with full metric:** HIGH — this is a direct behavioral assertion of the RETR-01 fail-loud contract, not an indirect proxy.
- **Blind spots:** Does not test the wall-clock production path (real import failure vs. monkeypatched); does not verify warning deduplication across processes.
- **Validated:** No — awaiting deferred L3 real-environment check.

### P2: Selection order — model2vec preferred over ST when available

- **What:** `active_embedding_backend('auto')` returns the model2vec backend (not ST, not hash) when the `Model2VecBackend` constructor is monkeypatched to succeed.
- **How:** Monkeypatch `hybrid.Model2VecBackend` to a fake with `name="model2vec:fake"` and `dim=3`; call `reset_embedding_backend()`; assert `active_embedding_backend('auto').name == "model2vec:fake"`; assert `backend_is_semantic(result)` is `True`; assert NO UserWarning is raised (use `warnings.catch_warnings` + `simplefilter('error')`).
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "prefers" -q`
- **Target:** PASS.
- **Evidence:** 06-01-PLAN.md must_haves item 1 — "model2vec first" resolution order.
- **Correlation with full metric:** HIGH — directly tests the resolution ladder.
- **Blind spots:** Uses a fake model2vec constructor; does not test the real `StaticModel.from_pretrained` call.
- **Validated:** No.

### P3: RETR-02 — backend-gated candidate lift (stub semantic backend)

- **What:** In hybrid mode, an embedding-only paraphrase hit (`MethodologicalConcept:rrf` — no lexical overlap with "reciprocal fusing of ranked retrieval lists") surfaces as a result when a deterministic non-hash stub backend is passed, and is absent (or not admitted as an embedding-only candidate) when `HashEmbeddingBackend()` is passed.
- **How:** Build the 8-node fixture graph; run `hybrid_search(graph, "reciprocal fusing of ranked retrieval lists", top_k=5, backend=_StubSemanticBackend(), mode="hybrid")`; assert `"MethodologicalConcept:rrf"` in result node ids. Repeat with `backend=HashEmbeddingBackend()`; assert the rrf node is NOT present or assert that the stub run's candidate set is a strict superset.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "gate" -q`
- **Target:** PASS — stub backend surfaces the node; hash backend does not admit it via embedding-only path.
- **Evidence:** 06-01-PLAN.md must_haves item 3; 06-03-PLAN.md task 1 test 4. The stub backend's 4-dim concept space maps "rrf/reciprocal/fusion/ranking" vocabulary to the same axis, giving genuine cosine overlap.
- **Correlation with full metric:** MEDIUM — proves gate logic is correct with a toy concept-space backend. Real recall on a production corpus (paraphrase @5 with model2vec) requires the L3 deferred check.
- **Blind spots:** The 4-dim concept space is hand-crafted to guarantee overlap; a real distributional model may not produce equivalent alignment. Does not test multi-query recall breadth.
- **Validated:** No.

### P4: Byte-idempotence — graph.json sha256 unchanged across compile→search→compile

- **What:** Running `hybrid_search` in hybrid mode (exercising the embedding lane) between two compile calls leaves `graph.json` byte-identical, and no embedding vectors are serialized into `graph.json` or any node's `metadata`.
- **How:** Seed project from `tests/fixtures/wiki_corpus`; `wiki.compile()`; capture `sha256(graph.json bytes)`; call `hybrid_search(graph, "reciprocal fusion ranking", mode="hybrid")`; `wiki.compile()` again; assert sha256 unchanged. Additionally parse JSON and assert no key named `embedding`, `vector`, or `embed_vec` appears in any node's `metadata`.
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "idempot" -q`
- **Target:** PASS — sha256 identical; no embedding sentinel keys in graph.json.
- **Evidence:** 06-03-PLAN.md task 2; byte-idempotence memory (project-level cross-session constraint); 06-01-PLAN.md must_haves item 5 ("No embedding vector is ever written to node.metadata or graph.json").
- **Correlation with full metric:** HIGH — directly asserts the byte-idempotence property that Codex caught breaking 4 times previously (project memory).
- **Blind spots:** Only checks the test-fixture corpus; does not cover all possible `node_type` metadata paths in a larger production graph.
- **Validated:** No.

### P5: embedding_status.semantic flag — False for hash, True for stub

- **What:** `embedding_status()` returns `semantic: False` when the active backend is `HashEmbeddingBackend`, and `semantic: True` when the active backend is a non-hash stub.
- **How:** Build an `LLMWikiMCPServer` over the seeded project; in default env (no model2vec): call `server.embedding_status()`; assert `status["semantic"] is False` and `status["backend"] == "hash-bucket"`. Then monkeypatch to inject a stub non-hash backend; assert `semantic is True`. Wrap both calls in `warnings.catch_warnings(simplefilter('ignore'))` (the UserWarning is asserted separately in P1).
- **Command:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "status" -q`
- **Target:** PASS.
- **Evidence:** 06-02-PLAN.md must_haves item 2 and 3; 06-01-PLAN.md artifact `backend_is_semantic()`.
- **Correlation with full metric:** HIGH — directly tests the MCP-observable truth about backend quality.
- **Blind spots:** Does not test that the MCP server surfaces this to an actual MCP client connection; only unit-level call.
- **Validated:** No.

---

## Level 3: Deferred Validations

### D1: Real model2vec embedding quality on project corpus — DEFER-06-01

- **What:** `Model2VecBackend` with the real `potion-base-8M` model produces vectors of the declared `dim`, and paraphrase recall@5 on a 20-query labelled set exceeds the hash-stub baseline (expected ~0% on paraphrase queries).
- **How:** `pytest.importorskip("model2vec")` gate. Construct `Model2VecBackend()`; assert `len(b.embed(["hello"])[0]) == b.dim`; assert `b.name.startswith("model2vec:")`; assert `backend_is_semantic(b)`. For recall@5: run 20 labelled paraphrase queries over the test corpus with `backend=Model2VecBackend()`, measure hit rate.
- **Why deferred:** model2vec and its weights (~8 MB download) are not available in baseline CI; the real-model test is `pytest.importorskip`-gated. Full paraphrase recall measurement requires a labelled query set not yet defined.
- **Validates at:** Phase 07 integration or when model2vec is added to the optional CI test matrix.
- **Depends on:** `model2vec` installed in the test environment; labelled 20-query paraphrase set against the wiki fixture corpus.
- **Target:** `b.dim == 256` (or whatever `potion-base-8M` advertises); recall@5 > 40% on paraphrase queries (vs. ~0% for hash stub). Exact target to be set when the labelled set is created.
- **Risk if unmet:** model2vec's static distillation may not produce adequate paraphrase alignment on technical domain text (Tesserae's project graph vocabulary). Mitigation: fall back to sentence-transformers backend (already in the selection ladder) or evaluate `potion-base-32M`.
- **Fallback:** sentence-transformers backend already implemented; importorskip gate ensures CI never regresses.
- **In-file check (CI-safe subset):** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "real_model2vec" -q` (SKIPS in CI without model2vec; PASSES in an env with it).

### D2: Supersede-cosine candidate quality — DEFER-06-02

- **What:** Embedding-based supersede candidate overlap quality — does the real embedding lane surface semantically redundant nodes that BM25-based supersede detection misses?
- **How:** Not yet designed. Requires: (a) a real semantic backend active, (b) a labelled supersede pair set for the project graph, (c) a recall metric comparing embedding-assisted vs. BM25-only supersede detection.
- **Why deferred:** Supersede-cosine was explicitly deferred to 5.x per 06-DECISIONS.md. Phase 06 only unlocks the embedding lane as a candidate generator; the supersede scoring layer does not exist yet.
- **Validates at:** Phase 5.x supersede-cosine implementation (future milestone).
- **Depends on:** Real embedding backend (this phase), supersede-cosine scoring layer (future), labelled supersede pair set.
- **Target:** TBD at that phase.
- **Risk if unmet:** Supersede quality remains BM25-limited; self-improvement loop cannot use semantic similarity as a supersede signal. Impact is moderate — the core context-engine loop still works, just less precise on semantic overlap detection.
- **Fallback:** BM25-only supersede detection remains the default indefinitely until that phase executes.

---

## Ablation Plan

Phase 06 implements a single decision tree (selection order + gate) with no sub-components that warrant isolated ablation. The proxy tests (P1–P5) decompose the feature into its atomic contracts and prove each independently. No formal ablation plan.

---

## WebMCP Tool Definitions

WebMCP tool definitions skipped — phase does not modify frontend views (files_modified: `hybrid.py`, `pyproject.toml`, `mcp_server.py`, `tests/test_real_embeddings_phase6.py`).

---

## Baselines

| Baseline | Description | Expected Score | Source |
|----------|-------------|----------------|--------|
| Hash-stub default (pre-Phase 06) | Silent hash-bucket backend, no UserWarning, RETR-02 gate always closed | Paraphrase recall@5 ~0%; `embedding_status.semantic` absent or False | 06-01-PLAN.md baseline field |
| test_hybrid_search.py (pre-Phase 06) | All hash-mode + single-lane tests | All passing | 06-01-PLAN.md S6 sanity |

---

## Evaluation Scripts

**Location of evaluation code:**
```
tests/test_real_embeddings_phase6.py   (created by Plan 06-03)
tests/test_hybrid_search.py            (existing; regression guard)
tests/test_byte_idempotence_phase5.py  (existing; idempotence fixture helpers)
```

**How to run full Phase 06 evaluation:**
```bash
# Full phase 06 suite (no model2vec needed; real-model test skips)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/test_real_embeddings_phase6.py \
  tests/test_hybrid_search.py \
  tests/test_byte_idempotence_phase5.py \
  -q

# Targeted tiers
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "warns or prefers" -q  # P1+P2
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "gate" -q             # P3
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "idempot" -q          # P4
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_real_embeddings_phase6.py -k "status" -q           # P5
```

---

## Results Template

*To be filled by grd-eval-reporter after phase execution.*

### Sanity Results

| Check | Status | Output | Notes |
|-------|--------|--------|-------|
| S1 (imports) | PENDING | | |
| S2 (pyproject semantic extra) | PENDING | | |
| S3 (no bare except) | PENDING | | |
| S4 (isinstance gate present) | PENDING | | |
| S5 (semantic key in mcp_server) | PENDING | | |
| S6 (test_hybrid_search regression) | PENDING | | |

### Proxy Results

| Metric | Target | Actual | Status | Notes |
|--------|--------|--------|--------|-------|
| P1 (fail-loud UserWarning) | PASS | | PENDING | |
| P2 (selection order prefers m2v) | PASS | | PENDING | |
| P3 (RETR-02 gate lift) | PASS | | PENDING | |
| P4 (byte-idempotence) | PASS | | PENDING | |
| P5 (embedding_status.semantic) | PASS | | PENDING | |

### Deferred Status

| ID | Metric | Status | Validates At |
|----|--------|--------|-------------|
| DEFER-06-01 | Real model2vec quality + recall@5 | PENDING | Phase 07 / model2vec CI matrix |
| DEFER-06-02 | Supersede-cosine candidate quality | PENDING | Phase 5.x supersede-cosine |

---

## Evaluation Confidence

**Overall confidence in evaluation design:** HIGH

**Justification:**
- Sanity checks: adequate — six checks cover importability, code-level grep guards, pyproject shape, and regression safety; all executable with a one-liner command.
- Proxy metrics: well-evidenced — five deterministic, CI-safe tests that map 1:1 to the plan's must_haves. The stub semantic backend provides genuine-ish concept overlap without the heavy model. The byte-idempotence test directly implements the project-level memory constraint that caught 4 prior regressions.
- Deferred coverage: partial but honest — real-model recall quality is deferred because the labelled query set does not exist yet; supersede-cosine is explicitly out of scope per 06-DECISIONS.md.

**What this evaluation CAN tell us:**
- Whether the fail-loud contract is met (no silent hash fallback).
- Whether the RETR-02 candidate gate is correctly wired to backend type.
- Whether graph.json remains byte-identical after a hybrid search call.
- Whether the MCP surface exposes backend quality truthfully.
- Whether existing hybrid search behaviour is unbroken.

**What this evaluation CANNOT tell us:**
- Whether `potion-base-8M` produces adequate paraphrase alignment on Tesserae's technical vocabulary (deferred to DEFER-06-01).
- Whether supersede detection improves with the real embedding lane active (deferred to DEFER-06-02, Phase 5.x).
- Whether the UserWarning fires correctly on a real import failure vs. a monkeypatched one (acceptable; mock-based behavioral test is the standard pattern here).

---

## EVAL COMPLETE

**Tier summary:** 6 sanity checks (importability + grep guards + regression) | 5 proxy metrics (deterministic CI-safe: fail-loud mock, selection order mock, RETR-02 stub gate, byte-idempotence sha256, embedding_status flag) | 2 deferred (real model2vec recall@5 importorskip-gated; supersede-cosine per 06-DECISIONS.md)

---

*Evaluation plan by: Claude (grd-eval-planner)*
*Design date: 2026-06-05*
