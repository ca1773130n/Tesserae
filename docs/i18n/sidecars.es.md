# `.tesserae/` — qué hay dentro y qué cuesta borrarlo

<!-- translations:start -->
<p align="center"><a href="../sidecars.md">English</a> · <a href="sidecars.ko.md">한국어</a> · <a href="sidecars.zh.md">中文</a> · <a href="sidecars.ja.md">日本語</a> · <a href="sidecars.ru.md">Русский</a> · <a href="sidecars.es.md">Español</a> · <a href="sidecars.fr.md">Français</a> · <a href="sidecars.de.md">Deutsch</a></p>
<!-- translations:end -->
Un proyecto maduro acumula alrededor de sesenta entradas bajo `.tesserae/`, y un
listado del directorio no dice nada sobre cuáles reconstruye una compilación
gratis, cuáles cuestan una pasada de LLM y cuáles guardan trabajo que no puede
reconstruir nada. `compile.lock` y un archivo tmp huérfano de cero bytes se ven
exactamente igual que `candidate-same-as.json`, que lleva veredictos humanos.

Esta página es esa respuesta, ordenada por consecuencia. La clasificación en sí
vive en `tesserae/sidecars.py` — una entrada de registro por archivo, cada una
anotando su propietario, su tipo y qué se pierde al borrarlo. El registro es la
fuente de verdad; esta página es su proyección legible, y `tesserae doctor`
imprime la real.

Cada entrada tiene dos campos independientes:

| Tipo | De dónde vienen los bytes |
|---|---|
| `derived` | una compilación los republica a partir de las fuentes |
| `accumulated` | se acumula con el tiempo; ninguna compilación lo vuelve a derivar |
| `cache` | una respuesta guardada a una pregunta que puede volver a hacerse |
| `scratch` | contabilidad de procesos: locks, pidfiles, restos tmp |

El tipo dice de dónde vienen los bytes. **No** dice si borrarlo es seguro:
`safe_to_delete` es un campo aparte, y discrepan con suficiente frecuencia como
para importar. Una `cache` cuya respuesta vino de un modelo no es segura de
borrar, y un archivo `derived` puede llevar aprobaciones humanas. Las secciones
siguientes están ordenadas por ese segundo campo, porque es el que realmente
quieres.

## Seguro de recuperar — una compilación los rehace

Borra cualquiera de estos y la siguiente compilación lo devuelve byte a byte,
sin llamar a ningún modelo:

<!-- sidecars:safe-list -->
`agent_harness` · `code-graph.json` · `combined-graph.json` ·
`competitive_report.md` · `diverged-fields.md` · `doctor-report.json` ·
`doctor-report.md` · `graph.json` · `graph.kuzu` · `graphiti_episodes.jsonl` ·
`hierarchy.json` · `lint-report.json` · `lint-report.md` · `log.md` ·
`markdown_projection` · `merge-ledger.json` · `okf` ·
`okf-imported.graph.json` · `output-snapshot.json` · `report.md` · `research` ·
`schema-drift.md` · `site` · `summaries` · `temporal_facts.jsonl` · `wiki` ·
`.watch-cache.json` · `arxiv-cache.json` · `code-graph-cache.json` · `external`

`graph.json` está en esa lista a propósito. El grafo compilado es una función
pura de las fuentes más los sidecars acumulados de más abajo — que es
precisamente por lo que hay que proteger *esos*, y por lo que el reflejo de
«borro `.tesserae/` y recompilo» es equivocado aunque su archivo más visible sea
desechable.

## Cuesta una pasada de modelo — y cambia los bytes de `graph.json`

Estas son respuestas de un LLM guardadas. Rehacerlas cuesta una pasada, y el
modelo no devuelve las mismas palabras dos veces, así que todo lo que dependa de
ellas también cambia de bytes.

