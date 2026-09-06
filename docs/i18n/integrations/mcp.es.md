# MCP — conecta Tesserae con Claude Code, Codex, Cursor

<!-- translations:start -->
<p align="center"><a href="../../integrations/mcp.md">English</a> · <a href="mcp.ko.md">한국어</a> · <a href="mcp.zh.md">中文</a> · <a href="mcp.ja.md">日本語</a> · <a href="mcp.ru.md">Русский</a> · <a href="mcp.fr.md">Français</a> · <a href="mcp.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae incluye un servidor stdio de [Model Context Protocol](https://modelcontextprotocol.io) que expone el grafo tipado compilado a cualquier cliente compatible con MCP: Claude Code, Codex CLI, Cursor, Claude Desktop y otros. El servidor anuncia las tres superficies completas de MCP — **tools**, **resources** y **prompts** — de modo que los clientes pueden tanto consultar el grafo bajo demanda como sembrar contexto de forma económica a partir de URIs canónicas.

## Requisitos previos

El servidor lee desde `.tesserae/graph.json`, por lo que se requiere una compilación inicial:

```bash
cd /path/to/your-project
tesserae init    # interactive; or --yes for non-interactive
tesserae compile  # deterministic, no LLM calls, no API keys
```

Recompila siempre que cambien tus fuentes. El servidor recogerá el nuevo grafo en la siguiente llamada a una tool sin necesidad de reiniciar.

## 1) Generar la configuración del cliente

```bash
tesserae projects mcp-config
```

