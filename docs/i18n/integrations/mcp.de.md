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
| `graph_map` | **Hier anfangen.** Budgetierte Karte der Graphhierarchie — der Einstieg für Descent. Ohne Scope liefert es den Wurzel-Kartensatz (Zählungen, Top-Hubs, eine Karte je gröbster Community); `scope='<scope_id einer Karte>'` steigt eine Dendrogramm-Ebene ab; `org:root` läuft den Agenten-Organisationsbaum ab. Orientiert einen Agenten, ohne dass er Suchbegriffe raten muss |
| `schema` | Kontrolliertes Vokabular für Nodes, Edges und Wiki-Kinds |
| `graph_summary` | Anzahl von Nodes/Edges und Typverteilungen für das aktive Projekt |
| `search_nodes` | Filtert öffentliche Graph-Nodes nach `query`, `type`/`types`, `kind`, `limit`, hybriden `mode`/`weights`; `include_superseded` zeigt ausgemusterte Nodes; `explain` fügt ein Retrieval-`profile` hinzu (siehe unten) |
| `node_context` | Ein Node plus dessen anliegende Edges und Nachbar-Nodes. `use_ppr` rankt Nachbarn per personalisiertem PageRank statt eines 1-Hop-Walks; `include_superseded` und `limit` begrenzen das Ergebnis. Eine `node_id`, die einen Merge in einer späteren Kompilation verloren hat, ist **kein** Miss: Sie wird über das Merge-Ledger zum aufnehmenden Node aufgelöst, und die Antwort trägt `status: "merged"` mit `merged_from` / `merged_into`, sodass du die ID erfährst, die du von nun an halten solltest. Das Ledger wird nur nach einem Graph-Miss konsultiert, also kann eine Live-ID niemals umgeleitet werden |
| `embedding_status` | Meldet das aktive Embedding-Backend, das die Hybridsuche antreibt, plus seinen persistent gecachten Vector-Cache — `vectors_cached` für diesen Backend/Dim-Schlüssel, und prozessbreite `cache_hits` / `cache_misses` / `cache_errors`, sodass ein kalter oder nicht beschreibbarer Cache nicht als schneller Pfad verwechselt werden kann. Akzeptiert `graph_path` / `project` zum Auswählen des Projekts, dessen Sidecar gemeldet wird |
| `search_facts` | Temporale Fakten, projiziert aus dem Graphen (Graphiti-Stil), bewertet über den INHALT des Fakts — Subjekt, Prädikat, Objekt, Evidenz — nie über den serialisierten Fakt, sodass ein ID- oder Metadaten-Fragment kein Treffer ist; `dated` (`any`, `dated`, `undated`) wählt danach aus, ob ein Fakt ein nutzbares `valid_from` trägt; `current_only` filtert auf aktuelle Fakten, `as_of` antwortet zu einem vergangenen Stichtag. Beide zusammen werden abgelehnt — sie drücken verschiedene Uhren aus — und `undated_included` meldet, wie viele der gelieferten Zeilen kein Datum tragen |
| `timeline` | Nach GEPARSTEM `valid_from` sortierte Fakten für eine longitudinale Sicht, wobei undatierte Fakten hinter allen datierten gebündelt und als `undated_events` zurückgemeldet statt dazwischengemischt werden; `dated` (`any`, `dated`, `undated`) wählt danach aus, ob ein Fakt ein nutzbares `valid_from` trägt; `as_of` antwortet zu einem vergangenen Stichtag — ein Zeitpunkt über Gültigkeitsintervallen, keine Bereichsgrenze — und `undated_included` meldet, wie viele der gelieferten Zeilen kein Datum tragen. Ein undatierter Fakt bleibt unter `as_of` erhalten, deshalb unterscheidet erst dieser Zähler eine dünne Antwort von einer vollständigen. Akzeptiert auch `observed_as_of`. `total_events` zählt jeden Fakt, der GEPASST hat, nicht die Seite, die du erhältst — die ganze Match-Menge wird nach Datum sortiert, bevor die Seite geschnitten wird, also sind die frühesten Ereignisse diejenigen, die ein Zeitstrahl eigentlich zurückgibt, und `total_events > len(events)` ist das Zeichen einer vollen Seite gegenüber einer vollständigen Antwort |
| `graph_ppr` | Personalisierter PageRank, ausgehend von einem oder mehreren `seed_node_id`; liefert die top-K relevantesten Nodes mit einstellbaren `alpha`, `directed`, `edge_type_weights` |
| `wiki_page` | Der kompilierte Markdown-Seiteninhalt für einen Node plus die internen Links, die er referenziert. Eine veraltete `node_id` folgt derselben Merge-Ledger-Umlenkung stille — der absorbierte Node's Name ist ein Alias auf dem Gewinner, sodass die Seite des Gewinners *ist* die Seite, die du angefordert hast |
| `raw_source` | Das ursprüngliche Quell-Markdown (auf 16 KB begrenzt). Gibt nie Bytes zurück: für einen `Artifact`-Node zeigt es dich auf `drill_down`, das stattdessen den Asset's Pfad und Site-Adresse meldet |
| `verify_claim` | Prüft EIN Tripel gegen den Graphen — exakte Suche, kein LLM, kein unscharfer Abgleich, keine gerankten Ergebnisse. Liefert `{verdict, reason, triple, citation, provenance, advisory}`; `verdict` ist `SUPPORTED` (die Kante existiert **und** ihr Beleg ist eine wörtliche Dokumentstelle), `PRESENT_UNEVIDENCED` oder eine Ablehnung. Verketten Sie `search_nodes` → `verify_claim`, wenn Sie nur Prosa haben |
| `doctor_run` | Führt die Gesundheitsprüfungen aus und liefert den Bericht als JSON (`findings`, `exit_code` 0/1/2). **Immer nur lesend** — Reparaturen laufen nie über MCP; nutzen Sie dafür `tesserae doctor --fix` in der CLI |
| `doctor_report` | Der Inhalt von `.tesserae/doctor-report.md` (auf 64 KB begrenzt); leer, bis `tesserae doctor` gelaufen ist |
| `lint_report` | Die zuletzt beim Compile gefundenen Lint-Befunde (auf 64 KB begrenzt) |

