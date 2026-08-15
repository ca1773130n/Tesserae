# Feature-Map

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a></p>
<!-- translations:end -->
Dieses Dokument fasst die aktuell in Tesserae implementierten Features zusammen, mit Status, Quelldateien und Fundstellen der Dokumentation.

Tesserae ist eine **Kontext-Engine**, die auf drei Säulen läuft: (1) Sitzungsüberwachung, (2) autonome proaktive Wissensaufnahme und (3) Dokumente/Kontext auf Abruf. Der typisierte Graph, der Vault und die statische Site sind Projektionen der Wissensbasis. Die Features unten sind danach gruppiert, welcher Säule sie dienen; der Meilenstein **v0.5.0** (Juni 2026) lieferte das Engine-Rückgrat und das Pillar-3-Aushängeschild, den On-Demand-Kontext-Compiler.

Status-Legende: ✅ ausgeliefert · ⚠ in Arbeit / teilweise.

> **Lesereihenfolge.** Die Abschnitte unten sind Meilensteine, neueste zuerst.
> Versionen zwischen v0.12.0 und v0.28.7 werden hier nicht wiederholt — ihr
> Detail pro Release liegt in [`docs/release-notes/`](../release-notes/), dem
> maßgeblichen Änderungsprotokoll. Diese Karte beschreibt die Gestalt des
> Systems, nicht jeden Commit.

## Agenten-Gedächtnis, zeitliche Tiefe & Retrieval-Views — seit v0.31.0 (August 2026)

Der Zyklus, der Neo4j's Agent-Memory-Design las und die Teile nahm, die unter Tesseraes eigenen Zwängen überleben: eine zweite Zeitachse, benannte Edge-Partitionen, ein Identity-Grabstein und ein langlebiger Ort für Urteile, die eine Maschine nicht erneut ableiten kann. Die Datenbank selbst blieb draußen — siehe `docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md` für das, was genommen wurde, was es kostete und warum.

