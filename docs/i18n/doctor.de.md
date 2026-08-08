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

Die Checks, nach Kategorie gruppiert:

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
| `compile_lock` | processes | ob ein lebendiger Compile-Lock gehalten wird, und von welcher PID **und welchem Host** | nur Bericht — doctor **killt nie einen Prozess und entfernt nie einen lebendigen Lock** |
| `filesystem_locking` | processes | ob `.tesserae/` auf einem Netzwerk-Dateisystem liegt, wo `flock(2)` ein stilles No-op sein kann | nur Bericht (kann host-übergreifende Durchsetzung nicht beweisen — siehe unten) |
| `daemon_pid` | processes | `daemon.<host>.pid` zeigt auf einen lebendigen Engine-Prozess | **SAFE**: entfernt die Pidfile **dieses Hosts**, wenn ihr Eigentümer tot ist; die einer anderen Maschine wird gemeldet, nie angefasst |
| `llm_login` | environment | ob die Config-Verzeichnisse existieren, die das Projekt tatsächlich benutzen würde | nur Bericht — **verifiziert keine Credentials** (siehe unten) |
| `optional_deps` | environment | Status optionaler Abhängigkeiten (memex, raganything) | nur Bericht (Installationen brauchen Netzwerk) |
| `embedding_backend` | environment | ein echtes semantisches Embedding-Backend ist verfügbar | nur Bericht (schlägt `pip install tesserae[semantic]` vor) |
| `environment` | environment | Gesamtzusammenfassung der Umgebungserkennung | Berichtsabschnitt |
| `build_history` | hygiene | Größe und Form von `.build-history` | **SAFE**: kürzt sie und bewahrt immer den neuesten `git_head`-Eintrag (der Staleness-Check hängt davon ab) |
| `idempotence` | hygiene | der Output-Snapshot-Stolperdraht `idempotence_suspect` | nur Bericht (das ist ein Bug-Signal, nichts zum Auto-Reparieren) |
| `orphan_worktrees` | hygiene | verwaiste `git worktree`-Registrierungen | **SAFE**: `git worktree prune`; Verzeichnisse löschen ist nur Bericht |
| `hook_log_bloat` | hygiene | Wachstum von `.tesserae/.session-*-hook.log` | **SAFE**: rotiert/kürzt Logs über 10 MB |
| `code_scope_leftovers` | hygiene | Überreste der stillgelegten Code-Schicht: `code-graph*.json`, code-typisierte Zeilen in `sqlite.db` | nur Bericht — die Bereinigung ist ein Massenlöschen und lebt daher auf einem eigenen Verb (siehe unten) |

Ein abstürzender Check wird als Fehler-Befund gemeldet — doctor selbst wirft nie eine Exception.

## Was `llm_login` dir sagt — und was nicht

Er meldet, dass ein Config-Verzeichnis existiert. Er meldet **nicht**, dass die
CLI darin ein gültiges Token hält, und sagt das in seinem eigenen Befundtext auch
so.

Die Unterscheidung ist keine Erbsenzählerei. Der Check meldete früher
`credentialed LLM CLI: claude, codex`, gestützt auf Dateien wie
`~/.claude/history.jsonl` — die belegen, dass die CLI *benutzt* wurde, nicht,
dass sie sich *jetzt* authentifizieren kann. In derselben Sekunde hintereinander
ausgeführt, gab `tesserae compile` `Claude CLI not logged in (tried 1 config
dir)` aus, während doctor einen grünen Haken druckte. Eine Diagnose, die dem
Fehler widerspricht, in dem du gerade steckst, ist schlimmer als gar keine
Diagnose.

Credentials zu verifizieren hieße, bei jedem `tesserae doctor` einen echten
LLM-Call auszugeben — Kosten, die dieser Check nicht aus eigenem Antrieb auf sich
nimmt. Also nennt er nur, was er tatsächlich geprüft hat. Für die verbindliche
Antwort nimm `tesserae compile`.

Der Check ist auf die Verzeichnisse begrenzt, die das Projekt wirklich probieren
würde, aufgelöst über denselben Pfad, den `ProjectWiki._build_json_client`
benutzt — und er sagt nichts über claude-Config-Verzeichnisse, wenn der Provider
des Projekts `codex` ist.

## Geteilte Platten und `flock(2)`

Jede Nebenläufigkeitsgarantie in Tesserae — der Compile-Lock vor allem — beruht
darauf, dass `flock(2)` von dem Dateisystem durchgesetzt wird, auf dem
`.tesserae/` liegt. Über NFS und SMB ist das konfigurationsabhängig: ohne
funktionierenden Lock-Daemon kann `flock` still zum No-op degradieren, und dann
kompilieren zwei Hosts dasselbe Projekt zur gleichen Zeit, während jeder glaubt,
einen exklusiven Lock zu halten.

