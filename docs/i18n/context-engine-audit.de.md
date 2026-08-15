# Tesserae als Kontext-Engine — Lückenanalyse

<!-- translations:start -->
<p align="center"><a href="../context-engine-audit.md">English</a> · <a href="context-engine-audit.ko.md">한국어</a> · <a href="context-engine-audit.zh.md">中文</a> · <a href="context-engine-audit.ja.md">日本語</a> · <a href="context-engine-audit.ru.md">Русский</a> · <a href="context-engine-audit.es.md">Español</a> · <a href="context-engine-audit.fr.md">Français</a> · <a href="context-engine-audit.de.md">Deutsch</a></p>
<!-- translations:end -->
> **Mission (2026-06-02):** Tesserae ist eine *Kontext-Engine* — sie erzeugt
> agentenfertigen Kontext, indem sie eine **sich selbst verbessernde**
> Wissensbasis über drei Säulen rekonstruiert: **(1) Sitzungsüberwachung**,
> **(2) autonome proaktive Aufnahme** und **(3) Dokumente auf Abruf**. Wissen
> muss **in Echtzeit und sich entwickelnd** sein, bereit zur Übergabe an
> Agenten.

Dieses Dokument prüft die aktuelle Codebasis an dieser Mission. Es ist das
Ergebnis einer vierfach parallelen Begutachtung (Aufnahme/Sitzungen,
Selbstverbesserung, Ausgabe/agentenseitig, Orchestrierung/Lebenszyklus).

> **Status zum Zeitpunkt von v0.5.0 (2026-06-06):** Dieses Dokument ist ein **Stichtags-Audit** (Snapshot vom 2026-06-02) und wird für das Archiv unverändert beibehalten. Die meisten seiner übergreifenden Befunde sind nun **behoben**: der fehlende Supervisor-Daemon und der In-Process-Pipeline-Orchestrator wurden ausgeliefert (Engine-Rückgrat, `tesserae/engine/`), das Live-Tailing von Sitzungen ersetzt den nachträglichen Scan (Säule 1), die Selbstverbesserungs-Pässe sind über das `node_memory`-Sidecar aktiviert und persistiert (Supersede standardmäßig aktiv mit Unterdrückung, numerische Wiederholungs-Konfidenz — Säule 2), das Hash-Bucket-Standard-Embedding wird durch ein echtes, laut fehlschlagendes Backend ersetzt (Säule 3), und **der On-Demand-Kontext-Compiler der Säule 3 existiert nun** (`compile_context`). Eine konzipierte inkrementelle Schicht über den `GraphStore`-Port ist als Infrastruktur gelandet, bleibt aber **mit Flag OFF/experimentell**, und die Vereinheitlichung von serve+watch+deploy (Schritt 7 der Build-Reihenfolge) ist noch offen. Den Status je Phase siehe in der [Phasen-Roadmap](context-engine-roadmap.de.md), die Änderungsübersicht in den [v0.5.0-Release-Notes](release-notes/v0.5.0.de.md). Die Befunde unten bleiben unverändert als ursprünglicher Snapshot.

## Urteil in einer Zeile

Tesserae ist heute ein **mechanisch gesunder, gut getesteter Batch-CLI-
Compiler**. Gemessen an der Vision der Kontext-Engine ist es auf allen drei
Säulen **manuell ausgelöst + nachträglich + an git-HEAD gebunden**. Die
Maschinerie zum Bau der Engine existiert bereits als Primitive — was fehlt,
ist die **kontinuierliche, sich selbst steuernde Schicht**, die sie
zusammensetzt.

Das größte fehlende Stück in jedem Querschnitt: ein **langlaufender
Supervisor/Daemon, der eine einzige Ereignisschleife besitzt** und
Sitzungs-Tailing, Aufnahme, inkrementelle Kompilierung und Veröffentlichung
autonom antreibt. Alles andere ist inkrementell darauf.

---

## Säule 1 — Sitzungsüberwachung → **nachträglich, nicht live**

