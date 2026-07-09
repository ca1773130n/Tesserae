# Compile Output Snapshot Hash Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** After every `ProjectWiki.compile`, hash the byte-idempotent compiled artifact set and report `output_changed` / `output_sha256` / `idempotence_suspect` in the compile result dict, print it from the CLI, gate `compile --strict` on the suspect flag, and persist the hashes to a deterministic `.tesserae/output-snapshot.json` state file so downstream automation (vault sync, CI refresh PRs, agents) gets a machine-readable no-op signal. This is OpenWiki's snapshot-gate pattern (`createOpenWikiContentSnapshot` in `src/agent/utils.ts`: SHA-256 the output dir excluding its own metadata file; write metadata only when the snapshot changed) repurposed as (a) a no-op detector and (b) a **permanent byte-idempotence tripwire** — this repo's compile determinism broke 4× via wall-clock/mutable state, and tests only catch drift over the fixture corpus; the tripwire watches every real compile.

**Architecture:** One new stdlib-only module `tesserae/output_snapshot.py` computes a two-part snapshot: a **graph layer** hash (`config.json` + `graph.json` + `code-graph.json` + `combined-graph.json` — the exact inputs the projections are a pure function of) and a **projections layer** hash (`wiki/`, `site/`, `markdown_projection/`, minus the append-only ledgers). `Project.compile` snapshots the tree **before** ingest and **after** the post-compile lint, inside the existing `compile_lock`. Comparing before/after within one run needs no input fingerprint and has zero false positives:

- `output_changed` = any part differs → the no-op signal (OpenWiki-equivalent).
- `idempotence_suspect` = graph layer **unchanged** but projections layer **changed** → this compile rewrote a projection of identical inputs differently, i.e. a deterministic-projection violation. Sound because every projector under `_write_artifacts` derives `wiki/`/`site/`/`markdown_projection/` purely from (canonicalized graph, config).

Graph-layer nondeterminism (the historical 4×: wall-clock in `graph.json`) cannot be auto-proven at runtime without fingerprinting every input (docs + sessions + vault edits + LLM caches — rejected as over-engineering with false-positive risk); it becomes **observable** instead: a no-op recompile logs/prints `output: changed`, and a cron CI refresh produces visible junk PRs immediately, which is exactly how OpenWiki's gate surfaces the same failure.

**Tech Stack:** Python 3 stdlib (`hashlib`, `json`, `dataclasses`, `pathlib`), pytest. No new dependencies.

## Global Constraints

- **Byte-idempotence audit — every disk write this plan adds:**
  1. `.tesserae/output-snapshot.json` — the ONLY new artifact. Lives at the `.tesserae/` root (like `vault_snapshot.json` / `manifest.json`), is **excluded from the hash by construction** (the hash scope is an allowlist that never includes it), contains **no timestamps and no wall-clock state** (only hex digests + a bool), is serialized with `sort_keys=True, indent=2` + trailing `"\n"` (mirroring `BatchIngestRunner._write_manifest`), and is written via tmp-file + `os.replace`. Two compiles over identical inputs write byte-identical state files. Existing idempotence tests hash only `site/`, `wiki/`, `graph.json`, `temporal_facts.jsonl` → unaffected.
  2. Nothing else touches disk. Log lines go to `logger`/stdout only. The CI example is a doc.