Imprime un fragmento JSON aproximadamente así:

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "python3",
      "args": [
        "-m", "tesserae.mcp_server",
        "--graph", "/path/to/your-project/.tesserae/graph.json"
      ]
    }
  }
}
```

La ruta exacta se completa a partir del proyecto actual. Pasa `--name <alias>` si quieres un nombre de entrada de servidor distinto a `tesserae`.

## 2) Pégalo en tu cliente MCP

| Cliente | Ubicación de la configuración |
|---|---|
| Claude Code | `~/.claude/mcp-servers.json` (o `~/.config/claude-code/mcp-servers.json`) |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex CLI | `~/.config/codex/mcp-servers.json` |
| Cursor | Settings → MCP Servers → pega el JSON |
| Hermes | `~/.hermes/config.toml` (usa el bloque equivalente en TOML impreso por `mcp-config --format hermes`) |

Reinicia el cliente después de editarlo. La siguiente sesión se conectará y descubrirá la superficie de Tesserae.

## 3) Lo que ve el cliente

### Tools — invocadas por el modelo

Cada tool acepta un `graph_path` o `project` (alias del registro) opcional, de modo que un solo servidor puede resolver cualquier vault registrado por llamada. Si se omite, recurre al proyecto activo.

**Consulta y recuperación del grafo**

| Tool | Propósito |
|---|---|
| `graph_map` | **Empieza aquí.** Mapa presupuestado de la jerarquía del grafo: el punto de entrada de Descent. Sin ámbito devuelve el conjunto de tarjetas raíz (recuentos, hubs principales, una tarjeta por cada comunidad más gruesa); `scope='<scope_id de una tarjeta>'` desciende un nivel del dendrograma; `org:root` recorre el árbol organizativo de agentes. Orienta al agente sin que tenga que adivinar términos de búsqueda |
| `schema` | Vocabulario controlado de nodos, aristas y wiki-kinds |
| `graph_summary` | Conteo de nodos y aristas y distribución de tipos del proyecto activo |
| `search_nodes` | Filtra nodos públicos del grafo por `query`, `type`/`types`, `kind`, `limit`, `mode`/`weights` híbridos; `include_superseded` muestra nodos retirados; `explain` añade un `profile` de recuperación (ver abajo) |
| `node_context` | Un nodo + sus aristas incidentes + nodos vecinos. `use_ppr` ordena los vecinos con PageRank personalizado en lugar de un recorrido de 1 salto; `include_superseded` y `limit` acotan el resultado. Una `node_id` que perdió una fusión en una compilación posterior **no** es un fallo: se resuelve a través del ledger de fusión al nodo que la absorbió, y la respuesta gana `status: "merged"` con `merged_from` / `merged_into` para que aprendas la id a mantener de ahora en adelante. El ledger se consulta solo después de que el grafo falla, de modo que una id viva nunca puede ser redirigida |
| `embedding_status` | Reporta el backend de embeddings activo que impulsa la búsqueda híbrida, más su caché de vectores persistido — `vectors_cached` para esta clave backend/dim, y `cache_hits` / `cache_misses` / `cache_errors` a nivel de proceso, de modo que un caché frío o inescribible no pueda ser confundido con una ruta rápida. Acepta `graph_path` / `project` para escoger cuyo sidecar se reporta |
| `search_facts` | Hechos temporales proyectados desde el grafo (al estilo Graphiti), puntuados sobre el CONTENIDO del hecho — sujeto, predicado, objeto, evidencia — nunca sobre el hecho serializado, de modo que un fragmento de id o de metadatos no es una coincidencia; `dated` (`any`, `dated`, `undated`) selecciona según si el hecho lleva un `valid_from` utilizable; `current_only` filtra a los hechos vigentes, `as_of` responde a una fecha pasada. Ambos juntos se rechazan — expresan relojes distintos — y `undated_included` informa cuántas de las filas devueltas no llevan fecha |
| `timeline` | Hechos ordenados por el `valid_from` ANALIZADO para una vista longitudinal, con los hechos sin fecha agrupados detrás de todos los fechados y devueltos como `undated_events` en lugar de intercalados; `dated` (`any`, `dated`, `undated`) selecciona según si el hecho lleva un `valid_from` utilizable; `as_of` responde a una fecha pasada — un punto sobre los intervalos de validez, no un límite de rango — y `undated_included` informa cuántas de las filas devueltas no llevan fecha. `as_of` conserva los hechos sin fecha, así que ese recuento es lo que distingue una respuesta pobre de una completa. Lleva `observed_as_of` también. `total_events` cuenta cada hecho que COINCIDIÓ, no la página que recibiste — el conjunto completo de coincidencias se ordena por fecha antes de cortar la página, de modo que los eventos más antiguos son los que una línea temporal devuelve realmente, y `total_events > len(events)` es cómo distingues una página completa de una respuesta completa |
| `graph_ppr` | PageRank personalizado sembrado en uno o varios `seed_node_id`; devuelve los top-K nodos más relevantes con `alpha`, `directed`, `edge_type_weights` ajustables |
| `wiki_page` | El cuerpo de la página markdown compilada de un nodo, más los enlaces internos que referencia. Una `node_id` anticuada sigue el mismo redireccionamiento del ledger de fusión, silenciosamente — el nodo absorbido es un alias en el superviviente, de modo que la página del superviviente *es* la página que pediste |
| `raw_source` | El markdown fuente original (limitado a 16 KB). Nunca devuelve bytes: para un nodo `Artifact` te señala a `drill_down`, que informa la ruta del activo y la dirección del sitio en su lugar |
| `verify_claim` | Verifica UNA tripleta contra el grafo: búsqueda exacta, sin LLM, sin coincidencia difusa, sin resultados ordenados. Devuelve `{verdict, reason, triple, citation, provenance, advisory}`; `verdict` es `SUPPORTED` (la arista existe **y** su evidencia es un fragmento literal de documento), `PRESENT_UNEVIDENCED`, o un rechazo. Encadena `search_nodes` → `verify_claim` cuando solo tengas prosa |
| `verify_attribution` | ¿Cada cifra que reporta una respuesta se atribuye al sistema y al benchmark por los que se preguntó? Determinista, sin LLM ni grafo, sobre tres cadenas: la respuesta, la evidencia de la que se escribió y los dos nombres. Localiza cada cifra en la evidencia y comprueba de quién es el registro (fila de tabla o párrafo delimitados por una línea en blanco) en que aparece, y si ese documento nombra el benchmark. Devuelve `{flagged, reason, detail}`; `flagged` en true significa que no se pudo confirmar la propiedad, no que la cifra sea falsa; un `reason` que termina en `_uncheckable` significa que la comprobación no pudo ejecutarse. Detecta la alucinación que una comprobación de cobertura no ve: un número real tomado de la fila vecina. |
| `doctor_run` | Ejecuta las comprobaciones de salud y devuelve el informe como JSON (`findings`, `exit_code` 0/1/2). **Siempre de solo lectura**: las reparaciones nunca se ejecutan por MCP; usa `tesserae doctor --fix` en la CLI |
| `doctor_report` | El contenido de `.tesserae/doctor-report.md` (limitado a 64 KB); vacío hasta que se haya ejecutado `tesserae doctor` |
| `charter_route` | Sitúa UNA tarea en el árbol de dominios chartered en una sola llamada — la alternativa a paginar las tarjetas de `graph_map` cuando ninguna se puede elegir por nombre. Puntúa cada dominio vivo (slug, nombre del ancla y su brief si hay uno en caché) y desciende beam-1 hasta el dominio cuyo subárbol lleva la mejor evidencia; devuelve `{routed, path, brief, parent, siblings, route_quality}`, y un slug de dominio es un ámbito que sobrevive a un ingest, cosa que un community id no hace. `altitude` (`auto`/`division`/`department`/`team`) limita hasta qué profundidad puede bajar el recorrido. **Es best-effort, y lo dice**: los bytes de `charter.json` son idempotentes, este ranking no — la vía de embeddings varía según el backend de la máquina, y la fila de un dominio lleva su brief una vez que se ha escrito alguno. **Ninguna compilación escribe briefs aún**, así que hoy cada fila está fría y `warm_rows` es `0`; `route_quality` informa `{backend, semantic, corpus_rows, warm_rows, evidenced_rows}` y cada tarjeta lleva `evidence`: `lexical` (coincidencia de términos, sobrevive a un cambio de backend), `semantic` (solo similitud de embeddings, no sobrevive) o `none` (solo de paso). Una tarea que no puede situar vuelve como `routed: false` sin nombrar **ningún** dominio: no hay candidato de baja confianza del que sacar una conjetura. Necesita `.tesserae/charter/charter.json`, escrito por `tesserae compile` |
| `lint_report` | Los hallazgos de lint más recientes en tiempo de compilación (limitado a 64 KB) |

**Perfil de una recuperación.** `search_nodes` y `compile_context` toman
`explain: true` y responden con un `profile` — para cada una de las vías `bm25`, `lexical`
y `embedding` su peso, `candidates_in`, cuántas puntuó,
`embed_calls` / `cache_hits` / `cache_misses` y su tiempo de pared, más el total
`candidates_in` / `admitted` / `returned` y qué vías en realidad contribuyeron
a cada uno de los nodos que cuenta. `returned` y esa atribución de vía por nodo
son **pre-presupuesto**: la fusión fija ambos sobre su propio corte top-`k`, y un
`budget_chars` vinculante recorta ese corte después, en la capa MCP, sin
reescribir el profile. Así que bajo un presupuesto ajustado `returned` describe el
corte que produjo el recuperador en lugar de las filas de la respuesta, y la línea
`continuation` es lo que informa de la diferencia. `search_nodes` devuelve un profile; `compile_context`
devuelve una lista, una por búsqueda de semilla que ejecutó.

Desactivado por defecto, y desactivado no es una formalidad: la medición cuesta tiempo, de modo que esto es un
diagnóstico en lugar de algo que dejes puesto. No puede mover una clasificación — cada
número se lee de tablas de score y rango que la fusión ya había producido — y
con el flag desactivado la respuesta lleva exactamente las claves que siempre tuvo. Los
contadores `cache_hits` / `cache_misses` son cómo distingues un caché de vectores cálido
de uno frío en una consulta viva en lugar de por inspeccionar `embedding_status`
después del hecho.

**Compilador de contexto bajo demanda** (Phase 7)

| Tool | Propósito |
|---|---|
| `compile_context` | Compila un documento de contexto **con citas** a medida para una `query` o `seeds` explícitas. Recorre un subgrafo de profundidad acotada (`depth`, 1–10, por defecto 2), ordena con PPR y llena un `budget` de caracteres (por defecto 32000; pasa `0` para ilimitado). Determinista por defecto; con `synthesize: true` genera un corte narrativo "topic" escrito por el LLM. Devuelve `body`, `citations`, `selected_node_ids` y `char_budget_used`. Pon `preview: N` para devolver una vista previa acotada + un `handle` en lugar del cuerpo completo (disciplina de lectura estilo memex). `view` restringe el recorrido a una partición de arista nombrada — `semantic`, `temporal`, `causal` o `entity`; pasa un array de nombres para ejecutar un recorrido por vista y fusionarlos (RRF ponderado). Siempre que se solicita una vista — un nombre o varios — cada cita porta `via_views` (las vistas cuyo recorrido la alcanzó). `explain` añade `profile`, uno por búsqueda de semilla |
| `get_handle` | Pagina en porciones (`offset`, `limit`) una carga grande devuelta antes como `handle` (p. ej. `compile_context` con `preview`): traer más bajo demanda en vez de volcarlo todo en el contexto |
| `list_communities` | Lista los nodos `COMMUNITY_SUMMARY` creados por el paso post-compilación, ordenados por número de miembros (`min_size`, `limit`); con `node_context` recorre las aristas `summarizes` de vuelta a los miembros |
| `fresh_insights` | Hallazgos de sesión ordenados por una puntuación de decaimiento estilo Ebbinghaus (primero los más nuevos y más consultados); descarta los reemplazados por casi-duplicados más recientes. Opcional `kind`, `limit`, `include_superseded` |

**Memoria de sesiones** (ver [sessions.md](sessions.es.md))

| Tool | Propósito |
|---|---|
| `list_sessions` | Sobres de sesión (id, started_at, title, files_touched, conteos de hallazgos) del proyecto activo; `since`, `limit` |
| `find_session_findings` | Todos los hallazgos de sesión vinculados a `node_id` vía `discussed_in` / `references`, filtrables por `kinds` (insight / decision / question / todo / hypothesis / takeaway) |
| `find_code_symbol_mentions` | Expande un hallazgo de sesión a los símbolos `CodeFunction`/`CodeClass`/`CodeMethod` que menciona, vía aristas `discusses` del paso opcional de enlace insight↔símbolo. La capa de código es opcional: sin una entrada `external_tools` para `codegraph`, esto no devuelve nada |
| `activity_summary` | Resumen diario/semanal de los proyectos registrados: sesiones, hallazgos, commits de git, PR y documentos ingeridos, cada uno acotado por **su propia** marca de tiempo, nunca por el `started_at` de una sesión. Renderiza markdown determinista y, salvo que se desactive, antepone una narrativa del LLM |
| `query_decisions` | Decisiones tomadas en los proyectos registrados dentro de un rango temporal: elecciones **humanas** explícitas, parseadas de forma determinista del `AskUserQuestion` de Claude Code (la pregunta y la opción elegida), más las decisiones de agente extraídas de la conversación |

**Memoria de agentes y escritura de vuelta** (véase [agent-memory.es.md](../agent-memory.es.md))

| Herramienta | Propósito |
|---|---|
| `agent_view_explain` | Explica una vista con ámbito de agente *sin cargarla*: modo de resolución (worker / manager / org), agentes miembros, la ruta y el recuento de nodos de cada artefacto L1, y la marca de obsolescencia `distilled_through` |
| `drill_down` | Resuelve un `member_ref` de un destilado hasta su nodo L0 original: la escalada explícita y registrada del responsable más allá de la visibilidad destilada. Devuelve el estado `alive` / `changed` / `absorbed` / `gone`; cada llamada queda registrada en el sidecar. Perforar un `Artifact` de tipo **figura** cuyo activo se resolvió dentro del proyecto añade tres claves que ningún otro nodo porta: `asset_path` (dónde viven los bytes en disco), `asset_sha256` (el digest de esos bytes, que junto con el kind siembra la id del nodo) y `asset_site_path` (la dirección dirigida por contenido bajo `raw-assets/` de un sitio construido). Los `Artifact` de tabla y ecuación no tienen activo alguno — su contenido *es* su descripción — y una figura resuelta fuera de la raíz del proyecto nunca almacenó una ruta; ambos se perforan con las claves ordinarias. Una hash declarada malformada deja caer `asset_site_path` en lugar de inventar una dirección |
| `read_audit` | Quién ha estado leyendo el grafo: eventos de lectura registrados (`tool`, `actor`, `node_ids`, `at`, `tesserae_version`), los más recientes primero, más un recuento por actor, de modo que los contadores de acceso que gobiernan el olvido por desuso puedan atribuirse a un lector. **Opcional** — no se registra nada salvo que `TESSERAE_READ_AUDIT=1` esté definido en el proceso del servidor, porque una auditoría siempre activa convierte cada lectura en una escritura. Las filas ya registradas siguen siendo legibles tras apagar el flag; `enabled` informa del ajuste actual. Filtra por `actor`, `tool`, `node_id` |
| `graph_write` | Escribe nodos y aristas tipados directamente en el grafo: sin markdown, sin pasada de extracción. La escritura se añade a una capa append-only y se reproduce como productor de compilación, así que **sobrevive a la recompilación**. Es estricto: tipos desconocidos, aristas sin evidencia o extremos que no están ni en la carga ni son un id de nodo existente se rechazan. **Para retractar** algo simplemente erróneo, sin inventar un reemplazo: apunta una arista `retracts` al nodo equivocado **por id** — el destino sale del descubrimiento (`search_nodes`, `fresh_insights`), sale de la selección de contexto (`compile_context`) y sale de cada lista de vecinos y arista incidente que devuelve `node_context`. Lo que *no* hace es ocultar el nodo a quien lo nombra: una búsqueda exacta de `node_context` por id o nombre sigue devolviendo el nodo mismo, marcado `"retracted": true`, porque quien llamó pidió justamente ese. `include_superseded: true` lo devuelve a las superficies de descubrimiento, y no se borra nada |

**Preguntas y registro**

| Tool | Propósito |
|---|---|
| `ask` | Preguntas y respuestas en lenguaje natural a través del backend de memoria configurado (raganything, cognee, o wiki compilada). `backend`, `top_k`; fan-out entre vaults vía `scope`/`scope_aliases`; `claude_config_dir` para enrutamiento multicuenta. En una pregunta enrutada por grafo la envoltura porta `plan` (el razonamiento del planificador, los pasos que eligió, y `executed` — qué ejecutó en realidad), y puede portar `proposed_write`: nodos y aristas que el planificador piensa que merecen ser registrados, anclados solo en lo que la *pregunta* aseveró. Es una **sugerencia, nunca una escritura** — su procedencia siempre es nula, de modo que `graph_write` la rechaza hasta que un llamador con una clave de agente y un ancla fuera la suministra. Una mutación nunca es un efecto secundario de una consulta |
| `query` | Recuperación cruda, sin LLM: refleja `tesserae query`. `backend='wiki'` (por defecto) es búsqueda determinista BM25/semántica sobre el wiki compilado, con resultados ordenados y extractos; `backend='raganything'` consulta el índice RAG multimodal opcional cuando el proyecto lo tiene activado. Usa `ask` para una respuesta sintetizada y citada |
| `ingest` | Ingiere contenido web/texto crudo (p. ej. un recorte del navegador) en el grafo de conocimiento del proyecto resuelto |
| `list_projects` | Lista los proyectos registrados |
| `register_project` | Añade un proyecto al registro |
| `unregister_project` | Elimina un proyecto del registro (no existe un proyecto "activo" privilegiado) |

**Configuración guiada**

| Tool | Propósito |
|---|---|
| `tesserae_setup_plan` | Detecta el entorno y propone un plan de configuración como JSON. Solo lectura — nunca toca `.tesserae/` |
| `tesserae_setup_apply` | Aplica un plan (posiblemente editado): escribe `.tesserae/config.json` y ejecuta acciones de instalación/ejecución protegidas. Gobernado por `confirm_install_actions` / `confirm_run_actions` |

### Resources — cargados automáticamente al contexto del modelo

URIs que el cliente puede incorporar mediante su selector de recursos sin gastar un turno de tool:

- `tesserae://graph/schema` — la misma carga útil que la tool `schema`, lista como contexto estático
- `tesserae://graph/summary` — resumen del proyecto activo
- `tesserae://lint-report` — el último lint report en markdown

