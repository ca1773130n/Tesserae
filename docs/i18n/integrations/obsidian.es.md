# Obsidian — abre la wiki compilada como un vault real

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.ko.md">한국어</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.ru.md">Русский</a> · <a href="obsidian.fr.md">Français</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

La exportación a Obsidian de Tesserae convierte tu grafo tipado compilado en un vault de [Obsidian](https://obsidian.md) real y con criterio. No un directorio de markdown — un vault con configuración `.obsidian/`, [callouts](https://help.obsidian.md/Editing+and+formatting/Callouts) conscientes del tipo, frontmatter consultable con [Dataview](https://blacksmithgu.github.io/obsidian-dataview/), un dashboard del vault y un índice de referencias `wiki://` entre vaults.

## Requisitos previos

Compila primero el proyecto:

```bash
cd /path/to/your-project
tesserae init
tesserae compile
```

La compilación produce `.tesserae/graph.json` (la fuente de verdad) y una proyección en markdown plano en `.tesserae/markdown_projection/`. La exportación a Obsidian se construye sobre esa proyección, pero superpone enriquecimientos nativos de Obsidian en cada página.

## 1) Exportar el vault

```bash
tesserae vault export --vault ~/Documents/tesserae-vault
```

El directorio se crea si no existe. Reejecutar lo sobrescribe de forma idempotente — la proyección markdown es determinista dado el mismo grafo.

Lo que queda en disco:

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

## 2) Abrir el directorio en Obsidian

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`.

Obsidian detectará `.obsidian/`, lo reconocerá como un vault real y lo cargará. La lista de community-plugins incluye Dataview, así que Obsidian preguntará si habilitarlo (recomendado — sin él los bloques dataview se renderizan como bloques de código).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) Recorrido por el vault

### Puntos de entrada

- `README.md` — qué es este vault y cómo refrescarlo
- `index.md` — cada nodo por sección (papers, concepts, claims) con wikilinks
- `_meta/dashboard.md` — dataview overview: páginas recientes, papers, concepts/claims

### Enriquecimientos por página

Cada página de nodo ahora incluye:

**Callouts conscientes del tipo.** Un callout semántico en la parte superior de cada página hace visible el tipo de nodo de un vistazo:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

Mapeo (destacados): `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Aristas consultables con Dataview.** El frontmatter ahora lleva las aristas tipadas como mapas anidados:

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

**Puentes entre vaults.** Cualquier URI `wiki://<alias>/<kind>/<slug>` mencionada en la descripción o metadatos de un nodo aparece tanto como campo de frontmatter:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

como en una sección de cuerpo `Cross-vault references`. El índice `_bridges.md` a nivel de vault agrega cada referencia saliente agrupada por alias de destino, así puedes auditar los enlaces entre vaults desde una sola página.

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

`_meta/dashboard.md` incluye bloques dataview para las vistas agregadas más útiles: páginas actualizadas recientemente, todos los papers con columnas de metadatos, todos los concepts y claims ordenados por tipo. Edítalo libremente — es un punto de partida, no un contrato fijo.

### Vista de grafo del vault

La vista de grafo integrada de Obsidian (`Ctrl/Cmd+G`) ya funciona contra los wikilinks emitidos en las secciones `## Outgoing` / `## Incoming`. El `.obsidian/graph.json` preconfigurado colorea las rutas `papers/`, `concepts/`, `claims/` para facilitar la orientación. Puedes superponer vistas filtradas con dataview para cortes más ricos.

## Flujos de trabajo entre vaults

Registra varios vaults de Tesserae para que las URIs `wiki://` se resuelvan entre ellos:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

Vuelve a exportar cada vault tras el registro. `_bridges.md` en cada exportación mostrará ahora referencias resolubles entre vaults agrupadas por alias.

Obsidian en sí no sigue las URIs `wiki://` de forma nativa — se renderizan como texto inline — pero `_bridges.md` más la sección `Cross-vault references` por página te dan un índice manual hasta que aterrice un plugin dedicado de Obsidian.

## Flujo de refresco

Para incorporar nuevas fuentes o correcciones desde tus archivos fuente:

```bash
# Edita los archivos fuente bajo los directorios de fuentes del proyecto, luego:
tesserae compile
```

`compile` reproyecta el vault automáticamente — ya no tienes que ejecutar un paso de exportación aparte. (`tesserae vault export --vault <ruta>` sigue existiendo para una reproyección puntual sin una recompilación completa.) Obsidian recarga en caliente los archivos cambiados en disco.

Si has añadido notas markdown dentro del vault que no están proyectadas desde el grafo (por ejemplo, tus anotaciones personales), sobrevivirán — el proyector solo sobrescribe los archivos que le pertenecen bajo `papers/`, `concepts/`, `claims/`, además de `index.md`, `_bridges.md`, `_meta/dashboard.md` y `README.md`. Las páginas escritas a mano (sin la clave de frontmatter `node_id:`) y el bloque dedicado de notas de usuario (`<!-- user-notes:start -->` … `<!-- user-notes:end -->`) en cada página proyectada se conservan entre recompilaciones.

### Las ediciones en Obsidian vuelven al grafo (sincronización bidireccional)

A partir de v0.5.0, el vault **ya no es una exportación unidireccional**. Ahora es una *proyección bidireccional*: el grafo tipado sigue siendo la fuente de verdad, pero `project compile` ahora vuelve a leer tus ediciones desde el vault y las superpone sobre el grafo **antes** de reproyectar. Edita el `title`, los `aliases`, el callout de descripción o cualquier escalar de frontmatter no del sistema de un nodo en Obsidian, recompila, y el cambio sobrevive — y se propaga al sitio estático, a MCP y a todas las demás proyecciones.

```bash
tesserae compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

Lo que recoge la superposición (los campos donde *gana el vault*):

- `title` → `name` del nodo
- `aliases` → alias del nodo
- el callout de descripción del cuerpo (o el primer párrafo) → `description` del nodo
- cada escalar de frontmatter no reservado → `metadata.<key>` (las claves reservadas/del sistema `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault` nunca se tratan como overrides del usuario)

Cada ejecución de la superposición escribe un informe `.tesserae/diverged-fields.md` (`## Field overrides — N across M node(s)`) para que puedas auditar exactamente qué se recuperó. Los wikilinks que añadas dentro del bloque de notas de usuario se convierten en aristas `user_link`. Pasa `tesserae compile` (with `compile_options.no_vault_pull = true` in `.tesserae/config.json`) para omitir la superposición en una ejecución — útil para recuperación, o cuando quieras intencionadamente que gane el markdown fuente.

La primera compilación tras habilitar esta función obtiene un «pase libre»: sin una línea base `vault_snapshot.json` todavía, no se recoge nada; el snapshot escrito al final se convierte en la línea base para el diff de la siguiente compilación.

Para un flujo en vivo dedicado, `tesserae vault sync` vuelve a aplicar la superposición y reproyecta sin una recompilación completa:

```bash
# Previsualiza lo que una compilación recuperaría, sin mutar el grafo.
tesserae vault sync --dry-run

# Observa el vault y haz round-trip de las ediciones en vivo (Ctrl-C para parar).
tesserae vault sync --watch

# Tras renombrar/eliminar nodos, borra las páginas proyectadas que queden huérfanas.
tesserae vault sync --prune-orphans
```

Consulta [obsidian-sync.md](obsidian-sync.md) para la matriz completa de propiedad por campo y la justificación del diseño.

## Cuándo usar esto frente al sitio estático

El sitio HTML compilado (`tesserae export site` → `.tesserae/site/`) es una exportación unidireccional de solo lectura para compartir — publícalo en GitHub Pages, S3 o cualquier host estático. El vault de Obsidian es para **leer, consultar y editar** localmente con Dataview y la vista de grafo de Obsidian: es la única proyección cuyas ediciones vuelven al grafo (consulta la sección de sincronización bidireccional más arriba). Ambos proyectan desde el mismo grafo, así que nunca divergen — y las correcciones que hagas en Obsidian se propagan al sitio en la siguiente compilación.
