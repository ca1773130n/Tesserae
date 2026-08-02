# Historial de sesiones Harness

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae puede importar transcripts locales de agentes de IA y renderizarlos como memoria del proyecto bajo la sección `sessions/` del sitio estático.

Esta función es intencionadamente independiente de `export harness`:

- `export harness` es contexto saliente para herramientas como Claude Code, Codex, Gemini, Cursor, Kiro y OpenCode.
- `sessions ...` es historial entrante: normaliza sesiones previas de Claude Code/Codex para el proyecto actual, las guarda bajo `.tesserae/harness_sessions/`, y permite que `export site` publique páginas de índice/detalle de sesión.

## Dos vías de entrada: importación por lotes y monitorización en vivo

La ingesta de sesiones ya no es solo por lotes. Hay dos rutas hacia el mismo
almacén normalizado:

- **Importación por lotes** — `sessions discover/import` escanea las raíces de transcripts
  bajo demanda y escribe de una sola vez. Esta página documenta ese flujo más abajo.
- **Monitorización en vivo** — el daemon supervisor (`tesserae engine`) ejecuta un
  `SessionTailer` que vigila los transcripts de Claude Code y Codex *del propio
  proyecto* e ingiere los turnos nuevos conforme aterrizan. Cada
  tick busca un offset de bytes por archivo persistido, lee solo los bytes nuevos,
  y guarda los turnos completos en la `HarnessSessionsDB` de SQLite
  (`.tesserae/sqlite.db`) **antes** de encolar una recompilación con debounce, de modo que la
  compilación siempre lee un estado consistente. El tailer está acotado a las sesiones
  propias del proyecto (Claude `projects/<slug>/*.jsonl`; Codex filtrado por cwd) y
  se reanuda desde los offsets guardados tras un reinicio sin re-reproducir turnos.

Ejecuta el bucle en vivo con:

```bash
tesserae engine        # watch sources, coalesce bursts, auto-recompile
tesserae engine --once # single drain cycle then exit (deterministic)
```

`tesserae refresh` ejecuta el mismo pipeline ingest → compile → project
una vez, en el propio proceso, sin arrancar el watcher de larga vida (pasa
`--no-sessions` para saltarte el escaneo de descubrimiento de sesiones de harness).

## Modelo de privacidad

Ambas rutas de ingesta son explícitas: el tailer en vivo solo corre mientras mantengas
`tesserae engine` vivo, y el descubrimiento por lotes solo escribe con
`--import`. Un `tesserae compile` o `tesserae export site` normal lee las
sesiones ya normalizadas de `.tesserae/harness_sessions/` y los registros en vivo
de `.tesserae/sqlite.db`, pero no rasca por sorpresa los directorios privados de
transcripts de harness por su cuenta.

Los registros de sesión importados son artefactos locales del proyecto. Revísalos antes de publicar un sitio público, especialmente si tus transcripts pueden incluir secretos, rutas privadas, datos de clientes o código no publicado.

## Descubrir e importar sesiones locales

Desde la raíz del proyecto:

```bash
tesserae sessions discover --import
```

El descubrimiento escanea las raíces locales de transcripts de Claude Code y Codex que pertenecen al directorio de trabajo del proyecto actual. Usa `--root` para escanear un directorio de configuración específico, y repite `--harness` para limitar el descubrimiento:

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

Sin `--import`, el descubrimiento imprime lo que encontró sin escribir registros de sesión normalizados.

## Importar JSON normalizado directamente

Si otra herramienta ya ha producido JSON `HarnessSession` normalizado, importa un archivo o una lista de archivos:

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

Cada entrada puede contener un objeto de sesión o una lista de objetos de sesión.

## Cómo se escribe el almacén

Ambos puntos de entrada escriben `.tesserae/harness_sessions/`, y lo escriben de forma diferente:

- `sessions import <path>` **combina**. Los registros existentes se conservan; un registro con el mismo nombre de archivo se sobrescribe en su lugar.
- `sessions discover --import` **reemplaza dentro de las raíces que escaneo**. Un registro cuyo transcript vive bajo una raíz harness escaneada se poda cuando el escaneo ya no lo encuentra, de modo que un esquema de nombre de archivo renombrado o una importación deduplicada no pueden dejar páginas huérfanas y entradas de búsqueda atrás. Un registro de cualquier otro lugar está fuera de ese alcance y sobrevive.

