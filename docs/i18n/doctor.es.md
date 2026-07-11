# `tesserae doctor` — comprobaciones de salud del proyecto

<!-- translations:start -->
<p align="center"><a href="../doctor.md">English</a> · <a href="doctor.ko.md">한국어</a> · <a href="doctor.zh.md">中文</a> · <a href="doctor.ja.md">日本語</a> · <a href="doctor.ru.md">Русский</a> · <a href="doctor.es.md">Español</a> · <a href="doctor.fr.md">Français</a> · <a href="doctor.de.md">Deutsch</a></p>
<!-- translations:end -->
`tesserae doctor` inspecciona un workspace de Tesserae de extremo a extremo — inicialización,
integridad del grafo, consistencia del registro, frescura, locks, login del LLM e
higiene de disco — e imprime una lista de verificación. Es **de solo lectura por defecto**; `--fix`
aplica únicamente las reparaciones que son seguras de re-ejecutar y nunca puede destruir
estado vivo.

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## Qué comprueba

Veinte comprobaciones, agrupadas por categoría:

| Comprobación | Categoría | Qué verifica | Acción de `--fix` |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` existe y tiene aspecto de workspace de Tesserae | solo informe (sugiere `tesserae init`) |
| `graph_parse` | core | `graph.json` se parsea y tiene la forma esperada | solo informe (sugiere `tesserae compile`) |
| `config_valid` | core | `.tesserae/config.json` se parsea y valida contra la plantilla de init | solo informe |
| `vault_configured` | core | la ruta del vault configurada se resuelve | **SAFE**: crea el directorio del vault resuelto cuando vive dentro del proyecto |
| `registry_consistent` | registry | las entradas de `~/.tesserae/registry.json` apuntan a raíces de proyecto reales | **SAFE**: poda las entradas cuya raíz ha desaparecido, elimina la clave legacy `active`; un grafo ausente es solo informe |
| `graph_staleness` | freshness | delta de git desde el `git_head` registrado en la última compilación | solo informe (sugiere `tesserae refresh` — las compilaciones son pesadas) |
| `site_search_index` | freshness | el sitio estático / `search-index.json` es más reciente que `graph.json` | **SAFE**: reconstruye el sitio |
| `backend_artifacts` | freshness | los artefactos de RAG-Anything están al día | solo informe (su refresco es pesado en LLM/red) |
| `session_chunks` | freshness | la cobertura de [session-chunks diarios](session-chunks.es.md) no tiene huecos en la ventana reciente | solo informe (sugiere `tesserae sessions chunk-backfill`) |
| `wiki_lint` | graph | deriva grafo ⇄ wiki + hallazgos de lint trivialmente corregibles | **SAFE**: aplica las correcciones triviales del lint (`fix_trivial`) |
| `compile_lock` | processes | si hay un lock de compilación vivo retenido, y por qué pid | solo informe — doctor **nunca mata ni elimina un lock vivo** |
| `daemon_pid` | processes | `daemon.pid` apunta a un proceso de engine vivo | **SAFE**: elimina el pidfile cuando su propietario está muerto |
| `llm_login` | environment | el backend LLM configurado es realmente utilizable (CLI de claude/codex con sesión iniciada, o clave de API presente) | solo informe (sugiere `claude /login` / `codex login`) |
| `optional_deps` | environment | estado de las dependencias opcionales (memex, cognee, raganything) | solo informe (las instalaciones usan red) |
| `embedding_backend` | environment | hay disponible un backend real de embeddings semánticos | solo informe (sugiere `pip install tesserae[semantic]`) |
| `environment` | environment | resumen completo de la detección de entorno | sección de solo informe |
| `build_history` | hygiene | tamaño y forma de `.build-history` | **SAFE**: lo recorta, preservando siempre la entrada `git_head` más reciente (la comprobación de staleness depende de ella) |
| `idempotence` | hygiene | el tripwire `idempotence_suspect` del snapshot de salida | solo informe (es una señal de bug, no algo que auto-reparar) |
| `orphan_worktrees` | hygiene | registros obsoletos de `git worktree` | **SAFE**: `git worktree prune`; borrar directorios es solo informe |
| `hook_log_bloat` | hygiene | crecimiento de `.tesserae/.session-*-hook.log` | **SAFE**: rota/trunca los logs de más de 10 MB |

Una comprobación que crashea se reporta como un hallazgo de error — doctor en sí nunca lanza excepciones.

## Política de `--fix`

- `--fix` ejecuta **solo** las comprobaciones marcadas SAFE arriba, y luego re-detecta para
  que el informe refleje el estado posterior a las correcciones.
- Cada corrección es idempotente: ejecutar `doctor --fix` dos veces deja la segunda ejecución
  limpia.
- Doctor **nunca mata un proceso y nunca elimina un lock de compilación vivo** — un
  lock retenido se reporta con su pid propietario y se deja en paz.
- Las operaciones pesadas o que usan red (recompilaciones, instalaciones de dependencias,
  refrescos de backends) nunca se incorporan a `--fix`; doctor imprime el comando para que
  lo ejecutes tú.

## Códigos de salida

La misma convención que `tesserae lint`:

| Código de salida | Significado |
|---|---|
| `0` | saludable — sin hallazgos por encima de OK |
| `1` | hay advertencias |
| `2` | hay errores |

## Artefactos de informe

Cada ejecución escribe ambas formas del informe en el workspace:

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json` además imprime el informe JSON en stdout en lugar de la lista de verificación
markdown. `--all` itera sobre cada proyecto del registro (ignorando
`--project`) e informa por proyecto.

## MCP: `doctor_report`

El servidor MCP expone el mismo informe como la herramienta `doctor_report` (reflejando
`lint_report`, incluido su tope de bytes en el contenido devuelto), de modo que un agente pueda
comprobar la salud del workspace a mitad de conversación sin salir a la shell. Requiere una
raíz de proyecto — pasa `graph_path`/`project` o configura un grafo por defecto.