Además de plantillas de URI que el cliente puede construir bajo demanda:

- `tesserae://wiki/{kind}/{slug}` — el cuerpo de cualquier página wiki compilada
- `tesserae://raw/{source_path}` — cualquier markdown fuente sin procesar

### Prompts — plantillas de investigación de un solo clic

Aparecen en el menú de comandos del cliente (por ejemplo, la paleta `/` de Claude Code):

| Prompt | Argumentos | Qué hace |
|---|---|---|
| `summarize-paper` | `slug` (obligatorio) | Llama a `node_context` + `wiki_page` + opcionalmente `raw_source`, y devuelve un resumen estructurado: contribución, esbozo del método, resultados destacados, limitaciones, nodos relacionados |
| `find-related-work` | `topic` (obligatorio), `limit` | Encadena `search_nodes` + `node_context` para los top-K elementos relacionados con justificaciones de relevancia |
| `compare-approaches` | `a`, `b` (ambos obligatorios) | Recupera `node_context` para ambos + `search_facts` para reclamos de rendimiento; devuelve una comparación lado a lado con síntesis |
| `gap-analysis` | `topic` (opcional) | Saca a la luz preguntas abiertas no resueltas, benchmarks faltantes y afirmaciones poco respaldadas |
| `triage-open-questions` | _ninguno_ | Lista todos los nodos `OpenQuestion`, los agrupa por tema y propone un orden de prioridad |

