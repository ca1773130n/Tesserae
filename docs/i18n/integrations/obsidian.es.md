# Obsidian — abre la wiki compilada como un vault real

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.ko.md">한국어</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.ru.md">Русский</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.fr.md">Français</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

El export a Obsidian de Tesserae convierte tu grafo tipado compilado en un vault de [Obsidian](https://obsidian.md) real y con criterio. No un directorio de markdown — un vault con config `.obsidian/`, [callouts](https://help.obsidian.md/Editing+and+formatting/Callouts) conscientes del tipo, frontmatter consultable por [Dataview](https://blacksmithgu.github.io/obsidian-dataview/), un dashboard del vault y un índice de referencias `wiki://` entre vaults.

## Prerrequisitos

Compila el proyecto primero:

```bash
cd /path/to/your-project
tesserae init
tesserae compile
```

La compilación produce `.tesserae/graph.json` (la fuente de verdad) y una proyección markdown plana en `.tesserae/markdown_projection/`. El export de Obsidian se construye encima de esa proyección pero añade enriquecimientos nativos de Obsidian en cada página.

## 1) Exporta el vault

```bash
tesserae vault export --output ~/Documents/tesserae-vault
```

El directorio se crea si no existe. Volver a ejecutarlo lo sobrescribe de forma idempotente — la proyección markdown es determinista dado el mismo grafo.

Qué aterriza en disco:

```text
tesserae-vault/
  .obsidian/                  # Obsidian config (app.json, graph.json, plugins)
  README.md                   # Vault entry point
  index.md                    # All nodes grouped by section
  _bridges.md                 # Cross-vault wiki:// references, grouped by alias
  _meta/
    dashboard.md              # Dataview overview tables
  papers/                     # Paper / Repository / SourceDocument pages
  concepts/                   # Concept / Topic / Field / Method / Algorithm pages
  claims/                     # Claim / OpenQuestion / Evidence pages
  raw/                        # Optional raw-source attachments (created lazily)
```

## 2) Abre el directorio en Obsidian

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`.

Obsidian detectará `.obsidian/`, lo reconocerá como un vault real y cargará. La lista de community-plugins incluye Dataview, así que Obsidian pedirá habilitarlo (recomendado — sin él los bloques dataview se renderizan como bloques de código).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) Recorre el vault

### Puntos de entrada

- `README.md` — qué es este vault y cómo refrescarlo
- `index.md` — cada nodo por sección (papers, concepts, claims) con wikilinks
- `_meta/dashboard.md` — vista general dataview: páginas recientes, papers, concepts/claims

### Enriquecimientos por página

Cada página de nodo ahora incluye:

**Callouts conscientes del tipo.** Un callout semántico en la parte superior de cada página hace visible el tipo del nodo de un vistazo:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

Mapeo (destacados): `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Aristas consultables por Dataview.** El frontmatter ahora lleva las aristas tipadas como mapas anidados:

```yaml
edges_out:
  uses: [gaussian-splatting, volumetric-rendering]
  part_of: [3d-4d-vision-and-reconstruction]
  supports_claim: [performance-claim-..., comparison-...]
edges_in:
  mentioned_in: [project-pulse, topic-visual-slam]
```

Puedes escribir consultas como:

````markdown
```dataview
LIST FROM "papers" WHERE contains(edges_out.uses, "nerf")
```

```dataview
TABLE edges_out.supports_claim AS "Claims"
FROM "papers"
WHERE length(edges_out.supports_claim) > 3
SORT length(edges_out.supports_claim) DESC
LIMIT 10
```
````

**Puentes entre vaults.** Cualquier URI `wiki://<alias>/<kind>/<slug>` mencionada en la descripción o los metadatos de un nodo se expone tanto como un campo de frontmatter:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

como en una sección del cuerpo `Cross-vault references`. El índice `_bridges.md` a nivel de vault agrega cada referencia saliente agrupada por alias de destino, para que puedas auditar los enlaces entre vaults desde una sola página.

**Bloque Related (dataview).** Cada página termina con una consulta que muestra las páginas que enlazan de vuelta, poblada automáticamente:

````markdown
```dataview
LIST
FROM "papers" OR "concepts" OR "claims"
WHERE contains(file.outlinks, this.file.link) AND file.name != this.file.name
SORT file.name
LIMIT 25
```
````

### Dashboard del vault

`_meta/dashboard.md` incluye bloques dataview con las vistas agregadas más útiles: páginas actualizadas recientemente, todos los papers con columnas de metadatos, todos los concepts y claims ordenados por tipo. Edítalo libremente — es un punto de partida, no un contrato fijo.

### Vista de grafo del vault

La vista de grafo integrada de Obsidian (`Ctrl/Cmd+G`) ya funciona contra los wikilinks emitidos en las secciones `## Outgoing` / `## Incoming`. El `.obsidian/graph.json` pre-incluido colorea las rutas `papers/`, `concepts/`, `claims/` para orientarte. Puedes superponer vistas filtradas por dataview para slices más ricos.

## Flujos entre vaults

Registra varios vaults de Tesserae para que las URIs `wiki://` resuelvan entre ellos:

```bash
tesserae projects register /path/to/research --name research
tesserae projects register /path/to/notes    --name notes
```

Re-exporta cada vault tras el registro. `_bridges.md` en cada export mostrará ahora referencias resolubles entre vaults agrupadas por alias.

Obsidian en sí no sigue las URIs `wiki://` nativamente — se renderizan como texto inline — pero `_bridges.md` más la sección `Cross-vault references` por página te dan un índice manual hasta que llegue un plugin de Obsidian dedicado.

## Flujo de refresco

Para incorporar nuevas fuentes o correcciones desde tus archivos fuente:

```bash
# Edit source files under your project's source dirs, then:
tesserae compile
```

`compile` re-proyecta el vault automáticamente — ya no tienes que ejecutar un paso de export aparte. (`tesserae vault export --output <path>` sigue existiendo para una re-proyección puntual sin una recompilación completa.) Obsidian recarga en caliente los archivos cambiados en disco.

Si has añadido notas markdown dentro del vault que no están proyectadas desde el grafo (p. ej. tus propias anotaciones personales), sobreviven — el proyector solo sobrescribe los archivos que posee bajo `papers/`, `concepts/`, `claims/`, más `index.md`, `_bridges.md`, `_meta/dashboard.md` y `README.md`. Las páginas escritas a mano (sin frontmatter `node_id:`) y el bloque dedicado de user-notes (`<!-- user-notes:start -->` … `<!-- user-notes:end -->`) en cada página proyectada se preservan entre recompilaciones.

### Editar en Obsidian fluye de vuelta (sincronización bidireccional)

Desde v0.5.0 el vault **ya no es un export unidireccional**. Es una *proyección bidireccional*: el grafo tipado sigue siendo la fuente de verdad, pero `project compile` ahora lee tus ediciones de Obsidian desde el vault y las superpone sobre el grafo **antes** de re-proyectar. Edita el `title`, los `aliases`, el callout de descripción o cualquier escalar de frontmatter no-sistema de un nodo en Obsidian, recompila, y el cambio sobrevive — y se propaga al sitio estático, a MCP y a cada una de las demás proyecciones.

```bash
tesserae compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

Qué cosecha el overlay (los campos *vault-wins*):

- `title` → `name` del nodo
- `aliases` → alias del nodo
- el callout de descripción del cuerpo (o el primer párrafo) → `description` del nodo
- cada escalar de frontmatter no reservado → `metadata.<key>` (las claves reservadas/de sistema `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault` nunca se tratan como overrides de usuario)

Cada ejecución del overlay escribe un informe `.tesserae/diverged-fields.md` (`## Field overrides — N across M node(s)`) para que puedas auditar exactamente qué se trajo de vuelta. Los wikilinks que añades dentro del bloque de user-notes se convierten en aristas `user_link`. Ejecuta `tesserae compile` (con `compile_options.no_vault_pull = true` en `.tesserae/config.json`) para saltarte el overlay en una ejecución — útil para recuperación, o cuando intencionadamente quieres que gane el markdown fuente.

La primera compilación tras habilitar esta función recibe un "pase libre": sin una línea base `vault_snapshot.json` todavía, no se cosecha nada; el snapshot escrito al final se convierte en la línea base para el diff de la siguiente compilación.

Para un flujo en vivo dedicado, `tesserae vault sync` re-aplica el overlay y re-proyecta sin una recompilación completa:

```bash
# Preview what a compile would pull back, without mutating the graph.
tesserae vault sync --dry-run

# Watch the vault and round-trip edits live (Ctrl-C to stop).
tesserae vault sync --watch

# After renaming/removing nodes, delete projected pages left orphaned.
tesserae vault sync --prune-orphans
```

Ver [obsidian-sync.md](obsidian-sync.es.md) para la matriz completa de propiedad por campo y la justificación del diseño.

## Cuándo usar esto vs. el sitio estático

El sitio HTML compilado (`tesserae export site` → `.tesserae/site/`) es un export unidireccional de solo lectura para compartir — súbelo a GitHub Pages, S3, cualquier host estático. El vault de Obsidian es para **leer, consultar y editar** localmente con Dataview y la vista de grafo de Obsidian: es la única proyección cuyas ediciones fluyen de vuelta al grafo (ver la sección de sincronización bidireccional arriba). Ambos proyectan desde el mismo grafo, así que nunca derivan — y las correcciones que hagas en Obsidian se propagan al sitio en la siguiente compilación.
