# Git-Delta Staleness Signal for the Code Graph (OpenWiki Candidate D)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Record the project repo's git HEAD in the existing `.tesserae/.build-history.jsonl` ledger at compile time, and add one read-only lint check that diffs `recorded-head..HEAD` and reports (at `info` severity) which `SourceFile` graph nodes' underlying files changed since the last compile — a staleness *report*, never auto-regeneration. This is the OpenWiki `gitHead` → `git log <lastHead>..HEAD --name-status` freshness pattern, adapted to Tesserae's lint pipeline instead of a prompt.

**Architecture:** Two touch points, zero new files, zero new deps. (1) `tesserae/lint.py` gains a public `read_git_head(repo_root)` helper (stdlib `subprocess`) and a new `WikiLinter._check_code_graph_staleness()` check wired into `run()`. (2) `tesserae/project.py::ProjectWiki._append_build_history` calls `read_git_head` and adds a `git_head` key to the ledger entry. Node→file provenance already exists: `SourceFile` nodes (minted by `tesserae/code_graph.py::CodeGraphExtractor._extract_file`) use the repo-relative POSIX path as their `name`, so mapping git-delta paths to graph nodes is an exact string match against `graph.json`. No page-level mapping is attempted (SourceFile nodes are the provenance unit other lint checks already use via `LintFinding.node_id`).

**Tech Stack:** Python 3 stdlib (`subprocess`, `json`), pytest via `.venv/bin/python -m pytest` (system python3 is 3.9 and fails collection). No LLM, no network — consistent with lint.py's existing "stdlib only" contract (local git subprocess is the one addition; update the module docstring to say "stdlib + local git only").

## Why this is not redundant (verified 2026-07-09)

- `grep -rn "rev-parse\|git_head" tesserae/code_graph*.py tesserae/project.py` → zero hits. No git state is captured at compile anywhere. (`activity_summary.py` has a private `_run` git helper for digests, but it is windowed reporting, unrelated to compile provenance.)
- Freshness today is session-driven only (community-summary digest invalidation). Nothing ties graph/wiki artifacts to source commits.
- `.build-history.jsonl` (written by `project.py::_append_build_history`, line ~2851) is the designated volatile per-compile ledger, already read by lint (`_check_stale_build_history`) and by `karpathy_layer._render_log` — which renders **fixed columns only** (`built_at`, node/edge counts), so an extra key changes no rendered artifact.

## Global Constraints (byte-idempotence ledger — every disk write, and why it is stable)

1. **`.tesserae/.build-history.jsonl`** — gains one key `git_head` (omitted entirely when the project is not a git repo or git is unavailable). This ledger is *already* volatile (wall-clock `built_at` per compile) and explicitly outside the byte-idempotent wiki/site surfaces. `json.dumps(..., sort_keys=True)` is already used, so key ordering is stable.
2. **`log.md`** — derived from the ledger by `karpathy_layer._render_log` (line ~354), which reads only known keys. **Unchanged bytes.** Do not touch this renderer.
3. **`lint-report.md` / `lint-report.json`** — the only artifacts whose content changes, and only when HEAD ≠ recorded head. Findings are a pure function of `(graph.json, last anchored ledger entry, git object state)`. Stability rules the implementation MUST follow:
   - **No wall-clock anywhere** in this check (no `--since`, no `datetime.now`).
   - **No config-dependent git output:** use `--no-renames` (rename detection is `diff.renames`-config-dependent; renames must appear as stable D+A pairs) and `-c core.quotepath=false` (non-ASCII path quoting is config-dependent). Never use `rev-parse --short` (length is repo-state-dependent); slice full SHAs in Python (`sha[:12]`).
   - **No absolute paths** in messages — repo-relative paths only (git already emits them; combined with `--relative` they are relative to `project_root`).
   - **Compile-tail ordering guarantees double-compile stability:** `compile()` → `ingest()` → `_write_artifacts()` → `_append_build_history()` (records HEAD) **then** `compile()` runs tail lint (project.py compile body, `report = self.lint(severity_floor="warning")`). Recorded head == HEAD at that moment → zero delta → zero staleness findings → the existing `test_compile_twice_lint_report_is_byte_stable` (tests/test_lint.py:465) stays green. Verify this test still passes after Task 3.
4. **`graph.json`, wiki, site, vault** — untouched. The check is read-only over git and the graph.
5. **Severity is `info` for every finding.** `compile --strict` maps warnings→exit 1 (tests/test_lint.py:519); a repo that merely advanced by a commit must not fail strict CI. Staleness is advisory.

Reuse, don't duplicate: `LintFinding`, `LintReport`, `WikiLinter.run()` wiring, `self.build_history_path`, `self.project_root`, the `_check_*` iterator pattern, `tests/test_lint.py::_scaffold` + `_node` helpers.

