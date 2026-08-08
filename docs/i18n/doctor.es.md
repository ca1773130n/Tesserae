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

Las comprobaciones, agrupadas por categoría:

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
| `compile_lock` | processes | si hay un lock de compilación vivo retenido, y por qué pid **y qué host** | solo informe — doctor **nunca mata ni elimina un lock vivo** |
| `filesystem_locking` | processes | si `.tesserae/` está sobre un sistema de archivos de red, donde `flock(2)` puede ser un no-op silencioso | solo informe (no puede probar que se aplique entre hosts — ver abajo) |
| `daemon_pid` | processes | `daemon.<host>.pid` apunta a un proceso de engine vivo | **SAFE**: elimina el pidfile **de este host** cuando su propietario está muerto; el de otra máquina se reporta, nunca se toca |
| `llm_login` | environment | si existen los directorios de configuración que el proyecto usaría de verdad | solo informe — **no verifica credenciales** (ver abajo) |
| `optional_deps` | environment | estado de las dependencias opcionales (memex, raganything) | solo informe (las instalaciones usan red) |
| `embedding_backend` | environment | hay disponible un backend real de embeddings semánticos | solo informe (sugiere `pip install tesserae[semantic]`) |
| `environment` | environment | resumen completo de la detección de entorno | sección de solo informe |
| `build_history` | hygiene | tamaño y forma de `.build-history` | **SAFE**: lo recorta, preservando siempre la entrada `git_head` más reciente (la comprobación de staleness depende de ella) |
| `idempotence` | hygiene | el tripwire `idempotence_suspect` del snapshot de salida | solo informe (es una señal de bug, no algo que auto-reparar) |
| `orphan_worktrees` | hygiene | registros obsoletos de `git worktree` | **SAFE**: `git worktree prune`; borrar directorios es solo informe |
| `hook_log_bloat` | hygiene | crecimiento de `.tesserae/.session-*-hook.log` | **SAFE**: rota/trunca los logs de más de 10 MB |
| `code_scope_leftovers` | hygiene | restos de la capa de código retirada: `code-graph*.json`, filas de tipos de código en `sqlite.db` | solo informe — la limpieza es un borrado masivo, así que vive en su propio verbo (ver abajo) |

Una comprobación que crashea se reporta como un hallazgo de error — doctor en sí nunca lanza excepciones.

## Qué te dice `llm_login` y qué no

Informa de que existe un directorio de configuración. **No** informa de que la CLI
que hay dentro tenga un token válido, y lo dice en el texto de su propio hallazgo.

La distinción no es pedantería. La comprobación solía reportar `credentialed LLM
CLI: claude, codex` apoyándose en archivos como `~/.claude/history.jsonl` — que
prueban que la CLI se ha *usado*, no que pueda autenticarse *ahora*. Ejecutados
uno detrás de otro en el mismo segundo, `tesserae compile` imprimía `Claude CLI
not logged in (tried 1 config dir)` mientras doctor imprimía una marca verde. Un
diagnóstico que contradice el fallo que tienes delante es peor que no tener
diagnóstico.

Verificar credenciales significa gastar una llamada real al LLM en cada
`tesserae doctor`, y ese no es un coste que esta comprobación asuma por
iniciativa propia. Así que declara solo lo que comprobó. Usa `tesserae compile`
para la respuesta autoritativa.

La comprobación se limita a los directorios que el proyecto intentaría de
verdad, resueltos por la misma ruta que usa `ProjectWiki._build_json_client` — y
no dice nada sobre los directorios de configuración de claude cuando el provider
del proyecto es `codex`.

## Discos compartidos y `flock(2)`

Toda garantía de concurrencia en Tesserae — el lock de compilación por encima de
todo — se apoya en que `flock(2)` lo aplique el sistema de archivos que aloja
`.tesserae/`. Sobre NFS y SMB eso depende de la configuración: sin un lock daemon
en funcionamiento, `flock` puede degradarse silenciosamente a un no-op, y
entonces dos hosts compilarán el mismo proyecto a la vez creyendo cada uno que
tiene un lock exclusivo.

