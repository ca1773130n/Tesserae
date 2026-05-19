# Plugin de Claude Code

<!-- translations:start -->
<p align="center"><a href="../../integrations/claude-code-plugin.md">English</a> · <a href="claude-code-plugin.ko.md">한국어</a> · <a href="claude-code-plugin.zh.md">中文</a> · <a href="claude-code-plugin.ja.md">日本語</a> · <a href="claude-code-plugin.ru.md">Русский</a> · <a href="claude-code-plugin.fr.md">Français</a> · <a href="claude-code-plugin.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae incluye un plugin de [Claude Code](https://docs.claude.com/en/docs/claude-code) para que puedas ejecutar el flujo de trabajo completo de Tesserae desde dentro de una sesión TUI — comandos slash, un servidor MCP autoregistrado, una habilidad que orienta al agente y cuatro hooks que cierran el bucle agente↔memoria-de-proyecto. El plugin vive en el repositorio en `plugin/`.

## Instalación

```bash
# Requisito previo: `tesserae` ya instalado (`pip install tesserae` o `pipx install tesserae`).
/plugin install /path/to/Tesserae/
```

Requisito previo: `tesserae` ya instalado (`pip install tesserae` o `pipx install tesserae`). Si instalas con pipx, asegúrate de que `~/.local/bin` esté en el PATH que Claude Code hereda al lanzarse.

## Qué incluye

* **9 comandos slash** — siete envoltorios 1:1 alrededor de la CLI (`/tesserae:compile`, `/tesserae:ask`, `/tesserae:sessions-import`, `/tesserae:build-site`, `/tesserae:serve`, `/tesserae:obsidian-sync`, `/tesserae:setup`) más dos macros de flujo de trabajo (`/tesserae:refresh` encadena import + compile + obsidian-sync; `/tesserae:status` muestra conteos del grafo y última compilación).
* **Registro automático del servidor `tesserae_mcp`** — el agente obtiene `ask`, `search_nodes`, `list_sessions`, `find_session_findings`, etc. como `mcp__plugin_tesserae_tesserae__<tool>` sin ediciones manuales de configuración.
* **Habilidad `using-tesserae`** — se carga automáticamente cuando preguntas sobre el grafo tipado, recuperación de sesiones pasadas, contenido wiki/vault, o cualquier flujo de trabajo tesserae. Enseña al agente qué herramienta MCP usar vs qué comando slash sugerir.
* **4 hooks** — `SessionStart` imprime un resumen del grafo; `SessionEnd` ejecuta en segundo plano import+compile para que las ideas de esta conversación se conviertan en nodos del grafo para la próxima sesión; `PostToolUse` (opt-in) hace recompilación incremental en ediciones de docs/; `PreToolUse` controla compilaciones de grafos grandes mediante un diálogo de confirmación.

Detalles completos, tablas completas de comandos/hooks e instrucciones de opt-out por proyecto están en el propio [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md) del plugin.

## ¿Por qué un plugin Y un servidor MCP?

Roles diferentes:

- **Herramientas MCP** = consultas de grafo de solo lectura que el agente invoca durante una conversación. Siempre activas, baja fricción.
- **Comandos slash** = acciones de flujo de trabajo que invocas explícitamente (compile, refresh, obsidian-sync). Alto apalancamiento pero debe ser tu decisión.

Puedes usar solo el servidor MCP (edición manual de `claude_desktop_config.json` vía `tesserae project mcp-config`). El plugin simplemente lo empaqueta junto con los comandos slash, la habilidad y los hooks, haciendo que la instalación sea un solo paso.

## Verificar instalación

```
/plugin list
/mcp
/tesserae:status
```

## Desinstalar

```
/plugin uninstall tesserae
```

Reversible. No toca el directorio `.tesserae/` de ningún proyecto.

## Véase también

- [Plan de implementación](../../superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [Especificación de diseño](../../superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [Integración de sesiones](sessions.es.md) — la función del grafo de sesiones cuyo bucle cierran los hooks del plugin
