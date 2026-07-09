# Delta-Scoped Code-Graph Regeneration: the No-Op Extraction Cache (OpenWiki Candidate F)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the compile pipeline actually CONSUME the freshness delta that landed today (recorded `git_head` + `CODE_GRAPH_STALE_FILE` lint findings are report-only): when the code tree is provably unchanged since the last compile, skip the full code-graph re-extraction — today a full tree walk + AST parse of every code file on EVERY compile (`project.py:709`, measured on this repo: **52.8s, 17,226 files, 219K nodes/557K edges**, paid even when the markdown no-op skip fires) — by caching the pure extractor output keyed on a stat manifest of the walked file list. When re-extraction does run, report the code-tree delta (changed/added/removed file counts) in the compile result and CLI output. This is OpenWiki's `git log <lastHead>..HEAD --name-status` update-scoping (`src/agent/utils.ts:257-321`) adapted to a deterministic pipeline: **whole-layer grain** — reuse everything or re-extract everything, never a partial graph (the exact contract of the existing markdown no-op skip, `project.py:620-657`).

**Architecture:** Three touch points, zero new deps, one new cache file. (1) `tesserae/code_graph.py` gains a `stat_manifest` over the discovered file list, an extractor source fingerprint, and cache read/write helpers; `CodeGraphExtractor` gains `extract_files()` (the existing `extract_paths` body minus discovery). (2) `tesserae/research_graph.py` gains `graph_from_payload()` (extracted verbatim from `project.py::load_graph_file`, which becomes a thin wrapper) so the cache can rehydrate a graph without importing `project.py` (circular). (3) `tesserae/project.py::ingest` replaces the unconditional `CodeGraphExtractor(...).extract_paths(code_inputs)` at line 709 with the gate: manifest+fingerprint match → rehydrate cached graph; otherwise extract, write cache, report delta. Everything downstream (`merge_graphs`, `_record_producer_provenance("__code_graph__", ...)`, `partition_graph`) is untouched and byte-identical by construction.

**Tech Stack:** Python 3 stdlib (`hashlib`, `json`, `os`, `pathlib`), pytest via `.venv/bin/python -m pytest` (system python3 is 3.9 and fails collection). No LLM, no network, no git subprocess in the gate (see below).

## Why this is not redundant (verified 2026-07-09)

- **Incremental compile does not cover the code layer.** The experimental provenance differ (`incremental_compile`, OFF by default) scopes markdown re-extraction only; the code graph "re-derives its nodes/edges from the repo every compile" (comment at `project.py:713`), including on the `noop_skip` path where the markdown corpus is unchanged. Nothing here re-implements the differ: this plan never re-extracts a subset.
- **Today's git-delta machinery is read-only.** `read_git_head` / `git_head` in `.build-history.jsonl` / `CODE_GRAPH_BEHIND` + `CODE_GRAPH_STALE_FILE` (landed in commit f1cf98244b) report staleness; no code path consumes the delta to scope work. This plan is the consumer.
- **This project pays the cost.** `.tesserae/config.json` has `source_kind: "Repository"` → the `kind in {"CodeProject","Repository","Project"}` branch runs on every compile, and refresh is hook-triggered. `code-graph.json` is 33.6 MB.

## Why the gate is a stat manifest, not git (deliberate deviation — measured)

OpenWiki's gate is `lastHead..HEAD`. That gate is UNSOUND for Tesserae's extractor: `CodeGraphExtractor.iter_code_files` walks the filesystem, including gitignored files. Verified on this repo: `evals/` is in `.gitignore` and contributes **16,503 of the 17,226 walked code files** — a change there is invisible to `git status --porcelain` and to `recorded_head..HEAD`, so a git-only gate would serve a stale graph while claiming freshness. The sound generalization of the git delta to the extractor's actual input set is a manifest of `(rel_path, size, mtime_ns)` over the walked file list — measured at **0.07s** for all 17,226 files (the walk itself, which discovery needs regardless, is 17.1s). This is git's own index strategy (stat-first change detection). The git-flavored story stays where it landed today: lint's `CODE_GRAPH_BEHIND`/`CODE_GRAPH_STALE_FILE` findings.

