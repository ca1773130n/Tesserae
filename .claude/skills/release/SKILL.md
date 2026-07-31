---
name: release
description: Use when the user asks to "cut a release", "ship version", "release Tesserae", or wants to publish a new version. Runs the full release flow — tests, version bump, build, tag, push, GitHub release.
---

# Release Workflow

Cut a release of Tesserae. NEVER skip a step; NEVER --no-verify. Never
force-push `main` — amending an unmerged `release/vX.Y.Z` branch you alone hold
is fine, and `--force-with-lease` is the only acceptable form.

**PyPI AND npm publish are BOTH MANDATORY on every release** — a release is not
complete until the new version is live on PyPI *and* on npm, both install-verified.
The GitHub release, the PyPI publish, and the npm publish are all required;
finishing any one without the others is an incomplete release. Do not treat the
PyPI or npm sections as optional.

**Three version files must move together every release** and match the tag:
`pyproject.toml`, `.claude-plugin/plugin.json`, and `npm/package.json`. A mismatch
ships an incoherent release (the npm wrapper pins `tesserae==<npm version>`).

## Pre-flight

1. Confirm we are on `main` and the working tree is clean.
   - `git status` must show no uncommitted changes.
   - `git rev-parse --abbrev-ref HEAD` must print `main`.
   - If not clean / not on main, STOP and tell the user what's blocking.
2. Pull latest: `git pull --ff-only origin main`.
3. Determine bump type:
   - PATCH (0.1.0 → 0.1.1): bug fixes, no API change.
   - MINOR (0.1.0 → 0.2.0): new features, additive only.
   - MAJOR (0.1.0 → 1.0.0): breaking changes.
   - If unclear, ASK the user.

## Tests gate

4. Run the test suite. ABORT on any failure.
   ```bash
   uv run pytest tests/ -x
   ```
   Takes ~9 minutes; budget for it rather than assuming a hung run. Do NOT
   proceed past a red test.

   Do not run anything else through `uv run` while this is going — a second
   `uv run` re-syncs the shared `.venv` underneath the running suite.

   This local run is a pre-check, not the gate. The real gate is CI on the
   release PR (step 11), which runs the same suite on a clean checkout across
   three Python versions.
5. **NEVER compile in the project root during a release.** No `tesserae init`,
   no `tesserae compile`, no `export site` — not even with
   `--extractor deterministic`.

   A compile in the project root overwrites `.tesserae/graph.json`, and the
   deterministic extractor produces a much smaller graph than the LLM one, so
   a "smoke test" silently destroys the real knowledge base.

   **Nothing covers the compile path any more.** This step used to say the
   `build-demo` workflow ran it on every push to `main`, and the release gated
   on that run being green. That workflow was deleted (2026-08-01); `tests.yml`
   runs the unit suite and never exercises `init` → `compile` → `export site`
   end to end.

   So the end-to-end path is unverified at release time, deliberately. If you
   want it verified, run it from a scratch directory OUTSIDE the repo so the
   real graph is untouched — `cd ~/.blackhole/Tesserae/$(date +%F) && tesserae
   init --yes --source /path/to/repo && tesserae compile && tesserae export
   site` — and budget the minutes. Do not "just check quickly" in the repo root;
   that is the one thing this step exists to prevent.

## Version bump + changelog

6. Bump the version in ALL THREE version files to `X.Y.Z`:
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `.claude-plugin/plugin.json` → `"version": "X.Y.Z"`
   - `npm/package.json` → `"version": "X.Y.Z"`
   After editing, confirm they agree:
   ```bash
   grep -h 'version' pyproject.toml .claude-plugin/plugin.json npm/package.json
   ```
7. Write the release note + its 7 i18n translations (docs i18n is a project
   invariant): `docs/release-notes/vX.Y.Z.md` plus
   `docs/i18n/release-notes/vX.Y.Z.{ko,zh,ja,ru,es,fr,de}.md`. Run
   `.venv/bin/python -m pytest tests/test_docs_i18n.py -q` (must be green) —
   it fails if the English note exists without all 7 translations.
8. Generate a one-paragraph changelog from `git log --oneline v<prev>..HEAD` (where v<prev> is the previous tag, or HEAD~20 if no tags).
9. **Regenerate the lockfile, then commit it with everything else.** `uv.lock`
   pins `tesserae` at its own version, so a version bump makes it stale — and
   CI runs `uv sync --locked`, which FAILS on a stale lock. Omitting it turns
   the release PR red on the very check this project added in v0.28.5.
   ```bash
   uv lock && uv lock --check          # must exit clean
   git add pyproject.toml .claude-plugin/plugin.json npm/package.json \
           uv.lock docs/release-notes/ docs/i18n/release-notes/
   git commit -m "release: vX.Y.Z" -m "<changelog paragraph>"
   ```
   `git status` must be clean afterwards. A stray ` M uv.lock` means it did not
   get staged.

## Release PR + tag

10. **`main` is protected — you cannot push to it.** `enforce_admins` is on and
    three status checks are required, so the release commit goes through a PR
    like any other change. A direct `git push origin main` is rejected with
    `GH006`.
    ```bash
    git checkout -b release/vX.Y.Z
    git push -u origin release/vX.Y.Z
    gh pr create --title "release: vX.Y.Z" --body "<changelog>"
    ```
11. Wait for all three legs green, then merge and sync:
    ```bash
    gh run watch <run-id> --exit-status
    gh pr merge <n> --squash --delete-branch
    git checkout main && git pull --ff-only origin main
    ```
    If CI fails, STOP — fix it on the branch. Do not tag a red build.
