# Tesserae → motor de contexto — Hoja de ruta por fases

<!-- translations:start -->
<p align="center"><a href="../context-engine-roadmap.md">English</a> · <a href="context-engine-roadmap.ko.md">한국어</a> · <a href="context-engine-roadmap.zh.md">中文</a> · <a href="context-engine-roadmap.ja.md">日本語</a> · <a href="context-engine-roadmap.ru.md">Русский</a> · <a href="context-engine-roadmap.es.md">Español</a> · <a href="context-engine-roadmap.fr.md">Français</a> · <a href="context-engine-roadmap.de.md">Deutsch</a></p>
<!-- translations:end -->
Derivada de [`context-engine-audit.md`](./context-engine-audit.es.md).
Convierte el orden de construcción de 7 pasos en fases secuenciadas con
dependencias, alcance concreto y criterios de aceptación.

**Estrella polar:** un motor en ejecución continua que monitorea sesiones,
ingiere conocimiento de forma autónoma, mejora su base por sí mismo y sirve
contexto bajo demanda listo para agentes, reemplazando la CLI de
compilación por lotes manual de hoy.

> **Estado a fecha de v0.5.0 (2026-06-06):** Las fases 0–6 se han **entregado**. La columna vertebral del motor (P0 orquestador de pipeline, P1 demonio supervisor, P2 monitor de sesiones en vivo) está en `tesserae/engine/`; la infraestructura de compilación incremental P3 aterrizó pero permanece **con el flag OFF/experimental**; P4 la automejora persiste mediante el sidecar `node_memory` (confianza de recurrencia numérica, supersede activado por defecto); P5 los embeddings reales por defecto se entregaron (Pista B); y **P6 — el Compilador de Contexto Bajo Demanda — es la función estelar de v0.5.0**. La fase 7 (unificar serve + watch + deploy + tests de ciclo de vida) sigue **abierta**. El estado por fase se anota en línea más abajo. Véanse las [notas de la versión v0.5.0](release-notes/v0.5.0.es.md).

## Forma de las dependencias

```
P0 Orquestador de pipeline (extraer la cadena refresh a código)
        │
P1 Demonio supervisor (el bucle) ──────────────┐
        │                                      │
   ┌────┴─────────────── Pista A ──────┐   ┌── Pista B (paralelizable) ──┐
   P2 Monitor de sesiones en vivo      │   P5 Embeddings reales por defecto
   P3 Compilación incremental/streaming│   P6 Compilador de contexto bajo demanda
   P4 Persistencia de automejora ──────┘   (P6 depende de P5)
        │                                      │
        └──────────────► P7 Unificar serve+watch+deploy ◄──┘
```

La Pista A (ingesta en tiempo real) y la Pista B (salida de cara al agente)
pueden correr en paralelo una vez aterrice P1. P7 las hace converger.

---

## Fase 0 — Orquestador de pipeline (cimiento de mitigación de riesgo) ✅ Entregado en v0.5.0

> **Entregado en v0.5.0** — `tesserae/engine/pipeline.py` (columna vertebral del motor, fusionado en las fases 1–3).

**Objetivo:** Sacar el pipeline de refresh del markdown de slash-command a un
orquestador en proceso de primera clase que llamen el demonio, la CLI y MCP.

- **Por qué ahora:** Cada fase posterior necesita una sola ruta de código
  compartida para `ingest → compile → project → publish`. Hoy esa secuencia
  solo existe como prosa en un skill. Nada más puede automatizarse hasta que
  sea invocable.
- **Alcance:** Nuevo `tesserae/engine/pipeline.py` (un objeto `Pipeline` que
  envuelve la cadena actual `sessions discover --import → compile →
  obsidian-sync`). Enrutar los subcomandos de `cli.py` a través de él. Empezar a
  descomponer el despachador-dios `project_main` de ~2000 líneas en una tabla
  de comandos (mecánico, sin cambio de comportamiento).
- **Entregables:** `Pipeline.run(steps, changed_only=…)`; la CLI delega en él;
  tests unitarios de secuenciado de pasos + propagación de fallos.
- **Aceptación:** `tesserae project refresh` existe como código (no skill) y
  reproduce la cadena markdown byte a byte sobre el corpus de demo.
- **Riesgo:** Bajo. Refactor puro; los tests existentes guardan el
  comportamiento.
- **Hallazgos de auditoría cerrados:** «refresh vive en un skill»,
  «despachador-dios de cli».

## Fase 1 — Demonio supervisor (el bucle del motor) ✅ Entregado en v0.5.0

> **Entregado en v0.5.0** — `tesserae/engine/daemon.py` (columna vertebral del motor, fases 1–3).

**Objetivo:** Un proceso supervisado de larga duración que posea un bucle de
eventos y conduzca `Pipeline` ante disparadores, con manejo real del ciclo de
vida.

