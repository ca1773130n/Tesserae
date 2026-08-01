# Handoff — 2026-08-02

Written at the end of a session that hit 92% of the weekly quota. Two repos are
in play: **Tesserae** (clean, shipped) and **Judged** (mid-refactor, build
broken). Read the Judged section first — it is the only thing here that is
actively broken.

---

## 1. Start here: Judged has a broken build and uncommitted work

`~/Developer/Projects/Judged`, main at `b64ca87`.

```
 M crates/judged-core/src/lib.rs
 M crates/judged-core/src/roots/manifest.rs
```

`cargo test` fails to compile:

```
error[E0432]: unresolved imports `judged_core::gate1::content::{ContentClass,
    ContentEvidence, ContentGate, ContentVerdict, GeneratedVia, SequenceRank,
    SequenceScheme}`
error[E0425]: cannot find function `vendor_rule_census` in module
    `judged_core::gate1::content`
error[E0425]: cannot find function `generated_rule_census` in module
    `judged_core::gate1::content`
error: could not compile `judged-core` (test "gate1_content")
```

The test `gate1_content` expects a `gate1::content` module that does not exist
in the shape it wants. Either the module was being extracted and the work
stopped partway, or the test was written ahead of the implementation. **Find out
which before touching anything else** — `git diff` on the two modified files is
the fastest way in, and `git log -p --  crates/judged-core/src/gate1/` will show
whether `content` ever existed.

Do not start new Judged work on top of a red build.

Judged state otherwise: crates are `judged-cli`, `judged-core`, `judged-mutants`,
`judged-ratchet`. PR #2 (E2 mutation-injection suite + §9.14 ratchet) is merged.
PR #1 (research baseline) shows CLOSED, but the document is **not** lost — it
reached main via a later PR and lives at
`docs/research/2026-07-31-universal-safe-repo-cleaner-research.md`. There are
also four eval reports under `docs/evals/` that postdate this session's
knowledge; read them before assuming anything about where Judged has got to.

---

## 2. Tesserae is clean — nothing pending

| | |
|---|---|
| main | `4524492b`, clean, synced |
| version / tag | 0.28.5, `v0.28.5` — live on GitHub, PyPI, npm |
| branches | `main` + `gh-pages` only |
| worktrees | none |
| open PRs / issues | none |
| branch protection | `enforce_admins=true`, 3 required checks, force-push and deletion blocked, linear history |

Protection is real — verified by an actual rejected push (`GH006`), not by
reading the setting back. **`git push origin main` will be refused**, including
for you. Everything goes through a PR with three green legs. If GitHub Actions
is ever down and you are truly blocked:

```bash
gh api -X DELETE repos/ca1773130n/Tesserae/branches/main/protection/enforce_admins
# ... and -X POST to restore it. Do not leave it off.
```

---

## 3. What shipped this session

