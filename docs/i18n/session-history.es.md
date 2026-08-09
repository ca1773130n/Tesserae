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

El texto de un turno se copia en nombres y descripciones de nodos, y estos se
serializan en `graph.json` y en cada proyección suya, así que **los directorios
personales se redactan a la entrada**. `/Users/<nombre>` y `/home/<nombre>` nunca
llegan al grafo, lo cual importa porque una ruta es el único dato personal que
aparece en casi cada transcripción sin que nadie lo pretendiera.

## En qué se convierte un turno de sesión

Por cada transición *significativa* de una sesión —una llamada a herramienta o
una acción sustantiva del asistente, no charla— la pasada `Event`, que no usa
LLM, acuña exactamente un nodo con `{turn_id, actor, action, breve cambio de
estado}` y enlaza eventos consecutivos con aristas `precedes`, de modo que el
estado dinámico de una sesión puede reproducirse en orden. La pasada nunca llama
a un modelo, nunca lanza excepción ante una entrada mala y es idempotente a
nivel de bytes: cada id, cuerpo y `first_seen_at` acuñado deriva del contenido,
así que una reejecución produce nodos y aristas idénticos.

**El resultado de una herramienta es un turno.** Los códigos de salida y las
banderas de error sobreviven a la ingesta y aterrizan en el nodo `Event`, así
que el grafo distingue un comando que *falló* de otro que simplemente se
ejecutó. Antes de esto, un agente que leía su propia historia veía que había
ejecutado `pytest` y no tenía idea de si la suite había pasado: esa es la
diferencia entre un registro y una memoria.

### La arista `recovers`

A partir de dos resultados **observados** en una misma sesión, Tesserae deriva
la única arista causal de su vocabulario: una llamada a herramienta que informó
de un fallo, y una llamada posterior —misma herramienta, misma familia de
programa, mismo directorio de trabajo, mismo operando, sin ningún éxito
observado sobre ese operando en medio— que informó de éxito. El `Event` que
tiene éxito es el origen; el que falló, el destino. Ambos ids de turno se nombran
en la evidencia, y `metadata["basis"]` nombra cada dimensión en la que ambas
llamadas debían coincidir.

`CAUSAL_EDGE_TYPES` tiene exactamente un miembro, y es deliberado. Un repaso de
cuatro sistemas punteros de memoria de agentes encontró que ninguno deriva una
arista causal: dos infieren su vínculo más fuerte de la co-ocurrencia, uno acepta
la palabra de un LLM sobre un vocabulario abierto de etiquetas de relación sin
verificación alguna, y otro no tiene aristas. El fallo que esta estrechez existe
para evitar es publicar un `caused_by` que en realidad es un `happened_near`: en
un grafo ambos son indistinguibles, y el equivocado se lee como evidencia.

El ancla es el **operando**, no el comando, porque los comandos varían en
aspectos que no importan (opciones, orden) mientras que aquello sobre lo que se
actúa es lo que un reintento está reintentando de verdad.

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

Cada registro en `.tesserae/harness_sessions/` lleva un **productor (`producer`)** — el importador que lo escribió. `sessions discover --import` estampa `tesserae:discover`; `sessions import <path>` estampa `tesserae:import`. **Un escritor solo puede tocar registros que produjo**: solo poda los suyos, y no sobrescribirá el registro de otro productor para la misma sesión — la escritura entrante se salta y se reporta como `Left alone (written by another producer)`.

Esta regla existe porque la procedencia es lo único que realmente separa a los importadores. Dos de ellos rutinariamente describen la *misma* sesión: el escaneo local de Tesserae acuña un registro simple de un transcript bajo `~/.claude`, mientras que un orquestador exporta esa misma sesión llevando la identidad del agente que solo él conoce. Ambos derivan el mismo nombre de archivo del id de sesión, por lo que chocan. Ni la ubicación del transcript ni el nombre del harness pueden distinguirlos — por eso los arreglos anteriores con alcance root para [#104](https://github.com/ca1773130n/Tesserae/issues/104) no funcionaron, y por eso 0.28.6 seguía perdiendo tales registros de dos maneras: borrados cuando el escaneo ya no encontraba el transcript, silenciosamente sobrescritos cuando lo hacía.

Si escribes en este almacén desde tu propia herramienta, usa `tesserae sessions import <file>` y tus registros están protegidos a partir de ese momento. Nada más es necesario.

El alcance se reduce aún más, como una segunda puerta: un registro solo se poda si su transcript también vive bajo una raíz que esta ejecución escaneo y su harness fue uno que escaneo. Así `--harness codex` deja registros de claude-code solos aunque `~/.claude` fue recorrido.

### Varias máquinas compartiendo un mismo directorio de proyecto

Cada registro lleva además un **host (`host`)** — la máquina que lo cosechó. **Un host solo poda lo que él mismo cosechó.**

Este es un eje genuinamente distinto de `producer`, y las puertas de arriba no pueden sustituirlo. Cuando varios servidores ejecutan cada uno Claude Code y comparten un disco, comparten también `.tesserae` — pero cada uno solo ve sus propios transcripts locales. El escaneo de cada host estampa el mismo `tesserae:discover`, y el `~/.claude` de cada host se resuelve a la misma cadena de ruta, así que la puerta del productor y la puerta del alcance *pasan ambas* en una máquina que nunca vio el transcript. Acto seguido borra el registro de otra máquina y reporta éxito. Ahora los registros llevan el host que los cosechó, y podar exige que coincida.

El id de host vive en `~/.tesserae/host_id` — por máquina, no en el directorio compartido del proyecto — y se genera una sola vez en el primer uso. Anúlalo con `TESSERAE_HOST_ID`. Es un id persistido en lugar del hostname a propósito: una flota construida a partir de una sola imagen reutiliza hostnames, y una colisión de hostname entregaría en silencio los registros de una máquina a otra.

La ruta de **escritura** es deliberadamente ciega al host. Dos hosts solo pueden escribir la misma sesión cuando ambos ven el transcript, así que la escritura es idempotente y simplemente vuelve a estampar la propiedad sobre el host que demostró más recientemente que podía verlo. Condicionar en cambio las escrituras por host congelaría para siempre los registros de una máquina dada de baja, sin forma de reclamarlos.

Los registros escritos antes de este campo no llevan host. Son sin dueño en este eje y sobreviven a la poda de cualquier host hasta que `--adopt-unowned` los reclame — la misma regla que `producer` ya usa, y la razón por la que importa aquí es que *todo* registro escrito por 0.28.7 lleva productor y ningún host, así que la puerta del productor se abstendría y nada más los protegería.

Tres comportamientos que vale la pena conocer:

- **Registros escritos antes de 0.28.7 no llevan productor.** Son sin dueño, por lo que ningún importador los poda ni sobrescribe — seguro, pero el descubrimiento tampoco los refrescará. `sessions discover --import --adopt-unowned` los reclama para el descubrimiento. Ejecútalo una vez si el escaneo propio de Tesserae es la única cosa escribiendo en este almacén; *no* lo ejecutes si otra herramienta también escribe aquí, ya que entrega tus registros al descubrimiento.
- Un descubrimiento vacío nunca poda. Un escaneo que no encuentra nada — `HOME` incorrecto, raíces harness desacopladas — fusiona en lugar de borrar.
- Un descubrimiento que sí elimina o preserva registros imprime ambos recuentos junto al recuento de importación, de modo que el almacén no puede cambiar de tamaño dentro de una línea que solo reporta crecimiento.

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
