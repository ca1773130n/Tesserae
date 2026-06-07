# Flujo de trabajo complementario de Understand Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a> · <a href="understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) y Tesserae son proyectos complementarios.

- Understand Anything es excelente para producir un grafo de conocimiento de la base de código y un panel interactivo.
- Tesserae se centra en memoria de agente duradera: documentos, compilación markdown/wiki, publicación estática, historial de sesiones y exportaciones orientadas a agentes.

Tesserae no debe incorporar ni absorber Understand Anything. Trátalo como un acompañante independiente que puede producir artefactos de grafo útiles.

## ¿Por qué usar ambos?

Understand Anything puede escribir:

```text
.understand-anything/knowledge-graph.json
```

Ese grafo captura la estructura del código, como archivos, funciones, clases, módulos, conceptos, dependencias, capas y recorridos.

Luego Tesserae puede conservar ese artefacto junto con el resto de la memoria del proyecto:

- documentos fuente y páginas markdown;
- archivos del repositorio;
- notas de investigación;
- historial local de sesiones Claude Code / Codex;
- páginas wiki estáticas generadas;
- vistas web del grafo 2D / 3D;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json` y siblings de agente por página.

## Flujo actual de baja fricción

La ruta recomendada es el asistente de configuración:

```bash
tesserae init
```

Elige Understand Anything en el paso de herramientas complementarias. Tesserae instala/actualiza las skills complementarias cuando se solicita y escribe un comando de actualización administrado en `.tesserae/config.json`. Las llamadas futuras a `tesserae compile` ejecutan automáticamente ese wrapper cuando falta el grafo de UA o está obsoleto.

Para automatización no interactiva, usa:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
tesserae compile
```

El comando almacenado pertenece a Tesserae; no es algo que el usuario tenga que inventar:

```bash
tesserae integrations refresh understand-anything --platform codex
```

Durante la compilación, Tesserae:

1. comprueba si `.understand-anything/knowledge-graph.json` existe y coincide con el commit git actual cuando hay metadatos disponibles;
2. ejecuta la plataforma de agente configurada (`codex`, `opencode` o `claude`) solo cuando falta el grafo, está obsoleto o se fuerza la actualización;
3. verifica que el grafo se haya escrito;
4. materializa `.tesserae/external/understand-anything.md`;
5. continúa la compilación normal de memoria.

Puedes forzar todos los comandos externos de actualización configurados antes de compilar:

```bash
tesserae compile --refresh-integrations
```

¿También necesitas Cognee? Añade las banderas de memoria en tiempo de ejecución al mismo comando setup:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## Equivalente manual

Se prefiere la ruta de configuración administrada. Si intencionalmente quieres usar UA fuera de Tesserae, ejecuta primero Understand Anything dentro de tu entorno de agente:

```bash
/understand
```

Luego ejecuta el asistente de configuración y **activa Understand Anything
cuando se te pregunte** para que Tesserae registre la fuente de proyección
markdown. Los archivos JSON directos se mantienen como artefactos
complementarios sin procesar, no como rutas fuente introducidas a mano.

```bash
tesserae init
# activa Understand Anything cuando el asistente lo pregunte
tesserae compile
tesserae export site
```

Para automatización no interactiva, ejecuta `tesserae init --yes`
(integraciones DESACTIVADAS), activa Understand Anything en
`.tesserae/config.json` (el asistente escribe la integración bajo la clave
`external_tools`), y luego ejecuta `tesserae integrations refresh
understand-anything` antes de compilar.

Si también quieres memoria local de sesiones de agente:

```bash
tesserae sessions discover --import
tesserae export site
```

## Sincronización nativa de grafos

Tesserae mantiene la markdown projection para legibilidad y también importa el grafo de UA de forma nativa durante compile cuando la herramienta configurada usa `sync_mode: native_graph`.

El adaptador nativo lee `.understand-anything/knowledge-graph.json`, mapea nodos/aristas de UA a la ontology controlada de Tesserae y escribe un sync manifest:

```text
.tesserae/external/understand-anything-sync.json
```

Mapeo actual:

| Understand Anything | Dirección de Tesserae |
|---|---|
| `project` | repository/project metadata |
| `nodes[type=file]` | `SourceFile` nodes |
| `nodes[type=function]` / `method` | `CodeFunction` nodes |
| `nodes[type=class]` / `component` | `CodeClass` nodes |
| `nodes[type=module]` / `package` | `CodeModule` nodes |
| `nodes[type=concept]` / `topic` | canonical `Concept` nodes |
| `nodes[type=feature]` / `capability` | `Capability` nodes |
| `edges[type=imports]` | `imports` edges |
| `edges[type=contains]` | `contains` edges |
| `edges[type=calls]` | `calls` edges |
| unknown edge types | `shares_concept_with` con metadata `ua_edge_type` |

Concept synchronization se canonicaliza en vez de duplicarse a ciegas. Si UA emite `Mermaid Rendering` y Tesserae ya tiene `Mermaid rendering`, compile conserva un único concept node y añade provenance de UA en `metadata.external_refs`.

Tesserae sigue siendo el memory compiler; UA sigue siendo un companion graph generator independiente.

## Principio de colaboración

No presentes Tesserae como reemplazo de Understand Anything.

Un mejor encuadre:

- Understand Anything ayuda a un desarrollador a entender una base de código ahora.
- Tesserae ayuda a los agentes a recordar, buscar, citar, actualizar y publicar conocimiento del proyecto con el tiempo.