| Entrada | Tipo | Qué cuesta rehacerla |
|---|---|---|
| `session_findings` | `cache` | el caso más afilado: estos hallazgos se convierten en **nodos** del grafo, así que tirar la caché vuelve a ejecutar un extractor no determinista y el siguiente `graph.json` difiere en bytes — la ruptura de idempotencia por bytes que este repositorio ya ha sufrido cuatro veces |
| `community_summaries` | `cache` | resúmenes de comunidad escritos por un LLM, indexados por el hash de miembros |
| `distill_cache` | `cache` | resultados de destilación de agentes |
| `distillation_cache` | `cache` | resultados de destilación |
| `extraction_guidance_cache` | `cache` | una viñeta redactada por el LLM por cada clúster de feedback |
| `schema_drift_cache` | `cache` | propuestas de subtipo del LLM por tipo anfitrión |
| `supersede_cache` | `cache` | arbitraje de sustitución (supersede) del LLM |
| `schema-drift-proposals.json` | `derived` | bytes derivados, contenido no derivable: el registro lleva la puerta humana `approved` y un `proposed_type` editable, así que rehacerlo cuesta una pasada **y** descarta las aprobaciones |

## Irrecuperable — nada rehace esto

Ninguna compilación vuelve a derivar nada de aquí. Borrar uno es pérdida de
datos, no una demora.

| Entrada | Tipo | Qué se pierde |
|---|---|---|
| `candidate-same-as.json` | `accumulated` | veredictos same-as humanos. Una compilación que no lo encuentra no falla: vuelve a plantear en silencio una pregunta que un humano ya respondió, y un par rechazado vuelve sin rechazar |
| `sqlite.db` | `accumulated` | mixto; ver más abajo |
| `agent-writes.jsonl` | `accumulated` | la capa escrita por agentes, reproducida como quinto productor en cada compilación; borrarla borra cada escritura de agente |
| `vault_snapshot.json` | `accumulated` | la línea base contra la que compara `vault_pull`. Bórrala a mitad de una edición y la siguiente compilación no podrá distinguir tu edición de su propia proyección anterior — todo el mecanismo de anulación del vault |
| `obsidian_vault` | `accumulated` | bidireccional y del usuario: tus ediciones aquí se reincorporan al grafo, así que no es una proyección que baste con redibujar |
| `config.json` | `accumulated` | configuración del proyecto, incluido `obsidian.vault_path` — entrada del usuario, nunca se regenera |
| `charter` | `derived` | cada compilación lo deriva de `graph.json`, pero ninguna reconstrucción lo reproduce: los slugs se acuñan a partir de las anclas que la reconstrucción escoja, así que borrarlo refunda cada dominio con otro nombre, rompe toda ruta de anclaje fijada y descarta las lápidas que eran el único registro de adónde fueron los nombres viejos |
| `agents` | `accumulated` | el `registry.json` por agente y el `purpose.md` escrito a mano |
| `discovered_links.json` | `accumulated` | la capa de asociación acumula enlaces puntuados a lo largo de varias ejecuciones; una sola no la reconstruye |
| `extraction-feedback.jsonl` | `accumulated` | correcciones humanas capturadas durante la superposición del vault y review-apply |
| `extraction-guidance.md` | `accumulated` | guía editada a mano en la que una pasada de evolve fusiona lo suyo |
| `harness_sessions` | `accumulated` | estado de sesiones importadas |
| `harness_sessions.db` | `accumulated` | sesiones de agente importadas, cuyas transcripciones de origen rotan y desaparecen: reimportar no las reconstruye |
| `session_chunks.db` | `accumulated` | turnos normalizados escritos en vivo por el tailer del demonio, desde transcripciones que no permanecen disponibles |
| `manifest.json` | `accumulated` | estado de ingesta por fuente; sin él el siguiente lote reingiere todo y vuelve a extraer sobre fuentes que ya leyó |
| `.build-history.jsonl` | `accumulated` | una línea por build con el `git_head` con el que se compiló; borrarlo deja la obsolescencia del grafo permanentemente desconocida |

### `sqlite.db` es mixto, y se clasifica por su tabla más valiosa

El espejo del grafo que contiene es derivado y `node_vectors` es una caché de
vectores desechable, pero el mismo archivo guarda `node_memory` (decaimiento,
conteos de acceso, confianza reforzada), `fact_observed` (tiempo de transacción,
un reloj real que sólo avanza) y `read_audit`, y nada de eso se recupera. Borrar
el archivo para recuperar la caché de vectores reinicia a «ahora» el «cuándo lo
supimos» de cada hecho. Recupera espacio con `tesserae doctor --fix`, que hace
vacuum, en lugar de borrando la base de datos.

## Locks, pidfiles y restos

