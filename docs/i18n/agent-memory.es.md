# Memoria de agente estratificada — gráficos de conocimiento por agente

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.zh.md">中文</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.fr.md">Français</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

Nadie recuerda todo — y ninguna ventana de contexto de agente cabe todo.
La respuesta de Tesserae es una **base de conocimiento estratificada**: cada agente desarrolla
su propia memoria a partir de sus propias sesiones, esa memoria se **destila** periódicamente
(se organiza, comprime, pule y refina — y se olvida de forma segura), y los gerentes
solo ven la capa destilada de sus informes. El gerente del gerente ve un resumen adicional.
Como una organización real, ningún lector individual necesita el archivo completo.

Todo lo siguiente es opcional y aditivo: los proyectos que nunca ejecutan `tesserae distill`
se comportan exactamente como antes.

## Las capas

- **L0 — gráfico de proyecto** (`.tesserae/graph.json`). Invariable, sigue siendo
  idempotente en bytes. El paso estructural de compilación ahora genera un nodo `Agent`
  por agente observado más bordes `performed_by` de cada sesión — atribución sin procesar,
  costo LLM cero.
- **L1 — un artefacto por agente** (`.tesserae/agents/<key>/distilled.graph.json`).
  Escrito por `tesserae distill`. Un archivo de gráfico ordinario acotado a **una lectura de 48k**,
  por lo que cualquier agente puede cargar toda su memoria destilada en una sola llamada.
- **L2 — rollups de gerentes.** Al destilar un agente que tiene informes, se hace rollup
  de los L1 de los informes: deduplicar por linaje, agrupar por evidencia sin procesar compartida
  y conservar la mejor nota **literalmente** — la profundidad de resumen de LLM está limitada
  a 1, por lo que un resumen nunca es una paráfrasis de un resumen. El mismo paso se repite
  recursivamente a cualquier profundidad organizacional.

## Identidad del agente

Los agentes se clave como `harness:account:role` — nivel de rol, de modo que un subagente
`reviewer` y un subagente `planner` desarrollan *diferentes* conocimientos especializados
incluso en una máquina. Los roles provienen de descriptores de subagentes en transcripciones,
luego de reglas de coincidencia de registro declarativo, luego regresan a `default`.

```bash
tesserae agents init         # escanear sesiones, proponer .tesserae/agents/registry.json
tesserae agents list         # claves observadas, etiquetas, padres, recuentos de sesiones
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # migra directorio de artefactos + registro atómicamente
```

Funciona sin configuración: cada agente observado reporta implícitamente a `org:root`,
y `agent="org"` proporciona una descripción general de equipo plana sin registro.

## Destilación

```bash
tesserae distill                      # todos los agentes, hojas primero, gerentes último
tesserae distill --agent <key>        # un agente
tesserae distill --dry-run            # estimar llamadas LLM, no escribir nada
tesserae distill --max-llm-calls 50   # presupuesto duro; las ejecuciones limitadas convergen en reiteraciones
tesserae distill --retry-fallbacks    # reintentar clústeres que fallaron
tesserae distill --full               # ignorar marcas de agua, redestilación desde cero
```

El paso agrupa hallazgos de un agente, resume cada clúster (lista blanca de citas
y verificación de fidelidad), y genera notas destiladas cuya identidad es una **clave de linaje** —
el hash de la evidencia L0 sin procesar subyacente, nunca la redacción de LLM. El almacenamiento
en caché es agresivo y compartido: las entradas sin cambios se omiten con marca de agua,
los clústeres en crecimiento se plieguen incrementalmente, las fallas del proveedor se abren
en circuito y producen reservas estructurales deterministas (marcadas, reintentables, nunca
cacheadas como éxito).

La destilación es **opcional**: establecer `TESSERAE_AGENT_DISTILL=1` (o
`{"agent_distill": {"enabled": true}}` en `config.json`). Cuando está habilitado,
`tesserae refresh` también se destila automáticamente — pero solo agentes bajo *presión de memoria*
(sus hallazgos no destilados ya no caben en la mitad de una lectura de contexto), disparador
de consolidación tipo MemGPT.