Six PRs (#95–#98, #100–#103) plus the v0.28.5 release.

- **#95** stale `uv.lock` — `uv lock --check` was failing on main.
- **#96** pytest collected the gitignored upstream clones under `evals/`; added
  `[tool.pytest.ini_options]` with `testpaths` and `pythonpath`.
- **#97** the test CI itself. Nothing ran the suite on push before this.
- **#98** removed `build-demo.yml` (at your request).
- **#100** fixed six defects in the release skill, found by *running* it.
- **#101/#102** tracked `.claude/skills/`, `AGENTS.md`, `CLAUDE.md` — they had
  been gitignored, so project procedure existed on one laptop only.
- **#103** the KG growth PoC (below).

---

## 4. The KG growth PoC — what it proves and what is left

`evals/growth/` on main: `run.py`, `questions.yaml`, `sweep_hops.py`,
`report.md`.

```bash
uv run python evals/growth/run.py --out evals/growth/report.md
```

Compiles `examples/demo-corpus` in cumulative chronological slices (50 papers,
2016→2024) and asks after each whether 15 multi-hop questions have become
answerable.

| N | through | nodes | edges | answerable | controls |
|---|---|---|---|---|---|
| 8 | 2021-03 | 416 | 1,026 | 0/15 | 0 |
| 16 | 2021-11 | 808 | 2,063 | 3/15 | 0 |
| 24 | 2023-02 | 1,183 | 3,063 | 7/15 | 0 |
| 32 | 2023-10 | 1,614 | 4,147 | 9/15 | 0 |
| 40 | 2023-12 | 1,970 | 5,078 | 12/15 | 0 |
| 50 | 2024-04 | 2,387 | 6,169 | **14/15** | **0** |

Nodes grow linearly (~40/paper, edges/node flat at 2.58) while answerability
moves in steps tied to specific papers landing. Every question unlocked at or
after the slice its required papers arrived — never before.

**It proves the graph holds the connection an answer would traverse. It does not
prove an agent answered correctly.** Keep that distinction in anything you write
on top of this.

### Open items, roughly in value order

1. **Anchor matching is substring-based and brittle.** The one unanswered
   question (`hash-encoding`) has its answer sitting in the graph —
   `ContributionClaim: "Orders-of-magnitude speedup without sacrificing
   quality"` — but the anchor string `"training speed"` selects four unrelated
   nodes instead. It was left failing deliberately: rewording the question to
   make it pass is tuning the test to the result. Fixing the *mechanism*
   (embeddings, alias expansion, node-id pinning) is legitimate and would
   probably take this to 15/15 honestly.
2. **The curve is not bit-reproducible.** 14/15 here, 15/15 in the sweep, same
   code. LLM extraction varies. Either pin extraction or report a band across
   runs rather than a single number.
3. **The corpus stops at 50 dated papers.** 24 repos, 6 daily digests, 2 weekly
   syntheses and 3 open questions carry no `date:` and are therefore excluded
   from slicing. Adding dates would roughly double the corpus and let questions
   span document *kinds*, not just papers.
4. **No CI wiring.** Deliberate — a fresh run is ~75 min. If it ever becomes a
   regression gate it needs a small fixed slice set and a warm cache.

### The trap this experiment kept setting

Four separate defects, each caught by a control or by a number that did not fit,
**none by inspection**:

- A `starts & goals` guard meant to reject vague anchors discarded exactly the
  nodes that answered the question. It suppressed 8 of 15. The better the graph
  answered, the more surely it scored zero.
- A `CommunitySummary` hub made everything two hops from everything.
- A shared `LPIPS` metric linked Direct Sparse Odometry to Magic3D.
- A degree-percentile fix (my idea, stated confidently) was **refuted by
  measurement**: the spurious path runs through degree-2/3/16 nodes while real
  answer nodes sit at 38–54.

`MAX_HOPS=3` came from sweeping 1–4 (`sweep_hops.py`, one compile pass). Do not
raise it without re-running that sweep — 4 fails the controls.

**Keep the two controls.** They are the only reason any of the above was found.

---

## 5. Traps that cost real time — do not rediscover these

- **Never compile in the repo root.** It overwrites `.tesserae/graph.json` with
  a much smaller graph. Always `--work ~/.blackhole/...`.
- **`git push --dry-run` does not test branch protection.** It reported success
  against a branch that rejects real pushes; the server does not run the
  pre-receive hook for dry runs. The only valid probe is a real push getting
  `GH006`.
- **`pip` caches the index.** It will report a *live* PyPI version as missing.
  Use `--no-cache-dir`, and check `https://pypi.org/pypi/tesserae/json` for the
  truth rather than trusting pip.
- **`build` and `twine` are not in `.venv`.** Use
  `uv run --with build --no-project python -m build`.
- **`uv run` re-syncs the shared `.venv`.** Do not run one while a long test run
  is in flight — it mutates the environment underneath it.
- **A fresh `--work` dir re-extracts everything** (~730s/slice). Re-*evaluating*
  against an existing compiled graph is seconds. Cheap to iterate on scoring,
  expensive to iterate on compiling.
- **Anything under `docs/` needs all 7 translations** or CI fails
  (`tests/test_docs_i18n.py`). Excluded dirs: `i18n`, `launch`, `superpowers`,
  `screencasts`, `assets`, and now `handoffs`. Generated eval output belongs
  beside its harness (`evals/growth/report.md`), not in `docs/`.
- **`gh run list` right after a push returns the *previous* run.** I reported a
  stale failure as if it were a new one. Match on `headSha` before trusting a
  conclusion.
- **`evals/` holds 877MB of vendored clones** (cognee, MegaMem) carrying
  uncommitted original work — custom `claude_cli`/`codex_cli` adapters, smoke
  tests, `.env` files — that exists in **no git repository**. Not recoverable if
  removed. Never clean it. `evals/*` is ignored with negations only for our own
  harnesses.

---

## 6. Disk to reclaim

`~/.blackhole/Tesserae/2026-08-01/` is **793MB**, mostly 13 `kg-*` work dirs
from this session and from a failed multi-agent run.

**Keep** `kg-final/` (134MB) — it holds the compiled graph behind the committed
`evals/growth/report.md`, and regenerating it costs ~75 minutes.

**Disposable** — every one answered its question already: `kg-growth`,
`kg-sweep`, `kg-pprlift`, `kg-edgetype`, `kg-edgetype-work`, `kg-pctl`,
`kg-pctl-scratch`, `kg-witness-src`, `kg-evidence`, `kg-evidence-wt`, `kg-h1`,
`kg-growth-probe`. That is ~660MB.

Also on disk and worth keeping until you are sure: `2026-07-31/`
`branch-prune-restore.txt` (restore commands for 17 pruned branches),
`agents-claude-md-backup/`, `release-skill-backup/`.

---

## 7. Things I got wrong, so you can discount accordingly

Stated plainly because the alternative is you trusting these patterns.

- Recommended a degree-percentile fix with more confidence than the evidence
  supported. Two minutes of measurement refuted it.
- Claimed "multi-hop contributes nothing" from five of six slices while the
  sixth was still compiling. It was false at N=50.
- Walked back the nondeterminism caveat after seeing two runs agree, then the
  next run disagreed. Two data points is not a reproducibility claim.
- Reported a CI failure that belonged to the previous commit, because I read a
  run ID fetched before the new run existed.
- Ran a real `git push` to test branch protection while admin enforcement was
  off, which landed a junk commit on `main`. It had to be force-pushed away.

The pattern: I was reliably wrong when I reasoned instead of measuring, and the
measurements were cheap every single time.

---

## 8. Quota

92% of the weekly allowance used, resetting **Aug 6, 3am (Asia/Seoul)**. A
multi-agent workflow launched near the end returned nothing at all — all 10
agents hit the session limit after burning 576k tokens and 48 minutes. Check
`/usage` before starting anything with a fan-out.