| Status | Befund | Was nötig ist |
|---|---|---|
| Lücke | Die Sitzungserfassung ist ein nachträglicher Scan: `discover_harness_sessions()` durchläuft fertige Transkripte nur, wenn ein Mensch `sessions discover --import` oder `compile` ausführt. `compile` verweigert bewusst das Scannen von `~/.claude/projects/` (Latenz). | Ein **Tailer**, der die JSONL-Dateien des Harness beobachtet und Züge während der laufenden Sitzung aufnimmt. |
| Lücke | Der einzige echte Beobachter (`watch.py WatchLoop`) deckt **Quell-Markdown** ab, pollt alle 2 s und feuert einen vollständigen `compile`. Er überwacht weder Sitzungen noch Code. | Auf Sitzungs- + Quellwerkzeug-Trigger unter einem Supervisor erweitern. |
| Lücke | Die „Live"-Schleife von `vault_watch.py` wirkt auf die **Ausgabe** (Obsidian-Rücksynchronisierung), nicht auf die Aufnahme. | Kein Ersatz für Live-Wissensabruf. |
| grob | Die Sitzungs-Neuextraktion ist per `session_id` zwischengespeichert, aber **sitzungsweit**: ein einziger neuer Zug invalidiert den ganzen Cache und führt den vollständigen LLM-Durchlauf erneut aus. | Zug-Granularität für Live-Tailing. |
| grob | Der `harness_sessions`-Speicher ist ein flacher Glob mit Vollscan bei jedem list/write. | Indizierter/anhängender Speicher für eine kontinuierlich wachsende Erfassungsmenge. |
| fehlend | Keine knotenweisen Frische-/Herkunfts-Zeitstempel; Aktualität wird nur auf Artefaktebene (git HEAD) verfolgt. | Faktweise Frische für „wie frisch ist das?". |

## Säule 2 — Sich selbst verbessernde Wissensbasis → **Einmal-Neuextraktion, angeflanschte Evolution**

Die „evolvierenden" Durchläufe existieren, laufen aber **nur innerhalb eines
einzigen `compile`** (eine Neuextraktion von Grund auf), und die meisten sind
**Opt-in per Umgebungs-Flag oder manueller CLI**. Fakten werden bei jeder
Kompilierung neu berechnet, nicht an Ort und Stelle revidiert.