Accepted residual risk (documented, same class git accepts): a content change that preserves both size and `mtime_ns` is not detected. This requires deliberate tampering; any normal write changes `mtime_ns`.

## Why per-file delta re-extraction is scoped out (the trap in the candidate)

Per-file subgraphs are NOT disjoint: `ResearchGraphBuilder.add_node` keys symbol/`Dependency` nodes by name+type, so many files share nodes (every file importing `json` shares one `Dependency` node; same-named functions collide). Removing one file's contribution requires orphan tombstoning across shared nodes — i.e. re-implementing the incremental-compile differ for the code layer, the exact failure mode this spec was warned against. The whole-layer skip captures the dominant win (the unchanged-tree compile: doc-only edits, session imports, repeated refreshes, CI loops) with zero tombstone risk. If profiling later shows the changed-tree case matters, that is a separate plan with its own parity gates.

## Why the "soft diff budget" is scoped out

OpenWiki's budget (`prompt.ts:160-174`, "fewer than ~5 source files changed → update at most 1-2 wiki pages") is a prompt-level editorial policy for an LLM that CHOOSES what to rewrite. Tesserae's projectors are deterministic — output churn is a mathematical consequence of the input delta, not a choice, so a budget warning is noise on legitimate wide changes. The intent is already covered: `idempotence_suspect` (landed today) trips the inputs-unchanged/projections-changed case; the LLM layer is delta-scoped by content digests (community summaries); and this plan's `delta` counts in the compile result are the surgicality observability ("3 files changed → re-extracted" vs "tree unchanged → reused").

## Global Constraints (byte-idempotence ledger — every disk write, and why it is stable)

1. **`.tesserae/code-graph-cache.json` — the ONLY new artifact.** An input-state cache like `manifest.json`, NOT a compiled artifact:
   - **Excluded from every hash scope by construction:** `output_snapshot.GRAPH_LAYER_FILES` / `PROJECTION_LAYER_DIRS` are allowlists that never include it (verify: no edit to `output_snapshot.py`); `tests/test_idempotence.py::_hash_tree` scopes `site/`, `wiki/`, `graph.json`, `temporal_facts.jsonl` only. It carries `mtime_ns` (volatile across checkouts) — which is exactly why it MUST stay out of hash scopes, and does.
   - **Deterministic given its inputs:** payload is `{"fingerprint": ..., "manifest": [...], "graph": graph.model_dump()}` serialized `sort_keys=True` + trailing `"\n"`; `model_dump()` is the canonical content-derived ordering graph.json already uses; manifest is sorted by rel path. Two compiles over an identical tree write byte-identical cache files.
   - **Atomic:** tmp file + `os.replace` (mirrors `output_snapshot.write_state`). Compiles are serialized by `compile_lock`.
   - Size ≈ `code-graph.json` (33.6 MB on this repo, <5 MB on typical repos). Note it in the module docstring.
2. **`graph.json` / `code-graph.json` / `combined-graph.json` / wiki / site — writers untouched.** The cache-hit path must be byte-identical to the extract path; `test_cache_parity_with_fresh_project` is the gate. A rehydrated graph equals the extracted graph because extraction is deterministic over the same tree (`model_dump` canonical order + `graph_from_payload` round-trip proven by `test_cache_roundtrip_graph_bytes_identical`).
3. **Nothing else touches disk.** Delta counts go to the result dict + `logger` + CLI stdout only. The result dict is returned, never serialized into artifacts (`_append_build_history` is untouched).
4. **Cache failures never fail a compile:** any `OSError` / `json.JSONDecodeError` / shape mismatch on read → full extraction; write failure → `logger.exception`, compile unaffected (mirrors the `write_state` guard at `project.py:1549-1552`).
5. **Extraction semantics untouched:** `SKIP_PARTS`, `CODE_SUFFIXES`, `_extract_file` are NOT modified — changing discovery changes graph bytes. (Observed follow-up, out of scope: `SKIP_PARTS` lacks `evals`/`.worktrees`/`pytest-of-*`.)
6. Run tests with `.venv/bin/python -m pytest`. One commit per task; conventional messages.

## File Structure