**Profiling einer Retrieval-Operation.** `search_nodes` und `compile_context` nehmen
`explain: true` an und antworten mit einem `profile` — für jede der `bm25`, `lexical`
und `embedding`-Spuren sein Gewicht, `candidates_in`, wie viele es bewertet hat,
`embed_calls` / `cache_hits` / `cache_misses` und seine Wandzeit, plus die Gesamtzahl
`candidates_in` / `admitted` / `returned` und welche Spuren jeden zurückgegebenen Knoten tatsächlich beitrugen. `search_nodes` gibt ein Profile zurück; `compile_context`
gibt eine Liste zurück, eins pro Seed-Suche, die sie lief.

Standardmäßig aus, und aus ist nicht formell: Messung kostet Zeit, deshalb ist dies ein
Diagnose-Feature statt etwas, das man angeschaltet lässt. Es kann eine Rangfolge nicht verschieben — jede
Zahl wird aus Punkt- und Rangtabellen gelesen, die die Fusion bereits erzeugt hat — und
mit dem Flag nicht gesetzt trägt die Antwort genau die Schlüssel, die sie immer hatte. Die
`cache_hits` / `cache_misses`-Zähler sind wie du einen warmen Vector-Cache
von einem kalten auf einer Live-Abfrage unterscheidest statt durch Inspizieren der `embedding_status`
hinterher.

**On-Demand-Kontext-Compiler** (Phase 7)