| Status | Befund | Was nötig ist |
|---|---|---|
| Lücke | Der **Zerfall (Decay)** (`memory/decay.py`, Ebbinghaus-Halbwertszeit 14 T) wird nur *zur Abfragezeit* berechnet, nie bei der Kompilierung persistiert oder zurückgeschrieben. | Zerfallsschreibung zur Kompilierzeit + persistierter Score. |
| Lücke | Die Zugriffsschleife des Zerfalls ist **tot**: `last_accessed_at == first_seen_at`, `access_count` wird nie erhöht. Das Signal „ich schaue es ständig an → es ist wichtig" bewirkt nichts. | Eine Zugriffs-Aufzeichnungsfläche (MCP-Lesung → Inkrement). |
| Lücke | Das **Ersetzen (Supersede)** (`memory/supersede.py`) ist hinter `TESSERAE_SUPERSEDE_PASS=true` (standardmäßig aus) und *hängt* nur Kanten *an* — es stuft veralteten Inhalt nie herab/blendet ihn nie aus. Die Überzeugungsrevision ist kosmetisch. | Standardmäßig an + Konsumenten, die ersetzte Fakten in der Ausgabe unterdrücken. |
| Lücke | **Widersprüche (Contradictions)** werden *erkannt* (`lint.py`, info-Schweregrad, brüchiger String-Abgleich), aber nie *aufgelöst*. Keine Konfidenz-Schlichtung. | Ein Auflösungsdurchlauf, nicht nur eine Sonde. |
| Lücke | **Schema-Drift** (`schema_drift.py`) ist ein manueller `schema-drift`-Unterbefehl, der nur Vorschläge schreibt; das Schema verfeinert sich nie selbst. | Anwendungspfad + Pipeline-Integration. |
| Lücke | **Kanonisierung (Canonicalization)** führt nur hochkonfidente Aliase automatisch zusammen; der Rest wird zur menschlichen CLI-Freigabe in die Warteschlange gestellt. | Automatische, mit der Zeit LLM-geschlichtete Zusammenführung. |
| Lücke | **Rückkopplungsschleife halb geschlossen**: Der deterministische Basis-Extraktor *ignoriert Vorgaben vollständig* (`selective_extractor.py:43`); nur der optionale LLM-Pfad konsumiert Korrekturen. Mit ausgeschaltetem LLM gelangen menschliche Korrekturen nie zurück in die Extraktion. | Vorgabenbeachtung im deterministischen Pfad oder LLM standardmäßig. |
| Lücke | Keine **Verstärkung wiederkehrender Erkenntnisse**: nichts verstärkt die Konfidenz, wenn eine Erkenntnis über Sitzungen hinweg wiederkehrt. `temporal.infer_confidence` ist eine grobe String-Heuristik. | Sitzungsübergreifende Häufigkeit → numerische Konfidenz. |
| grob | Die Ersetzungs-Kandidatenpaarung ist **lexikalischer Jaccard (0,55)**; semantische Umformulierungen mit geringer lexikalischer Überlappung werden nie Kandidaten. | Embedding-basierte Kandidatengenerierung. |
| fehlend | **Der gesamte Selbstverbesserungs-Querschnitt ist ungetestet** (keine decay/supersede/feedback/drift/canonical/temporal-Tests). | Tests neben jeder Änderung hier. |

## Säule 3 — Dokumente auf Abruf → **existiert noch nicht**

Die Abfrage-/Abrufverrohrung ist ausgereift (hybrides RRF, PPR, ~20 MCP-Tools,
ask pro Seite, KI-Exporte). Aber **jedes Artefakt ist entweder eine statische
Gesamtkorpus-Projektion oder eine Einzelknoten-Suche.** „Nutzer fragt ‚gib mir
Kontext zu X' → maßgeschneidertes Dokument" ist nicht implementiert. Die
Primitive, es zu bauen, sind alle vorhanden, aber nie zusammengesetzt.

| Status | Befund | Was nötig ist |
|---|---|---|
| fehlend | **Dokumenterzeugung auf Abruf (die zentrale Lücke der Säule 3).** Kein Modul erzeugt ein maßgeschneidertes, abfragebegrenztes Dokument aus einer Anfrage. `report.py` ist eine Lint-Zusammenfassung zur Kompilierzeit, kein Wissensartefakt. | Neues `context_compiler`: Suche → PPR → Nachbarschaftsdurchlauf → Körper-Zusammenbau → optionale LLM-Synthese. |
| Lücke | `wiki_page` liefert einen vorkompilierten Knotenkörper; kein Mehrknoten-, abfragebegrenztes Zusammenbau-Tool. | MCP-Tool `compile_context(query|seeds, depth, budget)`. |
| Lücke | `ask` liefert Prosa oder eine Ergebnisliste, nie ein herunterladbares/übergebbares Kontext-Artefakt. | Antwortmodus, der ein strukturiertes, zitiertes Kontextbündel ausgibt. |
| Lücke | `agent_harness.py` ist eine **statische** Übergabe (fest verdrahtete Top-12-Knoten + feste Liste), nicht abfragebegrenzt oder aufgabenweise. | Thema/Saat annehmen → einen begrenzten Briefing rendern. |
| Lücke | `node_context` ist 1-Hop, unrangiert. Schwach als Agenten-Kontextprimitiv. | Über PPR für rangierten k-Hop-Kontext leiten. |
| Lücke | Exporte (`llms.txt`, `graph.jsonld`) sind Gesamtkorpus-Dumps; keine themenweise Scheibe. | Themenbegrenzter Teilgraph → llms-txt-Scheibe. |
| grob | Die Standard-Embedding-Spur ist ein **deterministisches Hash-Bucket-Pseudo-Embedding** (blake2b, 128 Dim); echtes semantisches Backend nur, wenn `sentence-transformers` installiert ist, und `auto` stuft still herab. Die „semantische" Abrufung ab Werk ist gefälscht. | Echte Standard-Embeddings oder eine laute Warnung zur Hash-Spur. |
| grob | `query.answer()` **verwirft eine gültige LLM-Antwort**, wenn ihr ein Knotenzitations-Regex-Treffer fehlt. | Antwort behalten; stattdessen fehlende Zitate markieren. |
| grob | Das ask-Widget des statischen Hosts liefert **konservierte `DEMO_QA`**; echtes ask funktioniert nur unter `serve`. Das öffentliche „ask" auf Pages ist Theater. | Für Demo akzeptabel; aber auf der veröffentlichten Seite nicht agentenkonsumierbar. |
| grob | Das `auto`-Backend von `ask` schluckt Ausnahmen und stuft unsichtbar auf BM25 herab. | Offenlegen, welches Backend geantwortet hat und warum Fallbacks ausgelöst wurden. |