## Olvido — nunca eliminación

- **Absorber**: un hallazgo decaído, de baja confianza cubierto por un destilado de calidad llm
  se pliega en él (`absorbed_refs`) y se suprime en lecturas predeterminadas — pero permanece
  accesible a través de `include_superseded` y `drill_down`.
- **Degradación**: todo lo demás en el peor de los casos cae del cuerpo completo a una línea
  de título+referencia en la nota de índice del agente. La edad sola nunca hace que el conocimiento
  sea invisible.
- **Libro mayor**: cada promoción/degradación/absorción se anexa al libro mayor de olvido
  y se muestra mediante `tesserae lint` (`AGENT_FORGET_LEDGER`), junto con una métrica de
  pendientes no destilados por agente (`AGENT_UNDISTILLED_BACKLOG`).

## Lectura como agente — argumento `agent=`

Cada herramienta de lectura de gráfico MCP acepta `agent=`:

- **clave de trabajador** → experiencia sin procesar propia ∪ notas destiladas propias,
  preferencia destilada (sin procesar absorbido se suprime automáticamente por una superposición
  derivada en tiempo de carga — nada se escribe nunca en `graph.json`).
- **clave de gerente** → una federación de solo artefactos L1 de informes. Los hallazgos
  sin procesar nunca se filtran hacia arriba.
- **`org`** → todos los artefactos destilados, sin configuración.

Herramientas de apoyo: `agent_view_explain` (miembros + marca de agua `distilled_through`
de antigüedad — cuán antiguo es el conocimiento especializado de cada informe), y
`drill_down` (resolver `member_refs` de una nota destilada de nuevo a evidencia sin procesar
L0 con estado vivo/cambiado/absorbido/desaparecido — cada llamada se registra en auditoría).
`compile_context --multi-pool` reserva espacios de presupuesto para notas destiladas
y perfiles de conocimiento especializado, y etiqueta el conocimiento de calidad anticuado
o fallback en la salida.

## El bucle de crecimiento

- **Arnés por agente**: el modo de agente `write_harness` emite un directorio de arnés
  por agente cuya configuración MCP alcanza la vista resuelta de ese agente, más una página
  de misión `purpose.md` sembrada una sola vez generada a partir de su perfil de conocimiento especializado.
- **Guía por agente**: dirigir la destilación de un agente a través de `.tesserae/extraction-guidance-<key>.md`,
  en capas sobre el nivel de proyecto `.tesserae/distill-guidance.md`. Editar la secuencia de
  un agente redestila solo ese agente.
- **Puentes semánticos** (opcional): vincule destilados *relacionados* entre agentes con
  bordes `shares_concept_with` en vistas de gerente/organización — bordes, nunca fusiones.
- **Mapas de temas**: `agent_topics` enrolla el conjunto de destilados de un agente en
  un `topics.md` determinista — la tabla de contenidos del agente.
- **Promoción de subagente**: las ejecuciones de subagente escritas generan hallazgos bajo
  la propia clave del subagente, por lo que el trabajo delegado se acumula en el conocimiento
  especializado del delegado.

## Garantías de determinismo

El gráfico de proyecto permanece idempotente en bytes; los artefactos destilados son
deterministas dados (bytes de gráfico, registro, directorio de caché, artefacto anterior, opciones).
El tiempo es siempre el **reloj de corpus** — el instante más reciente en las sesiones mismas,
recursivamente la marca de agua más reciente del hijo para gerentes — nunca reloj de pared.
La identidad del nodo no depende de la prosa de LLM. Un sonda lint rechaza metadatos
en forma de marca de tiempo/contador en nodos de nivel de agente, porque esa clase exacta
de estado ha roto la idempotencia de bytes antes.

Justificación de diseño completa: `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
