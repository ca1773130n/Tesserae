# Publishing Checklist

<!-- translations:start -->
<p align="center"><a href="i18n/publishing-checklist.ko.md">한국어</a> · <a href="i18n/publishing-checklist.zh.md">中文</a> · <a href="i18n/publishing-checklist.ja.md">日本語</a> · <a href="i18n/publishing-checklist.ru.md">Русский</a> · <a href="i18n/publishing-checklist.es.md">Español</a> · <a href="i18n/publishing-checklist.fr.md">Français</a> · <a href="../i18n/publishing-checklist.de.md">Deutsch</a></p>
<!-- translations:end -->
Use this checklist before presenting Tesserae publicly.

## Repository hygiene

- [ ] README explains what the project is and what problem it solves.
- [ ] Install command works from a fresh shell.
- [ ] Quickstart uses `tesserae`, not `python3 -m`.
- [ ] Architecture docs explain raw evidence → graph → projections.
- [ ] Feature map lists implemented features without overselling future work.
- [ ] Session-history docs explain explicit import, privacy review, generated routes, and transcript typography.
- [ ] Self-dogfood demo has been run and documented.
- [ ] Generated artifacts are reproducible and either ignored or intentionally published.
- [ ] RAG-Anything index refreshed (if enabled)

## Verification

```bash
.venv/bin/pytest tests/ -x          # ABORT on any failure — never ship a red build
./scripts/install.sh --help
tesserae init --help
tesserae compile --help
tesserae context --help     # On-Demand Context Compiler
```

### Demo build smoke (manual — nothing in CI covers this)

Run this by hand before every release. It used to mirror a `build-demo` CI job
that ran on each push to `main`; that workflow was removed, so this compile path
is now checked only here. `tests.yml` runs the unit suite and does not exercise
`init` → `compile` → `export site` end to end.

It compiles Tesserae against its own source tree with the deterministic
extractor (no LLM calls, no API keys) and builds the site:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## Release flow

Driven by the `release` skill (`.claude/skills/release/SKILL.md`). The latest tag
is `v0.5.0`.

- [ ] On `main`, working tree clean, `git pull --ff-only origin main`.
- [ ] Tests + demo-build smoke pass (above).
- [ ] Bump `pyproject.toml` `version = "X.Y.Z"` (mirror `package.json` if present); commit `release: vX.Y.Z` with a one-paragraph changelog from `git log v<prev>..HEAD`.
- [ ] Tag `git tag -a vX.Y.Z -m "vX.Y.Z"`; push commit then tag.
- [ ] Wait for CI green (`gh run watch <run-id>`) — do **not** cut the GitHub release on a red build.
- [ ] Publish the GitHub release. PyPI publish is optional/when-ready.

### GitHub Pages

**No workflow deploys the site any more.** The `build-demo` workflow did this on
every push to `main`; it was removed. The site last deployed by it is still
served, and the README still links it as the live demo — so that page is now a
snapshot frozen at the final `build-demo` run, not a current view of `main`.

Republishing is a manual `tesserae export site` plus an upload, or a new
workflow. Whichever way it goes, decide deliberately: a demo link that silently
drifts from the code is worse than no demo link.

## Self-dogfood

Integration opt-ins (RAG-Anything) are now
**interactive wizard prompts**, not CLI flags. Run the wizard and answer them:

```bash
tesserae init \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts
# when the wizard prompts:
#   - enable RAG-Anything, install: yes, parser: mineru, run after install: yes
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

For a fully non-interactive run, use `tesserae init --yes` (all integrations
OFF), then enable each integration in `.tesserae/config.json` — the wizard
writes them under the `memory_backends` and `external_tools`
(RAG-Anything) keys — and run `tesserae integrations
refresh <name>` for each before compiling. See the integration docs for the
exact config keys.

## Demo talking points

- Tesserae is not a generic noun-phrase graph. It uses a controlled ontology.
- Research and development code share infrastructure but keep distinct schemas.
- Markdown and HTML are projections, not authoritative truth stores.
- The default path is local and no-API-key friendly.
- Agent harnesses and MCP make the graph usable by coding agents.
- Imported harness session pages turn prior Claude Code/Codex work into searchable project memory while keeping transcript discovery explicit.
