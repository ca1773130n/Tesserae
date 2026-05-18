# Grafo de sesiones

<!-- translations:start -->
<p align="center"><a href="../../integrations/sessions.md">English</a> · <a href="sessions.ko.md">한국어</a> · <a href="sessions.zh.md">中文</a> · <a href="sessions.ja.md">日本語</a> · <a href="sessions.ru.md">Русский</a> · <a href="sessions.fr.md">Français</a> · <a href="sessions.de.md">Deutsch</a></p>
<!-- translations:end -->

El grafo de sesiones de Tesserae convierte tus conversaciones de Claude Code / Codex sobre un proyecto en nodos de primera clase en el grafo de conocimiento tipado, vinculados a los documentos que aparecieron. Después de una compilación, puedes preguntar `tesserae project ask "¿qué decidimos sobre 3D Gaussian Splatting?"` y obtener nodos específicos Insight / Decision / Question / TODO / Hypothesis / Takeaway con procedencia hasta la sesión que los produjo.

## Cómo funciona

Dos pasadas por sesión:

1. **Estructural** (siempre activa, sin LLM). Lee los registros `HarnessSession` normalizados que `tesserae sessions discover --import` escribe en `.tesserae/harness_sessions/`. Para cada sesión acuña un nodo sobre `Session`, emite aristas `discussed_in` desde cada documento que el agente abrió y convierte el campo `decisions` existente en nodos `SessionDecision`.
2. **LLM** (opcional, se ejecuta cuando se configura `ANTHROPIC_API_KEY`). Envía los turnos de conversación normalizados (el campo `metadata["turns"]` — no el archivo de transcripción sin procesar) a Claude con un esquema de hallazgos solo JSON. Devuelve seis tipos de hallazgos, cada uno citando turnos específicos e IDs de nodo de documento específicos en el grafo actual. Almacenado en caché por content_hash + project_root_hash, por lo que las sesiones sin cambios omiten la llamada en la siguiente compilación.

## Configuración

```bash
# Importa las sesiones de este proyecto a `.tesserae/harness_sessions/`. Filtra por cwd, por lo que solo se importan las sesiones que se ejecutaron dentro de este proyecto.
tesserae sessions discover --import

# Compila. La pasada estructural se ejecuta gratis; la pasada LLM se ejecuta automáticamente cuando la CLI `claude` está autenticada — sin claves de API.
tesserae project compile
```

Para compilar sin sesiones (por ejemplo, en un servidor sin historial de harness):

```bash
tesserae project compile --no-sessions
```

Para forzar solo estructural (omitir la llamada LLM incluso cuando se establece una clave):

```bash
tesserae project compile --sessions-llm=false
```

## Configuración

`.tesserae/config.json` acepta un bloque `sessions`:

```jsonc
{
  "sessions": {
    "enabled": true,
    "llm_enabled": "auto",
    "max_turns_per_chunk": 30,
    "model": "claude-sonnet-4-7-20251201",
    "include_doc_id_context": 200
  }
}
```

Las banderas CLI anulan la configuración. `llm_enabled = "auto"` (predeterminado) ejecuta la pasada LLM cuando la CLI `claude` está autenticada o cuando se establece `ANTHROPIC_API_KEY`; sin ninguno, solo se ejecuta la pasada estructural (sin error, sin llamadas salientes).

## Consulta

Dos herramientas MCP se añaden encima de las herramientas de búsqueda/wiki existentes:

* `list_sessions(since?, limit?)` — sobres Session para el proyecto activo (id, started_at, title, recuentos de hallazgos).
* `find_session_findings(node_id, kinds?)` — cada hallazgo derivado de sesión vinculado a `node_id` mediante `discussed_in` o `references`, opcionalmente filtrado a insight / decision / question / todo / hypothesis / takeaway.

Desde la CLI:

```bash
tesserae sessions list
tesserae project ask "what did we decide about extractor dedup?"
```

## Privacidad

* Sin la CLI `claude` autenticada Y sin `ANTHROPIC_API_KEY` (o con `--sessions-llm=false`), no hay llamadas de red salientes. Solo se ejecuta la pasada estructural.
* Cuando se ejecuta la pasada LLM, se envían los **turnos de conversación normalizados completos** para las sesiones aún no en caché. El archivo de transcripción en sí permanece en disco; solo la salida JSON del LLM se persiste en el grafo y la caché por sesión.
* Los archivos de caché viven en `.tesserae/session_findings/<session_id>.findings.json` con un `content_hash` y un `project_root_hash`. Un archivo de caché copiado entre proyectos se rechaza al leer — sin reproducción entre proyectos.
* Las sesiones se filtran a través de `session_matches_project` después de cargar, por lo que una transcripción cuyo `cwd` era un proyecto hermano nunca produce nodos en el grafo de este proyecto.

## Diseño de la bóveda

Los hallazgos se renderizan bajo la bóveda Obsidian como una página por hallazgo, agrupados por sesión:

```
<vault>/
  sessions/
    <session-id-slug>/
      cache-findings-by-content-hash.md
      path-index-needs-basename-suppression.md
      ...
```

Las notas del usuario dentro del bloque `<!-- user-notes:start -->` … `<!-- user-notes:end -->` en cualquier página de hallazgo sobreviven a la recompilación — el mismo contrato que cada otra página de bóveda.

## Solución de problemas

* **No aparecen nodos Session después de la compilación.** ¿Ejecutaste `tesserae sessions discover --import` primero? La ruta de compilación solo consume `.tesserae/harness_sessions/`; NO escanea `~/.claude/projects/` automáticamente (ese escaneo puede tomar minutos en máquinas con miles de sesiones históricas).
* **Preocupaciones de costo de LLM.** La caché significa que cada sesión se envía al LLM como máximo una vez por content-hash. Las sesiones largas se dividen en `max_turns_per_chunk` (predeterminado 30) con superposición de 5 turnos. Para limitar el costo total, reduce `max_turns_per_chunk`, reduce `include_doc_id_context`, o configura `--sessions-llm=false`.
* **Un hallazgo cita un ID de nodo que no existe.** El orquestador valida cada referencia citada contra el grafo de documentos en vivo y descarta silenciosamente los desconocidos. Si ves la advertencia en los registros, el LLM alucinó una cita — las referencias sobrevivientes siguen siendo confiables.

## Especificación

El diseño completo vive en [docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md](../../superpowers/specs/2026-05-19-session-graph-extractor-design.md). El plan de implementación es [docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md](../../superpowers/plans/2026-05-19-session-graph-extractor-plan.md).
