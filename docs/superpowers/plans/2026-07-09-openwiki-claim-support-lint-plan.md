# Claim-Level Citation-Support Lint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add an opt-in, LLM-assisted lint check (`tesserae lint --verify-claims`) that samples up to N cited claims from synthesis wiki pages and asks the configured LLM whether the cited source node's text actually **supports** the claim (supported / partial / unsupported), reporting the results as ordinary lint findings. Today `llm_synthesis._validate_response` only checks that cited node ids **exist** among the prompt inputs — nothing verifies semantic support. Research on the openwiki/Karpathy pattern (arXiv 2605.18490 §5.4, verified 6-0) showed holistic groundedness rubrics and per-claim citation support can *disagree in direction* on the same pages (wiki 40.2% supported vs RAG 18.9% conditional on being cited), so per-claim support is the discriminating quality signal and must be reported alongside existing checks.

**Architecture:** One new private check method `WikiLinter._check_claim_support` in `tesserae/lint.py`, gated behind a new keyword-only `verify_claims: bool = False` on `WikiLinter.run()` / `ProjectWiki.lint()` / the `tesserae lint` CLI. The check reads synthesis pages (`wiki/syntheses/*.md`), resolves each page's frontmatter `inputs:` ids to graph nodes, extracts paragraph-level claims that carry `[<node name>]` or `[<node id>]` citation markers, samples deterministically by content hash (no RNG), and issues **one** batched `complete_json` call via the existing `tesserae.llm_json.build_default_json_client()`. Verdicts become findings: `CLAIM_UNSUPPORTED` (warning), `CLAIM_PARTIAL` (info), plus one `CLAIM_SUPPORT_SUMMARY` (info) with counts, or `CLAIM_SUPPORT_SKIPPED` (info) when no LLM/candidates are available. The compile tail-lint (`project.py` `compile()` → `self.lint(severity_floor="warning")`) never passes the flag, so the check is structurally incapable of blocking or perturbing a compile.

**Tech Stack:** Python 3 stdlib (`hashlib`, `re`, `json`) + existing `tesserae.llm_json` client plumbing. No new dependencies. Tests in `tests/test_lint.py` with a stub JSON client (no network).

## Research anchor (do not re-read the research)

- Per-claim, conditional-on-cited support is the metric: "does the cited source text support this sentence — supported / partial / unsupported".
- Holistic rubric scores can look fine while individual citations fail; report both. This check is the per-claim half; the existing structural checks (ghost inputs, stale citations) stay as-is.
- Even good wikis land many "partial" verdicts (53.1% in the paper) — hence `CLAIM_PARTIAL` is **info**, not warning.

## Global Constraints

- **Off by default.** No env var auto-enable. Only `verify_claims=True` (CLI `--verify-claims`) runs it. `WikiLinter.run()` must not import `llm_json` unless the flag is set (lazy import inside the check) — the module docstring's "stdlib only" promise holds for the default path.
- **Never a compile blocker.** `ProjectWiki.compile()`'s tail lint call is not modified — it cannot opt in. No change to `compile --strict` semantics.
- **Capped + deterministic sampling.** Default cap 20 claims (`--claim-cap`). Candidates are sorted by `sha256(page_relpath + "\x00" + node_id + "\x00" + claim_text)` hex digest and the first `cap` are taken. Same artifacts → same sample, byte-for-byte, no RNG, no wall clock.
- **One LLM call.** All sampled claims go in a single `complete_json(system=..., user=..., schema_name="claim_support_v1")` batch. Any failure (client `None`, unparsable JSON, wrong shape) degrades to a single `CLAIM_SUPPORT_SKIPPED` info finding — never an exception out of `run()`.
- **Byte-idempotence: every disk write accounted for.** The check writes **nothing** new. The only writes in a lint run remain `lint-report.md` / `lint-report.json`, which `run()` already rewrites every invocation and which are *not* compile artifacts (compile idempotence checks cover `graph.json`, `wiki/`, `site/` — untouched here). No `auto_fixable=True` on any CLAIM_* finding, so `--fix-trivial` can never rewrite pages from this check. Finding messages are built only from deterministic inputs (fixed-width snippet truncation, sorted ordering via the existing `LintFinding.sort_key`); the *verdicts* may vary run-to-run because the LLM is nondeterministic — acceptable for an opt-in report, and exactly why this check must never gate compile.
- Reuse, don't duplicate: `_claim_text(node)` (already in `lint.py`) for source-node evidence text; `_split_frontmatter` for `inputs:`; `llm_json.build_default_json_client` for provider detection (returns `None` when no CLI/API backend is available — that is the availability check, do not invent another).
- One commit per task; conventional messages.