| Entrada | Tipo | Antes de borrar |
|---|---|---|
| `compile.lock` | `scratch` | el mutex de compilación. **Nunca** lo elimina ninguna vía automática — el fallo registrado son las acumulaciones de compilaciones en SessionEnd, y la comprobación `compile_lock` de doctor es de sólo informe por lo mismo |
| `.recompile.lock.d` | `scratch` | mutex de hooks basado en mkdir; borrar uno retenido deja que dos recompilaciones compitan |
| `session_chunks.lock` | `scratch` | el flock de «saltar si está tomado» del backfill; borrar uno retenido deja que dos backfills escriban el mismo día |
| `daemon*.pid` | `scratch` | pidfile del motor, con ámbito de host como `daemon.<host>.pid`. Doctor sólo lo elimina tras confirmar que el propietario registrado está muerto **en esta máquina** |
| `graph.json.bak-*` | `scratch` | ninguna ruta de código de Tesserae los escribe. Son copias hechas a mano en una sesión de restauración: se informan, nunca se eliminan, porque las hizo una persona |
| `*.tmp*` | `scratch` | la mitad huérfana de una escritura tmp+replace, con nombre `<target>.tmp.<pid>.<hex>`. Sólo se puede borrar cuando el pid propietario ya no existe: un escritor vivo está a mitad del rename |
| `.*-hook.log*` | `scratch` | diagnósticos de hooks de shell; doctor rota los que crecen demasiado |

## `~/.tesserae/` — a nivel de máquina, mismo nombre de directorio

El directorio de ámbito de usuario comparte nombre con el del proyecto y
significa otra cosa. `config.json` existe en los dos: en el proyecto es la
configuración del proyecto; aquí es la configuración de LLM para todos los
proyectos de la máquina.

| Entrada | Tipo | Qué se pierde |
|---|---|---|
| `registry.json` | `accumulated` | el registro de proyectos. Borrarlo desregistra todos los proyectos de esta máquina |
| `config.json` | `accumulated` | configuración de LLM para toda la máquina; entrada del usuario |
| `host_id` | `accumulated` | la identidad de esta máquina. Regenerarla hace que todo pidfile y registro de sesión con ámbito de host en almacenamiento compartido parezca ajeno |
| `harness_sessions` | `accumulated` | estado de importación de sesiones para toda la máquina |
| `llm_cache` | `cache` | respuestas de LLM cacheadas; rehacerlas llama a modelos y no las reproduce |
| `federation` | `cache` | cachés de enlaces y vectores entre proyectos — seguro de borrar |
| `wiki` | `derived` | scratch de serve con ámbito de máquina — seguro de borrar |
| `engine.pid` | `scratch` | pidfile de la flota; uno obsoleto llegó a retener un pid muerto desde hacía seis días, por eso pidlock valida en vez de confiar |
| `engine.pid.lock` | `scratch` | mutex del pidfile de la flota; borrar uno retenido deja arrancar dos flotas |
| `*.bak*` | `scratch` | copias previas a la migración de `registry.json` y `config.json`. Ninguna ruta de código las escribe, así que existen porque alguien quiso conservarlas |

## Ver la clasificación real

```bash
tesserae doctor          # the `sidecars` check, under hygiene
tesserae doctor --fix    # removes orphaned tmp halves, nothing else
```

La comprobación `sidecars` lee tu `.tesserae/` real contra el registro e informa
de tres poblaciones por separado: mitades tmp huérfanas, copias
`graph.json.bak-*` hechas a mano y entradas que ninguna entrada del registro
reclama. `--fix` elimina sólo las primeras, y sólo cuando el pid del escritor
está muerto y el archivo tiene más de 24 horas — porque un escritor vivo está
entre `write_text` y `replace`, y `os.kill(pid, 0)` sólo responde sobre la tabla
de procesos local cuando varios hosts pueden montar un mismo `.tesserae/`.

**Las entradas sin clasificar se informan y nunca se tocan.** Una entrada que el
registro no reclama es más probable que sea el archivo de otra persona — tus
notas, la caché de otra herramienta — que un fallo de Tesserae, así que la
respuesta al encontrar una es nombrarla, no borrarla. También es como se hace
visible un sidecar nuevo de Tesserae que se saltó el registro.

Tesserae no incluye ningún verbo `reset` masivo. La clasificación es lo que haría
posible uno; escribir la clasificación y entregar en el mismo movimiento un
comando destructivo basado en ella es el orden equivocado.