---

## File Structure

- **Modify** `tesserae/lint.py` — add `read_git_head()` + `_git()` module helpers, `WikiLinter._check_code_graph_staleness()`, one line in `run()`, docstring note.
- **Modify** `tesserae/project.py` — 3 lines in `_append_build_history` (call `read_git_head`, conditionally add key).
- **Modify** `tests/test_lint.py` — git-repo fixture helper + 7 named tests.

Finding codes introduced (all `severity="info"`):

| Code | Cardinality | Meaning |
|---|---|---|
| `CODE_GRAPH_BEHIND` | ≤1 per run | Repo has commits after the recorded compile head |
| `CODE_GRAPH_STALE_FILE` | ≤20 per run | A file backing a `SourceFile` node changed (M/A/D) since last compile |
| `CODE_GRAPH_HEAD_UNRESOLVED` | ≤1 per run | Recorded head no longer resolvable (rebase/gc) |

---

### Task 1: `read_git_head` helper + record `git_head` in the build-history ledger

**Files:** Modify `tesserae/lint.py`, `tesserae/project.py`; Test `tests/test_lint.py`

**Interfaces:**
- `tesserae/lint.py`:
  ```python
  def _git(repo_root: Path, *args: str) -> Optional[str]:
      """Run git in repo_root; stdout on success, None on any failure."""
      try:
          proc = subprocess.run(
              ["git", "-C", str(repo_root), *args],
              capture_output=True, text=True, timeout=10,
          )
      except (OSError, subprocess.SubprocessError):
          return None
      return proc.stdout if proc.returncode == 0 else None

  def read_git_head(repo_root: Path) -> Optional[str]:
      """Full 40-char HEAD sha of the repo at repo_root, or None."""
      out = _git(repo_root, "rev-parse", "HEAD")
      head = (out or "").strip()
      return head or None
  ```
  Add `import subprocess` to lint.py imports. Update the module docstring line "Stdlib only — no LLM, no network." → "Stdlib + local git only — no LLM, no network."
- `tesserae/project.py::_append_build_history` — after building `entry`, before `json.dumps`:
  ```python
  from .lint import read_git_head
  head = read_git_head(self.project_root)
  if head:
      entry["git_head"] = head
  ```
  (Local import inside the method, matching the method's existing local `datetime` import. Key omitted — not `null` — when unresolvable, so non-git projects' ledger lines keep their exact current shape.)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_lint.py`)

```python
import subprocess as _sp