## Querschnitt — Orchestrierung & Lebenszyklus → **Batch-CLI, keine Engine**

| Status | Befund | Was nötig ist |
|---|---|---|
| Lücke | **Kein Daemon-/Engine-Prozess.** Flacher Einmal-argparse-Dispatcher; der Prozess beendet sich nach jedem Unterbefehl. Null signal/SIGTERM/pidfile/launchd-Behandlung; Beobachter sterben bei nacktem `KeyboardInterrupt`. | Ein überwachter langlaufender Daemon, der eine Ereignisschleife + anmutiges Herunterfahren besitzt. |
| Lücke | „Kontinuierlich" = `while True: time.sleep(interval)`-Markdown-Poller. Keine Dateisystemereignisse, kein Gegendruck, kein Streaming. | Ereignisgetriebener Kern mit einem einzigen Scheduler. |
| Lücke | **„Refresh" lebt in einem Markdown-Skill eines Slash-Befehls**, nicht im Code — es reiht `sessions discover --import` → `compile` → `obsidian-sync` aneinander. | Erstklassiger In-Process-Pipeline-Orchestrator, gemeinsam für Daemon/CLI/MCP. |
| grob | Die inkrementelle `changed_only`-Kompilierung ist **brüchig und selbstbeschrieben als Behelf**: das Manifest ist `{path: sha256}`; man muss den vorherigen Graph neu laden, Projektor-/Synthese-Knoten abstreifen, neu extrahierte Quellknoten räumen und dann zusammenführen — sonst lässt eine 21-Dateien-Bearbeitung 2400 Knoten auf 1700 kollabieren. | Eine entworfene inkrementelle/Streaming-Schicht, die durch den `GraphStore`-Port fließt. |
| grob | `cli.py` ist ein ~2000-Zeilen-Gott-Dispatcher (`if args.command == ...`-Leiter); `ask`/`wiki` haben separate handgebaute Parser. | Befehlsregister / Unterbefehlsmodule. |
| grob | Phasengesteuerte Flags liefern halbfertige Oberfläche aus: die Hilfe von `--sessions-llm` sagt *„wird beachtet, sobald Phase 5 gelandet ist"*. | Fertigstellen oder verstecken. |
| grob | `graph_stores/url_resolver.py` umhüllt einen Async-Speicher mit `asyncio.run` **pro Aufruf** — eine frische Ereignisschleife pro Upsert. Pathologisch für Streaming. | Persistente Async-Laufzeit, falls die Engine in Produktion geht. |
| grob | Die hexagonalen Protokolle von `ports/` sind definiert, aber die eigenständige Pipeline **umgeht** sie und geht direkt zu JSON-Artefakten. Nur HypePaper nutzt den Port. | Die Kern-Pipeline konsistent durch `GraphStore` fließen lassen. |
| grob | Drei Persistenzformate (JSON-Artefakt, SQLite-Speicher, Kuzu) ohne einzige Wahrheitsquelle; der Kuzu-Adapter umhüllt jedes Feld mit base64, um einen 0.16-Korruptionsbug zu umgehen. | Auf eine Wahrheitsquelle konvergieren. |
| grob | `serve` (`TCPServer.serve_forever`) und `watch` sind **separate blockierende Prozesse** — man kann nicht serve + automatisches Neukompilieren zusammen. `deploy` ist ein manueller git push, entkoppelt. | serve + watch + deploy unter dem Supervisor für kontinuierliche Veröffentlichung vereinheitlichen. |
| fehlend | `frontend.py` ist ein **veraltetes totes Modul**, das noch ausgeliefert wird und `tesserae/site/` dupliziert. | Löschen oder Aufrufer migrieren. |
| grob | Die menschliche Review-Schleife von `review_workflow.py` gibt stringtypisiertes `"action": "TODO: merge|keep_separate"`-JSON zum Handbearbeiten aus; kein programmatischer Anwendungspfad. | Integrierte Review-Warteschlange, in die Kompilierung verdrahtet. |
| Hinweis | TODO/FIXME-Markierungen sind echt spärlich — die wahre Schuld sind die **in Kommentaren dokumentierten Behelfe** (changed-only-Merge, Kuzu-base64, asyncio-pro-Aufruf), nicht verstreute TODOs. | — |