---

## File Structure

- **Modify** `tesserae/lint.py` — `_check_claim_support` + helpers (`_iter_claim_candidates`, `_sample_claims`, `_judge_claims`); extend `WikiLinter.run()` signature; update module docstring ("stdlib only" → "stdlib only by default; `verify_claims=True` opts into one LLM call").
- **Modify** `tesserae/project.py` — pass-through kwargs on `ProjectWiki.lint()`.
- **Modify** `tesserae/cli.py` — `--verify-claims` / `--claim-cap` on `_build_lint_parser`, forwarded in `_handle_lint`.
- **Modify** `tests/test_lint.py` — all new tests live here (existing `_scaffold`, `_write_synthesis`, `_node` helpers are reused).

Not touched: `tesserae/mcp_server.py` (`lint_report` keeps calling `wiki.lint()` with defaults — MCP callers never pay LLM cost), `tesserae/llm_synthesis.py`, compile paths, wiki/site writers.

---

### Task 1: Candidate extraction + deterministic sampling (pure, no LLM)

**Files:** Modify `tesserae/lint.py`; Test `tests/test_lint.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class _ClaimCandidate:
    page_relpath: str   # "wiki/syntheses/<slug>.md" (posix, relative to wiki_root)
    node_id: str        # resolved graph node id
    claim_text: str     # the paragraph containing the citation, stripped

def _iter_claim_candidates(
    wiki_root: Path, nodes_by_id: Dict[str, dict]
) -> List[_ClaimCandidate]: ...

def _sample_claims(candidates: List[_ClaimCandidate], cap: int) -> List[_ClaimCandidate]: ...
```

Extraction rules (`_iter_claim_candidates`):

