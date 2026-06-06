# Tesserae como motor de contexto — Análisis de brechas

<!-- translations:start -->
<p align="center"><a href="../context-engine-audit.md">English</a> · <a href="context-engine-audit.ko.md">한국어</a> · <a href="context-engine-audit.zh.md">中文</a> · <a href="context-engine-audit.ja.md">日本語</a> · <a href="context-engine-audit.ru.md">Русский</a> · <a href="context-engine-audit.es.md">Español</a> · <a href="context-engine-audit.fr.md">Français</a> · <a href="context-engine-audit.de.md">Deutsch</a></p>
<!-- translations:end -->
> **Misión (2026-06-02):** Tesserae es un *motor de contexto*: genera contexto
> listo para agentes reconstruyendo una base de conocimiento **que se mejora a
> sí misma** mediante tres pilares: **(1) monitoreo de sesiones**, **(2)
> ingesta proactiva autónoma** y **(3) documentos bajo demanda**. El
> conocimiento debe ser **en tiempo real y en evolución**, listo para entregar
> a los agentes.

Este documento audita la base de código actual frente a esa misión. Es el
resultado de una revisión paralela de cuatro vías (ingesta/sesiones,
automejora, salida/de cara al agente, orquestación/ciclo de vida).

## Veredicto en una línea

Tesserae hoy es un **compilador CLI por lotes mecánicamente sano y bien
probado**. Frente a la visión de motor de contexto, está **con disparo manual
+ a posteriori + sujeto a git-HEAD** en los tres pilares. La maquinaria para
construir el motor ya existe como primitivas; lo que falta es la **capa
continua y autoconducida** que las compone.

La pieza faltante más grande en cada corte: un **supervisor/demonio de larga
duración que posea un único bucle de eventos** y conduzca de forma autónoma el
seguimiento de sesiones, la ingesta, la compilación incremental y la
publicación. Todo lo demás es incremental sobre eso.

---

## Pilar 1 — Monitoreo de sesiones → **a posteriori, no en vivo**

| Estado | Hallazgo | Lo que se necesita |
|---|---|---|
| brecha | La captura de sesiones es un escaneo a posteriori: `discover_harness_sessions()` recorre transcripciones terminadas solo cuando un humano ejecuta `sessions discover --import` o `compile`. `compile` se niega deliberadamente a escanear `~/.claude/projects/` (latencia). | Un **tailer** que vigile los archivos JSONL del harness e ingiera turnos mientras la sesión transcurre. |
| brecha | El único vigilante real (`watch.py WatchLoop`) cubre el **markdown fuente**, sondea cada 2 s y dispara un `compile` completo. No vigila ni sesiones ni código. | Extender a disparadores de sesión + herramientas fuente bajo un solo supervisor. |
| brecha | El bucle «en vivo» de `vault_watch.py` actúa sobre la **salida** (resincronización inversa de Obsidian), no sobre la ingesta. | No sustituye la extracción de conocimiento en vivo. |
| tosco | La reextracción de sesión se cachea por `session_id` pero **de sesión completa**: un solo turno nuevo invalida toda la caché y reejecuta el pase LLM completo. | Incrementalidad a nivel de turno para el tailing en vivo. |
| tosco | El almacén `harness_sessions` es un glob plano con reescaneo total en cada list/write. | Almacén indexado/anexable para un conjunto de capturas en crecimiento continuo. |
| ausente | No hay marcas de tiempo de frescura/procedencia por nodo; la vigencia se rastrea solo a nivel de artefacto (git HEAD). | Frescura por hecho para «¿qué tan fresco es esto?». |

## Pilar 2 — Base de conocimiento que se mejora a sí misma → **reextracción de un disparo, evolución acoplada**

Los pases «en evolución» existen, pero corren **solo dentro de un único
`compile`** (una reextracción desde cero), y la mayoría son **opt-in vía flag
de entorno o CLI manual**. Los hechos se recalculan en cada compilación, no se
revisan en su lugar.

