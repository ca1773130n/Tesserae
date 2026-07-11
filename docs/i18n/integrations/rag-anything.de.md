# RAG-Anything Multimodal-Companion

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) ist ein multimodales RAG-Framework (auf LightRAG aufbauend), das PDFs, Office-Dokumente, Bilder und Formeln über MinerU/Docling/PaddleOCR parst. Tesserae integriert es sowohl als multimodale Ingestion-Pipeline (native Graph-Projektion im UA-Stil) als auch als optionales Runtime-Memory-Backend.

## Warum beides nutzen?

- Tesserae — langlebige Agent-Memory, Wiki-Kompilation, Graph-Projektion.
- RAG-Anything — multimodale Ingestion + LightRAG-Retrieval zur Laufzeit.

Die beiden ergänzen sich: RAG-Anything bringt PDF-/Office-/Bildverständnis mit, das die text-orientierten Source-Loader von Tesserae nicht liefern; Tesserae hält die langlebige, abfragbare Memory, die über Sessions hinweg bestehen bleibt.

## Aktueller reibungsarmer Workflow

Empfohlen ist der Setup-Wizard:

```bash
tesserae init
```

RAG-Anything ist jetzt eine **interaktive Wizard-Abfrage** statt einer Reihe
von CLI-Flags. Wenn der Wizard läuft, beantworte die Integrations-Abfragen:

- aktiviere RAG-Anything, wenn danach gefragt wird;
- installiere es, wenn gefragt (installiert `raganything` + `docling`);
- wähle den Parser `mineru`;
- aktiviere den Refresh-Lauf nach der Installation, wenn angeboten.

Dann kompiliere:

```bash
tesserae compile
```

Für nicht-interaktive Automatisierung (CI) führe den Wizard mit Defaults aus
(alle optionalen Integrationen AUS), aktiviere RAG-Anything dann in
`.tesserae/config.json` — der Wizard schreibt die Integrations-Konfiguration
unter den Schlüssel `external_tools` / `memory_backends` — und führe den
verwalteten Refresh aus:

```bash
tesserae init --yes
# raganything in .tesserae/config.json aktivieren (external_tools-Schlüssel)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

Der Setup-Wizard installiert `raganything` und `docling` zusammen. MinerU bleibt opt-in: installiere es nur dann mit `pip install 'mineru[core]'`, wenn du PDFs oder Bilder ingestieren willst.

Tesserae hinterlegt einen verwalteten Refresh-Befehl, statt von Nutzern zu verlangen, sich einen auszudenken:

```bash
tesserae integrations refresh raganything --parser mineru
```

Während des Compiles geht Tesserae so vor:

1. prüft, ob `.tesserae/external/raganything/manifest.json` existiert und mit dem aktuellen git-commit übereinstimmt (über den hinterlegten `meta.json#gitCommitHash`);
2. führt den verwalteten Refresh-Wrapper aus, wenn dieser fehlt/veraltet ist oder `--refresh-external-tools` übergeben wurde;
3. erkennt nicht-Code-Quellen (PDFs, Office-Dokumente, Bilder, Markdown) und parst sie mit dem konfigurierten Parser;
4. schreibt `manifest.json` + `meta.json`;
5. setzt die normale Memory-Kompilation fort.

Du kannst vor einem Compile alle konfigurierten externen Refresh-Befehle erzwingen:

```bash
tesserae compile --refresh-integrations
```

## Manuelles Äquivalent

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## Compile-Zeit vs. Laufzeit

Tesserae trennt die Integration sauber:

- **Compile-time-Parsing** (`refresh-raganything` und `compile`): führt die Parser direkt aus — natives Lesen für `.md/.txt/.rst`, `docling.DocumentConverter` für alles andere. Die vollständige Pipeline von RAG-Anything wird hier *nicht* angestoßen, sodass für einen erfolgreichen Compile keine LLM-/Embedding-/Vision-Keys nötig sind.
- **Runtime-Queries** (`project ask`): `raganything_query.py` instanziiert `RAGAnything` mit den im Projekt konfigurierten LLM-/Embedding-/Vision-Funktionen und führt `aquery` gegen den Store von LightRAG aus. Dieser Pfad benötigt API-Keys.

