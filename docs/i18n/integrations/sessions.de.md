# Sitzungs-Graph

<!-- translations:start -->
<p align="center"><a href="../../integrations/sessions.md">English</a> · <a href="sessions.ko.md">한국어</a> · <a href="sessions.zh.md">中文</a> · <a href="sessions.ja.md">日本語</a> · <a href="sessions.ru.md">Русский</a> · <a href="sessions.es.md">Español</a> · <a href="sessions.fr.md">Français</a></p>
<!-- translations:end -->

Der Sitzungs-Graph von Tesserae verwandelt deine Claude Code / Codex-Konversationen über ein Projekt in erstklassige Knoten im typisierten Wissensgraphen, verknüpft mit den Dokumenten, die zur Sprache kamen. Nach einer Kompilierung kannst du `tesserae project ask "was haben wir über 3D Gaussian Splatting entschieden?"` fragen und konkrete Insight / Decision / Question / TODO / Hypothesis / Takeaway-Knoten mit Provenienz zurück zur Sitzung erhalten, die sie hervorgebracht hat.

## Funktionsweise

Zwei Durchgänge pro Sitzung:

1. **Strukturell** (immer aktiv, kein LLM). Liest die normalisierten `HarnessSession`-Datensätze, die `tesserae sessions discover --import` in `.tesserae/harness_sessions/` schreibt. Für jede Sitzung wird ein `Session`-Umschlag-Knoten geprägt, `discussed_in`-Kanten von jedem Dokument emittiert, das der Agent geöffnet hat, und das vorhandene `decisions`-Feld in `SessionDecision`-Knoten umgewandelt.
2. **LLM** (opt-in, läuft wenn `ANTHROPIC_API_KEY` konfiguriert ist). Sendet die normalisierten Gesprächs-Turns (das `metadata["turns"]`-Feld — nicht die rohe Transkriptdatei) an Claude mit einem JSON-only Findings-Schema. Gibt sechs Arten von Findings zurück, jedes zitiert zurück zu bestimmten Turns und bestimmten Dok-Knoten-IDs im aktuellen Graphen. Zwischengespeichert nach content_hash + project_root_hash, sodass unveränderte Sitzungen den Aufruf bei der nächsten Kompilierung überspringen.

## Einrichtung

```bash
# Importiere die Sitzungen dieses Projekts in `.tesserae/harness_sessions/`. Filtert nach cwd, sodass nur Sitzungen, die innerhalb dieses Projekts liefen, importiert werden.
tesserae sessions discover --import

# Kompiliere. Der strukturelle Durchgang läuft kostenlos; der LLM-Durchgang läuft automatisch, wenn die `claude` CLI angemeldet ist — kein API-Schlüssel nötig.
tesserae project compile
```

Um ohne Sitzungen zu kompilieren (z.B. auf einem Server ohne Harness-Historie):

```bash
tesserae project compile --no-sessions
```

Um strukturell-only zu erzwingen (LLM-Aufruf überspringen, auch wenn ein Schlüssel gesetzt ist):

```bash
tesserae project compile --sessions-llm=false
```

## Konfiguration

`.tesserae/config.json` akzeptiert einen `sessions`-Block:

```jsonc
{
  "sessions": {
    "enabled": true,
    "llm_enabled": "auto",
    "max_turns_per_chunk": 30,
    "model": "claude-sonnet-4-7-20251201",
    "include_doc_id_context": 200
  }
}
```

CLI-Flags überschreiben die Konfiguration. `llm_enabled = "auto"` (Standard) führt den LLM-Durchgang aus, wenn die `claude` CLI angemeldet ist oder `ANTHROPIC_API_KEY` gesetzt ist; ohne beides läuft nur der strukturelle Durchgang (kein Fehler, keine ausgehenden Aufrufe).

## Abfrage

Zwei MCP-Tools werden zu den bestehenden Such-/Wiki-Tools hinzugefügt:

* `list_sessions(since?, limit?)` — Session-Umschläge für das aktive Projekt (id, started_at, title, Finding-Anzahl).
* `find_session_findings(node_id, kinds?)` — jeder von einer Sitzung abgeleitete Finding, der über `discussed_in` oder `references` mit `node_id` verknüpft ist, optional gefiltert auf insight / decision / question / todo / hypothesis / takeaway.

Aus der CLI:

```bash
tesserae sessions list
tesserae project ask "what did we decide about extractor dedup?"
```

## Datenschutz

* Ohne angemeldete `claude` CLI UND ohne `ANTHROPIC_API_KEY` (oder mit `--sessions-llm=false`) gibt es null ausgehende Netzwerkaufrufe. Nur der strukturelle Durchgang läuft.
* Wenn der LLM-Durchgang läuft, werden die **vollständigen normalisierten Gesprächs-Turns** für noch nicht zwischengespeicherte Sitzungen gesendet. Die Transkriptdatei selbst bleibt auf der Festplatte; nur die JSON-Ausgabe des LLM wird im Graphen und im Pro-Sitzung-Cache persistiert.
* Cache-Dateien leben in `.tesserae/session_findings/<session_id>.findings.json` mit sowohl einem `content_hash` als auch einem `project_root_hash`. Eine zwischen Projekten kopierte Cache-Datei wird beim Lesen abgelehnt — keine projektübergreifende Wiederholung.
* Sitzungen werden nach dem Laden durch `session_matches_project` gefiltert, sodass ein Transkript, dessen `cwd` ein Schwesterprojekt war, niemals Knoten im Graphen dieses Projekts produziert.

## Vault-Layout

Findings werden unter dem Obsidian-Vault als eine Seite pro Finding gerendert, gruppiert nach Sitzung:

```
<vault>/
  sessions/
    <session-id-slug>/
      cache-findings-by-content-hash.md
      path-index-needs-basename-suppression.md
      ...
```

Benutzernotizen innerhalb des `<!-- user-notes:start -->` … `<!-- user-notes:end -->`-Blocks auf jeder Finding-Seite überleben eine Neukompilierung — derselbe Vertrag wie für jede andere Vault-Seite.

## Fehlerbehebung

* **Nach der Kompilierung erscheinen keine Session-Knoten.** Hast du zuerst `tesserae sessions discover --import` ausgeführt? Der Kompilierungspfad konsumiert nur `.tesserae/harness_sessions/`; er scannt `~/.claude/projects/` NICHT automatisch (dieser Scan kann auf Maschinen mit Tausenden historischer Sitzungen Minuten dauern).
* **LLM-Kostenbedenken.** Der Cache bedeutet, dass jede Sitzung höchstens einmal pro content-hash an das LLM gesendet wird. Lange Sitzungen werden bei `max_turns_per_chunk` (Standard 30) mit 5-Turn-Überlappung in Chunks aufgeteilt. Um die Gesamtkosten zu begrenzen, senke `max_turns_per_chunk`, senke `include_doc_id_context`, oder setze `--sessions-llm=false`.
* **Ein Finding zitiert eine nicht existierende Knoten-ID.** Der Orchestrator validiert jede zitierte Referenz gegen den lebenden Dok-Graphen und verwirft unbekannte still. Wenn du die Warnung in den Logs siehst, hat das LLM eine Zitation halluziniert — die überlebenden Referenzen sind immer noch vertrauenswürdig.

## Spezifikation

Das vollständige Design lebt in [docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md](../../superpowers/specs/2026-05-19-session-graph-extractor-design.md). Der Implementierungsplan ist [docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md](../../superpowers/plans/2026-05-19-session-graph-extractor-plan.md).
