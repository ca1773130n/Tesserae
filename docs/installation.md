# Installation

<!-- translations:start -->
<p align="center"><a href="i18n/installation.ko.md">한국어</a> · <a href="i18n/installation.zh.md">中文</a> · <a href="i18n/installation.ja.md">日本語</a> · <a href="i18n/installation.ru.md">Русский</a> · <a href="i18n/installation.es.md">Español</a> · <a href="i18n/installation.fr.md">Français</a> · <a href="../i18n/installation.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki is published on PyPI and exposes shell commands so users do not have to run `python3 -m llm_wiki.cli` manually.

## Install from PyPI (recommended)

```bash
pip install llm-research-wiki
```

That's it. `pip` registers three console scripts on your `PATH`:

```bash
llm_wiki --help
llm-wiki --help
llm_wiki_mcp --help
```

The canonical command in docs is `llm_wiki`; `llm-wiki` (with a dash) is an alias. `llm_wiki_mcp` starts the MCP server.

> **pipx is fine too.** If you prefer to keep CLI tools in their own isolated venvs:
> ```bash
> pipx install llm-research-wiki
> ```

## Upgrade

```bash
pip install --upgrade llm-research-wiki
```

## Optional integrations

The default wheel is intentionally light. The setup wizard can install the heavier companion/runtime pieces only when you ask for them:

```bash
# Understand Anything companion graph + RAG-Anything multimodal + Cognee runtime memory
llm_wiki project setup \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
```

Manual package installs are still available for advanced workflows:

```bash
pip install kuzu cognee graphiti-core
```

- `kuzu` — Kuzu graph persistence.
- `cognee` — runtime Cognee add/cognify workflows; setup stores `{python} -m pip install cognee` and retries once if Cognee is missing.
- Understand Anything — installed via the upstream installer when `--install-understand-anything` is selected; LLM-Wiki stores a managed refresh wrapper instead of asking users to invent a shell command.
- RAG-Anything — installed via `pip install 'raganything[all]'` when `--install-raganything` is selected; LLM-Wiki stores a managed refresh wrapper for multimodal parser runs.
- `graphiti-core` — live Graphiti/Neo4j sync. `export-graphiti` and `sync-graphiti --dry-run` work without it.

The Anthropic-backed synthesis path uses an extras marker:

```bash
pip install "llm-research-wiki[synthesis-llm]"
```

For the multimodal RAG-Anything stack with all parsers preinstalled:

```bash
pip install 'llm-research-wiki[raganything-all]'
```

> **System prerequisite:** parsing `.doc/.docx/.ppt/.pptx/.xls/.xlsx` requires LibreOffice on the host. Install it via your platform's package manager (e.g., `brew install --cask libreoffice`, `apt-get install libreoffice`); RAG-Anything skips Office documents with a warning when LibreOffice is missing.

## Install from source (for contributors)

If you want to hack on the codebase, install the editable checkout instead:

```bash
git clone https://github.com/ca1773130n/LLM-Wiki.git
cd LLM-Wiki
pip install -e .
```

A convenience installer is also bundled — it clones, creates a project-local `.venv`, runs `pip install -e .`, and drops the wrappers into `~/.local/bin`:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

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
llm_wiki project init --help
llm_wiki project compile --help
llm_wiki project build-site --help
```