`filesystem_locking` meldet, was ein einzelner Host feststellen kann: den
Dateisystemtyp hinter dem Projekt, ob es ein Netzwerk-Dateisystem ist, und ob
eine `flock`-Akquise überhaupt gelingt. Bei einem Netzwerk-Dateisystem warnt er.

Er **kann** host-übergreifende Durchsetzung **nicht** beweisen und behauptet es
auch nicht. Dass ein Host einen Lock nimmt, sagt nichts darüber aus, ob ein
zweiter Host daran gehindert wird, ihn ebenfalls zu nehmen. Wenn du Tesserae von
mehreren Maschinen gegen geteilten Speicher fährst, teste das direkt auf der
echten Hardware, bevor du dich auf den Compile-Lock verlässt.

## `tesserae doctor migrate-code-scope`

Eine einmalige Bereinigung für einen Workspace, der kompiliert wurde, bevor
Quellcode aus Tesseraes Geltungsbereich fiel. Neue Compiles erzeugen die
Code-Schicht nicht mehr, ein älterer Workspace trägt sie aber weiterhin, und das
meiste davon löst sich nur auf, wenn man darum bittet.

```bash
tesserae doctor migrate-code-scope            # Trockenlauf — berichtet, löscht nichts
tesserae doctor migrate-code-scope --apply    # löscht tatsächlich
```

Entfernt, in dieser Reihenfolge:

* projizierte Seiten unter `.tesserae/markdown_projection/`, deren eigenes
  `type:`-Frontmatter einen stillgelegten Code-Typ nennt;
* dieselben Seiten im Obsidian-Vault — sowohl im konfigurierten als auch im
  projektinternen Standard, denn ein Projekt, das später auf einen echten Vault
  zeigte, lässt den alten voll davon zurück. Eine Code-Seite mit nicht-leerem
  `user-notes`-Inhalt bleibt erhalten und wird gezählt, nie gelöscht;
* `code-graph.json` und `code-graph-cache.json`;
* SQLite-Sidecar-Zeilen (`node_provenance`, `edge_provenance`, `node_memory`),
  deren Knoten oder Kante nicht mehr existiert, danach `VACUUM`.

Zwei Dinge sollte man wissen.

**Lies die Zahl der Überlebenden, nicht die der Löschungen.** Das
Projektionsverzeichnis ist überwältigend code-abgeleitet — hier gemessen 218.796 von
224.876 Seiten — ein Prädikat-Bug, der alles löscht, und ein korrekter Lauf sehen an
der Löschzahl daher fast gleich aus. Der Bericht beginnt damit, wie viele
Nicht-Code-Seiten überleben; genau diese Zahl bräche ein, wäre das Prädikat falsch.
Entschieden wird strikt pro Datei, anhand ihres eigenen Frontmatters.

**Erst kompilieren, dann migrieren.** Die Tabellen `nodes` / `edges` und die
Provenance-Sidecars werden von jedem Compile komplett neu geschrieben — der Compile
macht diese Zeilen also zu Müll, und dieses Verb holt den Platz zurück, weil SQLite
bei `DELETE` nicht schrumpft. Es vorher auszuführen schadet nicht — es sagt das und
findet nichts zurückzuholen. `VACUUM` läuft niemals innerhalb eines Compiles: es
nimmt eine exklusive Sperre und braucht freien Speicher in der Größenordnung der
Datenbankdatei, und es wird mit einer Notiz übersprungen, wenn die Platte den Umbau
nicht trägt.

Es ist bewusst nicht über `--fix` erreichbar, das als ausschließlich sichere
Reparaturen dokumentiert ist.

## `--fix`-Policy

- `--fix` führt **nur** die oben als SAFE markierten Checks aus und detektiert danach
  neu, damit der Bericht den Zustand nach dem Fix widerspiegelt.
- Jeder Fix ist idempotent: `doctor --fix` zweimal auszuführen lässt den zweiten
  Lauf sauber.
- Doctor **killt nie einen Prozess und entfernt nie einen lebendigen Compile-Lock** —
  ein gehaltener Lock wird mit seiner besitzenden PID und deren Host gemeldet und
  in Ruhe gelassen.
- Doctor **fasst nie die Pidfile einer anderen Maschine an.** Auf geteiltem
  Speicher sagt die lokale Prozesstabelle nichts über eine PID aus, die ein
  anderer Host geschrieben hat; deshalb wird `daemon.<other-host>.pid` gemeldet
  und bedingungslos übersprungen — sie wird nicht einmal auf Liveness gelesen.
  Nur die eigene Pidfile dieses Hosts kommt zum Entfernen infrage.
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
