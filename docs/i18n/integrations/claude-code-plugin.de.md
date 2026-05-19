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
* **Automatische Registrierung des `tesserae_mcp` Servers** — der Agent erhält `ask`, `search_nodes`, `list_sessions`, `find_session_findings` etc. als `mcp__plugin_tesserae_tesserae__<tool>` ohne manuelle Config-Edits.
* **`using-tesserae` Skill** — wird automatisch geladen, wenn du nach dem typisierten Graphen, vergangenem Session-Rückruf, Wiki/Vault-Inhalten oder einem tesserae-Workflow fragst. Lehrt den Agenten, welches MCP-Tool zu verwenden vs welchen Slash-Befehl vorzuschlagen.
* **4 Hooks** — `SessionStart` druckt eine Graph-Zusammenfassung; `SessionEnd` führt im Hintergrund import+compile aus, damit die Erkenntnisse dieses Gesprächs zu Graph-Knoten für die nächste Sitzung werden; `PostToolUse` (opt-in) macht inkrementelle Neukompilierung bei docs/-Edits; `PreToolUse` gattert große-Graph-Kompilierungen über einen Bestätigungsdialog.

Vollständige Details, vollständige Befehls-/Hook-Tabellen und Per-Projekt-Opt-out-Anweisungen befinden sich im plugineigenen [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md).

## Warum ein Plugin UND ein MCP-Server?

Unterschiedliche Rollen:

- **MCP-Tools** = Read-only-Graph-Abfragen, die der Agent während eines Gesprächs aufruft. Immer an, geringe Reibung.
- **Slash-Befehle** = Workflow-Aktionen, die du explizit aufrufst (compile, refresh, obsidian-sync). Hoher Hebel, aber sollte deine Entscheidung sein.

Du kannst den MCP-Server allein nutzen (manuelle Bearbeitung von `claude_desktop_config.json` über `tesserae project mcp-config`). Das Plugin verpackt ihn einfach mit den Slash-Befehlen, der Skill und den Hooks, sodass die Installation ein Schritt ist.

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
