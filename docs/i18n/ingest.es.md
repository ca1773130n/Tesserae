# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
Fusiona un único archivo de documento o URL en la base de conocimiento.

## Uso

    tesserae ingest <input>...  [--title T] [--source-kind K] [--full] [--dry-run]

`<input>` es una o más rutas de archivo locales o URLs `http(s)`. Las URLs se descargan, se convierten a
markdown y se persisten bajo `data/ingested/<slug>.md` con front-matter de procedencia
(`source_url`, `fetched_at`, `content_sha256` y `arxiv_id` cuando se detecta), y luego se fusionan.
Los archivos locales de fuera del proyecto se copian a `data/ingested/` para que se conviertan en
fuentes rastreadas (una compilación completa posterior los reproduce idénticamente).

La ingesta por URL requiere el extra opcional:

    pip install tesserae[ingest-url]

## Cómo funciona

Por defecto `ingest` fusiona la nueva fuente mediante una compilación incremental — no re-extrae
todo el corpus — y el resultado es byte-idéntico a una compilación completa (un fallback automático
de recompilación completa garantiza la corrección para cualquier caso que la ruta incremental no pueda manejar).
Pasa `--full` para forzar una recompilación completa de todo el corpus.

## Flags

- `--full` — fuerza una recompilación completa de todo el corpus.
- `--dry-run` — descarga e informa de lo que se ingeriría; no escribe ningún grafo.
- `--title` — anulación del título, útil para URLs sueltas.
- `--source-kind` — anula la clasificación de la fuente.

## La capa de conceptos (`--extractor`)

Tesserae es una wiki LLM, así que `compile` construye la **capa de conceptos/afirmaciones por
defecto** (`--extractor llm`): lee cada documento a través de tu proveedor LLM
configurado — **codex / claude / Anthropic API**, según `llm_provider` — y acuña
conceptos, afirmaciones, capacidades, términos técnicos, spans de evidencia y las aristas
tipadas entre ellos. Esa es la capa que permite al grafo responder *"qué idea es
esta, y cómo se relaciona"*, no solo *"qué archivo lo dijo"*.

    tesserae compile                        # LLM concept layer, configured provider
    tesserae compile --llm-provider codex   # force a provider for this run

Si no hay ningún backend LLM configurado/autenticado, compile degrada al extractor
**determinista** (solo estructural — fuentes, secciones, enlaces explícitos) y avisa. También puedes
pedirlo explícitamente — es rápido, sin claves y byte-estable, el modo de CI /
reproducible:

    tesserae compile --extractor deterministic

### Elegir qué cuentas se gastan (`llm_claude_config_dirs`)

Con el proveedor `claude`, Tesserae rota entre tus cuentas de Claude CLI con sesión
iniciada: una cuenta que alcanza su límite cede el paso a la siguiente en lugar de
perder el resto de la ejecución en extracción determinista. Por defecto detecta
automáticamente todos los directorios `~/.claude*`.

El proveedor **codex** funciona igual: rota entre los directorios `~/.codex*`
autenticados (un directorio solo cuenta si contiene `auth.json`) y se configura con
`llm_codex_homes`. Cada proveedor tiene su propia clave porque cada uno tiene su propia
disposición de cuentas en disco: los directorios de configuración de Claude CLI y los
homes de Codex no son intercambiables:

| proveedor | clave de configuración | qué enumera |
|---|---|---|
| `claude` | `llm_claude_config_dirs` | directorios de configuración de Claude CLI (`~/.claude*`) |
| `codex`  | `llm_codex_homes`        | homes de Codex (`~/.codex*`) |

Para controlar exactamente qué cuentas pueden gastarse, y en qué orden, define
`llm_claude_config_dirs` en `.tesserae/config.json` (proyecto) o
`~/.tesserae/config.json` (global):

```json
{
  "llm_claude_config_dirs": [
    "/Users/you/.claude-work",
    "/Users/you/.claude-personal"
  ]
}
```

Esa lista es la autoridad final: no se prueba nada fuera de ella. También **gana a la
variable ambiental `CLAUDE_CONFIG_DIR`**, que hereda cada proceso lanzado desde una
sesión de Claude Code y que, de otro modo, ataría toda la compilación a la cuota de
esa única sesión. Sin nada configurado, `CLAUDE_CONFIG_DIR` sigue siendo la primera
cuenta que se intenta.

