# Tesserae → Kontext-Engine — Phasen-Roadmap

<!-- translations:start -->
<p align="center"><a href="../context-engine-roadmap.md">English</a> · <a href="context-engine-roadmap.ko.md">한국어</a> · <a href="context-engine-roadmap.zh.md">中文</a> · <a href="context-engine-roadmap.ja.md">日本語</a> · <a href="context-engine-roadmap.ru.md">Русский</a> · <a href="context-engine-roadmap.es.md">Español</a> · <a href="context-engine-roadmap.fr.md">Français</a> · <a href="context-engine-roadmap.de.md">Deutsch</a></p>
<!-- translations:end -->
Abgeleitet von [`context-engine-audit.md`](./context-engine-audit.de.md).
Verwandelt die 7-Schritte-Baureihenfolge in sequenzierte Phasen mit
Abhängigkeiten, konkretem Umfang und Abnahmekriterien.

**Nordstern:** eine kontinuierlich laufende Engine, die Sitzungen überwacht,
Wissen autonom aufnimmt, ihre Basis selbst verbessert und auf Abruf
agentenfertigen Kontext liefert — und so die heutige manuelle
Batch-Kompilierungs-CLI ersetzt.

> **Status zum Zeitpunkt von v0.5.0 (2026-06-06):** Die Phasen 0–6 wurden **ausgeliefert**. Das Engine-Rückgrat (P0 Pipeline-Orchestrator, P1 Supervisor-Daemon, P2 Live-Sitzungsmonitor) liegt in `tesserae/engine/`; die inkrementelle Kompilierung P3 ist als Infrastruktur in v0.5.0 gelandet und ist **seit v0.40.0 Standard**; P4 die Selbstverbesserung wird über das `node_memory`-Sidecar persistiert (numerisches Wiederholungs-Vertrauen, Supersede standardmäßig aktiv); P5 echte Standard-Embeddings wurden ausgeliefert (Spur B); und **P6 — der Kontext-Compiler auf Abruf — ist das Hauptfeature von v0.5.0**. Phase 7 (serve + watch + deploy + Lebenszyklustests vereinheitlichen) **wurde in v0.40.0 ausgeliefert** mit Veröffentlichung bewusst manuell gelassen. Der Status je Phase ist unten inline vermerkt. Siehe die [v0.5.0-Release-Notes](release-notes/v0.5.0.de.md).

## Form der Abhängigkeiten

```
P0 Pipeline-Orchestrator (refresh-Kette in Code extrahieren)
        │
P1 Supervisor-Daemon (die Schleife) ───────────┐
        │                                      │
   ┌────┴─────────────── Spur A ───────┐   ┌── Spur B (parallelisierbar) ──┐
   P2 Live-Sitzungsmonitor             │   P5 Echte Standard-Embeddings
   P3 Inkrementelle/Streaming-Kompil.  │   P6 Kontext-Compiler auf Abruf
   P4 Persistenz der Selbstverbesserung┘   (P6 hängt von P5 ab)
        │                                      │
        └──────────────► P7 serve+watch+deploy vereinheitlichen ◄──┘
```

Spur A (Echtzeit-Aufnahme) und Spur B (agentenseitige Ausgabe) können parallel
laufen, sobald P1 gelandet ist. P7 lässt sie konvergieren.

---

## Phase 0 — Pipeline-Orchestrator (risikomindernde Grundlage) ✅ Ausgeliefert in v0.5.0

> **Ausgeliefert in v0.5.0** — `tesserae/engine/pipeline.py` (Engine-Rückgrat, gemergt in den Phasen 1–3).

**Ziel:** Die refresh-Pipeline aus dem Slash-Command-Markdown in einen
erstklassigen In-Process-Orchestrator herausholen, den Daemon, CLI und MCP alle
aufrufen.

- **Warum jetzt:** Jede spätere Phase braucht einen gemeinsamen Codepfad für
  `ingest → compile → project → publish`. Heute existiert diese Sequenz nur als
  Prosa in einem Skill. Nichts anderes lässt sich automatisieren, bis sie
  aufrufbar ist.
