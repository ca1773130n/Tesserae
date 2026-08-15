# Memoria de agente en capas — gráficos de conocimiento por agente

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.zh.md">中文</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.fr.md">Français</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

Nadie lo recuerda todo y ninguna ventana de contexto de agente puede contenerlo todo.
La respuesta de Tesserae es una **base de conocimientos en capas**: cada agente cultiva su propia memoria a partir de sus propias sesiones, esa memoria se **destila** periódicamente (se organiza, compacta, pule, refina — y se olvida de forma segura), y los gerentes solo ven la capa destilada de sus informes. El gerente del gerente ve un resumen adicional. Como en una organización real, un único lector nunca necesita todo el archivo.

Todo lo siguiente es opcional y aditivo: los proyectos que nunca ejecutan `tesserae distill` se comportan exactamente como antes.

## Las capas

- **L0 — el gráfico del proyecto** (`.tesserae/graph.json`). Sin cambios. Sigue siendo
  idempotente de bytes. El pase estructural de la compilación ahora acuña un nodo `Agent`
  por agente observado más aristas `performed_by` desde cada sesión — atribución sin procesar, costo LLM cero.
- **L1 — un artefacto por agente** (`.tesserae/agents/<key>/distilled.graph.json`).
  Escrito por `tesserae distill`. Un archivo de gráfico ordinario limitado a **una lectura de 48 k**,
  por lo que cualquier agente puede cargar toda su memoria destilada en una sola llamada.
- **L2' — acumulaciones de gerentes.** Al destilar un agente que tiene informes, acumula
  L1 de informes: deduplicación por linaje, agrupación por evidencia cruda compartida y
  llevar la mejor nota **literalmente** — la profundidad de resurumen de LLM se limita a 1, por lo que un resumen nunca es una paráfrasis de un resumen. El mismo pase recurre a cualquier profundidad organizativa.

## Identidad del agente

Los agentes se codifican con `harness:account:role` — grado de rol, por lo que un subagente `reviewer` y un subagente `planner` desarrollan *diferentes* experiencias incluso en una máquina. Los roles provienen de descriptores de subagente en transcripciones, luego de reglas de coincidencia de registro declarativo, luego regresan a `default`.

```bash
tesserae agents init         # escanear sesiones, INFERIR la organización, escribir .tesserae/agents/registry.json
tesserae agents tree         # el organigrama, con recuentos de sesiones + obsolescencia de destilación
tesserae agents list         # claves observadas, etiquetas, padres, recuentos de sesiones
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # migra el directorio de artefactos + registro atómicamente
```

`init` deduce la jerarquía de la señal de rol. Una función de subagente(`claude-code:me:reviewer`) está subordinada al agente principal que la generó(`claude-code:me:default`), por lo que un comando le proporciona una organización multinivel que funciona — `set-parent` no es necesario. Pase `--flat` para forzar el gráfico anterior "todos bajo raíz". `set-parent` es solo para jerarquías más profundas diseñadas a mano. La configuración cero sigue funcionando: sin registro, cada agente informa a `org:root` y `agent="org"` es la descripción general del equipo plano.

## Destilación

```bash
tesserae distill                      # cada agente, hojas primero, gerentes último
tesserae distill --agent <key>        # un agente
tesserae distill --dry-run            # estimar llamadas LLM, no escribir nada
tesserae distill --max-llm-calls 50   # presupuesto duro; ejecuciones limitadas convergen en reruns
tesserae distill --retry-fallbacks    # reintentar grupos que fallaron
tesserae distill --full               # ignorar marcas de agua, redestilación desde cero
```

El pase agrupa los hallazgos del agente, resume cada grupo (lista blanca de citas y revisión de fidelidad) y acuña notas destiladas cuya identidad es una **clave de linaje** — el hash de la evidencia L0 sin procesar debajo, nunca la redacción del LLM. El almacenamiento en caché es agresivo y compartido: las entradas sin cambios se omiten con marca de agua, los grupos en crecimiento se pliegan incrementalmente, los errores del proveedor se cortan y producen alternativas estructurales deterministas (marcadas, reintentables, nunca en caché como éxito).

La destilación es **opcional**: configure `TESSERAE_AGENT_DISTILL=1` (o `{"agent_distill": {"enabled": true}}` en `config.json`). Cuando está habilitado, `tesserae refresh` también destila automáticamente — pero solo agentes bajo **presión de memoria** (sus hallazgos sin destilar ya no caben en media lectura de contexto), el activador de consolidación estilo MemGPT.

