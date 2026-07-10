# Tägliche Session-Chunks — `.tesserae/session_chunks.db`

<!-- translations:start -->
<p align="center"><a href="../session-chunks.md">English</a> · <a href="session-chunks.ko.md">한국어</a> · <a href="session-chunks.zh.md">中文</a> · <a href="session-chunks.ja.md">日本語</a> · <a href="session-chunks.ru.md">Русский</a> · <a href="session-chunks.es.md">Español</a> · <a href="session-chunks.fr.md">Français</a></p>
<!-- translations:end -->
Fensterbasierte Session-Abfragen — `tesserae summary`, `tesserae decisions` und die
Activity-Aktionen des `ask`-Planners — parsten früher bei jedem Aufruf jedes
Claude-Code-/Codex-Transkript im Fenster neu. Der tägliche Chunk-Store persistiert
jeden normalisierten Turn **einmal**, gebuckelt nach KST-Tageslabel, sodass ein
vollständig abgedeckter vergangener Tag aus SQLite bedient wird statt aus einem
Roh-Rescan. Gemessen an einem realen Korpus mit mehreren tausend Sessions macht das
fensterbasierte Summaries **~20x schneller**.

Der Store ist eine einzige SQLite-Datei, `.tesserae/session_chunks.db` (WAL,
kurzlebige Verbindung pro Operation): eine `turns`-Tabelle mit Tages-Index, eine
`day_coverage`-Tabelle, die festhält, welche `(day, harness)`-Paare vollständig sind,
und eine `meta`-Tabelle mit der Schema-Version.

## Was ihn schreibt

1. **Live — der Engine-Tailer.** Während `tesserae engine` läuft, hängt der
   Session-Tailer Turns an den Store an, während er sie tailt, pro Poll, und
   upsertet die Coverage für die betroffenen Tage (`source: "tailer"`). Der
   Schreibpfad ist append-only, idempotent gegenüber erneut zugestellten Turns und
   wirft nie in die Daemon-Schleife hinein. Es gibt bewusst **keinen
   SessionEnd-Hook-Writer** — im Hintergrund gestartete SessionEnd-Writer stauen
   sich auf (ein dokumentierter Fehlermodus).
2. **Backfill.** Zwei Einstiegspunkte laufen über existierende Transkripte und
   füllen die Historie (`source: "backfill"`):
   - `tesserae refresh` führt als Teil seines Sessions-Import-Schritts automatisch
     einen Backfill aus, sodass der erste Refresh nach einem Upgrade den Store ohne
     zusätzliche Aktion befüllt.
   - `tesserae sessions chunk-backfill [--since YYYY-MM-DD]` führt ihn explizit
     aus; `--since` begrenzt, wie weit zurück gelaufen wird (Default: gesamte
     Historie).

   Backfill nimmt ein **nicht-blockierendes** flock auf
   `.tesserae/session_chunks.lock` mit Skip-if-held-Semantik — ein nebenläufiger
   Backfill (oder eine Engine, die es bereits hält) lässt den zweiten Aufrufer
   sauber überspringen statt sich anzustellen. Backfill-Upserts sind auf
   `(session_path, ts, role, hash(text))` geschlüsselt, sodass Tailer-Zeilen und
   Backfill-Zeilen einander nie duplizieren. Ein Ein-Tages-Overlap bei
   inkrementellen Backfills heilt Turns, die erst landeten, nachdem die Coverage
   eines Tages erstmals beansprucht wurde.

## Was ihn liest

Der schnelle Pfad lebt am einzigen Scan-Engpass
(`activity_summary.iter_project_transcripts` / `scan_messages`), sodass alles
Nachgelagerte ihn transparent erbt:

- `tesserae summary` (einschließlich seiner eingebetteten Decisions-Sammlung)
- `tesserae decisions`
- `tesserae ask` — die `activity_summary`- / `decisions`-Aktionen des Planners
- MCP `activity_summary` und `query_decisions`
- die Live-Sessions-Ansicht

## Coverage-Regel: Heute wird immer roh gescannt

Ein Fenster wird nur dann aus Chunks bedient, wenn **alle** folgenden Bedingungen
gelten:

1. es ist ein exakt KST-ausgerichteter einzelner Tag;
2. dieser Tag liegt **strikt vor heute** — heute wird noch geschrieben, also nimmt
   er immer den rohen Transkript-Scan;
3. für **jeden** angefragten Harness an diesem Tag existiert eine
   `day_coverage`-Zeile.

Alles andere fällt für dieses Fenster auf den Roh-Scan zurück.

## Die Roh-Scan-Fallback-Garantie

Der Chunk-Store ist ein Beschleuniger, niemals eine Quelle der Wahrheit:

- Jeder DB-Fehler, eine fehlende/korrupte Datei oder ein
  `schema_version`-Mismatch liefert **nichts** aus dem Chunk-Pfad — der rohe
  Transkript-Scan des Aufrufers läuft exakt wie zuvor. Ein Schema-Mismatch
  verwirft den Store und baut ihn leer neu auf; die Coverage verschwindet mit ihm,
  also bleibt der Fallback korrekt.
- Tage ohne Coverage (zum Beispiel: die Engine lief nicht und es gab keinen
  Backfill) nehmen stillschweigend den langsamen Pfad. Korrekt, aber der Speedup
  verschwindet — `tesserae doctor` meldet Coverage-Lücken im jüngsten Fenster und
  verweist auf `tesserae sessions chunk-backfill` (siehe
  [doctor.md](doctor.de.md)).
- **Paritäts-Invariante:** Für einen vollständig abgedeckten Tag sind die aus
  Chunks bedienten Turns gleich dem, was der Roh-Scan produziert hätte (gleicher
  Timestamp, Role, Name, Text, Session-Key und Harness).

## Betriebsnotizen

- Lass `tesserae engine` laufen und vergangene Tage bleiben live abgedeckt;
  andernfalls schließt ein gelegentliches `tesserae refresh` (oder ein explizites
  `chunk-backfill`) die Lücken.
- Der Store ist pro Projekt, lebt unter `.tesserae/` und kann jederzeit gefahrlos
  gelöscht werden — der nächste Backfill baut ihn neu auf, und Leser fallen in der
  Zwischenzeit auf Roh-Scans zurück.