- **Umfang:** Neues `tesserae/engine/pipeline.py` (ein `Pipeline`-Objekt, das
  die aktuelle Kette `sessions discover --import → compile → obsidian-sync`
  umhüllt). `cli.py`-Unterbefehle durch ihn routen. Den ~2000-Zeilen-
  Gott-Dispatcher `project_main` in eine Befehlstabelle zu zerlegen beginnen
  (mechanisch, ohne Verhaltensänderung).
- **Lieferungen:** `Pipeline.run(steps, changed_only=…)`; die CLI delegiert an
  ihn; Unit-Tests für Schrittsequenzierung + Fehlerfortpflanzung.
- **Abnahme:** `tesserae refresh` existiert als Code (kein Skill) und
  reproduziert die Markdown-Kette byte-für-byte auf dem Demo-Korpus.
- **Risiko:** Niedrig. Reiner Refactor; bestehende Tests schützen das Verhalten.
- **Geschlossene Audit-Befunde:** „refresh lebt in einem Skill",
  „cli-Gott-Dispatcher".

## Phase 1 — Supervisor-Daemon (die Engine-Schleife) ✅ Ausgeliefert in v0.5.0

> **Ausgeliefert in v0.5.0** — `tesserae/engine/daemon.py` (Engine-Rückgrat, Phasen 1–3).

**Ziel:** Ein überwachter langlaufender Prozess, der eine Ereignisschleife
besitzt und `Pipeline` auf Trigger antreibt, mit echter Lebenszyklusbehandlung.

- **Warum jetzt:** Das ist das Rückgrat. Die größte einzelne Lücke des Audits.
  Alles „Kontinuierliche/Autonome" hängt daran.
- **Umfang:** Neues `tesserae/engine/daemon.py` — Ereignisschleife,
  Trigger-Queue, Debounce/Koaleszenz, anmutiges Herunterfahren via
  `SIGTERM`/`SIGINT`, pidfile, strukturiertes Logging. CLI-Einstiegspunkt
  `tesserae engine` / `tesserae daemon`. Den nackten `KeyboardInterrupt`-Tod in
  `watch.py`/`vault_watch.py` ersetzen, indem man sie als *Triggerquellen*
  einfaltet, die die Queue speisen.
- **Lieferungen:** Daemon, der unbegrenzt läuft, Bursts in einen
  Pipeline-Lauf koalesziert, sauber herunterfährt; launchd/systemd-Beispiel-Unit.
- **Abnahme:** Eine Quelldatei editieren → der Daemon koalesziert und führt
  innerhalb des Debounce-Fensters einen `compile(changed_only)` aus; `SIGTERM`
  beendet mit 0 ohne verwaiste Threads; überlebt eine Pipeline-Ausnahme, ohne zu
  sterben.
- **Risiko:** Mittel — Nebenläufigkeits-/Herunterfahr-Korrektheit. Mit einem
  einthreadigen asyncio-Kern + expliziter Task-Überwachung mildern.
- **Geschlossene Audit-Befunde:** „kein Daemon", „kontinuierlich = sleep-Poller",
  Beobachter-`KeyboardInterrupt`-Tod, keine Signalbehandlung.

## Phase 2 — Live-Sitzungsmonitor (Säule 1) ✅ Ausgeliefert in v0.5.0

> **Ausgeliefert in v0.5.0** — `tesserae/engine/session_tail.py` (Engine-Rückgrat, Phasen 1–3).

**Ziel:** Live-Harness-Transkripte tailen und Züge während laufender Sitzungen
aufnehmen, das nachträgliche `sessions discover --import` ersetzend.

- **Warum jetzt:** Braucht die Schleife von P1 zur Speisung. Liefert die Säule
  „Sitzungsüberwachung".
