# Referencia de ajuste — variables de entorno

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Cada control que Tesserae lee del entorno, su valor por defecto y cuándo
realmente querrías cambiarlo. Nada aquí es obligatorio: los valores por defecto
se eligieron para que un simple `tesserae compile` funcione correctamente.

La configuración del proyecto y global (`.tesserae/config.json`, `~/.tesserae/config.json`)
tienen prioridad sobre la configuración del servidor LLM; las variables de entorno
abajo anulan ambas para la ejecución donde estén establecidas.

---

## Hooks que gastan dinero

Los hooks que el plugin Claude Code envía pueden lanzar una compilación en segundo plano. Cualquier cosa que gaste está **desactivada por defecto**:

```sh
export TESSERAE_HOOK_AUTOCOMPILE=1   # opt in a compilaciones automáticas
```

Bajo control: `posttooluse-edit.sh` (se ejecuta en cada Edit/Write) y `session-end.sh`. No bajo control, porque no cuestan nada: `session-start.sh` ejecuta `tesserae code sync`, que es determinístico, y `pretooluse-compile.sh` solo intercepta un `tesserae compile` que escribiste tú mismo.

Este defecto existe porque fue medido. Una base de conocimiento en `~/.tesserae` hace que `$HOME` parezca la raíz del proyecto, y el resolver del hook caminaba *hacia arriba* desde el directorio de trabajo hacia el primer `.tesserae/` que encontraba — así que cualquier sesión fuera de un proyecto registrado se resolvía en `$HOME` y compilaba el directorio de inicio completo: 15k archivos, un gráfico de 795 MB, **~10 horas de gasto en LLM**, desde un proceso separado que sobrevivió a la sesión que lo inició.

`resolve_project_root()` ahora rechaza `$HOME` por cualquier ruta, y devuelve vacío en lugar de caer a la copia de seguridad del directorio de trabajo, así que los llamadores no-op en lugar de adivinar. Un hook que lanza trabajo del modelo debe activarse deliberadamente, no desactivarse después de que llegue la factura.

## Extracción

### `TESSERAE_EXTRACT_TIMEOUT`

**Por defecto `1800` (segundos), por intento.** Acota cada llamada de
extracción codex/claude para que un proceso hijo bloqueado no pueda colgar
la compilación.

Esto sucedió: una compilación se observó al 0% CPU durante **5 h 43 m**
con un proceso hijo `codex exec` inactivo durante **4 h 6 m**, manteniendo
`.tesserae/compile.lock` todo el tiempo. Ya había construido 32 resúmenes
de comunidad en memoria pero nunca llegó a persistirlos.

Por intento, no por documento — al timeout el cliente rota a la siguiente
carpeta de configuración `CODEX_HOME` / claude, así que el peor caso para
un documento es `timeout × perfiles configurados`.

```sh
export TESSERAE_EXTRACT_TIMEOUT=3600   # más tiempo para documentos muy grandes
export TESSERAE_EXTRACT_TIMEOUT=0      # sin límite — ejecutar hasta completarse
```

Un valor que se establece pero no es utilizable (`10m`, `600s`, negativo, `inf`)
advierte en stderr y mantiene el valor por defecto. Un error tipográfico no debe
desactivar silenciosamente una válvula de seguridad.

### `TESSERAE_EXTRACT_CONCURRENCY`

**Por defecto `4`.** Documentos extraídos en paralelo. Cada uno es un
proceso hijo CLI bloqueante que toma aproximadamente un minuto, por lo que
un bucle secuencial hace que el tiempo de reloj sea literalmente la suma
de cada ida y vuelta del modelo — medido en ~2 h 40 m para 161 documentos.

El techo es el límite de velocidad de tu cuenta de proveedor, no tu máquina,
por lo que el valor por defecto es modesto. Establece `1` para un comportamiento
estrictamente secuencial.

La concurrencia nunca cambia el resultado: la lista de trabajo se fija en
orden de ruta y los resultados se recopilan por índice, así que una ejecución
en paralelo es byte-idéntica a una secuencial.

### `TESSERAE_LLM_CACHE`

**Por defecto activado.** Caché de contenido direccionable de respuestas del
proveedor CLI bajo `~/.tesserae/llm_cache`, indexado por un resumen del prompt
realmente enviado, más el modelo y esfuerzo de razonamiento — así que una pregunta
diferente re-pregunta, y cambiar modelos re-pregunta en lugar de servir respuestas
del modelo anterior. Solo se almacenan respuestas parseables, por lo que una
generación deficiente no puede volverse permanente.

