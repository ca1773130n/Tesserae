# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Tesserae graph view showing concepts, papers, repos, syntheses, and entities clustered around a focused node" width="100%" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.ko.md">한국어</a> ·
  <a href="./README.zh.md">中文</a> ·
  <a href="./README.ja.md">日本語</a> ·
  <a href="./README.ru.md">Русский</a> ·
  <a href="./README.es.md">Español</a> ·
  <a href="./README.fr.md">Français</a>
</p>

> Kompiliere deine Quellen zu einem typisierten Wiki, das Agenten lesen können.

<p align="center">
  <img src="docs/screencasts/showcase.gif" alt="Three-step screencast: tesserae project setup -> compile -> ask, recorded against the 135-doc demo corpus" width="100%" />
</p>

<!-- TODO: replace https://ca1773130n.github.io/Tesserae with the real
GitHub Pages URL once the maintainer enables Pages for this repo. The
.github/workflows/build-demo.yml workflow publishes .tesserae/site/ on every
push to main. -->

[Live-Demo](https://ca1773130n.github.io/Tesserae) · [Dokumentation](docs/) · [MCP-Setup](docs/i18n/integrations/mcp.de.md) · [Obsidian-Export](docs/i18n/integrations/obsidian.de.md)

Tesserae ist ein Compiler für Projektgedächtnis. Richte ihn auf ein Verzeichnis mit Markdown, Quelldateien und (optional) PDFs/Office-Dokumenten/Bildern, und er extrahiert einen typisierten Knowledge Graph, schreibt ein abfragbares Wiki und erzeugt portierbare Artefakte: eine Markdown-Projektion, ein Cognee-fertiges Bundle, ein Agent-Harness sowie einen MCP-Server, den du in Claude Code, Codex oder jeden MCP-Client einbinden kannst. Es ist ein Build-Schritt für Projektkontext, kein Hosted Service.

## Wie es sich vergleicht

Ein nüchterner Vergleich gegen die vier nächstliegenden Open-Source-Alternativen. Ohne Beschönigung:

| Feature | Tesserae | Quartz | Logseq | Cognee | Foam |
|---|---|---|---|---|---|
| Static HTML output | yes | yes | partial (export) | no | partial (publish) |
| Built-in graph view | yes | yes | yes | yes (separate UI) | yes (VSCode) |
| Typed node schema | yes (41 types) | no | partial (tags) | yes | no |
| Concept extraction from sources | yes (LLM) | no | no | yes | no |
| Multimodal ingestion (PDF/image) | yes (via RAG-Anything) | no | partial (embeds) | yes | no |
| Code-graph ingestion | yes | no | no | partial | no |
| MCP server | yes | no | no | yes | no |
| Multi-project registry | yes | no | yes (graphs) | partial | no |
| Works without API key (OAuth) | yes | n/a | n/a | no | n/a |
| Multi-language i18n docs | yes | partial | yes | partial | partial |
| Deterministic byte-identical compile | yes | yes | n/a | no | n/a |
| Per-page ask widget (proposed B3) | not yet | no | no | no | no |
| Live edit | no | partial | yes | n/a | yes |
| Mobile-first reading | no | yes | yes | n/a | n/a |
| Real-time collaboration | no | no | yes (DB beta) | no | no |

Tesserae entscheidet sich für Compile-from-Source statt Live-Editing. Wenn du Notizen
in einer UI bearbeiten willst, nimm Logseq oder Obsidian. Wenn du ein Build-Tool für deinen
Knowledge Graph willst, ist das hier dein Projekt.

## Wann du es einsetzt (und wann nicht)

Einsetzen, wenn:

- Du einen langlebigen, inspizierbaren Knowledge Graph über die textlastigen Quellen eines einzelnen Projekts (Docs, Code, Forschungsnotizen) willst.
- Du einen lokalen MCP-Server willst, der Fragen geerdet in deinen eigenen Dateien beantwortet.
- Du ein sauberes Bundle in Cognee einspeisen oder eine Markdown-Projektion in Obsidian öffnen willst, ohne den Glue-Code selbst zu schreiben.

Überspringen, wenn:

- Du nur eine Vektor-Suche über ein kleines Verzeichnis brauchst — `ripgrep` plus eine Embedding-Bibliothek ist einfacher.
- Du ein Hosted Wiki mit Editing-UI willst. Die statische Site hier ist read-only.
- Du out-of-the-box präzise semantische Embeddings erwartest. Das Default-RAG-Anything-Embedding ist deterministisch (siehe [Status](#status)).
- Du einen schlüsselfertigen „Ask anything“-Agenten erwartest. Dies baut das Substrat; du verdrahtest es selbst in den Agenten deiner Wahl.

## Status

Dies ist ein sich entwickelndes Forschungs- und Agent-Tooling-Projekt. Bekannte Einschränkungen:

- Die Compile-Zeit skaliert grob linear mit der Korpus-Größe. Erste Compiles über große Markdown-Bäume (tausende Dateien) können Minuten dauern.
- Der Default-Embedding-Provider von RAG-Anything ist `deterministic`. Er ist reproduzierbar und dependency-frei, aber der semantische Recall ist begrenzt. Wechsle zu `ollama` (z. B. `qwen3-embedding:0.6b`) oder einem OpenAI-kompatiblen Endpoint für besseres Retrieval — siehe [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md).
- Vision-Support für RAG-Anything (Bildinhaltsextraktion) ist noch nicht durchgängig verdrahtet. Bilddateien werden strukturell geparst, aber nicht beschrieben.
- Cognee-Runtime-Cognify ist Best-Effort: fehlende Provider, kostenpflichtige API-Keys oder Netzwerk-Fehler werden geloggt und übersprungen, statt den Build abzubrechen.
- Der MCP-Server exponiert ein stabiles Set an Tools, aber das zugrunde liegende Graph-Schema kann noch erweitert werden.

## Quickstart

Erfordert Python 3.9+. RAG-Anything benötigt Python 3.10+, wenn aktiviert.

```bash
pip install tesserae

cd /path/to/my-project
tesserae project setup
tesserae project compile
tesserae project ask "Where is Mermaid rendering implemented?"
tesserae project build-site && tesserae project serve --port 8765
```

Der Setup-Wizard erkennt gängige Quellen (`README.md`, `docs/`, `src/`, `data/`) und schreibt `.tesserae/config.json`. LLM-aufrufende Features verwenden standardmäßig das `codex`-CLI über OAuth, sodass für den üblichen Pfad keine API-Keys nötig sind. Die ausführlichere Variante findest du in [docs/quickstart.md](docs/quickstart.md) und [docs/installation.md](docs/installation.md).

> [!tip]
> **`tesserae: command not found` nach der Installation?** `pip` legt das Binary möglicherweise an einem Ort ab, den deine Shell nicht durchsucht. Der zuverlässigste Fix auf **jeder Plattform** ist [`pipx`](https://pipx.pypa.io/) — es installiert CLI-Tools in isolierten venvs und verwaltet deinen `PATH` automatisch:
>
> ```bash
> # macOS — `brew install pipx`
> # Ubuntu / Debian — `sudo apt install pipx`
> # andere — `python3 -m pip install --user pipx`
> pipx ensurepath          # fügt ~/.local/bin zum PATH hinzu; danach neue Shell öffnen
> pipx install tesserae
> ```
>
> **Ubuntu 23.04+** typische Probleme bei einfachem `pip install tesserae`:
>
> | Fehler | Ursache | Lösung |
> |---|---|---|
> | `error: externally-managed-environment` | PEP 668 — System-Python ist gesperrt | `pipx` (oben) verwenden, oder `pip install --user --break-system-packages tesserae` (hässlich), oder ein venv |
> | `tesserae: command not found` nach `pip install --user …` | `~/.local/bin` ist nicht im `PATH` | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
> | `ModuleNotFoundError: pydantic` auf Ubuntu 20.04 | System-`python3` ist 3.8, tesserae braucht ≥3.9 | `sudo apt install python3.11 python3.11-venv` dann `python3.11 -m pip install --user tesserae` |


### Walkthrough

Jeder Schritt aus dem Quickstart, aufgenommen gegen den mitgelieferten 135-Dokumente-Demo-Korpus
(`examples/demo-corpus/data/research/`). Du kannst jedes dieser GIFs mit
`vhs docs/screencasts/<name>.tape` neu erzeugen — die Tape-Dateien dokumentieren, was sie
aufgenommen haben und welches Workspace sie voraussetzen.

<details>
<summary><strong>1. Setup</strong> — auf ein Forschungsverzeichnis zeigen, ein Projekt-Wiki-Scaffold bekommen</summary>
<br/>
<img src="docs/screencasts/setup.gif" alt="tesserae project setup --source ./research running non-interactively and writing .tesserae/" width="100%" />
</details>

<details>
<summary><strong>2. Compile + Site bauen</strong> — deterministisch, keine LLM-Calls</summary>
<br/>
<img src="docs/screencasts/compile.gif" alt="tesserae project compile followed by tesserae project build-site, emitting graph.json and the static site tree" width="100%" />
</details>

<details>
<summary><strong>3. Ask</strong> — das kompilierte Wiki über das CLI abfragen</summary>
<br/>
<img src="docs/screencasts/ask.gif" alt="tesserae project ask --backend wiki returning top-3 hits with score, kind, and outbound relations" width="100%" />
</details>

## Was du nach dem Compile bekommst

```text
.tesserae/
  config.json
  graph.json              # typed nodes/edges
  manifest.json           # source fingerprints (used by --changed-only)
  sqlite.db               # queryable graph store
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  markdown_projection/    # human-readable wiki pages
  obsidian_vault/         # ready to drop into Obsidian
  agent_harness/          # per-agent config (Claude/Codex/Gemini/Cursor/...)
  harness_sessions/       # imported Claude/Codex session memory
  cognee_bundle/          # JSONL ready for Cognee ingest
  site/                   # static site built by build-site
  external/               # companion-tool outputs (UA, RAG-Anything)
```

`ls .tesserae/` nach `project compile`, um zu prüfen, was gelandet ist.

## CLI-Übersicht

Befehle für den Alltag. Führe `tesserae <subcommand> --help` für alle Flags aus.

| Befehl | Was er tut |
|---|---|
| `tesserae project setup` | Interaktiver Wizard. Schreibt `.tesserae/config.json`. Akzeptiert `--with-understand-anything`, `--with-raganything`, `--run-cognee` etc. |
| `tesserae project compile` | Liest die konfigurierten Quellen, führt Companion-Refreshes aus und schreibt alle Artefakte unter `.tesserae/`. Nutze `--changed-only` für inkrementelle Rebuilds. |
| `tesserae project build-site` | Baut das statische Frontend unter `.tesserae/site/`. |
| `tesserae project serve --port 8765` | Serviert die statische Site lokal und exponiert `/api/ask`, damit das Inline-Ask-Widget jeder Detailseite Fragen an `ask_project` routen kann. Auf jedem anderen Host (file://, GitHub Pages, S3) klappt das Widget elegant zu einem einzeiligen statischen Footer zusammen. |
| `tesserae project refresh-understand-anything` | Führt den von Tesserae verwalteten Refresh-Wrapper für Understand Anything aus. |
| `tesserae project refresh-raganything --parser mineru` | Re-parst Nicht-Code-Quellen (PDFs, Office, Bilder) via RAG-Anything. |
| `tesserae project ask "<question>"` | Fragt das konfigurierte Backend (`auto`/`raganything`/`cognee`/`wiki`). |
| `tesserae project mcp-config` | Druckt ein MCP-Server-Config-Snippet, das du in Claude Code, Codex oder Hermes einfügen kannst. |
| `tesserae wiki register <path> --name <alias>` | Registriert ein Projekt in der gemeinsamen Registry. |
| `tesserae wiki list` / `tesserae wiki activate <name>` | Listet registrierte Projekte; setzt das aktive. |
| `tesserae ask "<question>" [--wiki <name>]` | Top-Level-Ask, der über die Registry auflöst. |

## Integrationen

Alle Integrationen sind opt-in. Keine ist erforderlich, um Tesserae auf einem reinen Markdown/Code-Projekt einzusetzen.

- **Understand Anything** — ein separates Projekt ([Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything)), das einen Code-Knowledge-Graph unter `.understand-anything/knowledge-graph.json` produziert. Aktivieren mit `--with-understand-anything`. Tesserae hinterlegt einen verwalteten Refresh-Wrapper, sodass `project compile` den Graph aktuell hält. Siehe [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md).
- **RAG-Anything** — Multimodale Ingestion ([HKUDS/RAG-Anything](https://github.com/HKUDS/RAG-Anything)) für PDFs, Office-Dokumente und Bilder via MinerU/Docling/PaddleOCR. Aktivieren mit `--with-raganything`. Dient auch als Runtime-Question-Backend (LightRAG). Erfordert Python 3.10+. Siehe [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md).
- **Cognee** — Graph- und Vector-Memory-Backend. Aktivieren mit `--run-cognee --install-cognee`. Der normale Compile schreibt immer `.tesserae/cognee_bundle/`; der Runtime-`cognify`-Pass ist Best-Effort und läuft nur, wenn explizit aktiviert.

## Multi-Project-Registry

Eine persistente Registry unter `~/.tesserae/registry.json` lässt das Top-Level-`ask`-CLI und den MCP-Server Projektnamen zu Roots auflösen, ohne bei jedem Aufruf `--project` zu setzen.

```bash
tesserae wiki register /path/to/my-project --name myproj
tesserae wiki activate myproj
tesserae ask "Where is the parser entry point?"
```

Dieselbe Registry liest auch der MCP-Server, sodass MCP-Clients `list_projects`, `activate_project` und `ask` gegen jedes registrierte Wiki aufrufen können.

### Cross-Vault-Linking (`wiki://`-URI-Schema)

Source-Markdown in einem registrierten Projekt kann einen Knoten in einem anderen registrierten Projekt über eine stabile URI referenzieren:

```
wiki://<alias>/<kind>/<slug>
```

Beispiele:

- `wiki://research/concepts/rlhf` — das RLHF-Konzept im `research`-Vault.
- `wiki://other-vault/papers/arxiv-2510-12323` — ein Paper in `other-vault`.
- `[See RLHF in research](wiki://research/concepts/rlhf)` — funktioniert auch innerhalb eines Markdown-Links.

Zur Compile-Zeit werden diese URIs zu *Bridge-Knoten* in der Graph-Ansicht (Group `external`, violett) mit einem Toggle „Cross-project bridges“ in der Toolbar, mit dem du sie ausblenden kannst. Nicht registrierte Aliases werden als Tombstones gerendert; registrierte, aber noch nicht gebaute Links erscheinen als Platzhalter.

### Vault-übergreifende Abfragen (`--scope all-registered`)

`tesserae ask` und das MCP-Tool `ask` akzeptieren ein `--scope`-Flag:

```bash
# Default — just the active/named project.
tesserae ask "..."

# Fan out across every registered project; aggregate envelopes by alias.
tesserae ask "..." --scope all-registered

# Restrict to a hand-picked subset of registered aliases.
tesserae ask "..." --scope all-registered --scope-aliases research work
```

Die aggregierte JSON-Form ist `{"scope": "all-registered", "question": ..., "by_project": {"<alias>": <envelope>, ...}}`. Per-Projekt-Fehler werden als `{"error": "..."}`-Einträge erfasst; ein einzelnes fehlschlagendes Projekt bricht das Fan-out nie ab.

## MCP

`tesserae project mcp-config` druckt einen Servereintrag, den du in Claude Code, Codex oder jeden MCP-fähigen Client einfügen kannst. Der Server exponiert Tools wie `schema`, `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`, `wiki_page`, `raw_source`, `lint_report`, `ask` sowie die Registry-Tools `list_projects` / `register_project` / `activate_project` / `unregister_project`. Tools, die ein bestimmtes Projekt brauchen, lösen über dieselbe Registry auf wie das CLI.

## Authentifizierung und LLM-Provider

Der übliche Pfad nutzt keine API-Keys:

- **Codex CLI** (Default) über OAuth. `--raganything-llm-provider codex` ist der Default; der Cognee-`codex_cognify`-Modus patcht den LLM-Client von Cognee auf das Codex-CLI.
- **Claude Code CLI** über OAuth. Setze `--raganything-llm-provider claude` für RAG-Anything-Runtime-Queries. Multi-Account-Setups verwenden `--raganything-claude-config-dir ~/.claude-personal2` (Tesserae exportiert `CLAUDE_CONFIG_DIR` vor jedem Aufruf).
- **Embeddings** verwenden standardmäßig einen deterministischen In-Process-Provider. Wechsle zu Ollama mit `--cognee-embedding-provider ollama --cognee-ollama-embedding-model qwen3-embedding:0.6b` oder verdrahte OpenAI-kompatible Endpoints — beides in den Integrations-Seiten dokumentiert.

Wenn du `ANTHROPIC_API_KEY` oder `OPENAI_API_KEY` setzt, werden sie von den entsprechenden Pfaden aufgegriffen, sie sind aber nicht erforderlich.

## Projekt-Layout

```text
tesserae/        # the package (CLI, compiler, MCP server, adapters)
docs/            # English docs + docs/i18n/ for the six other languages
ontology/        # node/edge schemas the compiler validates against
prompts/         # extraction and synthesis prompts
scripts/         # maintenance scripts
tests/           # pytest suite
evals/           # graph quality eval harnesses
data/            # example research notes used by self-dogfooding
```

## Lokalisierte Dokumentation

[한국어](./README.ko.md) ·
[中文](./README.zh.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md)

Langform-Dokumentation ist unter `docs/i18n/` und `docs/i18n/integrations/` gespiegelt.

## Lizenz

MIT. Siehe [LICENSE](LICENSE).