12. Tag the merged commit on `main` and push the tag. The tag push is what
    triggers the npm OIDC workflow (step 19), so it must come after the merge:
    ```bash
    git tag -a vX.Y.Z -m "vX.Y.Z"
    git push origin vX.Y.Z
    ```

## GitHub release

13. Create the release:
    ```bash
    gh release create vX.Y.Z --title "vX.Y.Z" --notes "<changelog paragraph>"
    ```
14. Verify the release URL `gh release view vX.Y.Z --json url --jq .url` and paste it back to the user.

## PyPI publish (REQUIRED — every release)

Tesserae is on PyPI (since v0.5.0; 0.1.0–0.4.0 published earlier). Every release
publishes here — this section always runs, never "when ready".
Credentials: `PYPI_TOKEN` env var (no ~/.pypirc on this machine).

15. Build from a CLEAN worktree of the tag — the working tree may carry
    post-tag work. `build` and `twine` are NOT in `.venv` (they are not project
    dependencies, and `.venv/bin/python -m build` fails with "No module named
    build"); pull them in per-invocation with `uv run --with`:
    ```bash
    git worktree add /tmp/tesserae-vX.Y.Z-build vX.Y.Z
    cd /tmp/tesserae-vX.Y.Z-build
    uv run --with build --no-project python -m build
    ```
    `--no-project` matters: without it uv tries to resolve the worktree as a
    project and re-syncs an environment you do not want.
16. Check + upload from inside that worktree:
    ```bash
    uv run --with twine --no-project python -m twine check dist/*
    TWINE_USERNAME=__token__ TWINE_PASSWORD="$PYPI_TOKEN" \
      uv run --with twine --no-project python -m twine upload \
        --non-interactive dist/tesserae-X.Y.Z*
    ```
17. Verify from a NEUTRAL cwd (the repo root pollutes sys.path) with a
    3.10+ interpreter (system python3 is 3.9 — use the venv's 3.11): fresh venv
    in /tmp, install, then check `importlib.metadata.version`, that the
    `tesserae` / `tesserae_mcp` console scripts exist, and that the CLI runs.

    **Use `--no-cache-dir`.** pip caches the index, so it keeps resolving to the
    PREVIOUS version and reports `No matching distribution found` for one that
    is already live. That failure is indistinguishable from propagation lag and
    will send you chasing a non-existent publish problem. Confirm the truth from
    the API, not from pip:
    ```bash
    curl -s https://pypi.org/pypi/tesserae/json | \
      python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"

    /tmp/verify-X.Y.Z/bin/pip install --no-cache-dir "tesserae==X.Y.Z"
    /tmp/verify-X.Y.Z/bin/tesserae status   # expect "project not initialized"
    ```
18. Clean up: `git worktree remove /tmp/tesserae-vX.Y.Z-build --force` and
    `rm -rf /tmp/verify-X.Y.Z`.

## npm publish (REQUIRED — every release)

Tesserae ships a thin Node wrapper at **`@jokerized/tesserae`** (the bare name
`tesserae` is taken on npm). Source lives in `npm/`; it forwards to the Python CLI.

Publishing is **OIDC trusted publishing** from GitHub Actions — there is no
npm token any more (the automation token was revoked 2026-07-31). The
`npm publish` workflow at `.github/workflows/npm-publish.yml` fires on the
`vX.Y.Z` tag pushed in step 12 and mints a short-lived credential from its
`id-token: write` claim. Provenance attestations are generated automatically.

**Do not publish npm by hand.** There is no token to do it with, and a manual
publish would skip the provenance attestation.

19. The tag push in step 12 triggers the workflow. Watch it:
    ```bash
    gh run list --workflow=npm-publish.yml --limit 1
    gh run watch <run-id> --exit-status
    ```
    If it fails with `ENEEDAUTH`, the trusted-publisher entry on npmjs.com no
    longer matches — every field is case-sensitive and npm does NOT validate
    them on save. Expected values: org `ca1773130n`, repo `Tesserae`, workflow
    filename `npm-publish.yml`, no environment, action `npm publish`.
20. Verify it landed + is public. `npm view` 404s for several minutes after a
    publish (read-replica lag), so do not trust it as the first check:
    ```bash
    npm view @jokerized/tesserae version    # retry until it shows X.Y.Z
    ```
21. Once it propagates, run the install-and-run smoke from a NEUTRAL cwd
    (forward to the dev venv to avoid the macOS system-python3 prompt / a slow
    pipx download):
    ```bash
    cd /tmp && TESSERAE_PYTHON=<repo>/.venv/bin/python \
      npx -y @jokerized/tesserae@X.Y.Z status   # expect the real CLI's "not initialized" line
    ```

## Final report

Tell the user:
- Version released (all three version files + tag agree)
- Tests passed count
- GitHub release URL
- PyPI URL + fresh-venv install verification (REQUIRED)
- npm URL (`https://www.npmjs.com/package/@jokerized/tesserae`) + the OIDC workflow run + npx smoke (REQUIRED — every release ends here)

## Rollback

If anything goes wrong AFTER step 12 (tag push), STOP and ask the user before
any rollback. Re-tagging or deleting a pushed tag is a manual decision — the
skill does not auto-rollback. Before the tag exists, the release lives entirely
on a PR branch and can be fixed in place.

Note that the tag push is the point of no return for npm: it fires the OIDC
workflow, and a published npm version cannot be reused even after unpublishing.
