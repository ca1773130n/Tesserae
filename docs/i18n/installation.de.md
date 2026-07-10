# Installation

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a></p>
<!-- translations:end -->
Tesserae wird auf PyPI veröffentlicht und stellt Shell-Befehle bereit, damit Nutzer nicht manuell `python3 -m tesserae.cli` ausführen müssen.

## Von PyPI installieren (empfohlen)

```bash
pip install tesserae
```

Das war's. `pip` registriert zwei Console-Scripts auf deinem `PATH`:

```bash
tesserae --help
tesserae_mcp --help
```

Der kanonische Befehl in den Docs ist `tesserae`. `tesserae_mcp` startet den MCP-Server (der jetzt das On-Demand-Tool `compile_context` exponiert — siehe Quickstart).

> **pipx geht auch.** Wenn du CLI-Tools lieber in eigenen isolierten venvs hältst:
> ```bash
> pipx install tesserae
> ```

## Upgrade

```bash
pip install --upgrade tesserae
```

## Maschinenweites Setup (einmal setzen, alle Projekte)

Konfiguriere Tesserae einmal statt pro Projekt und installiere die optionalen
Abhängigkeiten aus einem Befehl:

```bash
# Interactive machine-wide setup — pick LLM provider/effort + which deps to install:
tesserae setup
# …or non-interactively (written to ~/.tesserae/config.json) + install everything:
tesserae setup --llm-provider codex --reasoning-effort medium --install all

# Just see / manage optional dependencies
tesserae config deps                     # show what's installed
tesserae config deps --install memex     # fast transcript search (needs cargo)
```

Bekannte optionale Abhängigkeiten: **memex** (schnelle Transkript-Suche), **cognee**,
**raganything**, **understand-anything**. Eine projektbezogene `.tesserae/config.json`
überschreibt diese globalen Defaults weiterhin (Auflösungsreihenfolge: env → Projekt → global →
eingebaut). `tesserae init` bietet während eines interaktiven Setups ebenfalls an, memex zu installieren.

## Optionale Integrationen (pro Projekt)

Das Default-Wheel ist bewusst leicht, und die optionalen Memory-Backends sind
**standardmäßig aus**. `tesserae init` ist der einzige projektbezogene Onboarding-Schritt —
sein Wizard wählt den LLM-Provider und die erkannten Quellen; die schwereren
Companion-/Runtime-Teile werden maschinenweit über `tesserae setup
--install …` (oder `tesserae config deps --install …`) installiert und pro Projekt in
`.tesserae/config.json` aktiviert:

```bash
# Machine-wide installs of the optional pieces:
tesserae setup --install raganything --install understand-anything --install cognee

# Then per project: enable what you want in .tesserae/config.json
#   memory_backends.raganything.enabled: true
#   memory_backends.cognee.enabled: true        (query via `tesserae query --backend …`)
#   external_tools: understand-anything entry   (auto_refresh: false by default)
```

Manuelle Paketinstallationen bleiben für fortgeschrittene Workflows verfügbar:

```bash
pip install kuzu graphiti-core
pip install "tesserae[cognee]"
```

- `kuzu` — Kuzu-Graph-Persistenz.
- `tesserae[cognee]` — die Opt-in-Cognee-Runtime-add/cognify-Workflows (standardmäßig deaktiviert; der Codex-gepatchte cognify-Modus wurde entfernt).
- Understand Anything — installiert über den Upstream-Installer (`tesserae setup --install understand-anything`); Tesserae speichert einen verwalteten Refresh-Wrapper, statt Nutzer einen Shell-Befehl erfinden zu lassen.
- RAG-Anything — installiert via `pip install 'raganything[all]'` (`tesserae setup --install raganything`); Tesserae speichert einen verwalteten Refresh-Wrapper für multimodale Parser-Läufe.
- `graphiti-core` — Live-Graphiti-/Neo4j-Sync. `export graphiti` und `export graphiti --sync --dry-run` funktionieren ohne.

Der Anthropic-gestützte Synthesis-Pfad nutzt einen Extras-Marker:

```bash
pip install "tesserae[synthesis-llm]"
```

Echte semantische Embeddings (seit v0.5.0 die Default-Retrieval-Spur) liegen hinter dem `semantic`-Extra:

```bash
pip install "tesserae[semantic]"
```

Das zieht `model2vec` herein und lädt ein leichtgewichtiges, offline nutzbares, torch-freies statisches Modell herunter (~8 MB `potion-base-8M`, einmal beim ersten Gebrauch geholt). Ohne es fällt Hybrid-/Embedding-Retrieval auf einen nicht-semantischen Hash-Bucket-Stub zurück und gibt eine laute Warnung aus, daher wird die Installation dieses Extras allen empfohlen, die `tesserae ask`, `tesserae context` oder das MCP-Tool `compile_context` nutzen.

Für den multimodalen RAG-Anything-Stack mit allen Parsern vorinstalliert:

```bash
pip install 'tesserae[raganything-all]'
```

> **System-Voraussetzung:** Das Parsen von `.doc/.docx/.ppt/.pptx/.xls/.xlsx` erfordert LibreOffice auf dem Host. Installiere es über den Paketmanager deiner Plattform (z. B. `brew install --cask libreoffice`, `apt-get install libreoffice`); RAG-Anything überspringt Office-Dokumente mit einer Warnung, wenn LibreOffice fehlt.

## Aus dem Quellcode installieren (für Contributor)

Wenn du am Codebase arbeiten willst, installiere stattdessen den editierbaren Checkout:

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

Ein Convenience-Installer ist ebenfalls gebündelt — er cloned, erzeugt eine projektlokale `.venv`, führt `pip install -e .` aus und legt die Wrapper in `~/.local/bin` ab:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

Nützliche Flags (`./scripts/install.sh --help`):

| Option | Zweck |
| --- | --- |
| `--dir PATH` | Installiert oder aktualisiert den Checkout unter `PATH`. |
| `--branch NAME` | Installiert einen bestimmten Branch. |
| `--repo URL` | Überschreibt die Git-Repository-URL. Nützlich für Forks oder lokale Smoke-Tests. |
| `--bin-dir PATH` | Schreibt Befehls-Wrapper woandershin als `~/.local/bin`. |
| `--no-venv` | Installiert in die aktuelle Python-Umgebung statt eine `.venv` zu erzeugen. |
| `--skip-shell-config` | Vermeidet Edits an `.zshrc` / `.bashrc`. |

Wenn `--skip-shell-config` genutzt wurde, starte entweder die Shell neu oder führe aus:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Installation verifizieren

```bash
tesserae init --help
tesserae compile --help
tesserae export site --help
```