| Estado | Hallazgo | Lo que se necesita |
|---|---|---|
| brecha | El **decaimiento (Decay)** (`memory/decay.py`, semivida de Ebbinghaus de 14 d) se calcula solo *en tiempo de consulta*, nunca se persiste ni se reescribe en compilación. | Escritura de decaimiento en compilación + puntuación persistida. |
| brecha | El bucle de acceso del decaimiento está **muerto**: `last_accessed_at == first_seen_at`, `access_count` nunca se incrementa. La señal «sigo mirando esto → importa» no hace nada. | Una superficie de registro de acceso (lectura MCP → incremento). |
| brecha | La **sustitución (Supersede)** (`memory/supersede.py`) está cerrada tras `TESSERAE_SUPERSEDE_PASS=true` (apagado por defecto) y solo *añade* aristas, nunca degrada/oculta contenido obsoleto. La revisión de creencias es cosmética. | Encendido por defecto + consumidores que supriman hechos sustituidos en la salida. |
| brecha | Las **contradicciones (Contradictions)** se *detectan* (`lint.py`, severidad info, coincidencia de cadenas frágil) pero nunca se *resuelven*. Sin arbitraje de confianza. | Un pase de resolución, no solo una sonda. |
| brecha | La **deriva de esquema (Schema drift)** (`schema_drift.py`) es un subcomando manual `schema-drift` que solo escribe propuestas; el esquema nunca se autorrefina. | Ruta de aplicación + integración en el pipeline. |
| brecha | La **canonicalización (Canonicalization)** solo autofusiona alias de alta confianza; el resto se encola para aprobación humana por CLI. | Fusión automática arbitrada por LLM con el tiempo. |
| brecha | **Bucle de retroalimentación medio cerrado**: el extractor base determinista *ignora la guía por completo* (`selective_extractor.py:43`); solo la ruta LLM opcional consume correcciones. Con LLM apagado, las correcciones humanas nunca vuelven a la extracción. | Que la ruta determinista honre la guía, o LLM por defecto. |
| brecha | Sin **refuerzo de insights recurrentes**: nada refuerza la confianza cuando un insight reaparece entre sesiones. `temporal.infer_confidence` es una heurística de cadenas burda. | Frecuencia entre sesiones → confianza numérica. |
| tosco | El emparejamiento de candidatos a sustitución es **Jaccard léxico (0.55)**; las reformulaciones semánticas con poco solape léxico nunca llegan a candidatas. | Generación de candidatos basada en embeddings. |
| ausente | **Todo el corte de automejora está sin probar** (sin tests de decay/supersede/feedback/drift/canonical/temporal). | Tests junto a cualquier cambio aquí. |

## Pilar 3 — Documentos bajo demanda → **aún no existe**

La fontanería de consulta/recuperación es madura (RRF híbrido, PPR, ~20
herramientas MCP, ask por página, exportaciones para IA). Pero **cada
artefacto es o una proyección estática de todo el corpus o una búsqueda de un
solo nodo.** «El usuario pide „dame contexto sobre X“ → documento a medida» no
está implementado. Las primitivas para construirlo están todas presentes pero
nunca se componen.

| Estado | Hallazgo | Lo que se necesita |
|---|---|---|
| ausente | **Generación de documentos bajo demanda (la brecha central del Pilar 3).** Ningún módulo produce un documento a medida y acotado por consulta a partir de una petición. `report.py` es un resumen de lint en compilación, no un artefacto de conocimiento. | Nuevo `context_compiler`: búsqueda → PPR → recorrido de vecindario → ensamblado del cuerpo → síntesis LLM opcional. |
| brecha | `wiki_page` devuelve un cuerpo de nodo precompilado; no hay herramienta de ensamblado multinodo y acotado por consulta. | Herramienta MCP `compile_context(query|seeds, depth, budget)`. |
| brecha | `ask` devuelve prosa o una lista de resultados, nunca un artefacto de contexto descargable/transferible. | Modo de respuesta que emita un paquete de contexto estructurado y citado. |
| brecha | `agent_harness.py` es una transferencia **estática** (top-12 nodos cableados + lista fija), no acotada por consulta ni por tarea. | Aceptar un tema/semilla → renderizar un brief acotado. |
| brecha | `node_context` es de 1 salto, sin ranking. Débil como primitiva de contexto para agentes. | Enrutar por PPR para contexto k-salto rankeado. |
| brecha | Las exportaciones (`llms.txt`, `graph.jsonld`) son volcados de todo el corpus; sin corte por tema. | Subgrafo acotado por tema → rebanada llms-txt. |
| tosco | El carril de embeddings por defecto es un **pseudo-embedding determinista por cubos hash** (blake2b, 128 dim); backend semántico real solo si `sentence-transformers` está instalado, y `auto` degrada en silencio. La recuperación «semántica» de fábrica es falsa. | Embeddings reales por defecto, o una advertencia ruidosa sobre el carril hash. |
| tosco | `query.answer()` **descarta una respuesta LLM válida** si carece de coincidencia regex de cita de nodo. | Conservar la respuesta; marcar en su lugar las citas faltantes. |
| tosco | El widget ask del host estático sirve **`DEMO_QA` enlatado**; el ask real solo funciona bajo `serve`. El «ask» público de Pages es teatro. | Aceptable para demo; no consumible por agentes en el sitio publicado. |
| tosco | El backend `auto` de `ask` se traga las excepciones y degrada de forma invisible a BM25. | Exponer qué backend respondió y por qué se dispararon los fallbacks. |

## Transversal — Orquestación y ciclo de vida → **CLI por lotes, sin motor**