def _git_init(root: Path) -> str:
    """git init + one commit; returns the full HEAD sha."""
    def g(*args: str) -> str:
        return _sp.run(
            ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    g("init", "-q", "-b", "main")
    (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "c1")
    return g("rev-parse", "HEAD")


def test_read_git_head_returns_sha_in_repo_and_none_outside(tmp_path: Path) -> None:
    from tesserae.lint import read_git_head
    project = _scaffold(tmp_path)
    assert read_git_head(project) is None  # tmp scaffold is not a repo
    sha = _git_init(project)
    assert read_git_head(project) == sha
    assert len(sha) == 40
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_lint.py::test_read_git_head_returns_sha_in_repo_and_none_outside -v` → FAIL (ImportError).
- [ ] **Step 3: Implement** `_git` / `read_git_head` in lint.py and the `_append_build_history` change in project.py.
- [ ] **Step 4: Verify** — the new test passes; `.venv/bin/python -m pytest tests/test_lint.py -v` fully green (ledger shape for non-git projects unchanged, so `test_stale_build_history_emits_info` and compile-tail tests must not move).

---

### Task 2: `_check_code_graph_staleness` lint check

**Files:** Modify `tesserae/lint.py`; Test `tests/test_lint.py`

**Interfaces:** `WikiLinter._check_code_graph_staleness(self, nodes_by_id: Dict[str, dict]) -> Iterable[LintFinding]`, wired into `run()` immediately after `self._check_stale_build_history()`:
```python
findings.extend(self._check_code_graph_staleness(nodes_by_id))
```

**Algorithm (exact — messages are part of the byte-stability contract):**

1. **Recorded head:** parse `self.build_history_path` line-by-line (same tolerant JSONL loop as `_check_stale_build_history`); keep the **last** entry whose `git_head` is a non-empty `str`. No such entry, or file missing → `return` (silent: pre-feature ledgers and non-code projects produce no noise).
2. **Current head:** `head = read_git_head(self.project_root)`; `None` → `return` (not a git repo / git missing — silent). `head == recorded` → `return` (fast path).
3. **Resolvability:** `_git(self.project_root, "rev-parse", "--verify", "--quiet", recorded + "^{commit}")` → `None` → yield exactly one finding and stop:
   ```python
   LintFinding(
       severity="info", code="CODE_GRAPH_HEAD_UNRESOLVED",
       message=f"Recorded compile head {recorded[:12]} is not resolvable in this repo (history rewritten or pruned); staleness unknown",
       path=str(self.build_history_path),
       suggested_fix="Run `tesserae compile` to re-anchor the graph to the current HEAD.",
   )
   ```
4. **Commit distance:** `n_commits = int(_git(..., "rev-list", "--count", f"{recorded}..HEAD").strip())` (treat `None` as `0`).
5. **Changed files:** `out = _git(self.project_root, "-c", "core.quotepath=false", "diff", "--no-renames", "--relative", "--name-status", recorded, "HEAD")` — note: because `_git` prefixes `["git", "-C", root]`, the `-c` flag must be accepted; have `_git` splat args directly after `-C <root>` (git accepts `git -C x -c y diff ...`). Two-dot snapshot diff (not `log`) so merges/reverts net out; `--relative` re-roots paths at `project_root` when the workspace is a repo subdirectory, matching `SourceFile` node names minted by `code_graph.safe_relative`. Parse lines as `status, _, path = line.partition("\t")`; keep `(status[:1], path)` for non-empty lines. If diff output is empty AND `n_commits == 0` → `return` (diverged-but-identical trees, e.g. reset+recommit).
6. **Map to graph:** `source_files = {n["name"]: nid for nid, n in nodes_by_id.items() if n.get("type") == "SourceFile"}`; `matched = sorted((p, s) for s, p in changes if p in source_files)`.
7. **Emit summary** (always, when we got past step 2 with `n_commits > 0` or a non-empty diff):
   ```python
   yield LintFinding(
       severity="info", code="CODE_GRAPH_BEHIND",
       message=(
           f"Code graph compiled at {recorded[:12]} is {n_commits} commit(s) behind HEAD {head[:12]} "
           f"({len(changes)} changed file(s), {len(matched)} tracked in graph)"
       ),
       suggested_fix="Run `tesserae compile` to refresh the graph from the current working tree.",
   )
   ```
8. **Emit per-file findings**, `matched[:20]` (lexicographic path order = deterministic cap):
   ```python
   yield LintFinding(
       severity="info", code="CODE_GRAPH_STALE_FILE",
       message=f"Source file changed since last compile ({status}): {path}",
       node_id=source_files[path], path=path,
       suggested_fix="Run `tesserae compile` to refresh the graph from the current working tree.",
   )
   ```
   Deletions (`D`) matched to a node are the strongest signal (the graph cites a file that no longer exists) and are included; added files (`A`) have no node and only count toward the summary totals. Uncommitted worktree changes are deliberately out of scope (transient; OpenWiki also excludes them from the delta anchor).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_lint.py`)