Die Trennung sorgt dafür, dass `compile` schnell, deterministisch und key-frei ist; nur Retrieval-Operationen verursachen LLM-Token-Kosten.

## Native Graph-Synchronisation

Tesserae importiert das geparste Manifest nativ während des Compiles, sofern das konfigurierte Tool `sync_mode: native_graph` verwendet.

Der native Adapter liest `.tesserae/external/raganything/manifest.json`, projiziert jedes geparste Dokument in einen `SourceFile`-Node mit Metadaten zu multimodalen Blöcken und schreibt ein Sync-Manifest:

```text
.tesserae/external/raganything-sync.json
```

Aktuelles Mapping:

| RAG-Anything | Tesserae-Ziel |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | in `SourceFile.description` eingefaltet; Concepts über den bestehenden Extractor |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` und `metadata.equations[]` (LaTeX preserved) |

Die Provenance bleibt an jedem Node erhalten:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

Hinweis: Die interaktive Graph-Ansicht blendet Nodes der `sources`-Gruppe standardmäßig aus, um den Fokus auf Concepts und Entities zu legen — projizierte raganything-SourceDocuments bleiben in `graph.json` (MCP, Suche und Per-Page-Wiki-Ansichten sehen sie weiterhin), sie überfluten nur nicht die Canvas. Setze `graph_view.show_sources = true` in `.tesserae/config.json`, um die dichte Ansicht zurückzuholen.

## Runtime-Memory-Backend

`memory_backends.raganything` (Standardwert von `default_raganything_backend_config`) ist das einzige optionale Memory-Backend. RAG-Anything ist opt-in (default `enabled: false`); das Setup-Flag `--with-raganything` aktiviert es.

### LLM-Provider (kein API-Key nötig)

Das Runtime-Backend von RAG-Anything braucht ein LLM, um Anfragen zu beantworten. Tesserae nutzt standardmäßig die bestehenden OAuth-basierten CLI-Integrationen — kein API-Key erforderlich:

| Provider | Authentifizierung | Setup-Flag |
|---|---|---|
| `codex` (default) | `codex`-CLI-OAuth (du hast dich einmal mit `codex login` angemeldet) | `--raganything-llm-provider codex` |
| `claude` | `claude -p`-CLI; berücksichtigt `CLAUDE_CONFIG_DIR` für Multi-Account-Setups | `--raganything-llm-provider claude --raganything-claude-config-dir ~/.claude-personal2` |

Für Multi-Account-Setups von Claude (z. B. `~/.claude-personal1`, `~/.claude-personal2`) übergib `--raganything-claude-config-dir <path>` beim Setup. Das Runtime-Backend exportiert vor jedem Aufruf `CLAUDE_CONFIG_DIR=<path>`, sodass die Auth des gewählten Accounts verwendet wird, ohne deinen Standard-`~/.claude` zu berühren.

### Embeddings

| Provider | Wann verwenden |
|---|---|
| `deterministic` (default) | Keine externen Abhängigkeiten. Hash-basiert; geringe semantische Qualität, aber ausreichend, damit LightRAG einen Index aufbauen kann. Solide Baseline, um zu zeigen, dass die Integration funktioniert. |
| `ollama` | Lokales Ollama mit Embedding-Modell (z. B. `nomic-embed-text`). Übergib `--raganything-embedding ollama`; das Backend zeigt standardmäßig auf `http://localhost:11434`. |

