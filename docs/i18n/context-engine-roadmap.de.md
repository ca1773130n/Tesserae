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

## Phase 0 — Pipeline-Orchestrator (risikomindernde Grundlage)

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
- **Abnahme:** `tesserae project refresh` existiert als Code (kein Skill) und
  reproduziert die Markdown-Kette byte-für-byte auf dem Demo-Korpus.
- **Risiko:** Niedrig. Reiner Refactor; bestehende Tests schützen das Verhalten.
- **Geschlossene Audit-Befunde:** „refresh lebt in einem Skill",
  „cli-Gott-Dispatcher".

## Phase 1 — Supervisor-Daemon (die Engine-Schleife)

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

## Phase 2 — Live-Sitzungsmonitor (Säule 1)

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

## Phase 3 — Inkrementelle/Streaming-Kompilierung durch den GraphStore-Port

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

## Phase 4 — Selbstverbesserung aktivieren & persistieren (Säule: Selbstverbesserung)

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

## Phase 5 — Echte Standard-Embeddings (Grundlage von Spur B)

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

## Phase 6 — Kontext-Compiler auf Abruf (Säule 3)

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
  Mehrknoten-Dokument, dessen Zitate alle auflösen; der Harness-Brief
  regeneriert sich pro Thema statt fest verdrahteter Top-12.
- **Risiko:** Mittel — Synthesequalität; einen deterministischen
  Nicht-LLM-Zusammenbaumodus beibehalten.
- **Geschlossene Audit-Befunde:** „Dokumenterzeugung auf Abruf existiert nicht",
  abfragebegrenzte Synthese, statischer Harness, unrangiertes `node_context`,
  Gesamtkorpus-Exporte.

## Phase 7 — serve + watch + deploy + Lebenszyklustests vereinheitlichen

**Ziel:** Ein überwachter Prozess bedient die Seite, kompiliert bei Änderung neu
und veröffentlicht kontinuierlich; die Lebenszyklusschicht erhält Testabdeckung.

- **Warum jetzt:** Konvergenz. Braucht P1 (Daemon) und die Ausgabeseite (P6),
  damit es sich lohnt, kontinuierlich zu veröffentlichen.
- **Umfang:** `serve.py` (blockierender `TCPServer`) und `deploy.py` (manueller
  git push) in den Daemon einfalten, damit serve + watch + publish einen
  Supervisor teilen. Kontinuierliche/debouncte Veröffentlichung. Die fehlenden
  `test_watch`/`test_serve`/Daemon-Lebenszyklustests hinzufügen. Das veraltete
  tote Modul `frontend.py` löschen. Die stringtypisierte TODO-Schleife von
  `review_workflow.py` in einen echten Anwendungspfad verdrahten.
- **Lieferungen:** `tesserae engine --serve --publish` fährt die volle Schleife;
  Lebenszyklus-Testsuite; toter Code entfernt.
- **Abnahme:** Eine Quellbearbeitung pflanzt sich ohne manuelle Befehle zu einer
  live bedienten Seite und (optional) einem veröffentlichten Deploy fort;
  Lebenszyklustests grün.
- **Risiko:** Niedrig–Mittel — überwiegend Integration.
- **Geschlossene Audit-Befunde:** serve/watch/deploy-Aufspaltung, manueller
  Deploy, veraltetes `frontend.py`, Review-Schleifen-Stub, fehlende
  Lebenszyklustests.

---

## Sequenzierungs-Zusammenfassung

| Phase | Thema | Hängt ab von | Parallelisierbar mit |
|---|---|---|---|
| P0 | Pipeline-Orchestrator | — | — |
| P1 | Supervisor-Daemon | P0 | — |
| P2 | Live-Sitzungsmonitor | P1 | P5 |
| P3 | Inkrementelle Kompilierung | P1 | P5 |
| P4 | Persistenz der Selbstverbesserung | P3 | P5, P6 |
| P5 | Echte Embeddings | P0 | P2, P3, P4 |
| P6 | Kontext-Compiler auf Abruf | P5 | P2, P3, P4 |
| P7 | serve/watch/deploy vereinheitlichen | P1, P6 | — |

**Minimal funktionsfähige Engine:** P0 + P1 + P2 + P3 — ein laufender Daemon,
der Live-Sitzungen beobachtet und inkrementell kompiliert. **Differenziertes
Produkt:** P5 + P6 hinzufügen (Agentenkontext auf Abruf). **Poliert:** P4 + P7.