- **Por qué ahora:** Esta es la columna vertebral. La mayor brecha individual
  de la auditoría. Todo lo «continuo/autónomo» cuelga de ella.
- **Alcance:** Nuevo `tesserae/engine/daemon.py` — bucle de eventos, cola de
  disparadores, debounce/coalescencia, apagado elegante con `SIGTERM`/`SIGINT`,
  pidfile, logging estructurado. Punto de entrada CLI `tesserae engine` /
  `tesserae daemon`. Reemplazar la muerte por `KeyboardInterrupt` pelado en
  `watch.py`/`vault_watch.py` plegándolos como *fuentes de disparo* que
  alimentan la cola.
- **Entregables:** Demonio que corre indefinidamente, coalesce ráfagas en una
  ejecución de pipeline, se apaga limpiamente; unidad de ejemplo
  launchd/systemd.
- **Aceptación:** Editar un archivo fuente → el demonio coalesce y ejecuta un
  `compile(changed_only)` dentro de la ventana de debounce; `SIGTERM` sale con 0
  sin hilos huérfanos; sobrevive a una excepción de pipeline sin morir.
- **Riesgo:** Medio — corrección de concurrencia/apagado. Mitigar con un núcleo
  asyncio de un solo hilo + supervisión explícita de tareas.
- **Hallazgos de auditoría cerrados:** «sin demonio», «continuo = sondeador
  sleep», muerte del vigilante por `KeyboardInterrupt`, sin manejo de señales.

## Fase 2 — Monitor de sesiones en vivo (Pilar 1) ✅ Entregado en v0.5.0

> **Entregado en v0.5.0** — `tesserae/engine/session_tail.py` (columna vertebral del motor, fases 1–3).

**Objetivo:** Hacer tail de transcripciones del harness en vivo e ingerir
turnos mientras corren las sesiones, reemplazando el `sessions discover
--import` a posteriori.

- **Por qué ahora:** Necesita el bucle de P1 para alimentarse. Entrega el pilar
  de «monitoreo de sesiones».
- **Alcance:** Nueva fuente de disparo de tail de sesión (vigilar eventos de
  anexión JSONL de `~/.claude` / `~/.codex`) → encolar. Extracción incremental a
  nivel de turno en `session_graph*.py` para que un turno nuevo no invalide la
  caché de toda la sesión. Almacén indexado/anexable para `harness_sessions`
  (retirar el glob de re-escaneo total).
- **Entregables:** El demonio ingiere los turnos de una sesión en segundos tras
  ser escritos; `test_session_tailer.py`.
- **Aceptación:** Iniciar una sesión de agente en vivo en un proyecto vigilado →
  los nuevos hallazgos aparecen en el grafo sin comando manual; ratio de
  aciertos de caché a nivel de turno medido > re-extracción de sesión entera.
- **Riesgo:** Medio — los formatos JSONL difieren entre harnesses; lecturas de
  línea parcial.
- **Hallazgos de auditoría cerrados:** escaneo de sesión a posteriori,
  invalidación de caché de sesión entera, almacén glob plano.

## Fase 3 — Compilación incremental/streaming por el puerto GraphStore ⚙️ Infraestructura entregada en v0.5.0 (flag OFF/experimental)

> **Infraestructura entregada en v0.5.0** (sidecar de procedencia, superficie de borrado de GraphStore, runtime async persistente de url_resolver), pero el flag `incremental_compile` **permanece OFF/experimental** por brechas de multi-propietario/ciclo de vida del productor/cap-fallback. La paridad de bytes para las rutas cubiertas está demostrada. v0.5.0 también corrigió dos bugs reales de compilación hallados aquí: la idempotencia changed-only y el contrato de store inyectado.

**Objetivo:** Reemplazar el frágil parche de desalojo de grafo `changed_only`
por una capa incremental diseñada que fluya por `ports/graph_store.py`.

- **Por qué ahora:** La ingesta continua (P2) convierte el actual apaño
  reload-strip-evict-merge en una responsabilidad de corrección (la trampa
  documentada de «2400→1700 nodos»). La automejora (P4) necesita upserts por
  nodo.
- **Alcance:** Hacer que el pipeline autónomo fluya por `GraphStore` (hoy elude
  los puertos y va directo a JSON). Upsert/delete por nodo con procedencia +
  marcas de tiempo de frescura. Converger la persistencia hacia una sola fuente
  de verdad (auditoría: artefacto JSON vs SQLite vs Kuzu). Arreglar el
  `asyncio.run`-por-llamada de `url_resolver.py` (runtime async persistente).
- **Entregables:** Compilación incremental que añade/actualiza/elimina solo los
  nodos cambiados correctamente; `first_seen_at`/`last_updated_at` por nodo.
