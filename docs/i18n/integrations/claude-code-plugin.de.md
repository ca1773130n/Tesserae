# Claude Code Plugin

<!-- translations:start -->
<p align="center"><a href="../../integrations/claude-code-plugin.md">English</a> · <a href="claude-code-plugin.ko.md">한국어</a> · <a href="claude-code-plugin.zh.md">中文</a> · <a href="claude-code-plugin.ja.md">日本語</a> · <a href="claude-code-plugin.ru.md">Русский</a> · <a href="claude-code-plugin.es.md">Español</a> · <a href="claude-code-plugin.fr.md">Français</a></p>
<!-- translations:end -->

Tesserae bringt ein [Claude Code](https://docs.claude.com/en/docs/claude-code) Plugin mit, damit du den vollständigen Tesserae-Workflow aus einer TUI-Sitzung heraus ausführen kannst — Slash-Befehle, ein automatisch registrierter MCP-Server, eine Skill, die den Agenten orientiert, und vier Hooks, die den Agent↔Projekt-Speicher-Kreis schließen. Das Plugin liegt im Repo unter `plugin/`.

## Installation

```bash
# Voraussetzung: `tesserae` bereits installiert (`pip install tesserae` oder `pipx install tesserae`).
/plugin install /path/to/Tesserae/
```

Voraussetzung: `tesserae` bereits installiert (`pip install tesserae` oder `pipx install tesserae`). Bei Installation über pipx stelle sicher, dass `~/.local/bin` im PATH ist, den Claude Code beim Start erbt.

## Was enthalten ist

* **9 Slash-Befehle** — sieben 1:1-Wrapper um das CLI (`/tesserae:compile`, `/tesserae:ask`, `/tesserae:sessions-import`, `/tesserae:build-site`, `/tesserae:serve`, `/tesserae:obsidian-sync`, `/tesserae:setup`) plus zwei Workflow-Makros (`/tesserae:refresh` verkettet import + compile + obsidian-sync; `/tesserae:status` zeigt Graph-Counts und letzte Kompilierung).
* **Automatische Registrierung des `tesserae` Servers** — der Agent erhält die gesamte Tool-Oberfläche als `mcp__plugin_tesserae_tesserae__<tool>` ohne manuelle Config-Edits: Graph-Abfragen (`search_nodes`, `node_context`, `graph_ppr`, `search_facts`), den On-Demand-Compiler `compile_context` / `list_communities` / `fresh_insights`, Session-Memory (`ask`, `list_sessions`, `find_session_findings`, `find_code_symbol_mentions`) und geführtes Setup (`tesserae_setup_plan` / `tesserae_setup_apply`). Die vollständige Liste steht in [mcp.de.md](mcp.de.md).
* **`using-tesserae` Skill** — wird automatisch geladen, wenn du nach dem typisierten Graphen, vergangenem Session-Rückruf, Wiki/Vault-Inhalten oder einem tesserae-Workflow fragst. Lehrt den Agenten, welches MCP-Tool zu verwenden vs welchen Slash-Befehl vorzuschlagen.
* **5 Hooks** — `SessionStart` druckt eine Graph-Zusammenfassung; `SessionEnd` führt im Hintergrund import+compile aus, damit die Erkenntnisse dieses Gesprächs zu Graph-Knoten für die nächste Sitzung werden; zwei `PostToolUse`-Hooks feuern bei `Edit`/`Write`/`MultiEdit` — einer macht eine opt-in inkrementelle Neukompilierung bei docs/-Edits, der andere entprellt (~30 s) eine Code-Graph-Synchronisierung; `PreToolUse` (auf `Bash`) gattert große-Graph-Kompilierungen über einen Bestätigungsdialog.

> **Die Kompilierung beim Sitzungsende ist opportunistisch, nicht garantiert.**
> Der Hook löst seinen Hintergrundjob mit `setsid` ab, wo es existiert, und
> greift sonst auf `nohup` zurück. macOS liefert kein `setsid`, und `nohup`
> ignoriert lediglich `SIGHUP` — der Job bleibt in der Prozessgruppe der Sitzung —,
> sodass ein Harness, das die Gruppe beim Sitzungsende einsammelt, die
> Kompilierung weiterhin mittendrin killen kann. Was dabei zurückbleibt, ist
> wiederherstellbar, nicht unberührt: `graph.json` wird per atomarem Rename
> geschrieben und ist daher nie eine halbe Datei — aber die generierten
> Projektionen `wiki/` und `site/` werden zu Beginn des Artefaktschreibens
> gelöscht, und der SQLite-Store wird nach `graph.json` geschrieben, sodass ein
> Kill in diesem Fenster sie fehlend oder eine Kompilierung veraltet hinterlässt.
> Still geschieht das aber nie — `.tesserae/manifest.json` markiert ein Dokument
> erst als `graphed`, wenn die Artefakte liegen, also verweigert die nächste
> `compile --changed-only` ihren No-op, meldet `graph.json is not known to cover
> every tracked document` und extrahiert den gesamten Korpus neu, wodurch auch die
> Projektionen wieder entstehen. Diese Neuextraktion des gesamten Korpus ist ein
> Neudurchlauf, kein Neukauf. Antworten der codex- und claude-CLI-Provider werden
> unter `~/.tesserae/llm_cache` gecacht, adressiert über einen Hash des tatsächlich
> gesendeten Prompts, sodass jedes Dokument, das der gekillte Lauf bereits beendet
> hatte, kostenlos von der Festplatte abgespielt wird, und die Reparatur zahlt nur für
> die Dokumente, die sie nie erreicht hat. Ein Kill kostet dich die verstrichene Zeit des
> Laufs, nicht seine Extraktionen. Zwei Dinge machen das rückgängig: das Löschen des
> Cache-Verzeichnisses und die Verwendung des direkten API-Providers, der nur das
> kurzfristige Prompt-Caching des SDK hat und nichts, das einen Kill überlebt. In beiden
> Fällen kauft die Reparatur den gesamten Korpus erneut vom Anbieter zum vollen Preis. Baue keinen Workflow, der annimmt, dass eine lange Kompilierung
> die Sitzung überlebt, die sie gestartet hat — führe sie im Vordergrund aus oder
> über `tesserae engine`.
>
> Auf beiden Wegen kannst du zusehen. Eine Kompilierung ohne angehängtes Terminal —
> abgelöst, umgeleitet oder unter CI — protokolliert eine Zeile pro Dokument auf stderr
> im `tesserae.compile`-Kanal, mit Position, Pfad und ob dieses Dokument aus dem Cache
> stammt oder einen Modellanruf gekostet hat; `--quiet` schaltet es aus.

Vollständige Details, vollständige Befehls-/Hook-Tabellen und Per-Projekt-Opt-out-Anweisungen befinden sich im plugineigenen [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md).

## Warum ein Plugin UND ein MCP-Server?

Unterschiedliche Rollen:

- **MCP-Tools** = Read-only-Graph-Abfragen, die der Agent während eines Gesprächs aufruft. Immer an, geringe Reibung.
- **Slash-Befehle** = Workflow-Aktionen, die du explizit aufrufst (compile, refresh, obsidian-sync). Hoher Hebel, aber sollte deine Entscheidung sein.

Du kannst den MCP-Server allein nutzen (manuelle Bearbeitung von `claude_desktop_config.json` über `tesserae projects mcp-config`). Das Plugin verpackt ihn einfach mit den Slash-Befehlen, der Skill und den Hooks, sodass die Installation ein Schritt ist.

## Installation überprüfen

```
/plugin list
/mcp
/tesserae:status
```

## Deinstallieren

```
/plugin uninstall tesserae
```

Umkehrbar. Berührt das `.tesserae/`-Verzeichnis keines Projekts.

## Siehe auch

- [Implementierungsplan](../../superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [Design-Spezifikation](../../superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [Sessions-Integration](sessions.de.md) — die Sessions-Graph-Funktion, deren Schleife die Hooks des Plugins schließen