- **Umfang:** Neue Sitzungs-Tail-Triggerquelle (`~/.claude` / `~/.codex`
  JSONL-Anhänge-Ereignisse beobachten) → einreihen. Zuggranulare inkrementelle
  Extraktion in `session_graph*.py`, damit ein neuer Zug nicht den gesamten
  Sitzungs-Cache invalidiert. Indizierter/anhängender Speicher für
  `harness_sessions` (das Vollscan-Glob in Rente schicken).
- **Lieferungen:** Der Daemon nimmt die Züge einer Sitzung innerhalb von
  Sekunden nach dem Schreiben auf; `test_session_tailer.py`.
- **Abnahme:** Eine Live-Agentensitzung in einem überwachten Projekt starten →
  neue Befunde erscheinen im Graph ohne manuellen Befehl; zuggranulare
  Cache-Trefferquote gemessen > Neuextraktion der ganzen Sitzung.
- **Risiko:** Mittel — JSONL-Formate unterscheiden sich zwischen Harnesses;
  Teilzeilen-Lesungen.
- **Geschlossene Audit-Befunde:** nachträglicher Sitzungs-Scan,
  Ganzsitzungs-Cache-Invalidierung, flacher Glob-Speicher.

## Phase 3 — Inkrementelle/Streaming-Kompilierung durch den GraphStore-Port ⚙️ Infrastruktur ausgeliefert in v0.5.0 (Flag OFF/experimentell)

> **Infrastruktur ausgeliefert in v0.5.0** (Provenienz-Sidecar, GraphStore-Lösch-Oberfläche, persistente url_resolver-Async-Laufzeit), aber das `incremental_compile`-Flag **bleibt OFF/experimentell** wegen Multi-Owner-/Producer-Lifecycle-/Cap-Fallback-Lücken. Byte-Parität für die abgedeckten Pfade ist bewiesen. v0.5.0 hat zudem zwei echte Compile-Bugs behoben, die hier auftauchten: changed-only-Idempotenz und der Injected-Store-Vertrag.

**Ziel:** Den brüchigen `changed_only`-Graph-Räumungs-Patch durch eine
entworfene inkrementelle Schicht ersetzen, die durch `ports/graph_store.py`
fließt.