## Consolidación automática (ciclo de sueño)

No tiene que recordar destilación. Como un cerebro que consolida la memoria durante el descanso, el demonio siempre activado `tesserae engine` se consolida por sí solo cada vez que un proyecto entra en **inactividad**(sin ediciones o sesiones durante unos minutos), más un techo periódico para que un proyecto continuamente ocupado aún se consolide. Cada ejecución realiza cinco operaciones: **comprime y olvida** (el pase de destilación a continuación), permite que el conocimiento no recuperado **se desvanezca por desuso**(la descomposición LRU anterior), **descubre nuevas conexiones** entre lo que sobrevive, y después gasta dos pequeños presupuestos de llamadas LLM por tick **pre-calentando** los resúmenes que leen los agentes — resúmenes de comunidad para los alcances en los que desciende `graph_map`, y resúmenes de dominio para los dominios activos del acta. El paso de destilación envuelve exactamente el activador `maybe_distill_on_refresh` descrito anteriormente — el mismo gate de aceptación, marca de agua por agente y verificaciones de presión de memoria — por lo que el ciclo es una operación sin cambios a menos que `TESSERAE_AGENT_DISTILL` esté establecido, se ejecute bajo la puerta de compilación y no perturbe artefactos deterministas.

Comportamiento completo, banderas CLI(`--consolidate-idle` / `--consolidate-every` / `--consolidate-check` / `--summarize-budget` / `--brief-budget`) y notas de flota:
[docs/engine-consolidation.md](engine-consolidation.es.md).

## Olvido — nunca eliminación

- **Absorber**: un hallazgo decaído, de baja confianza cubierto por un destilado de calidad llm se pliega en él (`absorbed_refs`) y se suprime en lecturas predeterminadas — pero permanece accesible a través de `include_superseded` y `drill_down`.
- **Degradar**: todo lo demás en el peor de los casos cae de la cuerpo completo a una línea de título+referencia en la nota de Índice del agente. Solo la edad nunca hace que el conocimiento sea invisible.
- **Por desuso (LRU)**: la decadencia es impulsada por *recencia de recuperación*, no solo por edad de creación. Leer acceso de registro de superficie — `last_accessed_at` / `access_count` — en un `node_memory` sidecar (nunca en `graph.json`). La destilación fusiona ese estado de acceso en vivo en su vista de trabajo **antes de** calcular la decadencia, por lo que un hallazgo que nunca se recupera decae y se vuelve elegible para ser absorbido o degradado, mientras que uno que fue leído recientemente se mantiene independientemente de la edad. Un sidecar vacío reproduce exactamente el comportamiento anterior solo por edad.
- **Libro mayor**: cada promoción/degradación/absorción se agrega al libro mayor de olvido y se muestra en `tesserae lint` (`AGENT_FORGET_LEDGER`), junto con una métrica de atraso sin destilar por agente (`AGENT_UNDISTILLED_BACKLOG`).
- **Quién lo leyó** (opcional): el contador de acceso anterior dice que un nodo
  fue leído, no por quién — así que un agente parlanchín sondeando un nodo y una
  persona leyéndolo una vez son la misma entrada para el olvido. Define
  `TESSERAE_READ_AUDIT=1` en el servidor MCP y cada lectura se registra además
  como `{tool, actor, node_ids, at, tesserae_version}` en el mismo sidecar
  `.tesserae/sqlite.db`, legible mediante la herramienta `read_audit` con un
  recuento por actor. El actor es el argumento `agent` cuando la llamada
  resuelve una vista de agente, y si no `TESSERAE_ACTOR`; sin ninguno de los
  dos la lectura se registra como anónima en lugar de atribuirse a un nombre
  inventado. **Apagado por defecto a propósito** — un libro siempre activo sobre
  cada superficie de lectura convierte cada lectura en una escritura. Apagarlo
  detiene el registro sin borrar lo registrado, y nada de esto llega nunca a
  `graph.json`.

## Conexiones descubiertas

