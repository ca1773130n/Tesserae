# Installation

<!-- translations:start -->
<p align="center"><a href="i18n/installation.ko.md">한국어</a> · <a href="i18n/installation.zh.md">中文</a> · <a href="i18n/installation.ja.md">日本語</a> · <a href="i18n/installation.ru.md">Русский</a> · <a href="i18n/installation.es.md">Español</a> · <a href="i18n/installation.fr.md">Français</a> · <a href="../i18n/installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae is published on PyPI and exposes shell commands so users do not have to run `python3 -m tesserae.cli` manually.

## Install from PyPI (recommended)

```bash
pip install tesserae
```

That's it. `pip` registers two console scripts on your `PATH`:

```bash
tesserae --help
tesserae_mcp --help
```

The canonical command in docs is `tesserae`. `tesserae_mcp` starts the MCP server (which now exposes the on-demand `compile_context` tool — see the Quickstart).

> **pipx is fine too.** If you prefer to keep CLI tools in their own isolated venvs:
> ```bash
> pipx install tesserae
> ```

## Upgrade

```bash
pip install --upgrade tesserae
```

## Machine-wide setup (set once, all projects)

Configure Tesserae once instead of per project, and install the optional
dependencies from one command:

```bash
# Interactive machine-wide setup — pick LLM provider/effort + which deps to install:
tesserae setup
# …or non-interactively (written to ~/.tesserae/config.json) + install everything:
tesserae setup --llm-provider codex --reasoning-effort medium --install all

# Just see / manage optional dependencies
tesserae config deps                     # show what's installed
tesserae config deps --install memex     # fast transcript search (needs cargo)
```

Known optional dependencies: **memex** (fast transcript search), **cognee**,
**raganything**. A per-project `.tesserae/config.json`
still overrides these global defaults (resolution order: env → project → global →
built-in). `tesserae init` also offers to install memex during an interactive setup.

## Optional integrations (per project)

The default wheel is intentionally light, and the optional memory backends are
**off by default**. `tesserae init` is the single per-project onboarding step —
its wizard picks the LLM provider and detected sources; the heavier
companion/runtime pieces are installed machine-wide via `tesserae setup
--install …` (or `tesserae config deps --install …`) and enabled per project in
`.tesserae/config.json`:

```bash
# Machine-wide installs of the optional pieces:
tesserae setup --install raganything --install cognee

# Then per project: enable what you want in .tesserae/config.json
#   memory_backends.raganything.enabled: true
#   memory_backends.cognee.enabled: true        (query via `tesserae query --backend …`)
```

Manual package installs are still available for advanced workflows:

```bash
pip install kuzu graphiti-core
pip install "tesserae[cognee]"
```

- `kuzu` — Kuzu graph persistence.
- `tesserae[cognee]` — the opt-in Cognee runtime add/cognify workflows (disabled by default; the Codex-patched cognify mode was removed).
- RAG-Anything — installed via `pip install 'raganything[all]'` (`tesserae setup --install raganything`); Tesserae stores a managed refresh wrapper for multimodal parser runs.
- `graphiti-core` — live Graphiti/Neo4j sync. `export graphiti` and `export graphiti --sync --dry-run` work without it.

The Anthropic-backed synthesis path uses an extras marker:

```bash
pip install "tesserae[synthesis-llm]"
```

Real semantic embeddings (the default retrieval lane as of v0.5.0) ship behind the `semantic` extra:

```bash
pip install "tesserae[semantic]"
```

This pulls in `model2vec` and downloads a lightweight, offline, torch-free static model (~8 MB `potion-base-8M`, fetched once on first use). Without it, hybrid/embedding retrieval falls back to a non-semantic hash-bucket stub and emits a loud warning, so installing this extra is recommended for anyone using `tesserae ask`, `tesserae context`, or the MCP `compile_context` tool.

For the multimodal RAG-Anything stack with all parsers preinstalled:

```bash
pip install 'tesserae[raganything-all]'
```

> **System prerequisite:** parsing `.doc/.docx/.ppt/.pptx/.xls/.xlsx` requires LibreOffice on the host. Install it via your platform's package manager (e.g., `brew install --cask libreoffice`, `apt-get install libreoffice`); RAG-Anything skips Office documents with a warning when LibreOffice is missing.

## Install from source (for contributors)

If you want to hack on the codebase, install the editable checkout instead:

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

A convenience installer is also bundled — it clones, creates a project-local `.venv`, runs `pip install -e .`, and drops the wrappers into `~/.local/bin`:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

Useful flags (`./scripts/install.sh --help`):

| Option | Purpose |
| --- | --- |
| `--dir PATH` | Install or update the checkout at `PATH`. |
| `--branch NAME` | Install a specific branch. |
| `--repo URL` | Override the Git repository URL. Useful for forks or local smoke tests. |
| `--bin-dir PATH` | Write command wrappers somewhere other than `~/.local/bin`. |
| `--no-venv` | Install into the current Python environment instead of creating `.venv`. |
| `--skip-shell-config` | Avoid editing `.zshrc` / `.bashrc`. |

If `--skip-shell-config` was used, either restart the shell or run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Verify installation

```bash
tesserae init --help
tesserae compile --help
tesserae export site --help
```
