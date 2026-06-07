# Harness-Session-Historie

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a></p>
<!-- translations:end -->
Tesserae kann lokale AI-Agent-Transkripte importieren und sie als Projektgedächtnis unter dem `sessions/`-Bereich der statischen Site rendern.

Dieses Feature ist absichtlich von `export harness` getrennt:

- `export harness` ist Outbound-Kontext für Tools wie Claude Code, Codex, Gemini, Cursor, Kiro und OpenCode.
- `sessions ...` ist Inbound-Historie: es normalisiert frühere Claude-Code-/Codex-Sessions für das aktuelle Projekt, speichert sie unter `.tesserae/harness_sessions/` und lässt `export site` Session-Index-/Detailseiten veröffentlichen.

## Zwei Einstiege: Batch-Import und Live-Monitoring

Die Session-Aufnahme ist nicht länger nur Batch. Es gibt zwei Pfade in denselben normalisierten Store:

- **Batch-Import** — `sessions discover/import` scannt die Transcript-Roots on demand und schreibt einmalig. Dieser Flow ist unten dokumentiert.
- **Live-Monitoring** — der Supervisor-Daemon (`tesserae engine`) führt einen `SessionTailer` aus, der die Transkripte *des eigenen Projekts* (Claude Code und Codex) beobachtet und neue Turns aufnimmt, sobald sie eintreffen. Bei jedem Tick springt er per Seek an einen pro Datei persistierten Byte-Offset, liest nur die neu eingetroffenen Bytes und schreibt vollständige Turns in die SQLite-`HarnessSessionsDB` (`.tesserae/sqlite.db`) **bevor** eine entprellte Neukompilierung eingereiht wird, sodass die Kompilierung stets einen konsistenten Stand liest. Der Tailer ist auf die eigenen Sessions des Projekts beschränkt (Claude `projects/<slug>/*.jsonl`; Codex nach cwd gefiltert) und setzt nach einem Neustart von den gespeicherten Offsets fort, ohne Turns erneut abzuspielen.

Die Live-Schleife starten:

```bash
tesserae engine        # Quellen beobachten, Bursts zusammenfassen, automatisch neu kompilieren
tesserae engine --once # ein einzelner Drain-Zyklus, dann beenden (deterministisch)
```

`tesserae refresh` führt dieselbe Pipeline ingest → compile → project einmal in-process aus, ohne den langlebigen Watcher zu starten (mit `--skip-sessions` den Discovery-Scan der Harness-Sessions überspringen).

## Privacy-Modell

Beide Aufnahme-Pfade sind explizit: Der Live-Tailer läuft nur, solange du `tesserae engine` am Leben hältst, und die Batch-Discovery schreibt nur mit `--import`. Ein normaler `tesserae compile` oder `tesserae export site` liest die bereits normalisierten Sessions aus `.tesserae/harness_sessions/` und die Live-Records in `.tesserae/sqlite.db`, scrapet aber nicht von sich aus überraschend private Harness-Transcript-Verzeichnisse.

Importierte Session-Records sind lokale Projektartefakte. Überprüfe sie, bevor du eine öffentliche Site publishst, besonders wenn deine Transkripte Secrets, private Pfade, Kundendaten oder unveröffentlichten Code enthalten können.

## Lokale Sessions entdecken und importieren

Aus dem Projekt-Root:

```bash
tesserae sessions discover --import
```

Discovery scannt lokale Claude-Code- und Codex-Transcript-Roots, die zum aktuellen Projekt-Working-Directory gehören. Nutze `--root`, um ein bestimmtes Config-Directory zu scannen, und wiederhole `--harness`, um die Discovery einzugrenzen:

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

Ohne `--import` druckt Discovery, was sie gefunden hat, ohne normalisierte Session-Records zu schreiben.

## Normalisiertes JSON direkt importieren

Wenn ein anderes Tool bereits normalisiertes `HarnessSession`-JSON erzeugt hat, importiere eine Datei oder eine Liste von Dateien:

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

Jeder Input darf ein Session-Objekt oder eine Liste von Session-Objekten enthalten.

## Importierte Sessions auflisten

```bash
tesserae sessions list
```

Sessions werden hier abgelegt:

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

Live-überwachte Sessions werden zusätzlich in der SQLite-`HarnessSessionsDB` (`.tesserae/sqlite.db`) geführt, die auch die pro Datei gespeicherten Read-Offsets persistiert, von denen der Tailer fortsetzt. `sessions list` meldet die kombinierte Ansicht.

## Statische Session-Seiten bauen

Nach dem Import von Sessions die Site neu bauen:

```bash
tesserae export site
```

Die Site emittiert:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

Die generierte Site verlinkt Sessions von der globalen Rail, den Home-Browse-Cards, Such-Einträgen und dem Breadcrumb-Trail jeder Session-Detail-Seite.

## Layout der Session-Detail-Seite

Session-Detailseiten nutzen die geteilte Static-Site-Shell statt eines standalone Transcript-Dumps. Sie enthalten:

- Hero und Stat-Strip;
- High-Level-Summary;
- Timeline- und Size-Metadaten;
- Decisions, Files, Commands, Tools und Errors, wenn vorhanden;
- eingeklappten Subagent-Baum;
- Turn-by-Turn-User-/Assistant-Conversation;
- eingeklappte Tool-Use-Blöcke, angehängt unter dem vorhergehenden Assistant-Turn;
- eine Left-Conversation-Rail, die auf `#turn-N`-Anker verlinkt.

Conversation-Markdown wird durch den Site-Markdown-Renderer gerendert. Semantische Flächen wie Inline-Code, explizites Command-/Tag-Markup, Pfade, Filenames und Hashtags werden zu kompakten Chips dekoriert; zufällig großgeschriebene Nomen werden nicht automatisch chipfiziert.

Aktuelle Transcript-Typografie:

| Fläche | Selector | Größe |
|---|---|---|
| Conversation-Markdown-Prosa | `.session-turn-text`, Prose-Kinder | `8px` |
| Generische Conversation-Code-Fences | `.session-turn-text pre` | `10px` |
| Bash-/Shell-Fenced-Code-Content | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool-Details/Summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-Use-Header | `.session-tool-use-header` | `8px` |
| Tool-Payload-Text | `.session-tool-use-text` | `6px` |

## Publishing-Checkliste für Sessions

Bevor du eine öffentliche Site mit Sessions deployst:

1. Führe `tesserae sessions list` aus und bestätige, dass der Count wie erwartet ist.
2. Inspiziere `.tesserae/harness_sessions/` auf sensible Inhalte.
3. Baue neu mit `tesserae export site`.
4. Öffne `sessions/index.html` und mindestens eine Session-Detail-Seite lokal.
5. Bestätige, dass Tool-Blöcke standardmäßig eingeklappt sind und Raw-Tool-Payloads zum Publishen akzeptabel sind.
6. Deploye mit `tesserae export site --deploy`, sobald der Source-Tree committet ist.