Cada prompt se renderiza como un único mensaje de usuario que le indica al modelo exactamente qué tools de Tesserae encadenar, así el modelo no tiene que redescubrir la superficie cada vez.

## Multiproyecto: registra varias vaults bajo un mismo servidor

Un registro persistente en `~/.tesserae/registry.json` permite que el mismo servidor MCP resuelva cualquier proyecto registrado por nombre:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

A partir de esto, cada tool que acepte `project` o `graph_path` resolverá `project: "research"` contra el registro en lugar de necesitar una ruta completa. El servidor incluso valida que el `graph_path` registrado siga existiendo y devuelve un error claro si hace falta recompilar.

### Fan-out sobre cada vault registrada

La tool `ask` acepta `scope: "all-registered"` para consultar cada proyecto registrado en paralelo y devolver resultados agregados:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "Where is splatting used?",
    "scope": "all-registered"
  }
}
```

Restringe a un subconjunto con `scope_aliases: ["research", "notes"]`.

## Claude CLI multicuenta

Si tu tool `ask` se enruta a través de la Claude CLI y tienes varias cuentas (por ejemplo, `~/.claude` y `~/.claude-personal2`), pasa `claude_config_dir` por llamada:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "...",
    "claude_config_dir": "/Users/you/.claude-personal2"
  }
}
```

El servidor exporta `CLAUDE_CONFIG_DIR` solo durante esa llamada y restaura el valor anterior al terminar. Sin filtraciones entre llamadas.

## Verificación

Después de reiniciar tu cliente MCP, confirma la conexión:

- Claude Code: `/mcp` debería listar `tesserae` con el conteo de tools.
- Cursor: el icono MCP en la barra de chat debería mostrar `tesserae: connected` con los conteos de tools/resources/prompts.
- Codex / Hermes: invoca cualquier tool por nombre (por ejemplo, `schema`) y revisa la respuesta.

Si no aparece nada, verifica que `--graph` apunte a un `.tesserae/graph.json` existente — el servidor ahora valida esto al arrancar y en cada llamada a tool, así que verás un mensaje de error claro en lugar de un 500 silencioso.

## Dónde encaja esto

El servidor MCP es la **interfaz de lectura** al grafo tipado. Para la **ruta de escritura** (ingestar fuentes, recompilar, refrescar herramientas acompañantes como RAG-Anything) usa la CLI directamente. Ambas están desacopladas: la CLI actualiza `.tesserae/`, y el servidor MCP lee lo que haya allí en la siguiente llamada a tool.
