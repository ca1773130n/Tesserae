# Installation

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a></p>
<!-- translations:end -->
Tesserae ist auf PyPI veröffentlicht und stellt Shell-Commands bereit, damit Nutzer nicht manuell `python3 -m tesserae.cli` ausführen müssen.

## Von PyPI installieren (empfohlen)

```bash
pip install tesserae
```

Das war's. `pip` registriert zwei Console-Scripts in deinem `PATH`:

```bash
tesserae --help
tesserae_mcp --help
```

Der kanonische Befehl in der Doku ist `tesserae`. `tesserae_mcp` startet den MCP-Server (der jetzt das On-Demand-Tool `compile_context` bereitstellt — siehe Schnellstart).

> **pipx ist auch fine.** Wenn du CLI-Tools lieber in eigenen isolierten Venvs hältst:
> ```bash
> pipx install tesserae
> ```

## Upgrade

```bash
pip install --upgrade tesserae
```

## Optionale Integrationen

Das Default-Wheel ist bewusst leicht. Der Setup-Wizard kann die schwereren Companion-/Runtime-Teile nur dann installieren, wenn du sie anforderst:

```bash
# Understand Anything companion graph + RAG-Anything multimodal + Cognee runtime memory
tesserae project setup \
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

Manuelle Package-Installs stehen für fortgeschrittene Workflows weiter zur Verfügung:

```bash
pip install kuzu cognee graphiti-core
```

- `kuzu` — Kuzu-Graph-Persistenz.
- `cognee` — Runtime-Cognee-Add/Cognify-Workflows; Setup hinterlegt `{python} -m pip install cognee` und versucht es einmal nach, falls Cognee fehlt.
- Understand Anything — wird über den Upstream-Installer installiert, wenn `--install-understand-anything` gewählt ist; Tesserae hinterlegt einen verwalteten Refresh-Wrapper, statt von Nutzern zu verlangen, einen Shell-Befehl zu erfinden.
- RAG-Anything — wird über `pip install 'raganything[all]'` installiert, wenn `--install-raganything` gewählt ist; Tesserae hinterlegt einen verwalteten Refresh-Wrapper für multimodale Parser-Runs.
- `graphiti-core` — Live-Graphiti/Neo4j-Sync. `export-graphiti` und `sync-graphiti --dry-run` funktionieren auch ohne.

Der Anthropic-gestützte Synthese-Pfad nutzt einen Extras-Marker:

```bash
pip install "tesserae[synthesis-llm]"
```

Echte semantische Embeddings (seit v0.5.0 die Standard-Retrieval-Lane) liefert das Extra `semantic`:

```bash
pip install "tesserae[semantic]"
```

Das installiert `model2vec` und lädt ein leichtgewichtiges, offline-fähiges, torch-freies statisches Modell herunter (rund 8 MB `potion-base-8M`, beim ersten Gebrauch einmalig geladen). Ohne dieses Extra fällt das hybride/Embedding-Retrieval auf einen nicht-semantischen Hash-Bucket-Stub zurück und gibt eine deutliche Warnung aus. Wer `project ask`, `project context` oder das MCP-Tool `compile_context` nutzt, sollte dieses Extra daher installieren.

Für den multimodalen RAG-Anything-Stack mit allen Parsern vorinstalliert:

```bash
pip install 'tesserae[raganything-all]'
```

> **System-Voraussetzung:** Das Parsen von `.doc/.docx/.ppt/.pptx/.xls/.xlsx` erfordert LibreOffice auf dem Host. Installiere es über den Paketmanager deiner Plattform (z. B. `brew install --cask libreoffice`, `apt-get install libreoffice`); RAG-Anything überspringt Office-Dokumente mit einer Warnung, wenn LibreOffice fehlt.

## Aus Source installieren (für Contributors)

Wenn du an der Codebase hacken willst, installiere stattdessen den editierbaren Checkout:

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

Ein Convenience-Installer ist ebenfalls dabei — er klont, erzeugt ein projektlokales `.venv`, läuft `pip install -e .` und legt die Wrapper in `~/.local/bin` ab:

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
| `--bin-dir PATH` | Schreibt Command-Wrapper woanders hin als `~/.local/bin`. |
| `--no-venv` | Installiert in die aktuelle Python-Umgebung, statt `.venv` zu erzeugen. |
| `--skip-shell-config` | Vermeidet das Bearbeiten von `.zshrc` / `.bashrc`. |

Wurde `--skip-shell-config` verwendet, starte entweder die Shell neu oder führe aus:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Installation verifizieren

```bash
tesserae project init --help
tesserae project compile --help
tesserae project build-site --help
```