Direkte OpenAI-Embedding-Unterstützung ist in v1 nicht über diese Flags verdrahtet — wer einen OpenAI-Key besitzt, kann `OPENAI_API_KEY` setzen und `memory_backends.raganything.embedding.provider` direkt in `.tesserae/config.json` überschreiben (RAGAnything zieht die env var über die LightRAG-Defaults nach).

### Aufruf über die CLI

```bash
# `ask` never enters memory backends: it plans retrieval over the compiled
# graph and synthesizes a cited LLM answer (--no-llm = ranked hits only).
tesserae ask "What does the integration spec say about parser routing?"

# Explicit backends live on `query` — raw retrieval, no LLM synthesis.
tesserae query "..." --backend raganything
tesserae query "..." --backend wiki
```

`tesserae query --backend raganything` ruft `tesserae.raganything_query.query` direkt auf. Ein relativer `working_dir` in `memory_backends.raganything` wird vor dem Aufruf gegen die Projekt-Root aufgelöst.

### Top-Level-`ask` (nutzt die Multi-Projekt-Registry)

Für Workflows, in denen du über mehrere registrierte Tesserae-Projekte hinweg fragen willst, ohne in jedes hineinzucden, löst der Top-Level-Befehl `tesserae ask` das Projekt über die persistente Registry auf, die er mit dem MCP-Server teilt:

```bash
# One-time: register your projects (saved to ~/.tesserae/registry.json).
tesserae projects register ~/Developer/Projects/Tesserae --name tesserae
tesserae projects register ~/Developer/Projects/Other --name other

# List registered projects.
tesserae projects list

# Ask from inside a project (the smart router picks the target otherwise).
tesserae ask "How does the parser routing work?"

# Ask a specific registered project by name.
tesserae ask "What is the architecture?" --name other

# Pass a direct path, or hit a memory backend explicitly via `query`.
tesserae ask "..." --project /tmp/somewhere
tesserae query "..." --backend raganything --json
```

Die Dispatch-Logik — `--project > --name > Router` — ist im Top-Level-Ask-Handler implementiert; die Antwortformatierung wird über `tesserae.query.ask_project` mit dem MCP-Tool `ask` geteilt (Memory-Backends sind nur über `tesserae query --backend …` erreichbar). Die Registry ist datei-gestützt (standardmäßig `~/.tesserae/registry.json`), bleibt also über Sessions erhalten und ist mit der Projektliste des MCP-Servers synchron.

#### Über mehrere Vaults abfragen (`--scope all-registered`)

Bet B2 — wenn du mehrere registrierte Projekte hast (Research-Vault, Work-Vault, Side-Project-Vault) und dieselbe Frage gegen alle stellen willst, nutze `--scope all-registered`:

```bash
# Fan out across every registered project. The aggregated envelope is
# {"scope": "all-registered", "question": "...", "by_project": {"<alias>": <envelope>}}.
tesserae ask "What did I write about RLHF?" --scope all-registered --json

# Restrict to a hand-picked subset of aliases.
tesserae ask "..." --scope all-registered --scope-aliases research side-projects
```

Der Handler iteriert die registrierten Projekte in alphabetischer Reihenfolge, ruft für jedes `ask_project` auf und aggregiert die per-Projekt-Envelopes. Ein einzelnes fehlschlagendes Projekt — fehlende Config, RAG-Anything nicht aktiviert — wird im Slot des jeweiligen Alias als `{"error": "..."}` festgehalten und bricht den Rest des Fan-out nie ab. Dasselbe `scope`-Argument akzeptiert auch das MCP-Tool `ask`, sodass MCP-gesteuerte Coding-Agenten denselben Fan-out ohne zusätzliche Verkabelung bekommen.

### Multi-Projekt-Registry (`tesserae projects`)

| Befehl | Zweck |
| --- | --- |
| `tesserae projects list [--json]` | Listet registrierte Projekte (alle sind gleichgestellt — es gibt kein „aktives“). |
| `tesserae projects register <path> [--name <alias>]` | Fügt ein Projekt der Registry hinzu; der Alias wird standardmäßig vom bereinigten Verzeichnisnamen abgeleitet. |
| `tesserae projects unregister <name>` | Entfernt einen Eintrag aus der Registry. |