| Estado | Hallazgo | Lo que se necesita |
|---|---|---|
| brecha | **Sin proceso demonio/motor.** Despachador argparse plano de un disparo; el proceso sale tras cada subcomando. Cero manejo de signal/SIGTERM/pidfile/launchd; los vigilantes mueren con un `KeyboardInterrupt` pelado. | Un demonio supervisado de larga duración que posea un bucle de eventos + apagado elegante. |
| brecha | «Continuo» = sondeador markdown `while True: time.sleep(interval)`. Sin eventos de sistema de archivos, contrapresión ni streaming. | Núcleo orientado a eventos con un único planificador. |
| brecha | **«Refresh» vive en un skill markdown de slash-command**, no en código: secuencia `sessions discover --import` → `compile` → `obsidian-sync`. | Orquestador de pipeline en proceso de primera clase, compartido por demonio/CLI/MCP. |
| tosco | La compilación incremental `changed_only` es **frágil y autodescrita como un apaño**: el manifiesto es `{path: sha256}`; hay que recargar el grafo previo, despojar nodos de proyector/síntesis, desalojar nodos fuente reextraídos y luego fusionar, o una edición de 21 archivos colapsa 2400 nodos a 1700. | Una capa incremental/streaming diseñada que fluya por el puerto `GraphStore`. |
| tosco | `cli.py` es un despachador-dios de ~2000 líneas (escalera `if args.command == ...`); `ask`/`wiki` tienen parsers hechos a mano aparte. | Registro de comandos / módulos de subcomando. |
| tosco | Los flags con compuerta de fase entregan superficie a medio terminar: la ayuda de `--sessions-llm` dice *«se respetará cuando aterrice Phase 5»*. | Terminar u ocultar. |
| tosco | `graph_stores/url_resolver.py` envuelve un almacén async con `asyncio.run` **por llamada**: un nuevo bucle de eventos por upsert. Patológico para streaming. | Runtime async persistente si el motor entra en producción. |
| tosco | Los protocolos hexagonales de `ports/` están definidos, pero el pipeline autónomo los **eludía**, yendo directo a artefactos JSON. Solo HypePaper usa el puerto. | Hacer fluir el pipeline núcleo por `GraphStore` de forma consistente. |
| tosco | Tres formatos de persistencia (artefacto JSON, almacén SQLite, Kuzu) sin única fuente de verdad; el adaptador Kuzu envuelve en base64 cada campo para esquivar un bug de corrupción de 0.16. | Converger a una sola fuente de verdad. |
| tosco | `serve` (`TCPServer.serve_forever`) y `watch` son **procesos bloqueantes separados**: no se puede servir + recompilar automáticamente a la vez. `deploy` es un git push manual, desacoplado. | Unificar serve + watch + deploy bajo el supervisor para publicación continua. |
| ausente | `frontend.py` es un **módulo muerto deprecado** que aún se entrega, duplicando `tesserae/site/`. | Eliminar o migrar los llamadores. |
| tosco | El bucle de revisión humana de `review_workflow.py` emite JSON `"action": "TODO: merge|keep_separate"` con tipado de cadenas para edición manual; sin ruta de aplicación programática. | Cola de revisión integrada cableada en la compilación. |
| nota | Los marcadores TODO/FIXME son genuinamente escasos: la deuda real son los **apaños documentados en comentarios** (fusión changed-only, base64 de Kuzu, asyncio-por-llamada), no TODOs dispersos. | — |

---

## Orden de construcción recomendado (delta de arquitectura → visión)

1. **Demonio supervisor + orquestador de pipeline en proceso.** Un bucle de
   eventos, señales/apagado, reemplazando la cadena de refresh del skill
   markdown. *Desbloquea todos los demás pilares.*
2. **Monitor de sesiones en vivo.** Tail del JSONL del harness → extracción
   incremental a nivel de turno → alimentar el bucle. (Reemplaza el manual
   `sessions discover --import`.)
3. **Compilación incremental/streaming real** por el puerto `GraphStore`,
   retirando el frágil parche de desalojo `changed_only`.
4. **Activar los pases de automejora por defecto + persistirlos**: escritura
   de decaimiento en compilación, incremento de access-count en lecturas MCP,
   supersede encendido (con supresión), resolución de contradicciones,
   confianza de insights recurrentes.
5. **Compilador de contexto bajo demanda** (herramienta MCP `compile_context`
   + CLI): consulta → PPR/híbrido → recorrido de vecindario → documento
   ensamblado, citado y listo para agentes.
6. **Embeddings reales por defecto** (o una advertencia ruidosa de
   degradación) para que la recuperación semántica no sea un stub hash de
   fábrica.
7. **Unificar serve + watch + deploy** para publicación continua; añadir tests
   de ciclo de vida (la capa de la que más depende la visión es hoy la menos
   cubierta).

## Fortalezas que vale la pena preservar

Compilación determinista idéntica byte a byte; amplia cobertura de tests sobre
la maquinaria por lotes; recuperación RRF híbrida limpia + ponderación
reflexiva de aristas PPR; superficie de herramientas MCP amplia y
correctamente particionada (pública/privada); exportaciones estáticas sólidas
(`llms.txt`, JSON-LD, RSS) y un widget ask consciente de la seguridad. La base
es fuerte; el trabajo es añadir encima la capa dinámica y autoconducida.