- **Modify** `tesserae/code_graph.py` — `extract_files()`, `stat_manifest()`, `extractor_fingerprint()`, `CodeGraphCache` dataclass, `read_code_graph_cache()`, `write_code_graph_cache()`, `manifest_delta()`.
- **Modify** `tesserae/research_graph.py` — `graph_from_payload(payload) -> ResearchGraph` (moved verbatim from `project.load_graph_file`).
- **Modify** `tesserae/project.py` — `ProjectPaths.code_graph_cache` field; gate block replacing line 709; `result["code_graph_cache"]` key; `load_graph_file` delegates to `graph_from_payload`.
- **Modify** `tesserae/cli.py` — `_handle_compile` prints the code-graph line (after the `Output:` line at ~1286).
- **Create** `tests/test_code_graph_cache.py` — unit + integration + parity tests.
- **Modify** `tests/test_cli_commands.py` — CLI print test.

---

### Task 1: cache primitives in `code_graph.py` + `graph_from_payload`

**Files:** Modify `tesserae/code_graph.py`, `tesserae/research_graph.py`, `tesserae/project.py` (load_graph_file only); Test `tests/test_code_graph_cache.py`

**Interfaces:**

```python
# code_graph.py
StatManifest = List[List[object]]  # [[rel_posix_path, size_bytes, mtime_ns], ...] sorted by path
                                   # (lists not tuples: JSON round-trips to lists; == must hold)

def stat_manifest(files: Sequence[Path], project_root: Path) -> Optional[StatManifest]:
    # safe_relative(f, project_root) + f.stat(); ANY OSError -> None (caching disabled this run)

def extractor_fingerprint() -> Optional[str]:
    # sha256 of Path(__file__).read_bytes(); None on OSError.
    # Auto-invalidates the cache on ANY edit to this module — no version constant to forget.

@dataclass(frozen=True)
class CodeGraphCache:
    fingerprint: str
    manifest: StatManifest
    graph_payload: Dict[str, object]   # model_dump() shape; rehydrate lazily on hit only

def read_code_graph_cache(path: Path) -> Optional[CodeGraphCache]:
    # None on missing file, parse error, or wrong shape (fingerprint/manifest/graph keys). Never raises.

def write_code_graph_cache(path: Path, graph: ResearchGraph, manifest: StatManifest, fingerprint: str) -> None:
    # json.dumps({"fingerprint":..., "manifest":..., "graph": graph.model_dump()},
    #            ensure_ascii=False, sort_keys=True, indent=2) + "\n"; tmp + os.replace. Never raises to caller
    # (catch OSError, logger.exception).

def manifest_delta(old: Optional[StatManifest], new: StatManifest) -> Dict[str, int]:
    # {"changed": n, "added": n, "removed": n} comparing by path; old=None counts everything as added.

class CodeGraphExtractor:
    def extract_files(self, files: Sequence[Path]) -> ResearchGraph:  # body of extract_paths minus discovery
    def extract_paths(self, paths): return self.extract_files(self.iter_code_files(paths))  # unchanged behavior
```

```python
# research_graph.py — moved verbatim from project.py:3174 (project.load_graph_file becomes
# `return graph_from_payload(json.loads(Path(path).read_text(encoding="utf-8")))`)
def graph_from_payload(payload: Dict[str, object]) -> ResearchGraph: ...
```

- [ ] **Step 1: Write the failing tests** (`tests/test_code_graph_cache.py`; build a tiny tree fixture: two `.py` files + one `.md` non-code file under `tmp_path`):
  - `test_stat_manifest_sorted_and_deterministic` — two calls over the same tree are `==`; entries sorted by rel path; the `.md` file (not walked by `iter_code_files`) is absent.
  - `test_stat_manifest_none_on_vanished_file` — pass a path list containing a nonexistent file → `None`.
  - `test_cache_roundtrip_graph_bytes_identical` — `extract_paths` → `write_code_graph_cache` → `read_code_graph_cache` → `graph_from_payload(cache.graph_payload).to_json(indent=2) == original.to_json(indent=2)`.
  - `test_cache_write_is_byte_stable` — write twice from the same graph+manifest → identical file bytes.
  - `test_read_cache_rejects_garbage` — nonexistent path, invalid JSON, and a JSON dict missing `graph` each → `None`, no exception.
  - `test_manifest_delta_counts` — changed (same path, different size/mtime), added, removed each counted; `old=None` → all added.
  - `test_extract_files_equals_extract_paths` — `extract_files(iter_code_files([root])).to_json() == extract_paths([root]).to_json()`.
- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_code_graph_cache.py -v` → FAIL (ImportError).
- [ ] **Step 3: Implement** per the interfaces; move `load_graph_file`'s body into `research_graph.graph_from_payload` and delegate (keep the `project.load_graph_file` name — it has many callers).
- [ ] **Step 4: Re-run new tests + `tests/test_code_graph.py` + `tests/test_idempotence.py`** → PASS. Commit: `feat(code-graph): stat-manifest extraction cache primitives`

---

### Task 2: the gate in `ProjectWiki.ingest`

**Files:** Modify `tesserae/project.py`; Test `tests/test_code_graph_cache.py`

**Interfaces:** `ProjectPaths` gains `code_graph_cache: Path = Path(".tesserae/code-graph-cache.json")` (appended after `output_snapshot`, same pattern), and `ProjectWiki.__init__` passes `code_graph_cache=self.root / "code-graph-cache.json"`. Result dict gains ONE additive key, present only when the code branch ran:

```python
result["code_graph_cache"] = {"reused": bool, "files": int, "delta": Optional[Dict[str, int]]}
# delta is None on reuse; on extraction it is manifest_delta(cached.manifest if cached else None, manifest or [])
```

**Algorithm (replaces `project.py:709-710`; lines 711-716 — merge + provenance — unchanged):**

```python
if kind in {"CodeProject", "Repository", "Project"}:
    cg_extractor = CodeGraphExtractor(self.project_root)
    cg_files = cg_extractor.iter_code_files(code_inputs)
    manifest = stat_manifest(cg_files, self.project_root)
    fingerprint = extractor_fingerprint()
    cached = read_code_graph_cache(self.paths.code_graph_cache)
    code_graph = None
    if (manifest is not None and fingerprint is not None and cached is not None
            and cached.fingerprint == fingerprint and cached.manifest == manifest):
        try:
            code_graph = graph_from_payload(cached.graph_payload)
        except Exception:
            logger.exception("code-graph cache rehydration failed; re-extracting")
            code_graph = None
    reused = code_graph is not None
    if code_graph is None:
        code_graph = cg_extractor.extract_files(cg_files)
        if manifest is not None and fingerprint is not None:
            write_code_graph_cache(self.paths.code_graph_cache, code_graph, manifest, fingerprint)
    code_graph_report = {
        "reused": reused,
        "files": len(cg_files),
        "delta": None if reused else manifest_delta(cached.manifest if cached else None, manifest or []),
    }
    logger.info("code graph %s (%d files)", "reused — tree unchanged" if reused else "re-extracted", len(cg_files))
    ...  # existing merge_graphs + _record_producer_provenance lines, unchanged
