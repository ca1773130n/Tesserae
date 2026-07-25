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
proveedor CLI bajo `~/.tesserae/llm_cache`, indexado por (documento, tipo, guía)
más el modelo y esfuerzo de razonamiento — así que cambiar modelos re-pregunta
en lugar de servir respuestas del modelo anterior. Solo se almacenan respuestas
parseables, por lo que una generación deficiente no puede volverse permanente.

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

`tesserae config status` imprime el servidor resuelto y lo verifica para vivacidad.

---

## Pasadas de compilación

| Variable | Por defecto | Qué controla |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **activado** | Pasada de resumen estilo GraphRAG. Una llamada LLM por cluster ≥ 5 miembros, almacenada en caché por resumen de membresía. Deshabilita con `false`/`0`/`no`/`off` |
| `TESSERAE_ENABLE_LLM_PASSES` | desactivado | Pasadas de enriquecimiento LLM opcionales más allá de la extracción |
| `TESSERAE_AGENT_DISTILL` | desactivado | Artefactos de pericia L1 por agente (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | desactivado | Nodos de memoria destilada Runbook/Gotcha |
| `TESSERAE_INSIGHT_SYMBOL_LINK` | activado | Vincula ideas de sesión a símbolos de código |
| `TESSERAE_SUPERSEDE_PASS` | activado | Aristas `superseded_by` entre reclamaciones revisadas |
| `TESSERAE_PROMPT_SIGNATURES` | desactivado | Registra firmas de indicación para detección de deriva |
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

---

## Rutas e infraestructura

| Variable | Por defecto | Notas |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Ubicación del registro de proyectos |
| `TESSERAE_DISCOVERY_CACHE` | — | Caché de descubrimiento de sesión |
| `TESSERAE_ARXIV_CACHE` | — | Caché de metadatos arXiv |
| `TESSERAE_NO_FEDERATION_CACHE` | desactivado | Deshabilita el LRU del gráfico federado |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | desactivado | Emite el gráfico combinado entre proyectos |
| `TESSERAE_FLEET_PIDFILE` | — | Archivo pidfile de la flota del motor |
| `TESSERAE_CLIP_TOKEN` | — | Secreto compartido para el cortador web |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | desactivado | Aplica propuestas de desvío de esquema (`tesserae lab`) |

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
