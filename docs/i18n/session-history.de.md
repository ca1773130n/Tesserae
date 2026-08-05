# Harness-Session-Historie

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a></p>
<!-- translations:end -->
Tesserae kann lokale KI-Agenten-Transkripte importieren und als Projektgedächtnis unter dem `sessions/`-Bereich der statischen Site rendern.

Dieses Feature ist bewusst von `export harness` getrennt:

- `export harness` ist ausgehender Kontext für Tools wie Claude Code, Codex, Gemini, Cursor, Kiro und OpenCode.
- `sessions ...` ist eingehende Historie: Es normalisiert frühere Claude-Code-/Codex-Sessions für das aktuelle Projekt, speichert sie unter `.tesserae/harness_sessions/` und lässt `export site` Session-Index-/Detailseiten publizieren.

## Zwei Wege hinein: Batch-Import und Live-Monitoring

Session-Ingestion ist nicht mehr nur Batch. Es gibt zwei Pfade in denselben
normalisierten Store:

- **Batch-Import** — `sessions discover/import` scannt Transkript-Roots
  auf Anfrage und schreibt einmalig. Diese Seite dokumentiert diesen Flow unten.
- **Live-Monitoring** — der Supervisor-Daemon (`tesserae engine`) betreibt einen
  `SessionTailer`, der die Claude-Code- und Codex-Transkripte *dieses Projekts
  selbst* beobachtet und neue Turns ingestet, sobald sie landen. Jeder
  Tick springt zu einem persistierten Byte-Offset pro Datei, liest nur die neuen Bytes
  und speichert vollständige Turns in die SQLite `HarnessSessionsDB`
  (`.tesserae/sqlite.db`), **bevor** ein debouncter Recompile eingereiht wird, sodass der
  Compile immer konsistenten Zustand liest. Der Tailer ist auf die eigenen Sessions
  des Projekts begrenzt (Claude `projects/<slug>/*.jsonl`; Codex nach cwd gefiltert) und
  setzt nach einem Neustart bei den gespeicherten Offsets fort, ohne Turns erneut abzuspielen.

Starte die Live-Schleife mit:

```bash
tesserae engine        # watch sources, coalesce bursts, auto-recompile
tesserae engine --once # single drain cycle then exit (deterministic)
```

`tesserae refresh` führt dieselbe Ingest- → Compile- → Projekt-Pipeline
einmal in-process aus, ohne den langlebigen Watcher zu starten (übergib
`--no-sessions`, um den Harness-Session-Discovery-Scan zu überspringen).

## Privacy-Modell

Beide Ingestion-Pfade sind explizit: Der Live-Tailer läuft nur, solange du
`tesserae engine` am Leben hältst, und Batch-Discovery schreibt nur mit
`--import`. Ein normales `tesserae compile` oder `tesserae export site` liest
bereits normalisierte Sessions aus `.tesserae/harness_sessions/` und die Live-Records
in `.tesserae/sqlite.db`, aber es scrapt nicht von sich aus überraschend private
Harness-Transkript-Verzeichnisse.

Importierte Session-Records sind lokale Projekt-Artefakte. Prüfe sie, bevor du eine öffentliche Site publizierst, besonders wenn deine Transkripte Secrets, private Pfade, Kundendaten oder unveröffentlichten Code enthalten könnten.

## Lokale Sessions discovern und importieren

Vom Projekt-Root:

```bash
tesserae sessions discover --import
```

Die Discovery scannt lokale Claude-Code- und Codex-Transkript-Roots, die zum aktuellen Projekt-Arbeitsverzeichnis gehören. Nutze `--root`, um ein bestimmtes Config-Verzeichnis zu scannen, und wiederhole `--harness`, um die Discovery einzugrenzen:

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

Ohne `--import` gibt die Discovery aus, was sie gefunden hat, ohne normalisierte Session-Records zu schreiben.

## Normalisiertes JSON direkt importieren

Wenn ein anderes Tool bereits normalisiertes `HarnessSession`-JSON produziert hat, importiere eine Datei oder eine Liste von Dateien:

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

Jeder Input darf ein Session-Objekt oder eine Liste von Session-Objekten enthalten.

## Wie der Store geschrieben wird

Jedes Record in `.tesserae/harness_sessions/` trägt einen **Produzent (`producer`)** — den Importer, der es geschrieben hat. `sessions discover --import` stempelt `tesserae:discover`; `sessions import <path>` stempelt `tesserae:import`. **Ein Writer darf nur Records anfassen, die er produziert hat**: er gibt nur seine eigenen frei, und er wird das Record eines anderen Produzenten für dieselbe Session nicht überschreiben — der eingehende Write wird übersprungen und als `Left alone (written by another producer)` gemeldet.