| Tool | Zweck |
|---|---|
| `compile_context` | Kompiliert ein maßgeschneidertes, **zitiertes** Kontextdokument für eine `query` oder explizite `seeds`. Durchläuft einen tiefenbegrenzten Subgraphen (`depth`, 1–10, Standard 2), rankt per PPR und füllt ein Zeichen-`budget` (Standard 32000; `0` für unbegrenzt). Standardmäßig deterministisch; mit `synthesize: true` entsteht ein vom LLM verfasster narrativer "topic"-Ausschnitt. Liefert `body`, `citations`, `selected_node_ids` und `char_budget_used`. `view` beschränkt den Walk auf eine benannte Edge-Partition — `semantic`, `temporal`, `causal` oder `entity`; übergeben Sie ein Array von Namen, um einen Walk pro View auszuführen und diese zu fusionieren (gewichtetes RRF). Wann immer eine View angefragt wird — ein Name oder mehrere — trägt jedes Zitat `via_views` (die Views, deren Walk es erreicht hat). `explain` fügt `profile` hinzu — eine pro Seed-Suche |
| `get_handle` | Blättert eine zuvor als `handle` zurückgegebene große Nutzlast (z. B. `compile_context` mit `preview`) in Scheiben (`offset`, `limit`) durch — bei Bedarf mehr holen, statt alles in den Kontext zu kippen |
| `list_communities` | Listet die vom Post-Compile-Pass erzeugten `COMMUNITY_SUMMARY`-Nodes, nach Mitgliederzahl geordnet (`min_size`, `limit`); per `node_context` über `summarizes`-Edges zurück zu den Mitgliedern |
| `fresh_insights` | Session-Befunde, geordnet nach einem Ebbinghaus-artigen Decay-Score (neueste + meistgenutzte zuerst); filtert von neueren Beinahe-Duplikaten verdrängte Befunde heraus. Optional `kind`, `limit`, `include_superseded` |

**Session-Memory** (siehe [sessions.md](sessions.de.md))

| Tool | Zweck |
|---|---|
| `list_sessions` | Session-Envelopes (id, started_at, title, files_touched, Befundzähler) für das aktive Projekt; `since`, `limit` |
| `find_session_findings` | Alle Session-Befunde, die über `discussed_in` / `references` mit `node_id` verknüpft sind, optional gefiltert nach `kinds` (insight / decision / question / todo / hypothesis / takeaway) |
| `find_code_symbol_mentions` | Erweitert einen Session-Befund auf die `CodeFunction`/`CodeClass`/`CodeMethod`-Symbole, die er erwähnt, über `discusses`-Edges aus dem optionalen Insight↔Symbol-Link-Pass. Die Code-Ebene ist opt-in: ohne `external_tools`-Eintrag für `codegraph` liefert dies nichts |
| `activity_summary` | Tages-/Wochendigest über die registrierten Projekte — Sitzungen, Befunde, Git-Commits, PRs und aufgenommene Dokumente, jeweils nach **ihrem eigenen** Zeitstempel gefenstert, nie nach dem `started_at` einer Sitzung. Rendert deterministisches Markdown und stellt, sofern nicht abgeschaltet, eine LLM-Erzählung voran |
| `query_decisions` | Entscheidungen in den registrierten Projekten innerhalb eines Zeitraums: explizite **menschliche** Entscheidungen, deterministisch aus Claude Codes `AskUserQuestion` geparst (die Frage und die gewählte Option), plus aus dem Gespräch geförderte Agentenentscheidungen |

**Agent-Gedächtnis und Rückschreiben** (siehe [agent-memory.de.md](../agent-memory.de.md))