Las entradas antiguas son inalcanzables por diseño: la clave solía ser una etiqueta
proporcionada por la etapa de llamada en lugar de un resumen del prompt, por lo
que preguntas no relacionadas podían compartir una entrada. Nada las migra — se
puede eliminar el directorio sin riesgo, y una compilación lo rellenará.

```sh
export TESSERAE_LLM_CACHE=0   # siempre re-preguntar
```

### `TESSERAE_LLM_CHUNK_CHARS`

Caracteres por fragmento cuando un documento es demasiado grande para una
llamada. Déjalo sin establecer a menos que estés alcanzando límites de contexto.

---

## Servidor LLM

| Variable | Por defecto | Notas |
|---|---|---|
| `TESSERAE_LLM_PROVIDER` | `claude` | `codex`, `claude`, `anthropic`, `custom` |
| `TESSERAE_LLM_MODEL` | específico del proveedor | Limitado por proveedor para que un modelo tipo claude nunca llegue a la ruta codex |
| `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | La extracción estructurada no necesita el `xhigh` que puedas establecer para trabajo interactivo — `xhigh` hace que una compilación multiDocumento sea muchas veces más lenta |
| `TESSERAE_CLAUDE_CONFIG_DIRS` | — | Directorios de configuración de Claude separados por `os.pathsep`, en orden de rotación: el canal de entorno para un `--claude-config-dir` repetido. Solo una lista *configurada* es autoritativa; el `CLAUDE_CONFIG_DIR` del entorno deliberadamente no lo es, porque fijarse a él colapsa la rotación multicuenta a una sola cuenta |

`tesserae config status` imprime el servidor resuelto y lo verifica para vivacidad.

---

## Pasadas de compilación

| Variable | Por defecto | Qué controla |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **activado** | Pasada de resumen estilo GraphRAG. Una llamada LLM por cluster ≥ 5 miembros, almacenada en caché por resumen de membresía. Deshabilita con `false`/`0`/`no`/`off` |
| `TESSERAE_ENABLE_LLM_PASSES` | desactivado | Pasadas de enriquecimiento LLM opcionales más allá de la extracción |
| `TESSERAE_AGENT_DISTILL` | desactivado | Artefactos de pericia L1 por agente (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | desactivado | Nodos de memoria destilada Runbook/Gotcha |
| `TESSERAE_SESSION_EVENT_PASS` | **activado** | Nodos `Event` por turno a partir de las transcripciones de sesión. Sin LLM y determinista byte a byte, pero un nodo por cada turno significativo: considerable en un corpus largo. `false`/`0`/`no`/`off` lo desactiva |
| `TESSERAE_INSIGHT_SYMBOL_LINK` | activado | Vincula ideas de sesión a símbolos de código |
| `TESSERAE_SUPERSEDE_PASS` | activado | Aristas `superseded_by` entre reclamaciones revisadas |
| `TESSERAE_PROMPT_SIGNATURES` | desactivado | Registra firmas de prompt para detección de deriva |
| `TESSERAE_COMPILE_LOCK_WAIT` | — | Segundos a esperar `.tesserae/compile.lock` antes de rendirse |

**Sobre resúmenes de comunidades:** la pasada de compilación cubre ansiosamente
el nivel más grueso; `graph_map` además materializa lazily un resumen la primera vez
que desciendes a un alcance frío, almacenado en caché por nivel. Desactivar la pasada
es una estrategia de costo legítima — solo pagas por ramas que realmente visitas —
pero con una advertencia: **la descención federada nunca materializa lazily.**
Las tarjetas de un proyecto hermano solo pueden nombrarse desde sus resúmenes en gráfico
o cachés ya calientes, así que un proyecto en el que navegas entre proyectos quiere
la pasada ansiosa activada.

---

## Consulta y síntesis

| Variable | Por defecto | Notas |
|---|---|---|
| `TESSERAE_QUERY_LLM` | desactivado | Planificador LLM para `tesserae query` |
| `TESSERAE_QUERY_DRY_RUN` | desactivado | Planifica sin llamar al modelo |
| `TESSERAE_SYNTHESIS_LLM` | desactivado | Síntesis de prosa en `tesserae ask` |
| `TESSERAE_SYNTHESIS_MODEL` | — | Anula el modelo de síntesis |
| `TESSERAE_SYNTHESIS_WORKERS` | — | Trabajadores de síntesis paralela |
| `TESSERAE_SYNTHESIS_DRY_RUN` | desactivado | Salta el modelo, ejecuta la tubería |
| `TESSERAE_VERIFY_BAND` | desactivado | Vuelve a decidir con el modelo las marcas de revisión dudosas de `ask`. `on` usa la banda medida 0.30–0.70; `lo-hi` la sustituye. Desactivado, las marcas no cuestan tokens ni red |

### `TESSERAE_VERIFY_BAND`

Cada respuesta de `ask` lleva marcas de revisión por frase que no cuestan nada. Son
menos exactas que preguntar a un modelo — 0.870 frente a 0.926 sobre 755 frases
reservadas — y casi toda la diferencia son falsas alarmas sobre paráfrasis fieles,
que comparten poco vocabulario con su fuente.

Los dos se equivocan en frases distintas, así que pagar al modelo solo donde la
comprobación gratuita duda recupera la exactitud por una fracción del coste. Ceder la
cobertura 0.30–0.70 dio 0.932 en el 42% de las llamadas: indistinguible de preguntar
por cada frase (McNemar p=0.52), por el 42% del gasto.

```bash
export TESSERAE_VERIFY_BAND=on          # la banda medida 0.30-0.70
export TESSERAE_VERIFY_BAND=0.40-0.60   # más estrecha: 22% de las llamadas, 0.914
```

Desactivado por defecto, porque las marcas están documentadas como algo que no cuesta
tokens ni red, y una cascada que se encendiera sola rompería eso para todo el que
llame. El sobre informa de `adjudicated`: `null` cuando la cascada no se ejecutó, y un
recuento cuando sí. Un modelo que no puede responder deja en pie el veredicto
gratuito — una llamada fallida nunca puede dejar limpia una frase marcada.

---

## Rutas e infraestructura

| Variable | Por defecto | Notas |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Ubicación del registro de proyectos. La respetan **todos** los comandos — hasta 0.28.7 solo la leía el modo flota del engine, así que fijarla en cualquier otro sitio no tenía efecto y los comandos seguían usando el registro real |
| `TESSERAE_HOST_ID` | generado una vez en `~/.tesserae/host_id` | La identidad de esta máquina. Ver [ejecutar varias máquinas](#ejecutar-varias-máquinas-contra-un-mismo-proyecto) |
| `TESSERAE_DISCOVERY_CACHE` | — | Caché de descubrimiento de sesión |
| `TESSERAE_ARXIV_CACHE` | — | Caché de metadatos arXiv |
| `TESSERAE_NO_FEDERATION_CACHE` | desactivado | Deshabilita el LRU del gráfico federado |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | desactivado | Emite el gráfico combinado entre proyectos |
| `TESSERAE_FLEET_PIDFILE` | — | Archivo pidfile de la flota del motor |
| `TESSERAE_CLIP_TOKEN` | — | Secreto compartido para el cortador web |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | desactivado | Aplica los registros **aprobados** en `.tesserae/schema-drift-proposals.json` en tiempo de compilación (determinístico, sin LLM). Escribe propuestas con `tesserae schema-drift`; aprobar una significa editar primero `ResearchNodeType`, luego establecer `"approved": true` — un nombre no resolvible no retipifica nada. |

---

## Quién leyó el grafo

| Variable | Defecto | Notas |
|---|---|---|
| `TESSERAE_READ_AUDIT` | **desactivado** | Registra las lecturas que mueven recuentos de acceso — `{tool, actor, node_ids, at, tesserae_version}` — en una tabla `read_audit` en `.tesserae/sqlite.db`, legible de vuelta a través de la herramienta `read_audit` con un recuento por actor. Una fila se escribe dondequiera que un recuento de acceso sea bumped, así que el recuento de filas sigue la superficie en lugar de la llamada: una herramienta que devuelve una lista de nodos (`search_nodes`, `node_context`, `compile_context`, `graph_map`, `graph_ppr`, `ask` / `query`, `drill_down`, `find_session_findings`) escribe **una fila por llamada** nombrando cada nodo que contó, mientras `fresh_insights` bumps dentro de su propio bucle y así escribe **una fila por nodo** que devolvió. Una llamada que no devuelve nada no escribe ninguno, y una herramienta que no lee nodo alguno — `schema`, `graph_summary` — nunca alcanza la auditoría, porque una fila sin nombre de nodo no explica ningún recuento de acceso. Desactivado por defecto porque una auditoría siempre activa a través de toda la superficie de lectura convierte cada lectura en una escritura; la compuerta se sienta antes de abrir la tienda, ya que crear la tabla es en sí una escritura. Nada sobre esto alcanza nunca `graph.json` |
| `TESSERAE_ACTOR` | — | A quién atribuir una lectura cuando la llamada no porta una vista de agente. El actor es el argumento `agent` si la llamada resolvió uno, de otro modo esto; sin establecer registra la lectura como anónima en lugar de inventar un nombre |

Apagar `TESSERAE_READ_AUDIT` deja de registrar sin borrar lo que
ya fue registrado, y toma efecto sin reiniciar el servidor. Lo que la
auditoría es *para* es [olvido por desuso](agent-memory.es.md#olvido--nunca-eliminación):
los recuentos de acceso impulsan lo que se absorbe o se degrada, y sin un actor un
agente locuaz polleando un nodo y un humano leyéndolo una vez son la misma entrada.

---

## Ejecutar varias máquinas contra un mismo proyecto

La forma para la que está escrito esto: varios servidores ejecutan cada uno un
agente de código, cada uno tiene sus propios transcripts de sesión locales, y
comparten un disco — así que ven el mismo directorio de proyecto y el mismo
`.tesserae/`.

**Dale la compilación a un solo host, y deja que el resto solo cosechen.**

```bash
# on the compiling host
tesserae engine