1. Iterate `sorted((wiki_root / "wiki" / "syntheses").glob("*.md"))` (same pattern as `_check_synthesis_ghost_inputs`; return `[]` if the dir is missing).
2. Parse frontmatter with the existing `_split_frontmatter`; take `inputs` (list of node ids). Skip ids not in `nodes_by_id` (ghost inputs are another check's job).
3. Build the page's citation marker set: for each resolved input node, both the raw id and the node `name` (skip names containing `]` or newlines). This covers LLM bodies post-`rewrite_citations` (`[Display Name]`), pre-rewrite/heuristic bodies (`[Type:slug:hash]`), and name collisions deterministically: if two input nodes share a name, map the name to the **sorted-first** node id.
4. Split the body into paragraphs on blank lines (`re.split(r"\n\s*\n", body)`); skip paragraphs that start with `#` (headings), `|` (tables), or ``` (fences), and paragraphs shorter than 40 chars after stripping.
5. For each paragraph, for each marker `m` in the page's marker set: if `f"[{m}]"` occurs in the paragraph **not** immediately followed by `(` (regex `re.escape(f"[{m}]") + r"(?!\()"` — excludes markdown links), yield one `_ClaimCandidate(page_relpath, node_id, paragraph)`. Dedupe on `(page_relpath, node_id, paragraph)`.

Sampling (`_sample_claims`): sort candidates by `hashlib.sha256(f"{c.page_relpath}\x00{c.node_id}\x00{c.claim_text}".encode("utf-8")).hexdigest()`, tie-break by the tuple itself, take `[:cap]`. Pure function.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_lint.py`)

```python
from tesserae.lint import _ClaimCandidate, _iter_claim_candidates, _sample_claims

def _claim_graph() -> dict:
    return {"nodes": [
        _node("Paper:alpha:aaaa1111", "Paper", "Alpha Attention",
              metadata={"title_quality": "verified"}),
        _node("Concept:beta:bbbb2222", "Concept", "Beta Routing"),
    ], "edges": []}

def _claim_body() -> str:
    return (
        "## Overview\n\n"
        "Alpha introduced sparse attention over long contexts and reported "
        "a 2x speedup on retrieval tasks [Alpha Attention].\n\n"
        "Beta routing extends this with learned gating "
        "[Concept:beta:bbbb2222].\n\n"
        "See [Alpha Attention](papers/alpha.md) for details.\n"
    )

def test_claim_candidates_extracted_from_synthesis_pages(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_claim_graph())
    _write_synthesis(project, "daily-2026-07-01",
                     ["Paper:alpha:aaaa1111", "Concept:beta:bbbb2222"],
                     body=_claim_body())
    nodes = {n["id"]: n for n in _claim_graph()["nodes"]}
    got = _iter_claim_candidates(project / ".tesserae", nodes)
    pairs = {(c.node_id, c.claim_text[:20]) for c in got}
    # display-name marker resolved to the Paper id; raw-id marker resolved too
    assert ("Paper:alpha:aaaa1111", "Alpha introduced spa") in pairs
    assert any(c.node_id == "Concept:beta:bbbb2222" for c in got)
    # markdown link [Alpha Attention](...) is NOT a citation
    assert not any("See [Alpha Attention]" in c.claim_text for c in got)
    # heading paragraph skipped
    assert not any(c.claim_text.startswith("##") for c in got)

def test_claim_sampling_is_deterministic_and_capped(tmp_path: Path) -> None:
    cands = [
        _ClaimCandidate("wiki/syntheses/a.md", f"Concept:x{i}:c{i:04d}",
                        f"Claim text number {i} with enough length to matter.")
        for i in range(50)
    ]
    first = _sample_claims(list(cands), cap=20)
    second = _sample_claims(list(reversed(cands)), cap=20)
    assert first == second          # input order irrelevant
    assert len(first) == 20         # capped
```

- [ ] **Step 2: Implement** `_ClaimCandidate`, `_iter_claim_candidates`, `_sample_claims` in `tesserae/lint.py` (module-level, alongside the other helpers). Run:
  `.venv/bin/python -m pytest tests/test_lint.py -k claim -x`

---

### Task 2: LLM judgment + findings, wired into `WikiLinter.run`

**Files:** Modify `tesserae/lint.py`; Test `tests/test_lint.py`

**Interfaces:**

```python
# WikiLinter.run gains keyword-only params (defaults preserve all callers):
def run(self, *, fix_trivial: bool = False, severity_floor: str = "info",
        verify_claims: bool = False, claim_cap: int = 20,
        llm_client: Optional[object] = None) -> LintReport: ...

def _check_claim_support(
    self, nodes_by_id: Dict[str, dict], *, cap: int, llm_client: Optional[object]
) -> Iterable[LintFinding]: ...
```

Behavior of `_check_claim_support` (called from `run()` **only when** `verify_claims` is true, appended after `_check_stale_build_history`):

1. Collect + sample candidates (Task 1 helpers). If none → yield one `CLAIM_SUPPORT_SKIPPED` info finding, message `"claim support: no cited claims found in wiki/syntheses — nothing to verify."`; return.
2. Resolve the client: use `llm_client` if provided, else lazy `from tesserae.llm_json import build_default_json_client` and call it. If `None` → one `CLAIM_SUPPORT_SKIPPED` info finding, message `"claim support: no LLM backend available (claude/codex CLI or ANTHROPIC_API_KEY); skipped."`; return.
3. Build ONE batched prompt. System prompt (module constant `_CLAIM_SUPPORT_SYSTEM`, byte-stable): judge whether SOURCE text supports CLAIM; answer per item exactly one of `supported` / `partial` / `unsupported`; judge only from the given source text; respond as JSON `{"verdicts": ["supported", ...]}` with one entry per item, same order. User prompt: `json.dumps` (with `sort_keys=True`, `ensure_ascii=False`) of `{"items": [{"index": i, "claim": c.claim_text, "source": _claim_text(nodes_by_id[c.node_id])} ...]}` over the sampled list in sampled order. Truncate each `claim` to 600 chars and each `source` to 1200 chars (fixed constants — deterministic prompt bytes for fixed artifacts).
4. Call `client.complete_json(system=_CLAIM_SUPPORT_SYSTEM, user=user, schema_name="claim_support_v1")` inside `try/except Exception` → on exception or `None`, yield one `CLAIM_SUPPORT_SKIPPED` info finding (`"claim support: LLM call failed or returned unparsable output; skipped."`) and return.
5. Parse tolerantly: accept `{"verdicts": [...]}` or a bare list; normalize each entry `str(v).strip().lower()`; anything not in `{"supported","partial","unsupported"}` (including a length mismatch's missing tail) counts as `unverifiable`.
6. Emit findings, letting the existing sort in `run()` order them:
   - per `unsupported`: `LintFinding(severity="warning", code="CLAIM_UNSUPPORTED", message=f"Cited source does not support the claim: {snippet!r}", node_id=<cited id>, path=str(self.wiki_root / c.page_relpath), suggested_fix="Re-run synthesis for this page, or correct/remove the citation.")` where `snippet = c.claim_text[:160]`.
   - per `partial`: same shape, `severity="info"`, `code="CLAIM_PARTIAL"`, message `f"Cited source only partially supports the claim: {snippet!r}"`.
   - `supported` → no per-claim finding.
   - always one `LintFinding(severity="info", code="CLAIM_SUPPORT_SUMMARY", message=f"claim support: sampled {n} claims across {p} pages — {s} supported, {pa} partial, {u} unsupported, {x} unverifiable.")`.

`run()` changes: add the three kwargs; add `if verify_claims: findings.extend(self._check_claim_support(nodes_by_id, cap=claim_cap, llm_client=llm_client))` before the `fix_trivial` block. Nothing else moves. Update the module docstring line "Stdlib only — no LLM, no network." to note the opt-in exception.

- [ ] **Step 1: Write the failing tests**

```python
class _StubJsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []
    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload
    def complete_text(self, **kwargs):
        return None

class _ExplodingClient:
    def complete_json(self, **kwargs):
        raise AssertionError("LLM must not be called")
    complete_text = complete_json

def _claim_project(tmp_path: Path) -> Path:
    project = _scaffold(tmp_path, graph=_claim_graph())
    _write_synthesis(project, "daily-2026-07-01",
                     ["Paper:alpha:aaaa1111", "Concept:beta:bbbb2222"],
                     body=_claim_body())
    return project

def test_claim_support_off_by_default(tmp_path: Path) -> None:
    project = _claim_project(tmp_path)
    report = WikiLinter(project).run(llm_client=_ExplodingClient())
    assert not [f for f in report.findings if f.code.startswith("CLAIM_")]

def test_claim_support_unsupported_is_warning_partial_is_info(tmp_path: Path) -> None:
    project = _claim_project(tmp_path)
    stub = _StubJsonClient({"verdicts": ["unsupported", "partial"]})
    report = WikiLinter(project).run(verify_claims=True, llm_client=stub)
    codes = report.by_code
    assert codes.get("CLAIM_UNSUPPORTED") == 1
    assert codes.get("CLAIM_PARTIAL") == 1
    assert codes.get("CLAIM_SUPPORT_SUMMARY") == 1
    warn = [f for f in report.findings if f.code == "CLAIM_UNSUPPORTED"][0]
    assert warn.severity == "warning" and not warn.auto_fixable
    assert len(stub.calls) == 1  # ONE batched call
    # summary counts are embedded in the message
    summary = [f for f in report.findings if f.code == "CLAIM_SUPPORT_SUMMARY"][0]
    assert "1 unsupported" in summary.message and "1 partial" in summary.message

def test_claim_support_skipped_without_llm(tmp_path: Path, monkeypatch) -> None:
    project = _claim_project(tmp_path)
    import tesserae.llm_json as llm_json
    monkeypatch.setattr(llm_json, "build_default_json_client", lambda **kw: None)
    report = WikiLinter(project).run(verify_claims=True)
    assert report.by_code.get("CLAIM_SUPPORT_SKIPPED") == 1
    assert report.by_severity.get("warning", 0) == 0

def test_claim_support_bad_llm_output_degrades_to_skipped(tmp_path: Path) -> None:
    project = _claim_project(tmp_path)
    stub = _StubJsonClient({"nonsense": True})
    report = WikiLinter(project).run(verify_claims=True, llm_client=stub)
    # wrong shape → all entries unverifiable; still a summary, never a crash
    assert report.by_code.get("CLAIM_SUPPORT_SUMMARY") == 1
    assert report.by_code.get("CLAIM_UNSUPPORTED") is None

def test_claim_support_prompt_bytes_are_stable(tmp_path: Path) -> None:
    project = _claim_project(tmp_path)
    a, b = _StubJsonClient({"verdicts": []}), _StubJsonClient({"verdicts": []})
    WikiLinter(project).run(verify_claims=True, llm_client=a)
    WikiLinter(project).run(verify_claims=True, llm_client=b)
    assert a.calls == b.calls  # identical system+user bytes across runs
```

- [ ] **Step 2: Implement** `_CLAIM_SUPPORT_SYSTEM`, `_check_claim_support`, and the `run()` kwargs. Run:
  `.venv/bin/python -m pytest tests/test_lint.py -x`
  (the full existing suite must stay green — default-path behavior is unchanged).

---

### Task 3: CLI + `ProjectWiki.lint` pass-through; compile stays untouched

**Files:** Modify `tesserae/project.py`, `tesserae/cli.py`; Test `tests/test_lint.py`

**Interfaces:**

```python
# tesserae/project.py (line ~1517)
def lint(self, fix_trivial: bool = False, severity_floor: str = "info",
         *, verify_claims: bool = False, claim_cap: int = 20,
         llm_client: Optional[object] = None) -> LintReport:
    return WikiLinter(self.project_root).run(
        fix_trivial=fix_trivial, severity_floor=severity_floor,
        verify_claims=verify_claims, claim_cap=claim_cap, llm_client=llm_client)
```

CLI (`tesserae/cli.py`):
- `_build_lint_parser`: add
  `parser.add_argument("--verify-claims", dest="verify_claims", action="store_true", help="Opt-in: sample cited claims from synthesis pages and LLM-verify the cited node supports each (supported/partial/unsupported). Needs an LLM backend; costs one batched call.")`
  and `parser.add_argument("--claim-cap", dest="claim_cap", type=int, default=20, help="Max claims to sample for --verify-claims (default: 20).")`
- `_handle_lint`: forward `verify_claims=args.verify_claims, claim_cap=args.claim_cap` into `wiki.lint(...)`. Exit-code mapping is untouched: an opted-in `CLAIM_UNSUPPORTED` warning behaves like any other warning under the chosen `--severity` floor.
- **Do not touch** the compile tail-lint (`project.py` `compile()` → `self.lint(severity_floor="warning")`) nor `compile --strict` handling in `cli.py` (~line 1284) — compile can never opt in, so CLAIM_* findings can never appear in `result["lint"]` counts.
- **Do not touch** `mcp_server.py` `lint_report` — it inherits the safe defaults.

- [ ] **Step 1: Write the failing tests**

```python
def test_cli_lint_verify_claims_flag_forwards(tmp_path: Path, monkeypatch) -> None:
    project = _scaffold(tmp_path)
    seen = {}
    def fake_lint(self, fix_trivial=False, severity_floor="info", **kw):
        seen.update(kw); return LintReport()
    monkeypatch.setattr(ProjectWiki, "lint", fake_lint)
    rc = cli_main(["lint", "--project", str(project),
                   "--verify-claims", "--claim-cap", "5"])
    assert rc == 0
    assert seen["verify_claims"] is True and seen["claim_cap"] == 5

def test_compile_tail_lint_never_verifies_claims(tmp_path: Path, monkeypatch) -> None:
    # Reuse the arrangement of test_compile_runs_lint_at_tail; additionally
    # patch WikiLinter._check_claim_support to raise if ever invoked.
    from tesserae.lint import WikiLinter as WL
    monkeypatch.setattr(WL, "_check_claim_support",
                        lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("compile must not verify claims")))
    # ... same scaffold+compile as test_compile_runs_lint_at_tail; assert compile succeeds
```

  (For the second test, copy the compile setup already used by `test_compile_runs_lint_at_tail` in this file.)

- [ ] **Step 2: Implement** the pass-through and flags. Run:
  `.venv/bin/python -m pytest tests/test_lint.py tests/test_cli_commands.py -x`

---

## Verification

- `.venv/bin/python -m pytest tests/test_lint.py tests/test_cli_commands.py` — all green (note: use `.venv/bin/python`, system python3.9 fails collection).
- Manual smoke (needs a compiled project + claude/codex CLI): `tesserae lint --verify-claims --claim-cap 5 --severity error` — expect a `CLAIM_SUPPORT_SUMMARY` info line in `lint-report.md` and exit 0 unless errors exist.
- Byte-idempotence audit: `tesserae compile` twice → `graph.json`, `wiki/`, `site/` byte-identical (unchanged by this feature; `test_compile_twice_lint_report_is_byte_stable` still passes since compile never opts in).

## Explicitly out of scope (YAGNI)

- Verifying entity/concept wiki pages or compiled context bundles (no stable on-disk claim+citation format there today).
- An auto-fix for unsupported claims (would rewrite LLM prose — nondeterministic write into wiki/).
- MCP/`compile --strict` exposure, config-file knobs, per-page caps, holistic rubric scoring.
- Caching verdicts across runs (cap of 20 keeps cost trivial; add only if usage demands).