`filesystem_locking` informa de lo que un solo host puede determinar: el tipo de
sistema de archivos que respalda el proyecto, si es un sistema de archivos de
red, y si una adquisición de `flock` llega siquiera a tener éxito. Advierte
cuando se trata de un sistema de archivos de red.

**No puede** probar que se aplique entre hosts, y no pretende hacerlo. Que un
host tome un lock no dice nada sobre si a un segundo host se le impide tomarlo.
Si ejecutas Tesserae desde varias máquinas contra almacenamiento compartido,
pruébalo directamente sobre el hardware real antes de confiar en el lock de
compilación.

## `tesserae doctor migrate-code-scope`

Una limpieza de una sola vez para un espacio de trabajo compilado antes de que el
código fuente saliera del alcance de Tesserae. Las compilaciones nuevas ya no
producen la capa de código, pero un espacio de trabajo antiguo sigue cargándola, y
la mayor parte solo se resuelve si lo pides.

```bash
tesserae doctor migrate-code-scope            # simulación — informa, no borra nada
tesserae doctor migrate-code-scope --apply    # borra de verdad
```

Elimina, en este orden:

* las páginas proyectadas bajo `.tesserae/markdown_projection/` cuyo propio
  frontmatter `type:` nombra un tipo de código retirado;
* las mismas páginas en la bóveda de Obsidian — tanto la configurada como la
  predeterminada dentro del proyecto, porque un proyecto que luego apuntó a una
  bóveda real deja la antigua llena de ellas. Una página de código con contenido
  no vacío en `user-notes` se conserva y se cuenta, nunca se borra;
* `code-graph.json` y `code-graph-cache.json`;
* las filas de las tablas auxiliares de SQLite (`node_provenance`,
  `edge_provenance`, `node_memory`) cuyo nodo o arista ya no existe, y después
  `VACUUM`.

Dos cosas que conviene saber.

**Lee el recuento de supervivientes, no el de borrados.** El directorio de
proyección es abrumadoramente derivado del código — aquí medido, 218 796 de 224 876
páginas — así que un fallo del predicado que lo borrara todo y una ejecución
correcta se parecen muchísimo en el número de borrados. El informe empieza por
cuántas páginas no-código sobreviven, que es el número que se desplomaría si el
predicado estuviera mal. La decisión es estrictamente por archivo, según su propio
frontmatter.

**Compila primero, migra después.** Las tablas `nodes` / `edges` y las auxiliares
de procedencia se reescriben en cada compilación, así que es la compilación la que
convierte esas filas en basura; este verbo es el que recupera el espacio, porque
SQLite no encoge con `DELETE`. Ejecutarlo antes es inofensivo — lo dice y no
encuentra nada que recuperar. `VACUUM` nunca se ejecuta dentro de una compilación:
toma un bloqueo exclusivo y necesita espacio libre del orden del archivo de base de
datos, y se omite con una nota cuando el disco no puede con la reconstrucción.

Deliberadamente no es accesible desde `--fix`, que está documentado como
reparaciones seguras únicamente.

## Política de `--fix`

- `--fix` ejecuta **solo** las comprobaciones marcadas SAFE arriba, y luego re-detecta para
  que el informe refleje el estado posterior a las correcciones.
- Cada corrección es idempotente: ejecutar `doctor --fix` dos veces deja la segunda ejecución
  limpia.
- Doctor **nunca mata un proceso y nunca elimina un lock de compilación vivo** — un
  lock retenido se reporta con su pid y su host propietarios, y se deja en paz.
- Doctor **nunca toca el pidfile de otra máquina.** Sobre almacenamiento
  compartido, la tabla de procesos local no dice nada sobre un pid escrito por
  otro host, así que `daemon.<other-host>.pid` se reporta y se omite sin
  excepción — ni siquiera se lee para comprobar si sigue vivo. Solo el pidfile
  propio de este host es candidato a ser eliminado.
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