# on every other host
tesserae engine --harvest-only
```

`--harvest-only` va volcando los transcripts locales de esa máquina al almacén de
sesiones compartido y nunca toma el lock de compilación del proyecto. Eso elimina
la contención en vez de arbitrarla, y por eso gana a ajustar timeouts.

**Cuando sí quieres hacer cola en lugar de fallar**, pasa `--wait`:

```bash
tesserae compile --wait          # up to 30 min, reporting every 5s
tesserae compile --wait 120      # or name your own bound
```

Sin él, una compilación que encuentra el lock retenido sale con 2 — correcto para
un hook, exasperante para una persona. `--wait` es un flag y no algo inferido de
si stdout es un terminal, porque el mismo comando no debe cambiar de
comportamiento bajo `tee`, en una captura de tmux o en CI.
`TESSERAE_COMPILE_LOCK_WAIT=<seconds>` hace lo mismo para todo un árbol de
procesos.

**Mantener frescos todos los proyectos** desde una sola invocación:

```bash
tesserae refresh --all               # every registered project, sequentially
tesserae refresh --all --jobs 3      # three at a time
tesserae compile --all --name alpha --name beta
```

Que un proyecto falle no detiene a los demás. Sale `2` si alguno falló, `1` si
alguno estaba bloqueado por otra ejecución, `0` si todo se ejecutó. `--jobs` vale
1 por defecto porque una compilación es pesada en LLM y subirlo gasta cuota en
paralelo.

### Qué hace que esto sea seguro

El estado por máquina se guardaba antes bajo un único nombre compartido y lo leía
cada host. Cada uno de estos está ahora particionado por id de host:

| Estado | Dónde | Por qué tiene que ser por host |
|---|---|---|
| Registros de sesión | `.tesserae/harness_sessions/` | Un host solo poda lo que él mismo cosechó. Si no, el host B borra las sesiones del host A y reporta éxito — el escaneo de cada host estampa el mismo productor y sus rutas `~/.claude` se resuelven igual, así que nada más los distingue |
| Pidfile del engine | `.tesserae/daemon.<host>.pid` | La comprobación de vida es `os.kill(pid, 0)` contra la tabla de procesos **local**; un pid escrito por otra máquina se juzga contra un proceso local sin ninguna relación |
| Suelo de escaneo de Codex | `.tesserae/harness_sessions.db` | Una única marca de agua compartida hacía que el host que se ejecutara el último la moviera más allá de transcripts que el otro no había leído — esos no se importaban en absoluto |

El id de host se genera una sola vez en `~/.tesserae/host_id` (por máquina, **no**
en el directorio compartido del proyecto) y puede fijarse con `TESSERAE_HOST_ID`.
Es un id persistido en lugar del hostname porque una flota construida a partir de
una sola imagen reutiliza hostnames, y una colisión entregaría los registros de
una máquina a otra.

### La suposición que deberías probar

Todo lo anterior asume que `flock(2)` lo **aplica** el sistema de archivos que
aloja `.tesserae/`. Sobre NFS y SMB eso depende de la configuración, y sin un
lock daemon en funcionamiento `flock` puede degradarse silenciosamente a un
no-op — momento en el cual dos hosts compilan el mismo proyecto simultáneamente,
creyendo cada uno que tiene un lock exclusivo.

`tesserae doctor` advierte cuando el proyecto está sobre un sistema de archivos
de red, pero un solo host **no puede** probar que se aplique entre hosts.
Pruébalo directamente sobre el hardware real: retén un lock en el host A y
confirma que al host B se le deniega.

---

## Recuperación de un corpus degradado

Cuando la extracción falla para un documento, se sirve por la línea base
determinística y se **marca** en `.tesserae/manifest.json`. Sin la marca sería
indistinguible de una extracción limpia, así que `--changed-only` lo saltaría
para siempre y la degradación sería permanente hasta que el contenido del
archivo cambiara.

```sh
tesserae compile --changed-only --retry-fallbacks
```

Reintenta solo los documentos marcados; los limpios permanecen saltados.

## Inspeccionando la jerarquía

```sh
tesserae graph-map                          # mapa raíz
tesserae graph-map --scope <scope_id>       # descender
tesserae graph-map --scope '<alias>::'      # un proyecto hermano registrado
```

Cada tarjeta reporta `size` y `leaf_member_count` del archivo adjunto de
jerarquía, más `live_member_count` — cuántos miembros el gráfico *actual*
realmente lleva. Un `0` allí significa que el alcance está muerto
(sesgo de archivo adjunto/gráfico): sáltalo en lugar de descender.

## Agentes escriben en el gráfico

`graph_write` (MCP) toma nodos y bordes tipados validados por esquema con proveniencia obligatoria, por lo que un agente registra un hallazgo como *estructura* en lugar de prosa que un extractor tiene que adivinar los tipos.

Rechaza en lugar de obligar: bordes sin tipo, tipos de nodo o borde fuera del vocabulario controlado, puntos finales pendientes y escrituras sin proveniencia se rechazan todas. Las escrituras duplicadas son idempotentes. Los nodos escritos por agentes sobreviven a una recompilación completa, `graph.json` eliminado, `--limit` y eliminación de corpus completo.

## Verificar una reclamación contra el gráfico

`verify_claim` (MCP) responde si el gráfico licencia un triple. Toma `(subject, predicate, object)` — **no hay parámetro de lenguaje natural**, por diseño, porque un analizador hizo que la versión anterior respondiera SUPPORTED a la negación de un reclamo que apoyaba.

El veredicto es una función pura de bytes de gráfico: sin LLM, sin incrustación, sin coincidencia difusa en ningún lugar del camino de decisión.

| Veredicto | Significado |
|---|---|
| `SUPPORTED` | el borde existe, lleva su propia evidencia, y ese texto fue reorientado contra el archivo fuente |
| `PRESENT_UNEVIDENCED` | el borde existe pero nada respaldado por documento lo respalda |
| `CONTRADICTED` | un `contradicts_claim` respaldado por documento entre los mismos dos extremos |
| `DISPUTED_UNEVIDENCED` | desacuerdo afirmado, ninguno evidenciado |
| `CONFLICTING` | ambas polaridades respaldadas por documento — la herramienta se niega a arbitrar |
| `ABSENT` | este gráfico no afirma el triple. No es una refutación |
| `NOT_RESOLVABLE` | no se puede resolver un extremo o predicado exactamente |

Hay dos cosas que deliberadamente no hará. Nunca trata `supersedes` como refutación — esa relación dice que un *nodo* fue reemplazado, no que un triple sea falso. Y una escritura de agente solo puede *debilitar* una clase de procedencia, nunca actualizar una, por lo que nada de lo que un agente afirma puede presentarse como fundamentado en documentos.

Vale la pena saber al leer resultados: en un gráfico real de 15.284 bordes, alrededor del 40% de los veredictos `SUPPORTED` son tautológicos — bordes `evidenced_by` cuyo intervalo citado es el objetivo del propio borde. Verdadero, pero no informativo.

## Enrutamiento de una pregunta

`tesserae ask` elige una ruta de recuperación por forma de pregunta: las búsquedas de entidad única van al backend económico, las preguntas multi-salto / "qué cambió" / "por qué" / amplitud del corpus van al gráfico. Esa división codifica una **hipótesis, no una medición**: esperamos que el recorrido compense su costo en preguntas multi-salto, temporales y de síntesis, y que lo desperdicie en búsquedas de hechos simples. Nada en este repositorio lo comprueba — aquí no hay ninguna prueba de rendimiento de recuperación ni ninguna cifra publicada detrás de la tabla de enrutamiento, así que trátala como un valor por defecto que conviene anular, no como un resultado.

La decisión aparece en el sobre devuelto, por lo que una respuesta económica es auditable. Anularlo con `--route` en la CLI, o el parámetro `route` en la herramienta MCP.
