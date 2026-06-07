# Historial de sesiones Harness

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae puede importar transcript locales de AI-agent y renderizarlos como memoria del proyecto en la sección `sessions/` del sitio estático.

Esta función está separada intencionalmente de `export-agent-harness`:

- `export-agent-harness` es contexto saliente para herramientas como Claude Code, Codex, Gemini, Cursor, Kiro y OpenCode.
- `project sessions ...` es historial entrante: normaliza sesiones previas de Claude Code/Codex para el proyecto actual, las guarda bajo `.tesserae/harness_sessions/` y permite que `project build-site` publique páginas de índice/detalle de sesiones.

## Dos vías de entrada: importación por lotes y monitoreo en vivo

La ingesta de sesiones ya no es solo por lotes. Hay dos rutas hacia el mismo almacén normalizado:

- **Importación por lotes** — `project sessions discover/import` escanea las raíces de transcript bajo demanda y escribe de una sola vez. Esta página documenta ese flujo más abajo.
- **Monitoreo en vivo** — el supervisor daemon (`project engine`, alias `project daemon`) ejecuta un `SessionTailer` que observa los transcript *del propio proyecto* (Claude Code y Codex) e ingiere nuevos turnos a medida que llegan. En cada tick hace seek a un byte offset persistido por archivo, lee solo los bytes nuevos y guarda los turnos completos en el SQLite `HarnessSessionsDB` (`.tesserae/sqlite.db`) **antes** de encolar una recompilación con debounce, de modo que la compilación siempre lee un estado consistente. El tailer se limita a las propias sesiones del proyecto (Claude `projects/<slug>/*.jsonl`; Codex filtrado por cwd) y, tras un reinicio, reanuda desde los offset guardados sin reproducir turnos.

Ejecuta el bucle en vivo con:

```bash
tesserae project engine        # observar fuentes, fusionar ráfagas, recompilar automáticamente
tesserae project engine --once # un solo ciclo de drain y salir (determinista)
```

`tesserae project refresh` ejecuta el mismo pipeline ingest → compile → project una vez, in-process, sin iniciar el watcher de larga duración (pasa `--skip-sessions` para omitir el escaneo de discovery de sesiones del harness).

## Modelo de privacidad

Ambas rutas de ingesta son explícitas: el tailer en vivo solo corre mientras mantengas `project engine`/`daemon` activo, y el discovery por lotes solo escribe con `--import`. Un `project compile` o `project build-site` normal lee las sesiones ya normalizadas desde `.tesserae/harness_sessions/` y los registros en vivo de `.tesserae/sqlite.db`, pero por sí mismo no hace surprise-scrape de directorios privados de transcript del harness.

Los registros de sesiones importados son artefactos locales del proyecto. Revísalos antes de publicar un sitio público, especialmente si tus transcript pueden incluir secretos, rutas privadas, datos de clientes o código no publicado.

## Descubrir e importar sesiones locales

Desde la raíz del proyecto:

```bash
tesserae project sessions discover --import
```

Discovery escanea raíces locales de transcript de Claude Code y Codex que pertenecen al directorio de trabajo del proyecto actual. Usa `--root` para escanear un directorio de configuración específico y repite `--harness` para limitar discovery:

```bash
tesserae project sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

Sin `--import`, discovery imprime lo encontrado sin escribir registros de sesión normalizados.

## Importar JSON normalizado directamente

Si otra herramienta ya produjo JSON `HarnessSession` normalizado, importa un archivo o una lista de archivos:

```bash
tesserae project sessions import path/to/session.json path/to/more-sessions.json
```

Cada entrada puede contener un objeto de sesión o una lista de objetos de sesión.

## Listar sesiones importadas

```bash
tesserae project sessions list
```

Las sesiones se almacenan debajo de:

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

Las sesiones monitoreadas en vivo también se registran en el SQLite `HarnessSessionsDB` (`.tesserae/sqlite.db`), que además persiste los read offset por archivo desde los que el tailer reanuda. `project sessions list` reporta la vista combinada.

## Construir las páginas estáticas de sesiones

Después de importar sesiones, reconstruye el sitio:

```bash
tesserae project build-site
```

El sitio emite:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

El sitio generado enlaza Sessions desde el global rail, las tarjetas Browse de inicio, las entradas de búsqueda y el breadcrumb trail de cada página de detalle de sesión.

## Diseño de la página de detalle de sesión

Las páginas de detalle usan el shell compartido del sitio estático en vez de un transcript dump independiente. Incluyen:

- hero y stat strip;
- resumen de alto nivel;
- timeline y size metadata;
- decisions, files, commands, tools y errors cuando existen;
- subagent tree colapsado;
- conversación user/assistant turno por turno;
- tool-use blocks colapsados adjuntos bajo el turno assistant anterior;
- un conversation rail izquierdo que enlaza a anchors `#turn-N`.

El markdown de conversación se renderiza mediante el renderer markdown del sitio. Superficies semánticas como inline code, command/tag markup explícito, paths, filenames y hashtags se decoran como chips compactos; los sustantivos aleatorios en mayúscula no se convierten automáticamente en chips.

Typography actual de transcript:

| Surface | Selector | Size |
|---|---|---|
| Prosa markdown de conversación | `.session-turn-text`, prose children | `8px` |
| Code fences genéricos de conversación | `.session-turn-text pre` | `10px` |
| Contenido fenced code Bash/shell | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool details/summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-use header | `.session-tool-use-header` | `8px` |
| Tool payload text | `.session-tool-use-text` | `6px` |

## Checklist de publicación de sesiones

Antes de desplegar un sitio público que incluya sesiones:

1. Ejecuta `tesserae project sessions list` y confirma que el conteo sea el esperado.
2. Inspecciona `.tesserae/harness_sessions/` en busca de contenido sensible.
3. Reconstruye con `tesserae project build-site`.
4. Abre localmente `sessions/index.html` y al menos una página de detalle de sesión.
5. Confirma que los tool blocks estén colapsados por defecto y que los raw tool payloads sean aceptables para publicar.
6. Despliega con `tesserae project deploy --build` una vez que el source tree esté committed.
