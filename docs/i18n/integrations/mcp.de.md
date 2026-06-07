# MCP — Tesserae an Claude Code, Codex und Cursor anbinden

<!-- translations:start -->
<p align="center"><a href="../../integrations/mcp.md">English</a> · <a href="mcp.ko.md">한국어</a> · <a href="mcp.zh.md">中文</a> · <a href="mcp.ja.md">日本語</a> · <a href="mcp.ru.md">Русский</a> · <a href="mcp.es.md">Español</a> · <a href="mcp.fr.md">Français</a></p>
<!-- translations:end -->

Tesserae bringt einen [Model Context Protocol](https://modelcontextprotocol.io)-stdio-Server mit, der den kompilierten typisierten Graphen jedem MCP-fähigen Client zur Verfügung stellt: Claude Code, Codex CLI, Cursor, Claude Desktop und weiteren. Der Server bedient alle drei vollständigen MCP-Oberflächen — **tools**, **resources** und **prompts** — sodass Clients den Graphen sowohl on demand abfragen als auch günstig Kontext aus kanonischen URIs vorbefüllen können.

## Voraussetzungen

Der Server liest aus `.tesserae/graph.json`, daher ist ein einmaliger Compile erforderlich:

```bash
cd /path/to/your-project
tesserae init    # interactive; or --yes for non-interactive
tesserae compile  # deterministic, no LLM calls, no API keys
```

Recompiliere jederzeit, wenn sich deine Quellen ändern. Der Server greift beim nächsten Tool-Call automatisch auf den neuen Graphen zu — ein Neustart ist nicht nötig.

## 1) Client-Konfiguration generieren

```bash
tesserae projects mcp-config
```

Gibt ein JSON-Snippet etwa in dieser Form aus:

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "python3",
      "args": [
        "-m", "tesserae.mcp_server",
        "--graph", "/path/to/your-project/.tesserae/graph.json"
      ]
    }
  }
}
```

Der exakte Pfad wird aus dem aktuellen Projekt eingesetzt. Übergib `--name <alias>`, wenn du einen anderen Servernamen als `tesserae` möchtest.

## 2) In deinen MCP-Client einfügen

| Client | Ort der Konfiguration |
|---|---|
| Claude Code | `~/.claude/mcp-servers.json` (or `~/.config/claude-code/mcp-servers.json`) |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex CLI | `~/.config/codex/mcp-servers.json` |
| Cursor | Settings → MCP Servers → paste JSON |
| Hermes | `~/.hermes/config.toml` (use the TOML-equivalent block printed by `mcp-config --format hermes`) |

Starte den Client nach der Bearbeitung neu. Die nächste Sitzung verbindet sich und entdeckt die Tesserae-Oberfläche.

## 3) Was der Client sieht

### Tools — vom Modell aufgerufen

Jedes Tool akzeptiert ein optionales `graph_path` oder `project` (Registry-Alias), sodass ein einzelner Server pro Aufruf jeden registrierten Vault auflösen kann. Fehlt es, wird auf das aktive Projekt zurückgegriffen.

**Graph-Abfragen und Retrieval**

| Tool | Zweck |
|---|---|
| `schema` | Kontrolliertes Vokabular für Nodes, Edges und Wiki-Kinds |
| `graph_summary` | Anzahl von Nodes/Edges und Typverteilungen für das aktive Projekt |
| `search_nodes` | Filtert öffentliche Graph-Nodes nach `query`, `type`/`types`, `kind`, `limit`, hybriden `mode`/`weights`; `include_superseded` zeigt ausgemusterte Nodes |
| `node_context` | Ein Node plus dessen anliegende Edges und Nachbar-Nodes. `use_ppr` rankt Nachbarn per personalisiertem PageRank statt eines 1-Hop-Walks; `include_superseded` und `limit` begrenzen das Ergebnis |
| `embedding_status` | Meldet das aktive Embedding-Backend, das die Hybridsuche antreibt |
| `search_facts` | Temporale Fakten, projiziert aus dem Graphen (Graphiti-Stil); `current_only` filtert auf aktuelle Fakten |
| `timeline` | Nach `valid_from` sortierte Fakten für eine longitudinale Sicht |
| `graph_ppr` | Personalisierter PageRank, ausgehend von einem oder mehreren `seed_node_id`; liefert die top-K relevantesten Nodes mit einstellbaren `alpha`, `directed`, `edge_type_weights` |
| `wiki_page` | Der kompilierte Markdown-Seiteninhalt für einen Node plus die internen Links, die er referenziert |
| `raw_source` | Das ursprüngliche Quell-Markdown (auf 16 KB begrenzt) |
| `lint_report` | Die zuletzt beim Compile gefundenen Lint-Befunde (auf 64 KB begrenzt) |

**On-Demand-Kontext-Compiler** (Phase 7)

| Tool | Zweck |
|---|---|
| `compile_context` | Kompiliert ein maßgeschneidertes, **zitiertes** Kontextdokument für eine `query` oder explizite `seeds`. Durchläuft einen tiefenbegrenzten Subgraphen (`depth`, 1–10, Standard 2), rankt per PPR und füllt ein Zeichen-`budget` (Standard 32000; `0` für unbegrenzt). Standardmäßig deterministisch; mit `synthesize: true` entsteht ein vom LLM verfasster narrativer "topic"-Ausschnitt. Liefert `body`, `citations`, `selected_node_ids` und `char_budget_used` |
| `list_communities` | Listet die vom Post-Compile-Pass erzeugten `COMMUNITY_SUMMARY`-Nodes, nach Mitgliederzahl geordnet (`min_size`, `limit`); per `node_context` über `summarizes`-Edges zurück zu den Mitgliedern |
| `fresh_insights` | Session-Befunde, geordnet nach einem Ebbinghaus-artigen Decay-Score (neueste + meistgenutzte zuerst); filtert von neueren Beinahe-Duplikaten verdrängte Befunde heraus. Optional `kind`, `limit`, `include_superseded` |

**Session-Memory** (siehe [sessions.md](sessions.md))

| Tool | Zweck |
|---|---|
| `list_sessions` | Session-Envelopes (id, started_at, title, files_touched, Befundzähler) für das aktive Projekt; `since`, `limit` |
| `find_session_findings` | Alle Session-Befunde, die über `discussed_in` / `references` mit `node_id` verknüpft sind, optional gefiltert nach `kinds` (insight / decision / question / todo / hypothesis / takeaway) |
| `find_code_symbol_mentions` | Erweitert einen Session-Befund auf die `CodeFunction`/`CodeClass`/`CodeMethod`-Symbole, die er erwähnt, über `discusses`-Edges aus dem optionalen Insight↔Symbol-Link-Pass |

**Q&A und Registry**

| Tool | Zweck |
|---|---|
| `ask` | Natürlichsprachliche Q&A über das konfigurierte Memory-Backend (raganything, cognee oder compiled wiki). `backend`, `top_k`; vault-übergreifender Fan-out über `scope`/`scope_aliases`; `claude_config_dir` für Multi-Account-Routing |
| `list_projects` / `register_project` / `activate_project` / `unregister_project` | Steuerung der Multi-Projekt-Registry |

**Geführtes Setup**

| Tool | Zweck |
|---|---|
| `tesserae_setup_plan` | Erkennt die Umgebung und schlägt einen Setup-Plan als JSON vor. Schreibgeschützt — fasst `.tesserae/` nie an |
| `tesserae_setup_apply` | Wendet einen (ggf. bearbeiteten) Plan an: schreibt `.tesserae/config.json` und führt abgesicherte Installations-/Ausführungsaktionen aus. Über `confirm_install_actions` / `confirm_run_actions` gesteuert |

### Resources — automatisch in den Modellkontext geladen

URIs, die der Client über seinen Resource-Picker einbinden kann, ohne einen Tool-Turn zu verbrauchen:

- `tesserae://graph/schema` — derselbe Payload wie das Tool `schema`, fertig als statischer Kontext
- `tesserae://graph/summary` — Zusammenfassung des aktiven Projekts
- `tesserae://lint-report` — der aktuelle Lint-Report als Markdown