Diese Regel existiert, weil Provenienz das einzige ist, das wirklich Importer unterscheidet. Zwei von ihnen beschreiben routinemäßig die *gleiche* Session: Tesseraes lokaler Scan prägt ein einfaches Record aus einem Transkript unter `~/.claude`, während ein Orchestrator diese gleiche Session mit der Agent-Identität exportiert, die nur er kennt. Beide leiten denselben Dateinamen von der Session-Id ab, also kollidieren sie. Weder die Transkript-Location noch der Harness-Name können sie unterscheiden — deshalb funktionierten die früheren Root-scoped Fixes für [#104](https://github.com/ca1773130n/Tesserae/issues/104) nicht, und deshalb verlor 0.28.6 solche Records immer noch auf zwei Arten: gelöscht, wenn der Scan das Transkript nicht mehr fand, stillschweigend überschrieben, wenn er es tat.

Wenn du mit deinem eigenen Tool in diesen Store schreibst, verwende `tesserae sessions import <file>` und deine Records sind ab diesem Punkt geschützt. Nichts sonst ist erforderlich.

Der Geltungsbereich verengt sich weiter, als zweites Gate: ein Record wird nur gelöscht, wenn sein Transkript auch unter einer Root lebt, die dieser Run gescannt hat, und sein Harness einer war, den er gescannt hat. Also lässt `--harness codex` claude-code Records in Ruhe, selbst wenn `~/.claude` durchsucht wurde.

Drei Verhaltensweisen, die es zu wissen gilt:

- **Records, die vor 0.28.7 geschrieben wurden, tragen keinen Produzent.** Sie sind unowned, also kein Importer gibt sie frei oder überschreibt sie — sicher, aber Discovery wird sie auch nicht auffrischen. `sessions discover --import --adopt-unowned` beansprucht sie für Discovery. Führe es einmal aus, wenn Tesseraes eigener Scan das einzige ist, das in diesen Store schreibt; führe es *nicht* aus, wenn auch ein anderes Tool hier schreibt, denn es übergibt deine Records an Discovery.
- Eine leere Discovery bereinigt nie. Ein Scan, der nichts findet — falsche `HOME`, gelöste Harness-Roots — fusioniert stattdessen.
- Eine Discovery, die Records entfernt oder behält, gibt beide Zählungen neben der Importanzahl aus, sodass der Store nicht innerhalb einer Zeile, die nur Wachstum meldet, an Größe verlieren kann.

## Importierte Sessions auflisten

```bash
tesserae sessions list
```

Sessions werden abgelegt unter:

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

Live-überwachte Sessions werden zusätzlich in der SQLite
`HarnessSessionsDB` (`.tesserae/sqlite.db`) getrackt, die auch die Lese-Offsets
pro Datei persistiert, bei denen der Tailer fortsetzt. `tesserae sessions list` berichtet die
kombinierte Sicht.

## Die statischen Session-Seiten bauen

Nach dem Import von Sessions baue die Site neu:

```bash
tesserae export site
```

Die Site emittiert:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

Die generierte Site verlinkt Sessions von der globalen Leiste, den Home-Browse-Karten, den Sucheinträgen und dem Breadcrumb-Pfad jeder Session-Detailseite.

## Schnelle Transkript-Suche (memex)

Wenn du die Site mit `tesserae serve` servst, erhält das **Sessions-Dashboard** ein
Volltext-Suchfeld über jedes indexierte Claude-/Codex-Transkript, gestützt auf
[`nicosuave/memex`](https://github.com/nicosuave/memex) (BM25). Ergebnisse zeigen
`project · role · date · score` plus einen passenden Snippet.

```bash
cargo install --git https://github.com/nicosuave/memex --locked   # or: tesserae config deps --install memex
memex index                                                        # build the index once
tesserae serve                                                     # search box appears on /sessions
```

Es ist **optional und graceful**: Ohne `memex`-Binary (oder Index) zeigt das Feld
eine klare, handlungsleitende Meldung und der Rest des Dashboards bleibt unberührt. Der
Such-Endpunkt (`GET /api/transcript-search`) ist auf Same-Origin-/Loopback-Aufrufer
begrenzt, damit eine besuchte Webseite deine lokale Historie nicht ausspähen kann.

## Layout der Session-Detailseite

Session-Detailseiten nutzen die geteilte Static-Site-Shell statt eines eigenständigen Transkript-Dumps. Sie enthalten:

- Hero und Stat-Leiste;
- eine High-Level-Zusammenfassung;
- Timeline- und Größen-Metadaten;
- Decisions, Files, Commands, Tools und Errors, wenn vorhanden;
- einen eingeklappten Subagent-Baum;
- die Turn-für-Turn-User-/Assistant-Konversation;
- eingeklappte Tool-Use-Blöcke, angehängt unter dem vorangehenden Assistant-Turn;
- eine linke Konversationsleiste, die auf `#turn-N`-Anker verlinkt.

Konversations-Markdown wird durch den Site-Markdown-Renderer gerendert. Semantische Oberflächen wie Inline-Code, explizites Command-/Tag-Markup, Pfade, Dateinamen und Hashtags werden als kompakte Chips dekoriert; zufällig großgeschriebene Substantive werden nicht auto-gechippt.

Aktuelle Transkript-Typografie:

| Oberfläche | Selektor | Größe |
|---|---|---|
| Konversations-Markdown-Prosa | `.session-turn-text`, Prosa-Kinder | `8px` |
| Generische Konversations-Code-Fences | `.session-turn-text pre` | `10px` |
| Bash-/Shell-fenced Code-Inhalt | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool-Details/Summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-Use-Header | `.session-tool-use-header` | `8px` |
| Tool-Payload-Text | `.session-tool-use-text` | `6px` |

## Publishing-Checkliste für Sessions

Vor dem Deploy einer öffentlichen Site, die Sessions enthält:

1. Führe `tesserae sessions list` aus und bestätige, dass die Anzahl erwartet ist.
2. Inspiziere `.tesserae/harness_sessions/` auf sensiblen Inhalt.
3. Baue mit `tesserae export site` neu.
4. Öffne `sessions/index.html` und mindestens eine Session-Detailseite lokal.
5. Bestätige, dass Tool-Blöcke standardmäßig eingeklappt sind und rohe Tool-Payloads publizierbar sind.
6. Deploye mit `tesserae export site --deploy`, sobald der Source-Tree committet ist.