Diese Befehle arbeiten direkt auf `tesserae.mcp_server.ProjectRegistry` — kein MCP-Roundtrip — und lassen sich ohne laufenden MCP-Server skripten.

### Aufruf aus MCP

Der stdio-MCP-Server stellt ein `ask`-Tool mit demselben Backend-Selektor bereit:

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "tesserae"
  }
}
```

Die Dispatch-Reihenfolge (`raganything` → kompilierte Wiki-Suche) und die `working_dir`-Auflösung spiegeln exakt den CLI-Handler, sodass Coding-Agenten und menschliche Bediener auf dieselben Antworten konvergieren.

## Systemvoraussetzungen

- **Python 3.10+** ist für RAG-Anything erforderlich (das Upstream-Paket `raganything` ≥1.3.0 hängt transitiv von `mineru[core]` ab, das wiederum Python 3.10+ verlangt). Auf älteren Python-Versionen deaktiviert Tesserae die Integration mit einer klaren Warnung, statt stillschweigend einen kaputten Platzhalter zu installieren.
- **LibreOffice** zum Parsen von `.doc/.docx/.ppt/.pptx/.xls/.xlsx` — separat über den Paketmanager deiner Plattform installieren. RAG-Anything überspringt Office-Dokumente mit einer Warnung, wenn LibreOffice fehlt.
- **MinerU-Modellgewichte** werden beim ersten Parse heruntergeladen und gecached (mehrere GB). Folgeläufe nutzen den Cache wieder.
- **OpenAI-kompatible LLM-/Embedding-/Vision-Keys** für das Runtime-Memory-Backend (`OPENAI_API_KEY`, `OPENAI_BASE_URL`). Der Parser-only-Modus benötigt keine Keys.

## Parser-Routing

Tesserae routet Quellen pro Dateiendung automatisch an den passenden Parser:

| Endung | Parser | Begründung |
|---|---|---|
| `.md`, `.markdown`, `.txt`, `.rst` | `docling` | Leichtgewichtig; kein MinerU-Modell-Download. |
| `.doc`, `.docx`, `.ppt`, `.pptx`, `.xls`, `.xlsx` | `docling` | Bessere Erhaltung der Office-Struktur laut Upstream. |
| `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp` | konfigurierter Default (`--raganything-parser`, default `mineru`) | OCR + Tabellenextraktion. |

Der verwaltete Wrapper `tesserae integrations refresh raganything` bietet `--parser` (der konfigurierte Default für PDFs/Bilder), `--parse-method {auto,ocr,txt}`, `--root` (wiederholbar, auf einen Teilbaum beschränken), `--force` und `--full`. Das Routing nach Text-/Office-Bucket ist fest (beide nutzen standardmäßig `docling`). Um den Text- oder Office-Parser explizit zu überschreiben, rufe das zugrunde liegende Modul direkt auf — `python -m tesserae.raganything_refresh --text-parser <p> --office-parser <p>` — das diese beiden zusätzlichen Flags bereitstellt. Der konfigurierte Default gilt weiterhin für PDFs und Bilder.

Bevor die Parse-Schleife läuft, prüft Tesserae, ob das Python-Paket jedes benötigten Parsers importierbar ist (`importlib.import_module(...)`), und bricht früh mit einem einzigen aggregierten Fehler ab, der jeden fehlenden Parser und seinen Install-Befehl listet. Wir verwenden bewusst nicht das Upstream-`RAGAnything.check_parser_installation()`, weil es nur den auf der Instanz konfigurierten Parser inspiziert und zusätzlich Model-Weight-Readiness-Checks bündelt, die nicht in eine Pre-Flight-Stage passen.

Tesserae wählt zudem den Konstruktor-Zeit-Parser von `RAGAnything` aus der tatsächlichen Routing-Verteilung (der am häufigsten gewählte Parser gewinnt) statt direkt aus `--raganything-parser`. Damit wird der Fehlerfall vermieden, dass `RAGAnything.__init__` einen schweren Parser (z. B. `mineru`) initialisiert, dessen Modellgewichte noch nicht auf der Platte liegen, und so den gesamten Lauf zerschießt, bevor per-call-`parser=`-Overrides greifen können. Das Flag `--raganything-parser` steuert weiterhin den Default für nicht-Text-, nicht-Office-Quellen (PDFs, Bilder).

### Parser-Pakete

Der Compile-time-Parse-Pfad verwendet `docling.DocumentConverter` direkt für jede nicht-Text-Quelle; einmal installiert, bist du abgedeckt:

| Parser | Install-Befehl |
|---|---|
| `docling` (compile-time-Default für alles außer nativem Text) | mitgeliefert, wenn du `--with-raganything --install-raganything` ausführst (oder eigenständig `pip install docling`) |
| `paddleocr` (optionale OCR-Alternative) | `pip install 'raganything[paddleocr]>=1.3.0'` und `pip install paddlepaddle` (plattformspezifisches Wheel) |

> Hinweis: `mineru` wird derzeit **nicht zur Compile-time aufgerufen**. Der Compile-Pfad umgeht die volle Pipeline von RAG-Anything (die LLM-/Embedding-/Vision-Callables erfordern würde) und routet jede nicht-Text-Quelle direkt durch docling. MinerU-Unterstützung ist für einen künftigen Direct-Import-Pfad reserviert, der eine extern erzeugte `content_list.json` einliest.

Wenn ein konfigurierter Parser fehlt, bricht `refresh-raganything` früh ab — listet jeden fehlenden Parser in einem einzigen Fehler mit dem passenden Install-Befehl auf — statt in Per-File-Fehler zu kaskadieren.

### Per-Page-Ask-Widget

Jede Detailseite (Concept, Paper, Repo, Synthesis, Entity, Topic, Question, Source) enthält ein Inline-„Ask about this page“-Widget. Es POSTet an `/api/ask` der lokalen `tesserae serve`-Instanz, ruft `tesserae.query.ask_project` auf und rendert die Antwort inline. Anders als die CLI (wo `tesserae ask` standardmäßig LLM nutzt) defaultet `/api/ask` aus Latenzgründen für das Widget auf **Nicht-LLM-Retrieval**; sende `{"llm": true}` im Payload, um in die geplante/synthetisierte Antwort zu optieren. Das Widget setzt der Frage des Nutzers den Node-Namen der aktuellen Seite als natürlich-sprachlichen Kontext-Hinweis voran (z. B. `` About `<NodeName>`: <question> ``); ein künftiger PR kann echtes Subgraph-Scoping direkt in `ask_project` einhängen.

Das Widget erkennt beim Laden die Backend-Verfügbarkeit über `/api/ask/health`. Wenn das Wiki statisch ausgeliefert wird (GitHub Pages, `file://`, S3, beliebiger statischer Host), kollabiert das Widget zu einer einzeiligen Notiz, die Leser zu `tesserae serve` für die lokale interaktive Nutzung verweist. Keine Requests schlagen fehl, und nichts blockiert das Page-Rendering — das Widget ist eine deferred JS-Insel, getrennt vom schwereren Graph-Bundle.

In Kombination mit der Multi-Projekt-Registry (`tesserae projects register`) kannst du das Wiki jedes registrierten Projekts aus jeder seiner Detailseiten heraus befragen.

## Kollaborationsprinzip

Tesserae bleibt der Memory-Compiler. RAG-Anything bleibt ein eigenständiger Companion: ein multimodaler Parser plus LightRAG-Retrieval-Engine.