| Werkzeug | Zweck |
|---|---|
| `agent_view_explain` | Erklärt eine agentenbeschränkte Sicht, *ohne sie zu laden*: Auflösungsmodus (worker / manager / org), Mitgliedsagenten, Pfad und Knotenzahl jedes L1-Artefakts sowie die Veraltungsmarke `distilled_through` |
| `drill_down` | Löst einen `member_ref` eines Destillats zurück auf seinen rohen L0-Knoten auf — die ausdrückliche, protokollierte Eskalation einer Führungskraft über die destillierte Sichtbarkeit hinaus. Liefert den Status `alive` / `changed` / `absorbed` / `gone`; jeder Aufruf wird im Sidecar protokolliert. Ein `Artifact` (eine Figur, Tabelle oder Gleichung) zu bohren, fügt drei Schlüssel hinzu, die andere Node-Typen nie tragen: `asset_path` (wo die Bytes auf der Platte leben), `asset_sha256` (der Digest, von dem die Node-ID geseedet wurde) und `asset_site_path` (die inhalts-adressierte Adresse unter der `raw-assets/` eines gebauten Sites). Ein fehlerhaft deklarierter Hash lässt `asset_site_path` ausfallen statt eine Adresse zu erfinden |
| `read_audit` | Wer den Graphen gelesen hat: aufgezeichnete Lesevorgänge (`tool`, `actor`, `node_ids`, `at`, `tesserae_version`), neueste zuerst, plus eine Auswertung pro Akteur — damit die Zugriffszähler, die das Vergessen-durch-Nichtnutzung steuern, einem Leser zugeordnet werden können. **Opt-in** — es wird nichts aufgezeichnet, solange auf dem Serverprozess nicht `TESSERAE_READ_AUDIT=1` gesetzt ist, denn ein dauerhaft aktives Audit macht jeden Lesevorgang zu einem Schreibvorgang. Bereits aufgezeichnete Zeilen bleiben lesbar, nachdem das Flag ausgeschaltet wurde; `enabled` meldet die aktuelle Einstellung. Filter: `actor`, `tool`, `node_id` |
| `graph_write` | Schreibt typisierte Knoten und Kanten direkt in den Graphen — kein Markdown, kein Extraktionslauf. Der Schreibvorgang landet in einem Append-only-Overlay und wird als Compile-Produzent wieder abgespielt, **überlebt also eine Neukompilierung**. Streng: unbekannte Typen, eine Kante ohne Beleg oder ein Endpunkt, der weder in der Nutzlast noch eine bestehende Knoten-ID ist, werden abgelehnt. **Zum Zurückziehen** von schlicht Falschem, ohne einen Ersatz zu erfinden: eine `retracts`-Kante **per ID** auf den falschen Knoten richten — das Ziel wird aus jedem Standard-Read unterdrückt (`search_nodes`, `fresh_insights`, `node_context`, `compile_context`), bleibt aber mit `include_superseded: true` erreichbar, und nichts wird gelöscht |

**Q&A und Registry**

| Tool | Zweck |
|---|---|
| `ask` | Natürlichsprachliche Q&A. Lasse `scope` aus und ein smarter Router wählt das Ziel über deine registrierten Projekte aus (Fallback-Föderierung) und leitet über aufeinanderfolgende Fragen um (übergebe `conversation_id`, um einen Faden zu isolieren). Ausdrücklicher `scope`: `current` (ein Projekt), `all-registered` (eine Antwort pro Projekt), `federated` (EINE zusammengeführte, querverweiste Antwort; `semantic` standardmäßig aktiv). Plus `backend`, `top_k`, `scope_aliases`, `claude_config_dir`. Bei einer Graphen-gerouteten Frage trägt die Hülle `plan` (die Planers Begründung, die Schritte, die sie gewählt hat, und `executed` — was tatsächlich lief), und kann `proposed_write` tragen: Knoten und Kanten, die der Planner für aufzeichnungswürdig hält, verankert nur in dem, was die *Frage* behauptete. Es ist ein **Vorschlag, niemals ein Write** — seine Provenance ist immer null, also weigert sich `graph_write`, es zu akzeptieren, bis ein Aufrufer mit einem Agent-Schlüssel und einem außenberührenden Anker ihn versorgt. Eine Mutation ist niemals eine Nebenwirkung einer Abfrage |
| `query` | Rohe Suche ohne LLM — spiegelt `tesserae query`. `backend='wiki'` (Standard) ist deterministische BM25-/semantische Suche über das kompilierte Wiki und liefert gerankte Treffer mit Auszügen; `backend='raganything'` fragt den optionalen multimodalen RAG-Index ab, sofern das Projekt ihn aktiviert hat. Für eine synthetisierte, belegte Antwort nutzen Sie `ask` |
| `ingest` | Nimmt rohe Web-/Textinhalte (z. B. einen Browser-Clip) in den Wissensgraphen des aufgelösten Projekts auf |
| `list_projects` | Listet die registrierten Projekte |
| `register_project` | Fügt dem Register ein Projekt hinzu |
| `unregister_project` | Entfernt ein Projekt aus dem Register (ein privilegiertes „aktives" Projekt gibt es nicht) |

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

Der MCP-Server ist das **Lese-Interface** zum typisierten Graphen. Für den **Schreibpfad** (Quellen ingestieren, recompilieren, Companion-Tools wie RAG-Anything aktualisieren) nutze die CLI direkt. Beide sind entkoppelt: Die CLI aktualisiert `.tesserae/`, der MCP-Server liest beim nächsten Tool-Call genau das, was dort liegt.