| Feature | Status | Quelle | Anmerkungen |
|---|---|---|---|
| Transaction time (`observed_as_of`) | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | Eine zweite Uhr: `as_of` antwortet „was war damals WAHR" aus den Quellen eigenen Zeitstempeln; `observed_as_of` antwortet „was hatten wir bis dahin GELERNT" aus einer `fact_observed`-Tabelle, die einmal pro Kompilation gestempelt wird. Sie komponieren. Sie lebt nur in `sqlite.db` — eine Wanduhr innen `graph.json` würde machen, dass dieselben Quellen morgen zu anderen Bytes kompilieren. Davor pries `as_of` sich selbst als „bitemporal" an, während nur eine Achse existierte. |
| Facts searched as content; `dated` as a predicate | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | `search_facts` rangiert über Subjekt / Prädikat / Objekt / Beweismaterial — nie die serialisierte Tatsache — sodass eine ID oder ein Metadaten-Fragment kein Match ist. `dated` (`any`/`dated`/`undated`) macht Datiertheit zu einem Filter statt etwas, das ein Aufrufer aus `undated_included` ableiten musste. |
| `resolved_by` closes an interval | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Der Widerspruchspass schlichtet einen Verlierer, aber der zeitliche Projektor ignorierte ihn, sodass ein geschlichteter Verlierer `current: true` weitlas. Es schließt von der **verlierenden** Seite — `resolved_by` läuft Quelle→Gewinner, das Gegenteil der invalidierenden Prädikate — plus Graphitis Überlappungsschutz: ein Gewinner, beobachtet beim oder vor dem Verlierer, kann nicht sagen, wann der Verlierer aufhörte, wahr zu sein. |
| Timeline counts its matches | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | `timeline` sortiert die **vollständige** Match-Menge nach Datum, bevor sie geblättert wird, und `total_events` zählt jedes Match. Vorher sortierte es eine ranggewählte 100-Zeilen-Scheibe und meldete diese Grenze als Corpus-Abdeckung — sodass die frühesten Ereignisse (das ist der Sinn eines Zeitstrahls) die waren, die am wahrscheinlichsten gelöscht wurden. |
| View registry + multi-view fusion | ✅ | [`tesserae/retrieval/views.py`](../../tesserae/retrieval/views.py), [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Ein Speicher, begehbar als vier orthogonale Graphen — `semantic` / `temporal` / `causal` / `entity`, jede eine benannte Untermenge des Edge-Vokabulars. Nicht ein neuer Ranking-Algorithmus: Eine Sicht wird auf Null-Gewichte für jeden Out-of-View-Edge-Typ aufgelöst, und die Nachbarschaftswanderung filtert auf derselben Menge, sodass ein nur-Out-of-View-Knoten nie aufgenommen wird. Mehrere Sichten verschmelzen durch gewichtete RRF, und jede Zitierung meldet `via_views`. |
| Persistent vector cache | ✅ | [`tesserae/retrieval/vector_cache.py`](../../tesserae/retrieval/vector_cache.py) | Jede Embedding-Aufrufstelle bettet ihren ganzen Corpus auf jeder Invokation neu ein. Eine `node_vectors`-Tabelle unterstützt jetzt alle drei, Schlüssel ist `(backend, dim, sha256(embedded_text))` — **nicht** die Node-ID, sodass ein unveränderter Knoten nach vollständiger Neukompilation oder Umzug trifft, ein neu beschriebener verfehlt und erneut einbettet, und zwei Modelle' Vektoren treffen sich nie. `embedding_status` meldet `vectors_cached` plus prozessbreite Treffer/Fehlschläge/Fehler. |
| Per-lane retrieval profiling | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `explain: true` auf `search_nodes` / `compile_context` gibt pro-Spur Gewicht, Corpus, Embedding-Aufrufe, Cache-Treffer/Fehlschläge und Wandzeit zurück, plus welche Spuren jeden Gewinner beitrugen. Opt-in wie Neo4j's `PROFILE`, weil Messung kostet — es kann nie eine Rangfolge verschieben, da jede Zahl aus Tabellen gelesen wird, die die Fusion bereits erzeugt hat. |
| Merge ledger — a dead id resolves to its survivor | ✅ | [`tesserae/merge_ledger.py`](../../tesserae/merge_ledger.py) | Jede Kompilation faltet Duplikate drei Wege zusammen und verwarf jede Antwort, sodass ein Agent, der eine Node-ID aus der letzten Kompilation hielt, einen bloßen Nicht-Gefunden bekam. `merge-ledger.json` ist ein Loser→Gewinner-Grabstein, nur nach dem Graphen konsultiert (eine Live-ID kann niemals umgeleitet werden); `node_context` meldet `status: merged` mit `merged_from` / `merged_into`. Abgeleiteter Zustand, keine Historie: ein Verlierer, der zurückkommt, fällt heraus. |
| Retraction (`retracts`) | ✅ | [`tesserae/research_graph.py`](../../tesserae/research_graph.py), [`tesserae/graph_filters.py`](../../tesserae/graph_filters.py) | Ein Agent kann „das ist falsch" sagen, ohne einen Ersatz zu erfinden: eine `retracts`-Kante, zeigend auf einen Knoten **nach ID**, entfernt ihn aus der Entdeckung (`search_nodes`, `fresh_insights`), aus der Kontextauswahl (`compile_context`) und aus den Nachbarn von `node_context`. Eine genaue `node_context`-Abfrage nach ID oder Name gibt den Knoten trotzdem zurück, gekennzeichnet als `retracted` — einen Knoten zu benennen heißt nicht, ihn zu entdecken. `include_superseded` stellt ihn auf den Entdeckungsflächen wieder her; nichts wird gelöscht. |
| Candidate same-as verdict ledger | ✅ | [`tesserae/candidate_ledger.py`](../../tesserae/candidate_ledger.py) | Ein Reviewer, der „diese sind unterschiedlich" antwortete, wurde dieselbe Frage für immer gefragt — `apply_decisions` verbrauchte `keep_separate` und tat nichts Dauerhaftes. `.tesserae/candidate-same-as.json` schlüsselt ein Urteil auf das sortierte Node-ID-Paar und nichts anderes, sodass eine neu beschriebene Beschreibung, eine neue Quelle oder ein anderes Embedding-Backend es allein lassen. Akkumuliert, nie gekürzt: ein Urteil ist das eine, das eine Maschine in dieser Pipeline nicht erneut ableiten kann. Geoberflächt als `PENDING_REVIEW`. |
| One blocking layer for both pairwise passes | ✅ | [`tesserae/blocking.py`](../../tesserae/blocking.py) | Canonicalization hatte einen Inline-Index; `supersede` verglich jedes Paar in einer Fundgruppe ohne Obergrenze. Beide teilen sich nun eine Schicht, mit zwei Eigenschaften, die Tests pinnen: die Obergrenze wird nach **sortierter ID** gekürzt, sodass ein gekappter Lauf nicht von Ankunftsreihenfolge abhängt, und der Aufrufer versorgt seinen eigenen Tokenizer, weil ein Blocker grober als sein Scorer stille wahre Matches löscht. Jeder Pass meldet eine Obergrenze, die er traf, statt stille eine kürzere Warteschlange zurückzugeben. |
| Artifact evidence nodes reach the site | ✅ | [`tesserae/raganything_adapter.py`](../../tesserae/raganything_adapter.py), [`tesserae/site/raw_view.py`](../../tesserae/site/raw_view.py) | Figuren, Tabellen und Gleichungen werden Erst-Klasse-`Artifact`-Knoten, jede ID geseedet nur von der Art des Artefakts und ihrer Content-Hash und nichts sonst — kein Dokument, Pfad, Bildunterschrift oder Seite. Eine Figur bekommt zusätzlich eine Rohseite und Content-adressierte Bytes unter `raw-assets/` (Tabellen und Gleichungen tragen kein Asset — ihr Inhalt *ist* die Beschreibung), und für eine Figur, deren Asset im Projekt ist, gibt `drill_down` `asset_path` / `asset_sha256` / `asset_site_path` zurück. Pro-Besitzer-Fakten — Art, Seite, Bildunterschrift, Ordinal — fahren die `part_of`-Kante, weil der Knoten Dokument-agnostisch nach Konstruktion ist und zwei Dokumente, die eine Figur drucken, andernfalls die Seite des zweiten verlieren würden. Beweise bleiben **off the graph canvas**: die gesamte Assertion-Schicht ist ausgeschlossen, dauerhaft. Siehe [rag-anything](integrations/rag-anything.de.md). |
| Planner walks the graph, and proposes writes | ✅ | [`tesserae/ask_planner.py`](../../tesserae/ask_planner.py) | Der Katalog hielt sieben Projektions-Primitiva und keinen Weg, den Graphen zu gehen; `compile_context` vereinigt ihn, mit der Sicht-Union aus der Registry interpoliert statt umgeschrieben. Der Planner kann auch `proposed_write` zurückgeben — Knoten und Kanten verankert nur in dem, was die *Frage* behauptete. **Vorschlagen, nie durchführen**: Provenance ist immer null, also `graph_write` weigert sich, bis ein Aufrufer mit einem Agent-Schlüssel und einem außenberührenden Anker ihn versorgt. |
| Read audit — who read the graph | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py), [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Zugriffszähler treiben Vergessen-durch-Disuse, aber nichts verzeichnete *wer* sie verursachte. `TESSERAE_READ_AUDIT=1` zeichnet `{tool, actor, node_ids, at, tesserae_version}` wo immer ein Bump stattfindet — eine Zeile, die jeden Knoten benennt, den ein Aufruf gezählt hat, außer `fresh_insights`, das pro Knoten bumpt und so eine Zeile pro Knoten schreibt, und ein Aufruf, der nichts ausgibt, schreibt keine — zurück gelesen durch `read_audit` mit Pro-Actor-Tally. **Standard aus**, und das Tor sitzt vor dem Öffnen des Stores — das Erstellen der Tabelle ist selbst ein Schreibvorgang. Siehe [agent memory](agent-memory.de.md#vergessen--keine-löschung). |
| `tesserae schema-drift` as a first-class verb | ✅ | [`tesserae/schema_drift.py`](../../tesserae/schema_drift.py) | Subtyp-Vorschläge waren nur über `lab` erreichbar. Vorschläge leben in `.tesserae/schema-drift-proposals.json`, nicht Node-Metadaten — ein Out-of-Band-Metadaten-Schlüssel würde eine inkrementelle Kompilation überleben und bei vollständiger verschwinden, der Byte-Idempotenz-Blindfleck, den dieses Repo vier Mal getroffen hat. Geoberflächt als `SUGGESTED_SUBTYPE`; **Beförderung bleibt ein manueller Edit** für `ResearchNodeType`, dann `"approved": true` und `TESSERAE_SCHEMA_DRIFT_APPLY=1`. |
| Portable compile + agent-write locks | ✅ | [`tesserae/locking.py`](../../tesserae/locking.py) | Der Lock war `if fcntl is None: yield` — auf Windows sperrte er nichts, und der Agent-Write-Overlay ist der eine Pfad, wo zwei unsynchronisierte Appends eine JSONL-Zeile zerreißen. Jetzt `flock(2)` wo vorhanden, `msvcrt.locking` andernfalls (gepinnt auf einen Ein-Byte-Bereich, da msvcrt von Dateiposition sperrt). Eine Plattform ohne beides warnt einmal pro Prozess. Eine übersprungene Replay-Zeile ist jetzt ein Lint-Befund (`AGENT_WRITE_SKIPPED`), nicht nur eine stderr-Warnung. |
| Sidecar registry | ✅ | [`tesserae/sidecars.py`](../../tesserae/sidecars.py) | Jeder `.tesserae/`-Eintrag deklariert seinen Besitzer, seine Art (`derived` / `accumulated` / `cache` / `scratch`) und was Löschen kostet — und `safe_to_delete` ist ein separates Feld, weil ein `cache`, dessen Antwort von einem Modell kam, nicht sicher zu löschen ist und ein `derived`-File menschliche Genehmigungen tragen kann. `doctor`'s `sidecars` liest dein echtes Verzeichnis dagegen. Siehe [sidecars](sidecars.de.md). |
| Kuzu is an export, never a store | ✅ | [`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) | Einseitig geregelt: `tesserae export kuzu` schreibt `graph.kuzu`, und kein Kompilier- oder Abfragepfad liest sie zurück — `read_graph` wird nur beibehalten, damit ein Export gegen den Graphen, aus dem er stammt, überprüft werden kann. Siehe [architecture § Kuzu export](architecture.de.md#kuzu-export). |

## Kognitives Gedächtnis und Geltungsbereich — v0.29.0 → v0.31.0 (August 2026)

Der Zyklus, der den Graphen *wissen* ließ, was geschehen ist, und nicht nur, was
geschrieben wurde: Ergebnisse überleben die Aufnahme, aus ihnen wird eine kausale
Kante abgeleitet, und die früher stummen Degradierungen melden sich jetzt.

| Funktion | Status | Quelle | Anmerkungen |
|---|---|---|---|
| Code-Schicht per Opt-in | ✅ | `cli.py`, [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | `compile` nimmt Codesymbole nicht mehr standardmäßig auf. In einem großen Repository überwogen sie alles andere zahlenmäßig und verdrängten die Suche; `tesserae code ingest` bindet CodeGraph weiterhin bewusst ein. Siehe [ingest](ingest.de.md). |
| Freigelegte Retrieval-Oberfläche | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Die bitemporalen und sichtselektiven Parameter waren gebaut und getestet, über MCP aber unerreichbar. `search_facts` nimmt nun `as_of` (Antwort zu einem vergangenen Datum) neben `current_only` — **zusammen abgelehnt**, das sind verschiedene Uhren — und meldet `undated_included`, damit ein Aufrufer weiß, wie viele der gelieferten Zeilen kein Datum tragen. |
| Laute Degradierungen | ✅ | [`tesserae/lint.py`](../../tesserae/lint.py), [`tesserae/ingest/fetch.py`](../../tesserae/ingest/fetch.py), [`tesserae/ingest/orchestrator.py`](../../tesserae/ingest/orchestrator.py) | Drei stille Fehlschläge wurden explizit: eine Binäraufnahme, die nichts hervorbrachte, undatierte Intervallabdeckung (`INTERVAL_COVERAGE`) und verworfener Nicht-Text-Inhalt. Schweigen las sich als Erfolg; das tut es nicht mehr. |
| Quellenabgeleitetes `first_seen_at` | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/session_graph.py`](../../tesserae/session_graph.py) | Ein Knoten wird nach dem Pfad datiert, unter dem seine Quelle aufgenommen wurde, nicht nach der Wanduhr beim Kompilieren — ein erneuter Lauf datiert ihn also gleich, und die byteweise Idempotenz bleibt erhalten. |
| Prozeduraler Retrieval-Pool | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py), [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `context` reserviert einen Platz für prozedurales Gedächtnis — was ausgeführt wurde und was dabei herauskam — **durch Provenienz verdient**, nicht standardmäßig gewährt. Der Lint-Code `PROCEDURAL_POOLS` meldet, wenn der Platz nicht ehrlich gefüllt werden kann. |
| Werkzeugergebnisse sind Züge | ✅ | [`tesserae/session_event.py`](../../tesserae/session_event.py), [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Exit-Codes und Fehler-Flags überleben die Aufnahme und landen auf `Event`-Knoten. Der Graph unterscheidet einen fehlgeschlagenen Befehl von einem, der bloß lief. Home-Verzeichnisse werden beim Hereinkommen geschwärzt. |
| Die `recovers`-Kante | ✅ | [`tesserae/session_recovery.py`](../../tesserae/session_recovery.py) | Die eine kausale Kante: „dies gelang, nachdem jenes fehlschlug", abgeleitet aus zwei **beobachteten** Ergebnissen einer Sitzung, die in Werkzeug, Programmfamilie, Arbeitsverzeichnis und Operand übereinstimmen. `CAUSAL_EDGE_TYPES` hat bewusst genau ein Element. Siehe [Sitzungshistorie](session-history.de.md). |
| Chartierte Domänenstruktur | ✅ | [`tesserae/charter.py`](../../tesserae/charter.py), [`tesserae/project.py`](../../tesserae/project.py), `cli.py` | Die Community-Erkennung *schlägt* ein Domänenvokabular vor; die Charta *besitzt* es zwischen ausdrücklichen Reorganisationen, denn die Erkennung ist deterministisch, aber nicht stabil (ein einziges Dokument mit 15 Knoten verschiebt ~29 % der Mitglieder). Jede Kompilierung leitet sie jetzt nach `.tesserae/charter/charter.json` ab, und `tesserae domains status` liest sie. Eine Neukompilierung, die nichts reorganisiert, lässt die Datei Byte für Byte unverändert — `reorg_seq` zählt Reorganisationen, nicht Kompilierungen. Ein Projekt, das in einen Lesevorgang passt, bleibt unter der Schwelle und bekommt keine Charta. |
| Mehrere Hosts auf gemeinsamer Platte | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | `TESSERAE_HOST_ID` grenzt Prune und Überschreiben danach ab, *wer* einen Datensatz geschrieben hat, sodass N Server auf einer Platte einander die Sitzungshistorie nicht mehr löschen. Siehe [Sitzungshistorie](session-history.de.md). |

## Cross-Project & UX — v0.11.0 (Juni 2026)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Cross-Project-Federation | ✅ | [`tesserae/federation.py`](../../tesserae/federation.py) | `ask --scope federated` assembliert EINEN Graph aus mehreren registrierten Projekten — Identity-Merge (gleiche arxiv/repo/hash/symbol) + Opt-out-embedding-gestützte `shares_concept_with`-Links — und liefert eine einzige querverwiesene, zitierte Antwort über die Vereinigung (PPR + `compile_context`). Projektbezogenes `graph.json` ist read-only; deterministisch für Identity-only. |
| Smarter `ask`-Router (kein aktives Projekt) | ✅ | [`tesserae/ask_router.py`](../../tesserae/ask_router.py) | Das Konzept "aktives Projekt" ist entfernt — alle registrierten Projekte sind gleich. Ein nacktes `ask` routet sich selbst (nennt ein Projekt → dieses; komparativ → federated; Follow-up → behält Route; sonst Federated-Fallback), mit optionalem LLM-Tiebreaker und Kontinuität pro Konversation. Projektbezogene Ops lösen das Projekt aus dem cwd auf. |
| Federation-Inspektion | ✅ | `tesserae/federation.py`, `cli.py` | `tesserae federation status` (Knotenzahlen pro Projekt, Identity-Merges, semantische Links) und `federation explain <node>` (warum ein Knoten Projekte überbrückt). |
| Multi-Project-Serve | ✅ | [`tesserae/serve.py`](../../tesserae/serve.py), `cli.py` | Nacktes `tesserae serve` served JEDES registrierte Projekt unter einem Server (Landing unter `/`, jedes unter `/<alias>/`, ein Projects-Switcher im Header, pfad-begrenzt); `--project X` served eines mit dem Live-Ask-Widget. |
| LLM-Konzept-Schicht in `compile` | ✅ | `cli.py`, [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py), [`tesserae/selective_extractor.py`](../../tesserae/selective_extractor.py) | `tesserae compile` baut die Konzept-/Claim-Schicht **standardmäßig** (`--extractor llm`) über den konfigurierten Provider (codex/claude/api gemäß `llm_provider`); `--extractor deterministic` ist der strukturelle, byte-stabile Opt-out; `selective-llm --llm-include … --llm-limit N` ist kostenbewusst. |
| `tesserae setup` (interaktiv) | ✅ | `cli.py`, [`tesserae/deps.py`](../../tesserae/deps.py) | Top-Level `tesserae setup` — standardmäßig interaktiv (LLM-Provider/Effort + welche optionalen Deps); Flags überspringen die Prompts. Installationen funktionieren in pip-losen uv-tool-Umgebungen (uv-pip-Fallback). |

## Interop, Suche & Setup — v0.10.0 (Juni 2026)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Google-**OKF-v0.1**-Import/Export | ✅ | [`tesserae/okf.py`](../../tesserae/okf.py) | `tesserae export okf [--import DIR]`. Markdown-+-YAML-Frontmatter-Bundle; round-trippt Tesseraes eigene Bundles verlustfrei über einen `x_tesserae`-Namespace, fremde Bundles best-effort. |
| Schnelle Transkript-Suche (memex) | ✅ | [`tesserae/memex_search.py`](../../tesserae/memex_search.py) | `nicosuave/memex`-BM25-Index über Claude-/Codex-Transkripte, verdrahtet mit dem `tesserae serve`-Sessions-Dashboard via `GET /api/transcript-search`. Optional + graceful, wenn abwesend. |
| Read-Discipline-Handles | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | `compile_context` `preview=N` liefert eine begrenzte Vorschau + ein content-keyed Handle; `get_handle` paginiert den Rest. Hält riesige Payloads aus dem Kontext des Agenten. |
| Extraktions-Qualitätssignale | ✅ | [`tesserae/session_graph_llm.py`](../../tesserae/session_graph_llm.py) | `confidence` + `confidence_rationale` + `revisit_signals` pro Befund (byte-stabil; in `fresh_insights` angezeigt). |
| Maschinenweites Setup + Deps | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py), `cli.py` | `tesserae setup` schreibt globale LLM-Defaults + installiert optionale Deps (memex, raganything); `tesserae config deps` listet/installiert; `tesserae init` bietet memex an. Projektbezogene Config überschreibt weiterhin. |

## Kontext-Engine — v0.5.0 (Juni 2026)

Das Engine-Rückgrat, das die drei Säulen antreibt. Siehe [`docs/architecture.md`](architecture.de.md) für die Engine-Rückgrat-Modul-Map, das Selbstverbesserungs-Memory-Sidecar und den Kontext-Compiler-Datenfluss.

### Engine-Rückgrat (Säulen 1 & 2)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| `Pipeline` — wiederverwendbare Refresh-Kette, gibt `List[StepResult]` zurück | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | Ein Step-Runner, den CLI, Daemon und MCP alle aufrufen. Fängt `Exception` pro Step; stoppt beim ersten Fehler. |
| `Daemon` — Single-Owner-asyncio-Supervisor | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | Beobachtet Quellen + Vault + Harness-Session-Verzeichnis; debouncter Cancel-and-Reschedule fasst einen Burst zu einem einzigen `Pipeline.run()` zusammen. Pidfile; überlebt In-Flight-Exceptions. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` ist ein Alias von `engine`. |
| `project refresh` — Prosa-Kette (ingest → compile → project) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only` (Opt-in-inkrementell), `--no-sessions`. |
| Live-Session-Monitor → Findings | ✅ | `harness_sessions.py` + Session-Graph-Module | Importierte Sessions speisen den Graph; `fresh_insights` / `find_session_findings` zeigen sie an. |

### Selbstverbesserungs-Memory (Säule 2)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| `node_memory`-SQLite-Sidecar (decay / confidence / superseded) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + store-agnostische Accessoren; nur mutierbarer Zustand. First-seen lebt im separaten `node_provenance`-Sidecar. |
| Ebbinghaus-Decay-Score | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | Rankt Session-Findings am neuesten + meistzugegriffen zuerst (treibt `fresh_insights`). |
| Supersede-Pass (**default-on**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | Deterministisches Verdikt markiert einen älteren Beinahe-Duplikat-Insight als von einem neueren superseded; fügt eine `supersedes`-Kante hinzu. |
| Insight-→-Code-Symbol-Verlinkung | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `discusses`-Kanten von Session-Insights zu den Symbolen, auf die sie verweisen. |
| Reinforce- + Contradiction-Pässe | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Access-Reinforcement + Widerspruchserkennung über dasselbe Sidecar. |
| Numerische Wiederkehr-Confidence im Output | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Temporale Fakten stempeln `confidence` aus `NodeMemoryRow.confidence`, mit Fallback auf `infer_confidence`. |

### Retrieval + Embeddings (Säulen 2 & 3)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Hybrid-Retriever (BM25 + lexikalisch + Embedding, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | Local-first, vollständig deterministisch. |
| Personalized PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | Multi-Hop-Seed-Expansion; tiefenbegrenzter Subgraph. |
| Echte Default-Embeddings (Track B, Phase 6) | ✅ | `retrieval/hybrid.py` | Default = deterministisches Hash-Bucket-Pseudo-Embedding (keine Deps); `sentence-transformers` (`all-MiniLM-L6-v2`) bevorzugt, lazy geladen, wenn installiert. Das MCP-Tool `embedding_status` meldet das aktive Backend. |

### On-Demand-Kontext-Compiler (Säule 3 — Aushängeschild)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| `compile_context` — zitiertes In-Memory-`ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Seed-Auflösung → PPR-Expansion → budget-begrenzte Auswahl → zitiertes Markdown → optionale LLM-Synthese. Deterministisch, außer `synthesize=true`. Schreibt nichts auf die Platte. |
| `project context`-CLI | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = unbegrenzt), `--llm`, `--output`. |
| `compile_context`-MCP-Tool | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Dieselbe Pipeline über MCP; `budget=0` ist unbegrenzt. |
| Topic-begrenzte Export-Slices | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | Topic-begrenzte `llms.txt` + `render_harness_context` via `compile_context`. |

### Inkrementeller Compile (Phase 4 — experimentell)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Provenance-Sidecar (`node_provenance`, first-seen) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | Fundament für Changed-only-Deletes; wird immer aufgezeichnet. |
| `GraphStore`-Delete-Oberfläche | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (verwirft Knoten, deren Provenance-Menge sich leert; dateiübergreifende Konzepte überleben). |
| `url_resolver`-Runtime-Store-Dispatch | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| `incremental_compile`-Flag | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **Default OFF / experimentell.** Byte-Parität für mehrere Edit-Formen bewiesen, aber Multi-Owner-/Producer-Lifecycle-Lücken bleiben; Full-Compile bleibt der Default. |

## Frontend-Redesign — April 2026

Ein dokument-orientiertes, hierarchisches Wiki ersetzt den alten Graph-Dump. Siehe [`docs/frontend-redesign.md`](frontend-redesign.de.md) für die Route-für-Route-Tour und [`docs/architecture.md`](architecture.de.md) für das Drei-Schichten-Modell.

### Wiki-Schicht (L2-Markdown)

| Feature | Status | Quelle | Doc-Anker |
|---|---|---|---|
| `WikiPageStore` (idempotente Body-Hash-Writes, Frontmatter-Parser) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Modul-Map](architecture.de.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — eine md-Seite pro Wiki-Schicht-Knoten | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.de.md#pipeline) |
| `sources/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.de.md#sources) |
| `concepts/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.de.md#concepts) |
| `entities/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.de.md#entities) |
| `papers/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.de.md#papers) |
| `repos/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.de.md#repos) |
| `topics/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.de.md#topics) |
| `questions/`-Seiten (Open Questions) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.de.md#questions) |
| `syntheses/`-Seiten | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.de.md#syntheses) |

### Synthesis-Arten (L2 → abgeleitet)

`SynthesisProjector` produziert sieben deterministische Templates und fügt `Synthesis`-Knoten + `synthesizes`- / `summarizes`-Kanten zurück in den Graph.

| Art | Status | Quelle | Notizen |
|---|---|---|---|
| `pulse` (eine global, treibt `/`) | ✅ | `synthesis.py` | Bei jedem Compile neu gebaut. |
| `daily_digest` | ✅ | `synthesis.py` | Eine pro `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Eine pro `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Eine pro `ResearchTopic`- / `ApproachFamily`-Cluster ≥ 3 Paper. |
| `comparison` | ✅ | `synthesis.py` | Eine pro Paar von `ApproachFamily`, die auf derselben Task konkurrieren. |
| `field_overview` | ✅ | `synthesis.py` | Eine pro `ResearchField`. |
| LLM-aufgewertete Summaries (env-geflaggt) | ⚠ | nur Hook | Heuristische Baseline ist ausgeliefert; der `TESSERAE_SYNTHESIS_LLM=1`-Hook bleibt ein Stub. |

### Statische Site-Routen

| Route | Status | Quelle | Notizen |
|---|---|---|---|
| `/` (Home, Hero-Pulse) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | Stat-Zeile + kuratierte Einstiegspunkte + jüngste Aktivität. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Heatmap + Tagesliste + Synthesis-Leiste. |
| `/timeline/<YYYY-MM-DD>.html` (Tages-Detail) | ⚠ | noch n/a | Heatmap-Zellen verlinken interimistisch auf die `digest.md`-Quellseite des Tages. Subagent P verdrahtet die Tages-Detailseiten durch `StaticSiteBuilder`. |
| `/graph/` (interaktiv 2D + 3D) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, Hover-Tooltips, Kantenlabels, cursor-verankerter Zoom. |
| `/about.html` | ✅ | `pages.py::render_about` | Schema, Build-Infos. |

### KI-freundliche Exporte

| Artefakt | Status | Quelle | Zweck |
|---|---|---|---|
| Per-Page-`<page>.txt`-Geschwister | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | Klartext-Ansicht einer Seite (keine Nav, kein Styling). |
| Per-Page-`<page>.json`-Geschwister | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | Kurzer Index nach llmstxt.org. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Jeder Seiten-Body, gedeckelt bei 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org-`Dataset`, nur Wiki-Schicht-Knoten. |
| `graph.json` | ✅ | `__init__.py::write_site` | Voller Graph-Payload (inkl. Code-Knoten für Tooling). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | Palette + Seitensuche; nur Wiki-Schicht-Arten. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Jede emittierte Route, `lastmod` aus dem Frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Die letzten 30 Syntheses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permissiv — Crawlen + Indexieren. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Maschinenlesbare Site-Map. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + Größe für jede emittierte Datei (Idempotenz-Harness). |

### Visuelles Design + UX

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Design-Tokens (Light- + Dark-Themes, Terrakotta-Akzent) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Ein CSS-Bundle in `assets/style.css`. |
| Theme-Toggle (persistiert, kein Flash) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` in `localStorage`, vor dem Paint angewandt. |
| Suchpalette (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Fuzzy-Match über `search-index.json`; Liste kürzlich besuchter Seiten. |
| Sticky rechtes TOC | ✅ | `pages.py` + `tokens.py` | Nur Desktop; Mobile-Drawer via `<details>`. |
| Aktivitäts-Heatmap mit Monats- + Wochentagslabels | ✅ | `components.py::heatmap_svg` | 26-Wochen-SVG, Zellen verlinken auf die `digest.md` des Tages. |
| Sparkline (pro Konzept/Entity) | ✅ | `components.py::sparkline_svg` | Wöchentliche Erwähnungszahlen, letzte 12 Wochen. |
| Mobile-Shell (Drawer-Leiste, Bottom-Nav, fluide Typografie) | ✅ | `tokens.py` + `pages.py` | Touch-Hit-Targets ≥ 44 px. |
| Seitenübergänge (120 ms Opacity, prefers-reduced-motion) | ✅ | `tokens.py` | |
| 3D- + 2D-Graphansicht (Hover, Kantenlabels, cursor-verankerter Zoom) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, als CDN-Snapshot gevendort. |
| Per-Page-KI-Geschwister-Footer | ✅ | `components.py::ai_siblings_footer` | Inline-Links zur `.txt` und `.json` der aktuellen Seite. |
| Harness-Session-Historie-Seiten | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Expliziter Claude-Code-/Codex-Import; `/sessions/`-Index- und Detailseiten mit Markdown-Turns, linker Turn-Leiste, eingeklapptem Tool-Use und Sucheinträgen. |

### Pipeline + CLI

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| `project compile` ruft Synthesis + Wiki + Site der Reihe nach auf | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | Phase 3 des Redesign-Plans. |
| `project build-site` standalone | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | Liest `wiki/` + `graph.json`, schreibt `site/`. |
| `project serve` lokales HTTP | ✅ | `cli.py` | Reiner stdlib-Server. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Worktree-Push nach `gh-pages`; optionales `--enable-pages` via `gh`-CLI. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Eingehende Session-Historie für Claude Code/Codex; Discovery ist explizit und auf das Projekt-Arbeitsverzeichnis begrenzt. |
| `project watch` Rebuild-on-Change | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | Eigenständiger Polling-Watcher: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. Der Multi-Source-Supervisor lebt unter `project engine`/`daemon` (siehe Kontext-Engine). |
| `project context` — kompiliert ein zitiertes Kontextdokument | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Pillar-3-Aushängeschild; siehe Abschnitt Kontext-Engine. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | Prosa-Refresh-Kette + Supervisor-Schleife; siehe Abschnitt Kontext-Engine. |

## Vorbestehende Features (unverändert weitergeführt)

### CLI und Installation

- ✅ Installierbares Python-Paket via `pyproject.toml`.
- ✅ Console-Befehle: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` für `curl | bash`-Installation.
- ✅ Editierbare Installationen als Default für schnelle lokale Entwicklung.

### Extraktion

- ✅ Deterministischer Research-Note-Extraktor mit kontrollierten Knoten-/Kanten-Vokabularen.
- ✅ Claude-CLI-/OAuth-Extraktor für höherwertige strukturierte Extraktion ohne API-Keys.
- ✅ Selektives Claude-Routing nach Glob und Budget-Limit.
- ✅ Deterministischer Entwicklungs-Code-Extraktor für Python-Projekte.
- ✅ Batch-Ingest mit Content-Hashing und `--changed-only`-Unterstützung.
- ✅ Toleranz für fehlgeformtes UTF-8 beim Quellenlesen.

### Graph-Governance

- ✅ Kontrollierte `ResearchNodeType`-Liste — enthält jetzt `SYNTHESIS`.
- ✅ Kontrollierte Kantentyp-Whitelist — enthält jetzt `synthesizes`, `summarizes`.
- ✅ Validierung zur Zurückweisung von Schema-Drift.
- ✅ Alias-Kanonisierung.
- ✅ Review-Queue für mehrdeutige Beinahe-Duplikat-Knoten.
- ✅ Review-Decisions-Template und Merge-/Keep-Separate-Workflow.
- ✅ Korpus-Trend-Zusammenfassung aus Per-File-Graphen.

### Persistenz und Reports

- ✅ Graph-JSON-Export.
- ✅ SQLite-Graph-Store.
- ✅ Optionaler Kuzu-Graph-Store.
- ✅ Graph-Report mit Zählungen, Evidence-Abdeckung, verwaisten Knoten, Datums-Buckets, alias-lastigen Knoten.

### Projektlokaler Workflow

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy` (GitHub Pages)
- ✅ `tesserae sessions discover/import/list` (expliziter lokaler Agent-Historie-Import)
- ✅ `tesserae export site --watch` (eigenständiger Polling-Watcher)
- ✅ `tesserae engine` (Supervisor-Schleife — v0.5.0)
- ✅ `tesserae refresh` (Prosa-Kette ingest → compile → project — v0.5.0)
- ✅ `tesserae context` (On-Demand-Kontext-Compiler — v0.5.0)
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ Sofort öffenbarer Vault-Export.
- ✅ `.obsidian/app.json` und Graph-Einstellungen.
- ✅ Markdown-Projektion.
- ✅ `raw/assets/`-Struktur.
- ✅ `_meta/dashboard.md` mit Dataview-Query.

### Agent-Harnesses

Generierte Target-Dateien für:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: Steering- und MCP-Einstellungen
- ✅ Cursor: Projekt-Regeln und MCP-Config
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / temporale Fakten

- ✅ Temporale Fakt-Projektion mit Provenance-, Currentness-, Confidence- und Invalidierungsfeldern.
- ✅ Abhängigkeitsfreier Graphiti-Episoden-JSONL-Export.
- ✅ `sync-graphiti --dry-run`-Smoke ohne installiertes Graphiti.
- ✅ Optionaler Live-Sync mit `graphiti_core` und Neo4j.

### MCP-Server

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` über stdio JSON-RPC.
- ✅ Retrieval-/Graph-Tools: `schema`, `graph_summary`, `search_nodes`, `node_context` (mit `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`.
- ✅ Kontext-Engine-Tools (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (decay-gerankt), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ Setup-Tools: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Multi-Projekt-Registry: `list_projects`, `register_project`, `unregister_project`, `list_sessions`. Store-URL-Dispatch via `url_resolver`.

## Tests

Die aktuelle Suite deckt ab:

- ✅ Ontologie-Leitplanken (inkl. neuem `Synthesis`-Knoten + `synthesizes`- / `summarizes`-Kanten);
- ✅ deterministische Extraktion;
- ✅ Claude-CLI-Wrapper-Parsing/-Validierung;
- ✅ selektives Claude-Routing;
- ✅ Kanonisierungs-/Review-Workflow;
- ✅ Batch-Ingest;
- ✅ Reports;
- ✅ SQLite-/Kuzu-Persistenz;
- ✅ Graphiti-Export/Sync-Dry-Run;
- ✅ Projekt-CLI-Workflow;
- ✅ Agent-Harness-Export;
- ✅ Obsidian-Export;
- ✅ Frontend-Generierung + Link-Integrität (kein `nodes/codeclass-*.html`);
- ✅ Wiki-Store-Idempotenz;
- ✅ Synthesis-Projector-Golden + -Idempotenz;
- ✅ Site-Komponenten, -Seiten, -Exporte, -Relevanz;
- ✅ KI-Geschwister-Form (`.txt` + `.json` pro Seite);
- ✅ End-to-End-Compile-zweimal-Idempotenz;
- ✅ Engine-Rückgrat: Pipeline, Refresh-Kette, Daemon-Core + -Quellen, `project engine`-CLI;
- ✅ Selbstverbesserungs-Memory: Sidecar, Decay/Supersede, Supersede-Suppression (inkl. MCP), Reinforce/Contradiction;
- ✅ Retrieval + Embeddings: Hybrid-Suche, PPR, echte Default-Embeddings (Phase 6);
- ✅ Kontext-Compiler: Form/Zitat-Integrität/Determinismus/Budget/PPR-Fallback, `project context`-CLI, MCP `compile_context`;
- ✅ inkrementeller Compile (experimentell): Differ, Paritäts-Gates, Provenance-Readiness, SQLite-Provenance;
- ✅ Paketinstallation und Installer-Vertrag.