Dazu URI-Templates, die der Client on demand zusammensetzen kann:

- `tesserae://wiki/{kind}/{slug}` — beliebiger kompilierter Wiki-Seiteninhalt
- `tesserae://raw/{source_path}` — beliebiges Quell-Markdown

### Prompts — Ein-Klick-Recherche-Templates

Diese erscheinen im Slash-Menü des Clients (z. B. in Claude Codes `/`-Palette):

| Prompt | Argumente | Funktion |
|---|---|---|
| `summarize-paper` | `slug` (required) | Ruft `node_context` + `wiki_page` + optional `raw_source` auf und liefert eine strukturierte Zusammenfassung: Beitrag, Methodenskizze, wichtigste Ergebnisse, Limitierungen, verwandte Nodes |
| `find-related-work` | `topic` (required), `limit` | Verkettet `search_nodes` + `node_context` für die Top-K verwandten Einträge inkl. Relevanzbegründungen |
| `compare-approaches` | `a`, `b` (both required) | Holt `node_context` für beide plus `search_facts` für Performance-Aussagen; liefert einen Side-by-Side-Vergleich mit Synthese |
| `gap-analysis` | `topic` (optional) | Fördert ungelöste offene Fragen, fehlende Benchmarks und schwach belegte Aussagen zutage |
| `triage-open-questions` | _none_ | Listet jeden `OpenQuestion`-Node, gruppiert nach Thema, schlägt eine Priorisierung vor |

