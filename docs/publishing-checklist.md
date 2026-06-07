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

### Demo build smoke (matches the `build-demo` CI job)

The release flow and CI both compile Tesserae against its own source tree with
the deterministic extractor (no LLM calls, no API keys) and build the site:

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

The `build-demo` workflow (push to `main`) always uploads the compiled dogfood
site as an inspectable workflow artifact, and **also** deploys it to GitHub Pages
when Pages is enabled. The Pages steps are `continue-on-error`: the default
`GITHUB_TOKEN` cannot *create* a Pages site, so the very first deploy needs a
one-time manual flip at **Settings → Pages → Source: GitHub Actions**. Until
that toggle is on, the build still stays green and the artifact is still produced.

## Self-dogfood

Integration opt-ins (Understand Anything, RAG-Anything, cognee) are now
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
#   - enable Understand Anything (platform: codex), install: yes
#   - enable RAG-Anything, install: yes, parser: mineru, run after install: yes
#   - enable cognee, install: yes
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

For a fully non-interactive run, use `tesserae init --yes` (all integrations
OFF), then enable each integration in `.tesserae/config.json` — the wizard
writes them under the `memory_backends` (cognee) and `external_tools`
(Understand Anything, RAG-Anything) keys — and run `tesserae integrations
refresh <name>` for each before compiling. See the integration docs for the
exact config keys.

## Demo talking points

- Tesserae is not a generic noun-phrase graph. It uses a controlled ontology.
- Research and development code share infrastructure but keep distinct schemas.
- Markdown and HTML are projections, not authoritative truth stores.
- The default path is local and no-API-key friendly.
- Agent harnesses and MCP make the graph usable by coding agents.
- Imported harness session pages turn prior Claude Code/Codex work into searchable project memory while keeping transcript discovery explicit.