- **Aceptación:** Una edición de 21 archivos actualiza exactamente los nodos
  afectados (sin colapso); paridad de compilación completa byte a byte
  conservada como test dorado.
- **Riesgo:** Alto — toca el modelo de datos núcleo. Cerrar tras un feature
  flag; diff contra la salida de compilación completa hasta tener confianza.
- **Hallazgos de auditoría cerrados:** `changed_only` frágil, puertos eludidos,
  asyncio por llamada, tres formatos de persistencia, sin frescura por nodo.

## Fase 4 — Activar y persistir la automejora (Pilar: automejora) ✅ Entregado en v0.5.0

> **Entregado en v0.5.0** mediante el sidecar `node_memory` (`tesserae/memory/`): **supersede** activado por defecto con veredicto determinista y supresión aguas abajo, más la **confianza de recurrencia numérica** mostrada en la salida (frecuencia entre sesiones → `TemporalFactProjector`).

**Objetivo:** Hacer que la base de conocimiento realmente evolucione en su
lugar, encendida por defecto, persistida en tiempo de compilación.

- **Por qué ahora:** Depende de los upserts por nodo de P3. Cierra el corte más
  no testeado.
- **Alcance:** Persistir puntuaciones de **decaimiento** en compilación
  (`memory/decay.py` ya no solo en tiempo de consulta); incrementar
  `access_count`/`last_accessed_at` en lecturas MCP. **Supersede** encendido por
  defecto con *supresión* aguas abajo del contenido obsoleto (no solo anexar
  aristas). Añadir **resolución de contradicciones** (elevar la detección de
  `lint.py` a un pase arbitrado por confianza). **Refuerzo de insights
  recurrentes** (frecuencia entre sesiones → confianza numérica). Cablear la
  ruta de aplicación de **deriva de esquema** y la **guía de retroalimentación**
  en la extracción (la ruta determinista hoy la ignora). Generación de
  candidatos a supersede basada en embeddings (retirar Jaccard léxico).
- **Entregables:** Cada pase corre en el pipeline por defecto y reescribe; una
  nueva suite `tests/` que cubra
  decay/supersede/feedback/drift/contradiction.
- **Aceptación:** Reformular un hecho entre sesiones eleva su confianza; un
  hecho sustituido deja de aparecer en la salida de contexto; las puntuaciones
  de decaimiento persisten y se desplazan entre ejecuciones; la suite de
  automejora está verde (hoy cero tests).
- **Riesgo:** Medio — cambios de comportamiento en la salida de extracción;
  guardar con fixtures dorados.
- **Hallazgos de auditoría cerrados:** toda la tabla del Pilar 2.

## Fase 5 — Embeddings reales por defecto (cimiento de la Pista B) ✅ Entregado en v0.5.0

> **Entregado en v0.5.0** (Pista B): un `Model2VecBackend` real por defecto, un `active_embedding_backend` que **falla de forma ruidosa** (sin degradación silenciosa a blake2b), el flag del backend semántico mostrado en `embedding_status`, y un suelo de coseno que permite al carril de embeddings admitir candidatos.

**Objetivo:** Dejar de entregar un pseudo-embedding determinista por cubos hash
como carril «semántico» por defecto.

- **Por qué ahora:** El compilador de contexto de P6 solo es tan bueno como la
  recuperación. Independiente del demonio — puede empezar tan pronto aterrice
  P0.
- **Alcance:** Entregar un backend de embeddings real por defecto (o hacer que
  `auto` falle ruidosamente en vez de degradar en silencio a blake2b en
  `retrieval/hybrid.py`). Dejar que el carril de embeddings genere candidatos
  (no solo re-rankear) una vez que los embeddings sean reales.
- **Entregables:** La instalación por defecto produce recuperación semántica
  genuina, o una advertencia explícita y visible de «corriendo en stub hash».
- **Aceptación:** Las consultas de paráfrasis/sinónimos hacen aflorar nodos
  relevantes que BM25 omite; calidad de recuperación medida en un set etiquetado
  pequeño frente a la línea base hash.
- **Riesgo:** Medio — peso de dependencias / instalación offline. Ofrecer un
  defecto escalonado.
- **Hallazgos de auditoría cerrados:** defecto de cubos hash, compuerta de
  candidatos del carril de embeddings.

## Fase 6 — Compilador de contexto bajo demanda (Pilar 3) ✅ Entregado en v0.5.0 (función estelar)

> **Entregado en v0.5.0 como función estelar.** El pipeline puro `compile_context` en `tesserae/context_compiler.py` devuelve un `ContextBundle` en memoria de `ContextCitation`s (consulta/semillas → PPR + búsqueda híbrida → vecindario k-hop acotado en profundidad → ensamblaje de cuerpos wiki → síntesis LLM opcional con fallback elegante → control de presupuesto). Expuesto como la herramienta MCP `compile_context` y el subcomando CLI `tesserae project context`; `node_context` ahora tiene una ruta clasificada `use_ppr`; se entregan slices de exportación `llms.txt` con alcance por tema.