- **Warum jetzt:** Kontinuierliche Aufnahme (P2) macht den aktuellen
  reload-strip-evict-merge-Behelf zu einer Korrektheitsschuld (die
  dokumentierte „2400→1700-Knoten"-Falle). Selbstverbesserung (P4) braucht
  knotenweise Upserts.
- **Umfang:** Die eigenständige Pipeline durch `GraphStore` fließen lassen
  (heute umgeht sie die Ports direkt zu JSON). Knotenweises Upsert/Delete mit
  Herkunft + Frische-Zeitstempeln. Persistenz auf eine Wahrheitsquelle
  konvergieren (Audit: JSON-Artefakt vs SQLite vs Kuzu). Das
  `asyncio.run`-pro-Aufruf in `url_resolver.py` beheben (persistente
  Async-Laufzeit).
- **Lieferungen:** Inkrementelle Kompilierung, die nur geänderte Knoten korrekt
  hinzufügt/aktualisiert/entfernt; knotenweises
  `first_seen_at`/`last_updated_at`.
- **Abnahme:** Eine 21-Dateien-Bearbeitung aktualisiert genau die betroffenen
  Knoten (kein Kollaps); byte-identische Vollkompilierungs-Parität als
  Golden-Test erhalten.
- **Risiko:** Hoch — berührt das Kerndatenmodell. Hinter einem Feature-Flag
  verriegeln; gegen die Vollkompilierungsausgabe diffen, bis vertraut.
- **Geschlossene Audit-Befunde:** brüchiges `changed_only`, umgangene Ports,
  asyncio pro Aufruf, drei Persistenzformate, keine knotenweise Frische.

## Phase 4 — Selbstverbesserung aktivieren & persistieren (Säule: Selbstverbesserung) ✅ Ausgeliefert in v0.5.0

> **Ausgeliefert in v0.5.0** über das `node_memory`-Sidecar (`tesserae/memory/`): standardmäßig aktives **Supersede** mit deterministischem Verdikt und nachgelagerter Unterdrückung sowie die in der Ausgabe gezeigte **numerische Wiederholungs-Konfidenz** (sitzungsübergreifende Häufigkeit → `TemporalFactProjector`).

**Ziel:** Die Wissensbasis soll tatsächlich an Ort und Stelle, standardmäßig
eingeschaltet, zur Kompilierzeit persistiert, evolvieren.

- **Warum jetzt:** Hängt von den knotenweisen Upserts von P3 ab. Schließt den am
  wenigsten getesteten Querschnitt.
- **Umfang:** **Zerfalls**-Scores bei der Kompilierung persistieren
  (`memory/decay.py` nicht mehr nur zur Abfragezeit); `access_count`/
  `last_accessed_at` bei MCP-Lesungen inkrementieren. Standardmäßig
  eingeschaltetes **Supersede** mit nachgelagerter *Unterdrückung* veralteten
  Inhalts (nicht nur Kantenanhang). **Widerspruchsauflösung** hinzufügen (die
  Detektion von `lint.py` zu einem konfidenz-geschlichteten Durchlauf erheben).
  **Verstärkung wiederkehrender Erkenntnisse** (sitzungsübergreifende Häufigkeit
  → numerische Konfidenz). Den Anwendungspfad der **Schema-Drift** und die
  **Feedback-Anleitung** in die Extraktion verdrahten (der deterministische Pfad
  ignoriert sie heute). Embedding-basierte Supersede-Kandidatengenerierung (den
  lexikalischen Jaccard in Rente schicken).
- **Lieferungen:** Jeder Durchlauf läuft in der Standard-Pipeline und schreibt
  zurück; eine neue `tests/`-Suite, die
  decay/supersede/feedback/drift/contradiction abdeckt.
- **Abnahme:** Eine sitzungsübergreifende Wiederholung eines Fakts hebt seine
  Konfidenz; ein ersetzter Fakt erscheint nicht mehr in der Kontextausgabe;
  Zerfalls-Scores persistieren und verschieben sich zwischen Läufen; die
  Selbstverbesserungs-Suite ist grün (derzeit null Tests).
- **Risiko:** Mittel — Verhaltensänderungen der Extraktionsausgabe; mit
  Golden-Fixtures schützen.
- **Geschlossene Audit-Befunde:** die gesamte Tabelle der Säule 2.

## Phase 5 — Echte Standard-Embeddings (Grundlage von Spur B) ✅ Ausgeliefert in v0.5.0

> **Ausgeliefert in v0.5.0** (Spur B): ein echtes Standard-`Model2VecBackend`, ein **laut fehlschlagendes** `active_embedding_backend` (kein stilles blake2b-Downgrade), das in `embedding_status` gezeigte Semantik-Backend-Flag und eine Kosinus-Untergrenze, die der Embedding-Spur erlaubt, Kandidaten aufzunehmen.

**Ziel:** Aufhören, ein deterministisches Hash-Bucket-Pseudo-Embedding als
Standard-„Semantik"-Spur auszuliefern.

- **Warum jetzt:** Der Kontext-Compiler von P6 ist nur so gut wie die Abrufung.
  Unabhängig vom Daemon — kann beginnen, sobald P0 gelandet ist.
- **Umfang:** Ein echtes Standard-Embedding-Backend ausliefern (oder `auto` laut
  scheitern lassen, statt in `retrieval/hybrid.py` still auf blake2b
  herabzustufen). Die Embedding-Spur Kandidaten generieren lassen (nicht nur
  neu ranken), sobald die Embeddings echt sind.
- **Lieferungen:** Die Standardinstallation liefert echte semantische Abrufung
  oder eine explizite, sichtbare „läuft auf Hash-Stub"-Warnung.
- **Abnahme:** Paraphrase-/Synonym-Abfragen fördern relevante Knoten zutage, die
  BM25 verpasst; Abrufqualität auf einem kleinen gelabelten Set gegen die
  Hash-Baseline gemessen.
- **Risiko:** Mittel — Abhängigkeitsgewicht / Offline-Installation. Eine
  gestufte Voreinstellung anbieten.
- **Geschlossene Audit-Befunde:** Hash-Bucket-Voreinstellung, Kandidatentor der
  Embedding-Spur.

## Phase 6 — Kontext-Compiler auf Abruf (Säule 3) ✅ Ausgeliefert in v0.5.0 (Hauptfeature)

> **In v0.5.0 als Hauptfeature ausgeliefert.** Die reine `compile_context`-Pipeline in `tesserae/context_compiler.py` liefert ein In-Memory-`ContextBundle` aus `ContextCitation`s (Query/Seeds → PPR + Hybridsuche → tiefenbegrenzte k-Hop-Nachbarschaft → Wiki-Body-Zusammenbau → optionale LLM-Synthese mit anmutigem Fallback → Budgetkontrolle). Bereitgestellt als MCP-Tool `compile_context` und CLI-Subbefehl `tesserae context`; `node_context` hat nun einen gerankten `use_ppr`-Pfad; themenbezogene `llms.txt`-Export-Slices werden ausgeliefert.

**Ziel:** Das Aushängeschild — „gib mir Kontext zu X" → ein maßgeschneidertes,
zitiertes, agentenfertiges Dokument.

- **Warum jetzt:** Hängt von P5 (Abrufqualität) ab. Profitiert von P4 (sauberere
  Basis). Das zentrale Wertversprechen des Produkts.
- **Umfang:** Neues `tesserae/context_compiler.py`: Abfrage/Saaten → PPR +
  hybride Suche → rangierter k-Hop-Nachbarschaftsdurchlauf → Wiki-Körper
  zusammenbauen → optionale LLM-Synthese → ein begrenztes Markdown-Dokument mit
  Zitaten + Budgetkontrolle. Als MCP `compile_context(query|seeds, depth,
  budget)` und CLI `tesserae context …` bereitstellen. `agent_harness`
  themenbegrenzt machen; `node_context` über PPR routen; themenbegrenzte
  `llms.txt`-Export-Scheiben.
- **Lieferungen:** Ein Tool, das ein herunterladbares, zitiertes Kontextbündel
  für jede Abfrage liefert; Tests, die die Bündelform + Zitationsintegrität
  behaupten.
- **Abnahme:** `compile_context("X")` liefert ein kohärentes
  Mehrknoten-Dokument, dessen Zitate alle auflösen; der Harness-Briefing
  regeneriert sich pro Thema statt fest verdrahteter Top-12.
- **Risiko:** Mittel — Synthesequalität; einen deterministischen
  Nicht-LLM-Zusammenbaumodus beibehalten.
- **Geschlossene Audit-Befunde:** „Dokumenterzeugung auf Abruf existiert nicht",
  abfragebegrenzte Synthese, statischer Harness, unrangiertes `node_context`,
  Gesamtkorpus-Exporte.

## Phase 7 — serve + watch + deploy + Lebenszyklustests vereinheitlichen ✅ Ausgeliefert in v0.40.0 (Veröffentlichung bleibt manuell)

**Ziel:** Ein überwachter Prozess bedient die Seite, kompiliert bei Änderung neu
und veröffentlicht kontinuierlich; die Lebenszyklusschicht erhält Testabdeckung.

> **Ausgeliefert in v0.40.0, mit einer Kugel absichtlich durchgestrichen.** `tesserae engine --serve` bedient `.tesserae/site` vom Daemon-Prozess auf seinem eigenen Thread, der Site-Build wurde zu einem atomaren Verzeichnistausch, sodass eine bediente Seite während einer Neukompilierung nie 404 zeigt, `frontend.py` ist gelöscht, und der Daemon wuchs der Stopp-Primitive, die ein blockierender Server braucht. **Kontinuierliche Veröffentlichung wurde abgeschnitten**, nicht aufgeschoben: `deploy.py` verweigert einen unsauberen Baum, fabriziert einen leeren Commit pro Lauf und mutiert das eigene `.git` des Nutzers mit `worktree add`; und `gh-pages` ist weltlesbar, während nichts in Tesserae Geheimnisse löscht. Veröffentlichung bleibt ein bewusstes `tesserae export site --deploy` oder CI bei Push.

- **Warum jetzt:** Konvergenz. Braucht P1 (Daemon) und die Ausgabeseite (P6),
  damit es sich lohnt zu dienen.
- **Umfang, wie ausgeliefert:** `serve.py` wird vom Daemon wiederverwendet, nicht
  neu geschrieben — derselbe Handler, dieselbe Pfad-Eindämmung, jetzt auf einem
  `ThreadingHTTPServer` (das eigenständige `tesserae serve` war einreihig, sodass
  ein LLM `/api/ask` jedes statische Gut blockierte). Der Daemon erhielt eine
  nähere Stoppsliste: `serve_forever` kontrolliert nie ein Stop-Ereignis erneut,
  sodass allein ein Ereignis ihn nicht stoppen kann. `build_site` schreibt auf
  einen Staging-Geschwister und tauscht es mit der Live-Site in einem Syscall
  aus (`renamex_np` auf macOS, `renameat2` auf Linux), fällt aber auf zwei
  Umbenennungen zurück. Der Anwendungspfad der Review-Schleife existiert bereits
  (`--apply-review-decisions`).
- **Lieferungen:** `tesserae engine --serve [--serve-host] [--serve-port]`;
  `tests/test_site_swap.py` (ein Leser hämmert den bediente Index während drei
  Rebuilds und muss null Misses sehen), serve-source Lebenszyklustests, ein
  Deploy-Test, dass ein fehlgeschlagener Push das Repo nicht mehr festlegt; toter
  Code entfernt.
- **Abnahme:** Eine Quellbearbeitung pflanzt sich ohne manuelle Befehle zu einer
  live bedienten Seite fort und die Seite ist nie vermisst; Lebenszyklustests grün.
- **Nicht getan:** `--publish`. Eine Flotte (`engine --all`) bedient nie; nutze
  `tesserae serve --all` daneben.
- **Geschlossene Audit-Befunde:** serve/watch-Aufspaltung, veraltetes `frontend.py`,
  Review-Schleifen-Stub, fehlende Lebenszyklustests. Manueller Deploy bleibt durch
  Entscheidung.

---

## Sequenzierungs-Zusammenfassung

| Phase | Thema | Hängt ab von | Parallelisierbar mit  Status |
|---|---|---|------|
| P0 | Pipeline-Orchestrator | — | —  ✅ Ausgeliefert in v0.5.0 |
| P1 | Supervisor-Daemon | P0 | —  ✅ Ausgeliefert in v0.5.0 |
| P2 | Live-Sitzungsmonitor | P1 | P5  ✅ Ausgeliefert in v0.5.0 |
| P3 | Inkrementelle Kompilierung | P1 | P5  ✅ Ausgeliefert in v0.5.0, Standard AN seit v0.40.0 |
| P4 | Persistenz der Selbstverbesserung | P3 | P5, P6  ✅ Ausgeliefert in v0.5.0 |
| P5 | Echte Embeddings | P0 | P2, P3, P4  ✅ Ausgeliefert in v0.5.0 |
| P6 | Kontext-Compiler auf Abruf | P5 | P2, P3, P4  ✅ Ausgeliefert in v0.5.0 |
| P7 | serve/watch/deploy vereinheitlichen | P1, P6 | —  ✅ Ausgeliefert in v0.40.0 (Veröffentlichung manuell) |

**Minimal funktionsfähige Engine:** P0 + P1 + P2 + P3 — ein laufender Daemon,
der Live-Sitzungen beobachtet und inkrementell kompiliert. **Differenziertes
Produkt:** P5 + P6 hinzufügen (Agentenkontext auf Abruf). **Poliert:** P4 + P7.