---

## Empfohlene Baureihenfolge (Architektur-Delta → Vision)

1. **Supervisor-Daemon + In-Process-Pipeline-Orchestrator.** Eine
   Ereignisschleife, Signale/Herunterfahren, die die Markdown-Skill-
   Refresh-Kette ersetzt. *Schaltet jede andere Säule frei.*
2. **Live-Sitzungsmonitor.** Harness-JSONL tailen → zuggranulare inkrementelle
   Extraktion → in die Schleife einspeisen. (Ersetzt das manuelle
   `sessions discover --import`.)
3. **Echte inkrementelle/Streaming-Kompilierung** durch den `GraphStore`-Port,
   der den brüchigen `changed_only`-Räumungs-Patch in Rente schickt.
4. **Selbstverbesserungs-Durchläufe standardmäßig aktivieren + persistieren**:
   Zerfallsschreibung zur Kompilierzeit, access-count-Inkrement bei MCP-
   Lesungen, supersede an (mit Unterdrückung), Widerspruchsauflösung,
   Konfidenz wiederkehrender Erkenntnisse.
5. **Kontext-Compiler auf Abruf** (MCP-Tool `compile_context` + CLI): Abfrage →
   PPR/Hybrid → Nachbarschaftsdurchlauf → zusammengebautes, zitiertes,
   agentenfertiges Dokument.
6. **Echte Standard-Embeddings** (oder eine laute Degradierungswarnung), damit
   semantische Abrufung kein Hash-Stub ab Werk ist.
7. **serve + watch + deploy vereinheitlichen** für kontinuierliche
   Veröffentlichung; Lebenszyklustests hinzufügen (die Schicht, von der die
   Vision am meisten abhängt, ist derzeit am wenigsten abgedeckt).

## Erhaltenswerte Stärken

Deterministische byte-identische Kompilierung; breite Testabdeckung der
Batch-Maschinerie; saubere hybride RRF-Abrufung + durchdachte
PPR-Kantengewichtung; breite, korrekt partitionierte (öffentlich/privat)
MCP-Tool-Oberfläche; solide statische Exporte (`llms.txt`, JSON-LD, RSS) und
ein sicherheitsbewusstes ask-Widget. Das Fundament ist stark; die Arbeit
besteht darin, obendrauf die dynamische, sich selbst steuernde Schicht
hinzuzufügen.