- **Hash scope is an ALLOWLIST of test-proven-stable artifacts only** (see `tests/test_idempotence.py::test_compile_is_byte_idempotent` and the phase-5 suite). Deliberately excluded for v1 because their byte-stability is unproven and one noisy artifact would make the signal cry wolf: `report.md`, `competitive_report.md`, `temporal_facts.jsonl` (depends on the mutable `node_memory` sidecar via `memory_by_id` — MCP reads bump `access_count` between compiles), `cognee_bundle/`, `graphiti_episodes.jsonl`, `agent_harness/`, `sqlite.db`, the Obsidian vault (bidirectional, user-owned), `manifest.json` (input state), lint reports, and all ledgers/caches. Extending scope later is a one-line allowlist edit.
- **Excluded basenames inside allowlisted dirs:** `.history.jsonl` (synthesis ledger inside `wiki/syntheses/`, see `synthesis.py:654`), `.build-history.jsonl` (defensive), `.DS_Store` — same exclusions `test_idempotence._hash_tree` already uses.
- **`config.json` belongs to the graph layer**, not the projections layer: `site_title`/`name` feed `KarpathyLayerWriter` and the site build, so a config-only change must read as "inputs changed" (no suspect flag), not as projection drift.
- The tripwire **never fails a default compile** — `idempotence_suspect` is a result key + `logger.warning`; only `compile --strict` turns it into exit 2 (consistent with strict's existing lint gate at `cli.py:1287`).
- New result-dict keys are additive; no existing key changes. `step_compile` in `tesserae refresh` needs no changes.
- Snapshot cost is two tree walks of already-written files — no LLM, no network.
- Run tests with `.venv/bin/python -m pytest` (system python3 fails collection).
- One commit per task; conventional messages.

---

## File Structure

- **Create** `tesserae/output_snapshot.py` — `OutputSnapshot` dataclass, `snapshot_output(root)`, `write_state(path, snapshot, changed)`, allowlist constants.
- **Modify** `tesserae/project.py` — `ProjectPaths.output_snapshot` field; before/after snapshot calls + result keys + warning log + state write in `ProjectWiki.compile`.
- **Modify** `tesserae/cli.py` — `_handle_compile`: print the output line; `--strict` gains the suspect gate.
- **Modify** `tests/test_idempotence.py` — snapshot unit tests + compile-integration tests (reuses `_seed_project`, `_deterministic_compile`, `_hash_tree`).
- **Modify** `tests/test_cli_commands.py` — CLI print + strict-gate tests.
- **Create** `docs/integrations/ci-refresh.md` — optional shipped CI workflow example (mirrors OpenWiki `examples/openwiki-update.yml`).

---

### Task 1: `tesserae/output_snapshot.py`

**Files:** Create `tesserae/output_snapshot.py`; Test `tests/test_idempotence.py`

**Interfaces:**

```python
GRAPH_LAYER_FILES: tuple[str, ...] = (
    "config.json", "graph.json", "code-graph.json", "combined-graph.json",
)
PROJECTION_LAYER_DIRS: tuple[str, ...] = ("wiki", "site", "markdown_projection")
EXCLUDED_BASENAMES: frozenset[str] = frozenset(
    {".history.jsonl", ".build-history.jsonl", ".DS_Store"}
)

@dataclass(frozen=True)
class OutputSnapshot:
    graph_sha256: str
    projections_sha256: str

    @property
    def output_sha256(self) -> str:
        # sha256 over the two part-digests — the single combined hash.
        ...

def snapshot_output(root: Path) -> OutputSnapshot: ...
def write_state(path: Path, snapshot: OutputSnapshot, changed: bool) -> None: ...
```

Hashing rules (all deterministic, platform-stable):
- Graph layer: for each name in `GRAPH_LAYER_FILES` in tuple order, feed `name + "\0"` then the file bytes into one `hashlib.sha256`; a missing file feeds the literal sentinel `b"missing"` (mirrors OpenWiki's `addDirectoryToSnapshot` race handling). A deleted `combined-graph.json` (config flip) therefore changes the graph hash — correct, it is an output change.
- Projections layer: for each dir in `PROJECTION_LAYER_DIRS`, walk `sorted(dir.rglob("*"))`, skip non-files and `EXCLUDED_BASENAMES`, feed forward-slashed `relative_to(root)` path + `"\0"` + bytes; a missing dir feeds `b"missing"`.
- `write_state` writes `{"changed": bool, "graph_sha256": ..., "output_sha256": ..., "projections_sha256": ...}` with `sort_keys=True, indent=2` + `"\n"`, via `path.with_suffix(".tmp")` + `os.replace`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_idempotence.py`):

```python
def test_snapshot_output_stable_and_ignores_ledgers_and_state(tmp_path: Path) -> None:
    from tesserae.output_snapshot import snapshot_output, write_state
    wiki = _seed_project(tmp_path / "proj")
    wiki.compile(session_options=SessionExtractionOptions(enabled=False))
    root = wiki.root
    first = snapshot_output(root)
    # Excluded churn: ledgers, state file, lint noise at the root.
    with (root / ".build-history.jsonl").open("a") as fh:
        fh.write('{"noise": true}\n')
    (root / "wiki" / "syntheses").mkdir(parents=True, exist_ok=True)
    with (root / "wiki" / "syntheses" / ".history.jsonl").open("a") as fh:
        fh.write('{"noise": true}\n')
    write_state(root / "output-snapshot.json", first, changed=False)
    assert snapshot_output(root) == first
    # Included churn: a projection file flips only the projections part.
    (root / "wiki" / "drift.md").write_text("drift", encoding="utf-8")
    second = snapshot_output(root)
    assert second.projections_sha256 != first.projections_sha256
    assert second.graph_sha256 == first.graph_sha256
    assert second.output_sha256 != first.output_sha256


def test_snapshot_output_handles_missing_artifacts(tmp_path: Path) -> None:
    from tesserae.output_snapshot import snapshot_output
    empty = snapshot_output(tmp_path / "nothing-here")
    assert empty == snapshot_output(tmp_path / "nothing-here")  # deterministic
    assert len(empty.graph_sha256) == 64 and len(empty.projections_sha256) == 64
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_idempotence.py -k snapshot_output -v` → FAIL (ModuleNotFoundError).
- [ ] **Step 3: Implement** `tesserae/output_snapshot.py` per the interface above. Module docstring must state the allowlist rationale and point at this plan.
- [ ] **Step 4: Re-run** → PASS. Commit: `feat(compile): output snapshot hashing module`

---

### Task 2: Hook into `ProjectWiki.compile`

**Files:** Modify `tesserae/project.py`; Test `tests/test_idempotence.py`

**Interfaces:**
- `ProjectPaths` gains `output_snapshot: Path = Path(".tesserae/output-snapshot.json")` (append after `extraction_guidance_cache` — keeps positional compatibility, same pattern as `session_findings`), and `ProjectWiki.__init__` passes `output_snapshot=self.root / "output-snapshot.json"` explicitly.
- `compile()` result dict gains: `output_sha256: str`, `output_changed: bool`, `idempotence_suspect: bool`.

Placement inside `compile()` (project.py:1421), all within the existing `compile_lock` block:
1. Immediately after entering `compile_lock`, before `self.ingest(...)`: `before = snapshot_output(self.root)`.
2. After the existing post-compile lint try/except (lint writes only root-level reports — outside the allowlist, so ordering is cosmetic; taking the snapshot last keeps the bracket honest):

```python
after = snapshot_output(self.root)
result["output_sha256"] = after.output_sha256
result["output_changed"] = after != before
result["idempotence_suspect"] = (
    after.graph_sha256 == before.graph_sha256
    and after.projections_sha256 != before.projections_sha256
)
if result["idempotence_suspect"]:
    logger.warning(
        "byte-idempotence regression suspected: projections changed while "
        "graph.json/config were byte-identical (before=%s after=%s)",
        before.projections_sha256[:12], after.projections_sha256[:12],
    )
else:
    logger.info(
        "compile output %s (sha256 %s)",
        "changed" if result["output_changed"] else "unchanged",
        after.output_sha256[:12],
    )
try:
    write_state(self.paths.output_snapshot, after, changed=result["output_changed"])
except OSError:
    logger.exception("output-snapshot state write failed; compile artifacts are unaffected")
```

Notes:
- First-ever compile: `before` hashes all-missing sentinels → `output_changed=True`, `graph` part differs → suspect stays False. No special-casing.
- New sessions / vault edits / config flips / LLM-minted `resolved_by` edges all mutate the graph layer → suspect never fires on legitimate input change.
- Import `snapshot_output, write_state` at module top (stdlib-only, no cycle: `output_snapshot` imports nothing from `tesserae`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_idempotence.py`; reuse `_seed_project` + the autouse `_deterministic_compile` fixture; pass `session_options=SessionExtractionOptions(enabled=False)` exactly like `test_compile_is_byte_idempotent` does):

```python
def test_compile_result_reports_output_unchanged_on_recompile(tmp_path: Path) -> None:
    wiki = _seed_project(tmp_path / "proj")
    opts = SessionExtractionOptions(enabled=False)
    first = wiki.compile(session_options=opts)
    second = wiki.compile(session_options=opts)
    assert first["output_changed"] is True          # first compile populated an empty tree
    assert second["output_changed"] is False        # no-op detected
    assert second["idempotence_suspect"] is False
    assert second["output_sha256"] == first["output_sha256"]


def test_compile_result_reports_output_changed_on_new_source(tmp_path: Path) -> None:
    wiki = _seed_project(tmp_path / "proj")
    opts = SessionExtractionOptions(enabled=False)
    first = wiki.compile(session_options=opts)
    (tmp_path / "proj" / "docs" / "new-note.md").write_text(
        "# New Note\n\nA fresh concept: snapshot gating.\n", encoding="utf-8"
    )
    second = wiki.compile(session_options=opts)
    assert second["output_changed"] is True
    assert second["output_sha256"] != first["output_sha256"]
    assert second["idempotence_suspect"] is False   # graph layer changed too


def test_compile_flags_idempotence_suspect_on_projection_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate the historical failure class: a projector emitting bytes not
    derived from the graph. Graph layer identical + projections drifted must
    raise the tripwire."""
    from tesserae.karpathy_layer import KarpathyLayerWriter
    wiki = _seed_project(tmp_path / "proj")
    opts = SessionExtractionOptions(enabled=False)
    wiki.compile(session_options=opts)

    original = KarpathyLayerWriter.write_all
    def drifting(self, graph, build_history_path=None):
        written = original(self, graph, build_history_path)
        (Path(self.wiki_root) / "drift.md").write_text("wall-clock leak", encoding="utf-8")
        return written
    monkeypatch.setattr(KarpathyLayerWriter, "write_all", drifting)

    second = wiki.compile(session_options=opts)
    assert second["idempotence_suspect"] is True
    assert second["output_changed"] is True


def test_output_snapshot_state_file_is_byte_stable(tmp_path: Path) -> None:
    wiki = _seed_project(tmp_path / "proj")
    opts = SessionExtractionOptions(enabled=False)
    wiki.compile(session_options=opts)
    state_path = wiki.paths.output_snapshot
    assert state_path.exists()
    first_bytes = state_path.read_bytes()
    payload = json.loads(first_bytes)
    assert set(payload) == {"changed", "graph_sha256", "output_sha256", "projections_sha256"}
    wiki.compile(session_options=opts)
    second = json.loads(state_path.read_bytes())
    assert second["changed"] is False
    # Identical hashes both runs; only `changed` may differ on the first run.
    assert second["output_sha256"] == payload["output_sha256"]
```

  (Signature verified: `write_all(self, graph: ResearchGraph, build_history_path: Optional[Path]) -> List[Path]` at `tesserae/karpathy_layer.py:194`; `wiki_root` is an attribute.)
- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_idempotence.py -k "output_unchanged or output_changed_on_new or projection_drift or state_file" -v` → FAIL (KeyError `output_changed`).
- [ ] **Step 3: Implement** the `ProjectPaths` field + `compile()` hook as specified.
- [ ] **Step 4: Re-run the new tests AND the full existing idempotence suites** — `.venv/bin/python -m pytest tests/test_idempotence.py tests/test_byte_idempotence_phase5.py -v` → all PASS (proves the state-file write breaks nothing). Commit: `feat(compile): output-changed no-op signal + idempotence tripwire`

---

### Task 3: CLI surface — print line + `--strict` gate

**Files:** Modify `tesserae/cli.py` (`_handle_compile`, around lines 1282–1310); Test `tests/test_cli_commands.py`

**Interfaces:**
- After the existing `print(f"Graph: {result['graph_path']}")`, add one stable, script-parseable line:
  `Output: unchanged (sha256 <first-12>)` or `Output: changed (sha256 <first-12>)` — keyed off `result.get("output_changed")`; omit the line entirely when the key is absent (defensive: injected compile doubles in older tests).
- `--strict` gains a gate BEFORE the lint checks (a suspected determinism regression outranks lint warnings):

```python
if getattr(args, "strict", False):
    if result.get("idempotence_suspect"):
        print(
            "compile --strict: projections changed while graph/config were "
            "byte-identical — byte-idempotence regression suspected",
            file=sys.stderr,
        )
        return 2
    ...existing lint gate unchanged...
```

- The `--strict` help string is updated to mention both gates.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli_commands.py`, following the `test_compile_options_flow_into_wiki_compile` monkeypatch pattern — stub `ProjectWiki.compile` to return a canned result dict including `lint`, `output_changed`, `output_sha256`, `idempotence_suspect`):
  - `test_compile_prints_output_change_line` — canned result with `output_changed=False` → stdout contains `Output: unchanged`; with `output_changed=True` → `Output: changed`.
  - `test_compile_strict_fails_on_idempotence_suspect` — canned result with `idempotence_suspect=True`, zero lint counts, `--strict` → exit code 2 and stderr mentions `byte-idempotence`.
  - `test_compile_strict_passes_when_output_clean` — suspect False, zero lint counts, `--strict` → exit 0.
- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_cli_commands.py -k "output_change_line or strict_fails_on_idempotence or strict_passes_when_output_clean" -v` → FAIL.
- [ ] **Step 3: Implement**, re-run → PASS. Also re-run the whole `tests/test_cli_commands.py` (the canned-result stubs elsewhere must not regress). Commit: `feat(cli): compile prints output-change line; --strict gates on idempotence tripwire`

---

### Task 4 (optional): shipped CI refresh workflow example

**Files:** Create `docs/integrations/ci-refresh.md`

Mirror OpenWiki's `examples/openwiki-update.yml` (daily cron `0 8 * * *` + `workflow_dispatch`, `peter-evans/create-pull-request`), adapted to Tesserae. The doc contains a short intro plus one fenced YAML block:

- Steps: checkout (`persist-credentials: true`) → `actions/setup-python` 3.11 → `pip install tesserae` → `tesserae compile --project .` → a gate step reading the state file this plan ships:
  `if [ "$(jq -r .changed .tesserae/output-snapshot.json)" != "true" ]; then echo "no-op"; fi` exposed as a step output → `peter-evans/create-pull-request@v7` with `add-paths: .tesserae/wiki`, `branch: tesserae/refresh`, `commit-message: "docs: refresh tesserae knowledge base"`, run only when the gate output says changed.
- Call out explicitly in the doc: the `changed` gate is what prevents endless scheduled-PR loops (the OpenWiki lesson), and a PR appearing when *nothing* changed in the repo is the live symptom of a byte-idempotence regression — file it as a bug.
- No test (docs-only); keep the YAML under ~60 lines.

- [ ] **Step 1:** Write `docs/integrations/ci-refresh.md`.
- [ ] **Step 2:** Sanity-check the YAML with `python3 -c "import yaml,sys; yaml.safe_load(open('...'))"` if PyYAML is available in `.venv`, else eyeball. Commit: `docs(integrations): scheduled CI refresh workflow example`

---

## Verification

- `.venv/bin/python -m pytest tests/test_idempotence.py tests/test_byte_idempotence_phase5.py tests/test_cli_commands.py -v` — all green.
- Manual: `cd` into a real registered project, run `tesserae compile` twice; second run must print `Output: unchanged (sha256 …)` and `.tesserae/output-snapshot.json` must be byte-identical across the two runs (`shasum` it). If it prints `changed`, that is the tripwire doing its job — investigate before shipping.