```

Thread `code_graph_report` into the ingest result dict next to the existing extraction counters (find where `processed`/`skipped` land in `result`; add `"code_graph_cache": code_graph_report` there, omitted when the branch didn't run).

- [ ] **Step 1: Write the failing tests** (seed with `ProjectWiki.init(root, name="cgcache", source_kind="Repository", sources=["."])`, one root-level `.py` file, `SessionExtractionOptions(enabled=False)`, LLM off — same seeding style as `tests/test_incremental_compile.py::_seed_project`):
  - `test_second_compile_reuses_code_graph` — compile twice; first result `["code_graph_cache"]["reused"] is False`, second `is True`; `code-graph.json` and `graph.json` bytes identical across the two compiles.
  - `test_cache_parity_with_fresh_project` — **the byte-parity gate.** Project A: compile, mutate the `.py` (add a function), compile again (cache miss on 2nd). Project B: identical final tree, single compile, cache file deleted before compiling. `code-graph.json` bytes of A == B.
  - `test_source_change_invalidates_cache` — modify the `.py` between compiles → second result `reused is False`, `delta == {"changed": 1, "added": 0, "removed": 0}`, and the new `CodeFunction` node is present in `code-graph.json`.
  - `test_file_add_and_remove_invalidate_cache` — add a second `.py` → `delta["added"] == 1`; delete it → `delta["removed"] == 1` and its `SourceFile` node is gone (full re-extract guarantees deletion; no tombstoning).
  - `test_corrupt_cache_recovers` — overwrite `.tesserae/code-graph-cache.json` with `"junk"` → compile succeeds, `reused is False`, cache file is valid JSON afterwards.
  - `test_cache_excluded_from_output_snapshot` — after a reuse compile, `result["output_changed"] is False` (the cache write on run 1 and the mtime-bearing state must not register as output churn).
  - `test_non_code_project_has_no_cache_key` — `source_kind="SourceDocument"` project: `"code_graph_cache" not in result` and no cache file written.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the `ProjectPaths` field + gate block + result key.
- [ ] **Step 4: Re-run new tests + `tests/test_incremental_compile.py` + `tests/test_incremental_parity.py` + `tests/test_idempotence.py`** (provenance + parity suites must be untouched by the gate) → PASS. Commit: `feat(compile): reuse cached code graph when the code tree is unchanged`

---

### Task 3: CLI reporting

**Files:** Modify `tesserae/cli.py`; Test `tests/test_cli_commands.py`

**Interfaces:** In `_handle_compile`, directly after the `Output:` print (~line 1286), same defensive `.get()` style:

```python
cg = result.get("code_graph_cache")
if cg is not None:
    if cg["reused"]:
        print(f"Code graph: reused (tree unchanged, {cg['files']} files)")
    else:
        d = cg.get("delta") or {}
        print(
            f"Code graph: re-extracted ({cg['files']} files; delta "
            f"+{d.get('added', 0)} ~{d.get('changed', 0)} -{d.get('removed', 0)})"
        )
```

`--strict` is NOT extended — reuse/extraction is never a failure condition (consistent with the info-only posture of `CODE_GRAPH_BEHIND`).

- [ ] **Step 1: Write the failing test** — `test_compile_prints_code_graph_cache_line` in `tests/test_cli_commands.py` (follow the existing `Output:`-line test pattern: injected compile result containing `code_graph_cache`, assert both the reused and re-extracted renderings; absent key → no line).
- [ ] **Step 2: Run to verify failure. Step 3: Implement. Step 4: Re-run + full `tests/test_cli_commands.py`** → PASS. Commit: `feat(cli): report code-graph reuse and tree delta on compile`

---

## Out of scope (deliberately)

- **Per-file delta re-extraction** — shared symbol/`Dependency` nodes make per-file subgraphs non-disjoint; scoping requires an orphan-tombstoning differ, i.e. incremental compile re-implemented for the code layer. Whole-layer skip only.
- **The soft diff budget** — editorial policy for an LLM writer; noise for a deterministic projector. Covered by `idempotence_suspect` + digest-scoped LLM caches + this plan's delta counts.
- **A git-based gate** — unsound for a filesystem-walking extractor (16.5K gitignored `evals/` files on this repo alone). Lint's `CODE_GRAPH_BEHIND`/`CODE_GRAPH_STALE_FILE` remain the git-facing story.
- **`SKIP_PARTS` hygiene** (`evals`, `.worktrees`, `pytest-of-*` are walked today) — changes graph bytes; separate plan.
- **Caching the walk itself** — discovery (~17s here, dominated by the same vendored dirs) must run to build the manifest; caching it would reintroduce the unsound-gate problem.
- **markdown/session layers** — already covered by the manifest no-op skip and the experimental incremental differ.

## Measured baseline (this repo, 2026-07-09)

| Step | Cost |
|---|---|
| `extract_paths(["."])` full | 52.8s (17,226 files → 219K nodes / 557K edges) |
| `iter_code_files` walk alone | 17.1s |
| `stat()` manifest over the walked list | 0.07s |
| Expected cache-hit path | walk + manifest + 33 MB JSON parse + rehydrate ≈ 20-25s (vs 53s) — bigger relative win on repos without giant vendored trees, where the walk is negligible and AST parsing dominates |
