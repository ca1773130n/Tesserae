# `tesserae doctor` — Projekt-Gesundheitschecks

<!-- translations:start -->
<p align="center"><a href="../doctor.md">English</a> · <a href="doctor.ko.md">한국어</a> · <a href="doctor.zh.md">中文</a> · <a href="doctor.ja.md">日本語</a> · <a href="doctor.ru.md">Русский</a> · <a href="doctor.es.md">Español</a> · <a href="doctor.fr.md">Français</a></p>
<!-- translations:end -->
`tesserae doctor` inspiziert einen Tesserae-Workspace von Ende zu Ende — Initialisierung,
Graph-Integrität, Registry-Konsistenz, Frische, Locks, LLM-Login und
Disk-Hygiene — und gibt eine Checkliste aus. Es ist **standardmäßig read-only**; `--fix`
wendet nur die Reparaturen an, die sicher wiederholbar sind und niemals Live-Zustand
zerstören können.

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## Was geprüft wird

Zwanzig Checks, nach Kategorie gruppiert:

| Check | Kategorie | Was er verifiziert | `--fix`-Aktion |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` existiert und sieht wie ein Tesserae-Workspace aus | nur Bericht (schlägt `tesserae init` vor) |
| `graph_parse` | core | `graph.json` parst und hat die erwartete Form | nur Bericht (schlägt `tesserae compile` vor) |
| `config_valid` | core | `.tesserae/config.json` parst und validiert gegen das Init-Template | nur Bericht |
| `vault_configured` | core | der konfigurierte Vault-Pfad lässt sich auflösen | **SAFE**: legt das aufgelöste Vault-Verzeichnis an, wenn es innerhalb des Projekts liegt |
| `registry_consistent` | registry | `~/.tesserae/registry.json`-Einträge zeigen auf reale Projekt-Roots | **SAFE**: entfernt Einträge, deren Root verschwunden ist, streicht den Legacy-Key `active`; ein fehlender Graph ist nur Bericht |
| `graph_staleness` | freshness | Git-Delta seit dem im letzten Compile aufgezeichneten `git_head` | nur Bericht (schlägt `tesserae refresh` vor — Compiles sind teuer) |
| `site_search_index` | freshness | die statische Site / `search-index.json` ist neuer als `graph.json` | **SAFE**: baut die Site neu |
| `backend_artifacts` | freshness | RAG-Anything-Artefakte sind aktuell | nur Bericht (deren Refresh ist LLM-/Netzwerk-lastig) |
| `session_chunks` | freshness | die Abdeckung der [täglichen Session-Chunks](session-chunks.de.md) hat keine Lücken im jüngsten Fenster | nur Bericht (schlägt `tesserae sessions chunk-backfill` vor) |
| `wiki_lint` | graph | Graph-⇄-Wiki-Drift + trivial behebbare Lint-Befunde | **SAFE**: wendet die trivialen Lint-Fixes an (`fix_trivial`) |
| `compile_lock` | processes | ob ein lebendiger Compile-Lock gehalten wird, und von welcher PID | nur Bericht — doctor **killt nie einen Prozess und entfernt nie einen lebendigen Lock** |
| `daemon_pid` | processes | `daemon.pid` zeigt auf einen lebendigen Engine-Prozess | **SAFE**: entfernt die Pidfile, wenn ihr Eigentümer tot ist |
| `llm_login` | environment | das konfigurierte LLM-Backend ist tatsächlich nutzbar (claude/codex-CLI eingeloggt, oder API-Key vorhanden) | nur Bericht (schlägt `claude /login` / `codex login` vor) |
| `optional_deps` | environment | Status optionaler Abhängigkeiten (memex, raganything) | nur Bericht (Installationen brauchen Netzwerk) |
| `embedding_backend` | environment | ein echtes semantisches Embedding-Backend ist verfügbar | nur Bericht (schlägt `pip install tesserae[semantic]` vor) |
| `environment` | environment | Gesamtzusammenfassung der Umgebungserkennung | Berichtsabschnitt |
| `build_history` | hygiene | Größe und Form von `.build-history` | **SAFE**: kürzt sie und bewahrt immer den neuesten `git_head`-Eintrag (der Staleness-Check hängt davon ab) |
| `idempotence` | hygiene | der Output-Snapshot-Stolperdraht `idempotence_suspect` | nur Bericht (das ist ein Bug-Signal, nichts zum Auto-Reparieren) |
| `orphan_worktrees` | hygiene | verwaiste `git worktree`-Registrierungen | **SAFE**: `git worktree prune`; Verzeichnisse löschen ist nur Bericht |
| `hook_log_bloat` | hygiene | Wachstum von `.tesserae/.session-*-hook.log` | **SAFE**: rotiert/kürzt Logs über 10 MB |

Ein abstürzender Check wird als Fehler-Befund gemeldet — doctor selbst wirft nie eine Exception.

## `--fix`-Policy

- `--fix` führt **nur** die oben als SAFE markierten Checks aus und detektiert danach
  neu, damit der Bericht den Zustand nach dem Fix widerspiegelt.
- Jeder Fix ist idempotent: `doctor --fix` zweimal auszuführen lässt den zweiten
  Lauf sauber.
- Doctor **killt nie einen Prozess und entfernt nie einen lebendigen Compile-Lock** —
  ein gehaltener Lock wird mit seiner besitzenden PID gemeldet und in Ruhe gelassen.
- Schwere oder netzwerkgebundene Operationen (Recompiles, Dependency-Installationen,
  Backend-Refreshes) werden nie in `--fix` gefaltet; doctor gibt stattdessen den
  Befehl aus, den du selbst ausführen kannst.

## Exit-Codes

Dieselbe Konvention wie `tesserae lint`:

| Exit-Code | Bedeutung |
|---|---|
| `0` | gesund — keine Befunde über OK |
| `1` | Warnungen vorhanden |
| `2` | Fehler vorhanden |

## Bericht-Artefakte

Jeder Lauf schreibt beide Berichtsformen in den Workspace:

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json` gibt zusätzlich den JSON-Bericht statt der Markdown-Checkliste auf stdout
aus. `--all` iteriert über jedes Projekt in der Registry (ignoriert dabei
`--project`) und berichtet pro Projekt.

## MCP: `doctor_report`

Der MCP-Server exponiert denselben Bericht als `doctor_report`-Tool (spiegelt
`lint_report`, inklusive seiner Byte-Obergrenze für zurückgegebenen Inhalt), sodass
ein Agent die Workspace-Gesundheit mitten im Gespräch prüfen kann, ohne eine Shell zu
bemühen. Es benötigt einen Projekt-Root — übergib `graph_path`/`project` oder
konfiguriere einen Default-Graph.