Cuando todas las cuentas configuradas informan de su límite de uso, la compilación
deja de llamar al LLM durante el resto de la ejecución en vez de volver a preguntar
documento a documento, marca esos documentos como `fallback: true` y te lo dice.
Recupéralos cuando el límite se reinicie sin recompilarlo todo:

    tesserae compile --changed-only --retry-fallbacks


**Consciente del coste (`selective-llm`)** — enruta solo los docs que coincidan a través del LLM, el
resto determinista:

    tesserae compile --extractor selective-llm \
      --llm-include "docs/**/*.md" --llm-limit 20

Los mismos flags funcionan en `tesserae extract <paths>` (standalone) y
`tesserae compile <paths>` (ingesta de rutas ad-hoc).

**Ajustes:**

- `--llm-provider codex|claude|anthropic` — anula el proveedor (por defecto:
  `llm_provider` en la config).
- `--llm-model` — modelo para el extractor (por defecto: el del proveedor).
- `--llm-include <glob>` — para `selective-llm`, qué archivos pasan por el LLM
  (repítelo para varios; los patrones casan en cualquier parte de la ruta absoluta, p. ej.
  `"*docs/superpowers*"`).
- `--llm-limit N` — limita cuántos archivos llegan al LLM (el resto queda determinista).

**Sin timeout por defecto.** Un documento de diseño grande genera mucho JSON y puede tardar
minutos; la extracción corre hasta completarse en lugar de cortarse silenciosamente (un
timeout es solo opt-in).

**Robusto sobre corpus reales.** Un documento ruidoso o lento nunca aborta toda la
compilación: un fallo del LLM en un doc (auth, error, una generación imparseable) recae
en la línea base determinista para *ese* doc, una arista o tipo de nodo fuera del
vocabulario controlado se descarta, y la caché indexada por contenido hace que una recompilación de
docs sin cambios reutilice la extracción previa.

> Los nombres de extractor `claude-cli` / `selective-claude` (y los flags `--claude-*`)
> son alias en desuso de `llm` / `selective-llm` (y `--llm-*`); todavía
> funcionan pero emiten una nota de deprecación.

## Gestionar el alcance de la compilación (`sources`)

`tesserae compile` (sin argumentos) compila los directorios de la lista `sources`
del proyecto. Gestiona esa lista — **local o global** — con los subcomandos de `sources`:

    tesserae sources add docs                 # local: inside the project (stored project-relative)
    tesserae sources add /data/shared-notes   # global: an absolute path outside the project
    tesserae sources add ../sibling-project   # global: a relative path that escapes the root
    tesserae sources list                     # shows each source tagged local/global, flags missing
    tesserae sources remove docs

Una ruta dentro del proyecto se guarda relativa al proyecto (portable); cualquier cosa fuera
se guarda absoluta. Ambas se resuelven en tiempo de compilación, así que una fuente global compila
igual que una local. (Las adiciones deduplican por ubicación resuelta, así que las formas absoluta y
relativa con `../` del mismo directorio nunca cuentan doble.)

## Comandos relacionados

- `tesserae compile` (sin argumentos) re-extrae todo el corpus rastreado.
- `tesserae ingest <x>` añade una fuente incrementalmente.
- `tesserae code ingest` acuña un grafo de código desde fuente Python (un comando distinto),
  para proyectos que activan la capa de código con una entrada `external_tools` para `codegraph`.

### Activar la capa de código

El código fuente es **opcional**. Añade una entrada `external_tools` a `.tesserae/config.json`:

```json
{
  "external_tools": [{"id": "codegraph"}]
}
```

Sin esa entrada no hay capa de código: la compilación no extrae nada, los hooks de sync-code permanecen en silencio y `code-graph.json` se elimina si una compilación anterior dejó uno. El tipo de proyecto no la activa: un proyecto `Repository` sin entrada no compila código.

Usa `"enabled": false` para desactivarla. Considera CodeGraph para inteligencia de código; Tesserae se centra en documentos y transcripciones de sesión.