**Objetivo:** La función estrella — «dame contexto sobre X» → un documento a
medida, citado y listo para agentes.

- **Por qué ahora:** Depende de P5 (calidad de recuperación). Se beneficia de P4
  (base más limpia). La propuesta de valor central del producto.
- **Alcance:** Nuevo `tesserae/context_compiler.py`: consulta/semillas → PPR +
  búsqueda híbrida → recorrido de vecindario k-salto rankeado → ensamblar
  cuerpos wiki → síntesis LLM opcional → un documento markdown acotado con citas
  + control de presupuesto. Exponer como MCP `compile_context(query|seeds,
  depth, budget)` y CLI `tesserae context …`. Hacer `agent_harness` acotado por
  tema; enrutar `node_context` por PPR; rebanadas de exportación `llms.txt`
  acotadas por tema.
- **Entregables:** Una herramienta que devuelve un bundle de contexto
  descargable y citado para cualquier consulta; tests que aseveran la forma del
  bundle + integridad de citas.
- **Aceptación:** `compile_context("X")` devuelve un documento multinodo
  coherente cuyas citas todas resuelven; el brief del harness se regenera por
  tema en vez de top-12 cableado.
- **Riesgo:** Medio — calidad de síntesis; conservar un modo de ensamblado
  determinista sin LLM.
- **Hallazgos de auditoría cerrados:** «la generación de documentos bajo demanda
  no existe», síntesis acotada por consulta, harness estático, `node_context`
  sin ranking, exportaciones de todo el corpus.

## Fase 7 — Unificar serve + watch + deploy + tests de ciclo de vida ⏳ Abierto (post-v0.5.0)

> **Abierto a fecha de v0.5.0.** La fase de convergencia sigue siendo el próximo hito; el demonio (P1) y el lado de salida (P6) que une ya están ambos en su sitio.

**Objetivo:** Un proceso supervisado sirve el sitio, recompila ante cambios y
publica de forma continua; la capa de ciclo de vida obtiene cobertura de tests.

- **Por qué ahora:** Convergencia. Necesita P1 (demonio) y el lado de salida
  (P6) para que valga la pena publicar continuamente.
- **Alcance:** Plegar `serve.py` (`TCPServer` bloqueante) y `deploy.py` (git
  push manual) en el demonio para que serve + watch + publish compartan un
  supervisor. Publicación continua/con debounce. Añadir los tests faltantes
  `test_watch`/`test_serve`/de ciclo de vida del demonio. Eliminar el módulo
  muerto deprecado `frontend.py`. Cablear el bucle TODO con tipado de cadenas de
  `review_workflow.py` a una ruta de aplicación real.
- **Entregables:** `tesserae engine --serve --publish` corre el bucle completo;
  suite de tests de ciclo de vida; código muerto eliminado.
- **Aceptación:** Una edición de fuente se propaga a una página servida en vivo
  y (opcionalmente) a un deploy publicado sin comandos manuales; tests de ciclo
  de vida verdes.
- **Riesgo:** Bajo–Medio — mayormente integración.
- **Hallazgos de auditoría cerrados:** división serve/watch/deploy, deploy
  manual, `frontend.py` deprecado, stub del bucle de revisión, tests de ciclo de
  vida faltantes.

---

## Resumen de secuenciado

| Fase | Tema | Depende de | Paralelizable con  Estado |
|---|---|---|------|
| P0 | Orquestador de pipeline | — | —  ✅ Entregado en v0.5.0 |
| P1 | Demonio supervisor | P0 | —  ✅ Entregado en v0.5.0 |
| P2 | Monitor de sesiones en vivo | P1 | P5  ✅ Entregado en v0.5.0 |
| P3 | Compilación incremental | P1 | P5  ⚙️ Infraestructura entregada en v0.5.0 (flag OFF/experimental) |
| P4 | Persistencia de automejora | P3 | P5, P6  ✅ Entregado en v0.5.0 |
| P5 | Embeddings reales | P0 | P2, P3, P4  ✅ Entregado en v0.5.0 |
| P6 | Compilador de contexto bajo demanda | P5 | P2, P3, P4  ✅ Entregado en v0.5.0 |
| P7 | Unificar serve/watch/deploy | P1, P6 | —  ⏳ Abierto (post-v0.5.0) |

**Motor mínimo viable:** P0 + P1 + P2 + P3 — un demonio en ejecución que vigila
sesiones en vivo y compila incrementalmente. **Producto diferenciado:** añadir
P5 + P6 (contexto de agente bajo demanda). **Pulido:** P4 + P7.