El alcance importa si alimentas Tesserae desde fuera de la convención local-harness — un orquestrador exportando sus propias sesiones de agentes, un trabajo de CI importando transcripts de otra máquina, un script de migración. Esos registros llevan atribución que un escaneo local no puede inferir, y un escaneo local no tiene autoridad sobre ellos. A través de 0.28.5 un descubrimiento no vacío podaba todo el *almacén*, de modo que se borraban silenciosamente, y el hook `SessionEnd` del plugin ejecuta un descubrimiento en cada cierre de sesión ([#104](https://github.com/ca1773130n/Tesserae/issues/104)).

Dos comportamientos que vale la pena conocer:

- Un descubrimiento vacío nunca poda. Un escaneo que no encuentra nada — `HOME` incorrecto, raíces harness desacopladas — combina en lugar de borrar.
- Un descubrimiento que sí elimina registros imprime el recuento junto al recuento de importación, de modo que el almacén no puede encogerse dentro de una línea que solo reporta crecimiento.

## Listar las sesiones importadas

```bash
tesserae sessions list
```

Las sesiones se guardan debajo:

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

Las sesiones monitorizadas en vivo se rastrean adicionalmente en la
`HarnessSessionsDB` de SQLite (`.tesserae/sqlite.db`), que también persiste los offsets
de lectura por archivo desde los que se reanuda el tailer. `tesserae sessions list` reporta la
vista combinada.

## Construir las páginas estáticas de sesiones

Después de importar sesiones, reconstruye el sitio:

```bash
tesserae export site
```

El sitio emite:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

El sitio generado enlaza Sessions desde el rail global, las tarjetas Browse de la home, las entradas de búsqueda y el rastro de breadcrumbs de cada página de detalle de sesión.

## Búsqueda rápida de transcripts (memex)

Cuando sirves el sitio con `tesserae serve`, el **dashboard de sesiones** gana una caja de búsqueda
de texto completo sobre cada transcript de Claude/Codex indexado, respaldada por
[`nicosuave/memex`](https://github.com/nicosuave/memex) (BM25). Los resultados muestran
`project · role · date · score` más un snippet coincidente.

```bash
cargo install --git https://github.com/nicosuave/memex --locked   # or: tesserae config deps --install memex
memex index                                                        # build the index once
tesserae serve                                                     # search box appears on /sessions
```

Es **opcional y degrada con elegancia**: sin binario `memex` (o sin índice) la caja muestra
un mensaje claro y accionable y el resto del dashboard no se ve afectado. El
endpoint de búsqueda (`GET /api/transcript-search`) está restringido a llamadores
same-origin/loopback para que una página web visitada no pueda sondear tu historial local.

## Layout de la página de detalle de sesión

Las páginas de detalle de sesión usan el shell compartido del sitio estático en lugar de un volcado de transcript independiente. Incluyen:

- hero y franja de estadísticas;
- resumen de alto nivel;
- metadatos de timeline y tamaño;
- decisiones, archivos, comandos, herramientas y errores cuando existen;
- árbol de subagentes colapsado;
- conversación usuario/asistente turno a turno;
- bloques de tool-use colapsados adjuntos bajo el turno de asistente precedente;
- un rail de conversación a la izquierda que enlaza a anclas `#turn-N`.

El markdown de la conversación se renderiza con el renderizador de markdown del sitio. Las superficies semánticas como código inline, marcado explícito de comando/etiqueta, rutas, nombres de archivo y hashtags se decoran como chips compactos; los sustantivos capitalizados aleatorios no se convierten en chips automáticamente.

Tipografía actual de los transcripts:

| Superficie | Selector | Tamaño |
|---|---|---|
| Prosa markdown de la conversación | `.session-turn-text`, hijos de prosa | `8px` |
| Bloques de código genéricos de la conversación | `.session-turn-text pre` | `10px` |
| Contenido de código Bash/shell | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Details/summary de herramientas | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Cabecera de tool-use | `.session-tool-use-header` | `8px` |
| Texto del payload de herramienta | `.session-tool-use-text` | `6px` |

## Lista de verificación de publicación para sesiones

Antes de desplegar un sitio público que incluya sesiones:

1. Ejecuta `tesserae sessions list` y confirma que el recuento es el esperado.
2. Inspecciona `.tesserae/harness_sessions/` en busca de contenido sensible.
3. Reconstruye con `tesserae export site`.
4. Abre `sessions/index.html` y al menos una página de detalle de sesión en local.
5. Confirma que los bloques de herramientas están colapsados por defecto y que los payloads en bruto de herramientas son aceptables para publicar.
6. Despliega con `tesserae export site --deploy` una vez que el árbol de fuentes esté commiteado.