```python
def _ledger(project: Path, sha: str) -> None:
    (project / ".tesserae" / ".build-history.jsonl").write_text(
        json.dumps({"built_at": "2026-07-01T00:00:00Z", "code_edges": 0, "code_nodes": 1,
                    "git_head": sha, "research_edges": 0, "research_nodes": 0},
                   sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _code_graph(paths: list[str]) -> dict:
    return {
        "nodes": [_node(f"sf{i}", "SourceFile", p, metadata={"layer": "raw-code"})
                  for i, p in enumerate(paths)],
        "edges": [],
    }


def test_code_graph_staleness_flags_changed_source_files(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    sha = _git_init(project)
    _ledger(project, sha)
    (project / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    _sp.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-aqm", "c2"], check=True)
    report = WikiLinter(project).run()
    behind = [f for f in report.findings if f.code == "CODE_GRAPH_BEHIND"]
    stale = [f for f in report.findings if f.code == "CODE_GRAPH_STALE_FILE"]
    assert len(behind) == 1 and "1 commit(s) behind" in behind[0].message
    assert len(stale) == 1
    assert stale[0].node_id == "sf0" and stale[0].path == "a.py"
    assert stale[0].severity == "info"


def test_code_graph_staleness_silent_when_head_unchanged(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    _ledger(project, _git_init(project))
    report = WikiLinter(project).run()
    assert not [f for f in report.findings if f.code.startswith("CODE_GRAPH")]


def test_code_graph_staleness_skips_without_git_repo(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    _ledger(project, "0" * 40)  # ledger has a head, but no repo exists
    report = WikiLinter(project).run()
    assert not [f for f in report.findings if f.code.startswith("CODE_GRAPH")]


def test_code_graph_staleness_skips_without_recorded_head(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    _git_init(project)  # repo exists, but ledger has no git_head key
    (project / ".tesserae" / ".build-history.jsonl").write_text(
        json.dumps({"built_at": "2026-07-01T00:00:00Z"}) + "\n", encoding="utf-8")
    report = WikiLinter(project).run()
    assert not [f for f in report.findings if f.code.startswith("CODE_GRAPH")]


def test_code_graph_staleness_unresolvable_head_emits_single_info(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    _git_init(project)
    _ledger(project, "1234567890abcdef1234567890abcdef12345678")
    report = WikiLinter(project).run()
    unresolved = [f for f in report.findings if f.code == "CODE_GRAPH_HEAD_UNRESOLVED"]
    assert len(unresolved) == 1 and unresolved[0].severity == "info"
    assert not [f for f in report.findings if f.code == "CODE_GRAPH_STALE_FILE"]


def test_code_graph_staleness_caps_per_file_findings_at_twenty(tmp_path: Path) -> None:
    names = [f"m{i:02d}.py" for i in range(25)]
    project = _scaffold(tmp_path, graph=_code_graph(names))
    sha = _git_init(project)
    _ledger(project, sha)
    for n in names:
        (project / n).write_text("x = 1\n", encoding="utf-8")
    _sp.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
             "add", "-A"], check=True)
    _sp.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "c2"], check=True)
    report = WikiLinter(project).run()
    stale = [f for f in report.findings if f.code == "CODE_GRAPH_STALE_FILE"]
    assert len(stale) == 20
    assert [f.path for f in stale] == sorted(f.path for f in stale)
    behind = [f for f in report.findings if f.code == "CODE_GRAPH_BEHIND"]
    assert "25 tracked in graph" in behind[0].message
```

  Note the fixture nuance: `_git_init` commits **after** `_scaffold`, so `.tesserae/` is committed too — irrelevant to the check (only paths matching `SourceFile` node names emit findings; `.tesserae/...` paths never match). No `.gitignore` needed.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_lint.py -k code_graph_staleness -v` → all FAIL (no findings emitted).
- [ ] **Step 3: Implement** the check per the algorithm above; wire into `run()`.
- [ ] **Step 4: Verify** — `-k code_graph_staleness` green, then the whole file: `.venv/bin/python -m pytest tests/test_lint.py -v`. Run the staleness tests **twice** and diff the two `lint-report.md` outputs of one fixture to confirm byte-stability under fixed git state.

---

### Task 3: Compile-tail integration — head recorded before lint, byte-stability preserved

**Files:** Modify `tests/test_lint.py` only (no source changes expected; this task proves the ordering claim).

- [ ] **Step 1: Write the test** (mirror the fixture used by `test_compile_runs_lint_at_tail`, tests/test_lint.py:455, adding `_git_init` before compile):

```python
def test_compile_records_git_head_and_tail_lint_sees_no_staleness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reuse the exact project fixture from test_compile_runs_lint_at_tail,
    # but git-init the project root first.
    ...  # fixture setup as in test_compile_runs_lint_at_tail
    sha = _git_init(project_root)
    wiki.compile(...)  # same call as the neighbouring test
    ledger = (project_root / ".tesserae" / ".build-history.jsonl").read_text(encoding="utf-8")
    last = json.loads(ledger.strip().splitlines()[-1])
    assert last["git_head"] == sha
    report = json.loads((project_root / ".tesserae" / "lint-report.json").read_text(encoding="utf-8"))
    assert not [f for f in report["findings"] if f["code"].startswith("CODE_GRAPH")]
```

- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/test_lint.py::test_compile_records_git_head_and_tail_lint_sees_no_staleness -v` → PASS with no further changes (if it fails, the `_append_build_history` → tail-lint ordering assumption broke; fix in project.py, never by weakening the test).
- [ ] **Step 3: Regression sweep** — `.venv/bin/python -m pytest tests/test_lint.py tests/test_cli_commands.py -v`; pay attention to `test_compile_twice_lint_report_is_byte_stable` (the load-bearing byte-idempotence guard) and `test_cli_compile_strict_*` (info findings must not change strict exit codes).

---

## Out of scope (deliberately)

- **Auto-regeneration / scoping compile to the delta** — this is a report only; OpenWiki's "soft diff budget" is prompt-enforced regeneration policy and does not map onto Tesserae's deterministic compile.
- **Uncommitted worktree changes** — transient; the anchor is commit-to-commit.
- **Wiki-page-level mapping** — `SourceFile` node ids are the provenance unit every other lint check uses; page mapping adds surface without adding signal.
- **Content-hash fallback** — `SourceFile.metadata.sha256` could detect changes without git, but it requires reading every file at lint time and cannot see deletions cheaply; git-delta is the minimal mechanism. Revisit only if non-git projects need the signal.