Jeder Prompt rendert zu einer einzigen User-Message, die dem Modell genau sagt, welche Tesserae-Tools zu verketten sind — so muss das Modell die Oberfläche nicht jedes Mal neu entdecken.

## Multi-Projekt: mehrere Vaults unter einem Server registrieren

Eine persistente Registry unter `~/.tesserae/registry.json` erlaubt es demselben MCP-Server, jedes registrierte Projekt namentlich aufzulösen:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

Danach löst jedes Tool, das `project` oder `graph_path` akzeptiert, `project: "research"` über die Registry auf, statt einen vollen Pfad zu benötigen. Der Server validiert sogar, dass der registrierte `graph_path` weiterhin existiert, und liefert eine klare Fehlermeldung, falls ein Recompile nötig ist.

### Fan-out über alle registrierten Vaults

Das Tool `ask` akzeptiert `scope: "all-registered"`, um jedes registrierte Projekt parallel abzufragen und die Ergebnisse aggregiert zurückzugeben:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "Where is splatting used?",
    "scope": "all-registered"
  }
}
```

Auf eine Teilmenge einschränken lässt sich das Ganze über `scope_aliases: ["research", "notes"]`.

## Multi-Account Claude CLI

Wenn dein `ask`-Tool über die Claude CLI läuft und du mehrere Accounts hast (z. B. `~/.claude` und `~/.claude-personal2`), übergib `claude_config_dir` pro Aufruf:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "...",
    "claude_config_dir": "/Users/you/.claude-personal2"
  }
}
```

Der Server exportiert `CLAUDE_CONFIG_DIR` nur für die Dauer dieses Aufrufs und stellt anschließend den vorherigen Wert wieder her. Kein Übertrag zwischen Aufrufen.

## Verifikation

Prüfe nach einem Neustart deines MCP-Clients die Verbindung:

- Claude Code: `/mcp` sollte `tesserae` mitsamt Tool-Anzahl auflisten.
- Cursor: Das MCP-Icon in der Chat-Leiste sollte `tesserae: connected` mit Tool-/Resource-/Prompt-Zählern zeigen.
- Codex / Hermes: Ein beliebiges Tool namentlich aufrufen (z. B. `schema`) und die Antwort prüfen.

Falls nichts erscheint, prüfe noch einmal, dass `--graph` auf eine existierende `.tesserae/graph.json` zeigt — der Server validiert das jetzt beim Start und bei jedem Tool-Call, sodass du eine klare Fehlermeldung statt eines stillen 500 erhältst.

## Wo das hineinpasst

Der MCP-Server ist das **Lese-Interface** zum typisierten Graphen. Für den **Schreibpfad** (Quellen ingestieren, recompilieren, Companion-Tools wie RAG-Anything oder Understand-Anything aktualisieren) nutze die CLI direkt. Beide sind entkoppelt: Die CLI aktualisiert `.tesserae/`, der MCP-Server liest beim nächsten Tool-Call genau das, was dort liegt.