Más allá de la compresión y el olvido, la consolidación también **descubre nuevas conexiones** entre notas destiladas — entre agentes dentro del proyecto, no solo dentro de un agente. Incrusta notas y vincula pares cercanos como aristas `shares_concept_with` (llevando un marcador `federation_semantic`). El descubrimiento está **cerrado por incorporación** — se ejecuta solo cuando hay un backend de incrustación real configurado y omite el código hash — por lo que nunca fabrica enlaces espurios. Los bordes se escriben en una superposición de **sidecar acumulativa** bajo `.tesserae`, *nunca* en `graph.json`, y se fusionan en memoria en tiempo de consulta/PPR/lectura de federación (exactamente como la superposición de vista de alcance). Cada ciclo de consolidación deduplica y extiende lo que encontraron ciclos anteriores. Véase
[docs/engine-consolidation.md](engine-consolidation.es.md) para la operación de ciclo de sueño que la ejecuta.

## Lectura de una vista de alcance

Desde la **CLI**, `--agent KEY` limita `query`, `ask` y `context`:

```bash
tesserae query "release checklist" --agent claude-code:me:reviewer   # vista de trabajador
tesserae ask "what does my team know about deploys?" --agent org      # equipo completo
tesserae agents show claude-code:me:manager    # modo, miembros, obsolescencia
tesserae agents drill SessionInsight:abc123 --agent claude-code:me:reviewer
```

En **MCP**, cada herramienta de lectura de gráfico acepta el mismo `agent=`. En ambos casos, la clave se resuelve a uno de:

- **clave de trabajador** → experiencia cruda propia ∪ notas destiladas propias, destilado preferido (la cruda absorbida se suprime automáticamente por una superposición derivada en tiempo de carga — nunca se vuelve a escribir en `graph.json`).
- **clave de gerente** → una federación de solo artefactos L1 de informes. Los hallazgos sin procesar nunca se filtran hacia arriba.
- **`org`** → todos los artefactos destilados, configuración cero.

Herramientas de apoyo: `agents show` / `agent_view_explain`(miembros + marca de agua antigüedad `distilled_through` — qué edad tiene la experiencia de cada informe) y `agents drill` / `drill_down`(resolver `member_refs` de nota destilada a evidencia L0 sin procesar con estado vivo/cambiado/absorbido/desaparecido — cada llamada se registra en auditoría). `compile_context --multi-pool` reserva espacios de presupuesto para notas destiladas y perfiles de experiencia y etiqueta conocimiento obsoleto o de calidad de reserva en la salida. Solo un nodo que un productor realmente creó puede ocupar un espacio —los pases de destilación, el pase session-event o el propio `graph_write` de un agente—, así que un grupo cuyo tipo solo está poblado por extracción de documentos queda vacío, y tanto la CLI como `knobs.pool_reservations` nombran los grupos que no devolvieron nada.

## El ciclo de crecimiento

- **Arnés por agente**: `write_harness` modo de agente emite un directorio de arnés por
  agente cuya configuración de MCP llega a la vista resuelta de ese agente, más un
  `purpose.md` página de misión que se genera a partir de su perfil de experiencia.
- **Orientación por agente**: dirigir la destilación de un agente a través de
  `.tesserae/extraction-guidance-<key>.md`, en capas sobre el nivel de proyecto
  `.tesserae/distill-guidance.md`. Editar la transmisión de un agente redestila solo ese agente.
- **Puentes semánticos** (opcional): vincular *destilados relacionados* entre agentes
  con aristas `shares_concept_with` en vistas de gerente/org — aristas, nunca fusiones.
- **Mapas de temas**: `agent_topics` convierte el conjunto de destilados de un agente en
  determinista `topics.md` — la tabla de contenidos del agente.
- **Promoción de subagente**: las ejecuciones de subagente tipadas emiten hallazgos bajo la
  propia clave del subagente, por lo que el trabajo delegado se acumula en la experiencia del delegado.

## Garantías de determinismo

El gráfico del proyecto sigue siendo idempotente de bytes; los artefactos destilados son
deterministas dados (bytes de gráfico, registro, directorio de caché, artefacto anterior,
opciones). El tiempo siempre es el **reloj del corpus** — el momento más nuevo en
las sesiones mismas, recursivamente la marca de agua secundaria más nueva para gerentes —
nunca reloj de pared. La identidad del nodo nunca depende de la prosa del LLM. Una sonda Lint
rechaza metadatos en forma de marca de tiempo/contador en nodos de capa de agente, porque
esa clase exacta de estado ha roto la idempotencia de bytes antes.

Fundamentos completos del diseño: `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
