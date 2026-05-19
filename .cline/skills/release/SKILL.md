---
name: release
description: ---
---
---
name: release
description: Use when the user asks to "cut a release", "ship version", "release Tesserae", or wants to publish a new version. Runs the full release flow — tests, version bump, build, tag, push, GitHub release.
---

# Release Workflow

Cut a release of Tesserae. NEVER skip a step; NEVER --no-verify; NEVER force-push.

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
   .venv/bin/pytest tests/ -x
   ```
   Do NOT proceed past a red test.
5. Run the demo build smoke (matches CI):
   ```bash
   .venv/bin/python -m tesserae project setup --yes --no-color --source . --no-cognee --skip-raganything --skip-install-cognee --skip-install-raganything --skip-install-understand-anything
   .venv/bin/python -m tesserae project compile
   .venv/bin/python -m tesserae project build-site
   ```

## Version bump + changelog

6. Edit `pyproject.toml` `version = "X.Y.Z"`.
7. If `package.json` exists, mirror the bump.
8. Generate a one-paragraph changelog from `git log --oneline v<prev>..HEAD` (where v<prev> is the previous tag, or HEAD~20 if no tags).
9. Commit:
   ```bash
   git add pyproject.toml package.json
   git commit -m "release: vX.Y.Z" -m "<changelog paragraph>"
   ```

## Tag + push

10. Tag: `git tag -a vX.Y.Z -m "vX.Y.Z"`.
11. Push commit + tag:
    ```bash
    git push origin main
    git push origin vX.Y.Z
    ```
12. Wait for CI green (`gh run watch <run-id>`). If CI fails, STOP — do not GH-release a broken build.

## GitHub release

13. Create the release:
    ```bash
    gh release create vX.Y.Z --title "vX.Y.Z" --notes "<changelog paragraph>"
    ```
14. Verify the release URL `gh release view vX.Y.Z --json url --jq .url` and paste it back to the user.

## Optional: PyPI publish (when ready)

Tesserae is not yet on PyPI. When the maintainer is ready to publish:

15. Build sdist + wheel: `.venv/bin/python -m build`.
16. Upload: `.venv/bin/twine upload dist/tesserae-X.Y.Z*`.
17. Verify: `pip install tesserae==X.Y.Z` in a fresh venv.

## Final report

Tell the user:
- Version released
- Tests passed count
- GitHub release URL
- Whether PyPI was published

## Rollback

If anything goes wrong AFTER step 11 (push), STOP and ask the user before any
rollback. Re-tagging or deleting a pushed tag is a manual decision — the skill
does not auto-rollback.

