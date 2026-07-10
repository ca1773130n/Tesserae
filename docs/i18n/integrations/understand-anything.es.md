# Flujo de trabajo complementario de Understand Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a> · <a href="understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) y Tesserae son proyectos complementarios.

- Understand Anything es excelente produciendo un grafo de conocimiento del codebase y un dashboard interactivo.
- Tesserae se enfoca en la memoria de agente de larga vida: docs, compilación de markdown/wiki, publicación estática, historial de sesiones y exports de cara a agentes.

Tesserae no debería vendorizar ni absorber Understand Anything. Trátalo como un acompañante independiente que puede producir artefactos de grafo útiles.

## ¿Por qué usar ambos?

Understand Anything puede escribir:

```text
.understand-anything/knowledge-graph.json
```

Ese grafo captura la estructura del código: archivos, funciones, clases, módulos, conceptos, dependencias, capas y tours.

Tesserae puede entonces preservar ese artefacto junto al resto de la memoria del proyecto:

- docs fuente y páginas markdown;
- archivos del repositorio;
- notas de investigación;
- historial local de sesiones de Claude Code / Codex;
- páginas wiki estáticas generadas;
- vistas web de grafo 2D / 3D;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json` y los siblings de agente por página.

## Flujo actual de baja fricción

La ruta recomendada es el asistente de configuración:

```bash
tesserae init
```

Elige Understand Anything en el paso de companion-tools (está **desactivado por defecto** — su refresco ejecuta un script de instalación remoto). Tesserae escribe un comando de refresco gestionado en `.tesserae/config.json` bajo `external_tools`. El auto-refresh en compile también está desactivado por defecto (`auto_refresh: false`); ponlo a `true` si quieres que `tesserae compile` ejecute el wrapper automáticamente cuando el grafo de UA falte o esté obsoleto.

Para automatización no interactiva, ejecuta `tesserae init --yes` (integraciones OFF), habilita Understand Anything en `.tesserae/config.json`, y luego:

```bash
tesserae integrations refresh understand-anything --platform codex
tesserae compile
```

El comando guardado es propiedad de Tesserae, no algo que el usuario tenga que inventar:

```bash
tesserae integrations refresh understand-anything --platform codex
```

Durante la compilación, Tesserae:

1. comprueba si `.understand-anything/knowledge-graph.json` existe y coincide con el commit actual de git cuando hay metadatos disponibles;
2. ejecuta la plataforma de agente configurada (`codex`, `opencode` o `claude`) solo cuando su entrada de `external_tools` tiene `auto_refresh: true` y el grafo falta/está obsoleto, o el refresco se fuerza;
3. verifica que el grafo se escribió;
4. materializa `.tesserae/external/understand-anything.md`;
5. continúa la compilación de memoria normal.

Puedes forzar todos los comandos de refresco externos configurados antes de una compilación:

```bash
tesserae compile --refresh-integrations
```

¿Necesitas Cognee también? Cognee es igualmente opt-in: instálalo con `pip install tesserae[cognee]` y establece `memory_backends.cognee.enabled: true` en `.tesserae/config.json` (consúltalo explícitamente con `tesserae query --backend cognee`).

## Equivalente manual

La ruta de setup gestionada es la preferida. Si intencionadamente quieres usar UA fuera de Tesserae, ejecuta Understand Anything primero dentro de tu entorno de agente:

```bash
/understand
```

Luego ejecuta el asistente de configuración y **habilita Understand Anything cuando se te pregunte** para que
Tesserae registre la fuente de proyección markdown. Los archivos JSON directos se mantienen como
artefactos de acompañamiento en bruto, no rutas fuente introducidas a mano.

```bash
tesserae init
# enable Understand Anything when the wizard prompts
tesserae compile
tesserae export site
```

Para automatización no interactiva, ejecuta `tesserae init --yes` (integraciones OFF),
habilita Understand Anything en `.tesserae/config.json` (el asistente escribe la
integración bajo la clave `external_tools`), y luego `tesserae integrations
refresh understand-anything` antes de compilar.

Si también quieres memoria local de sesiones de agente:

```bash
tesserae sessions discover --import
tesserae export site
```

## Sincronización nativa del grafo

Tesserae ahora mantiene la proyección markdown por legibilidad y además importa el grafo de UA nativamente durante la compilación cuando la herramienta configurada usa `sync_mode: native_graph`.

El adaptador nativo lee `.understand-anything/knowledge-graph.json`, mapea los nodos/aristas de UA a la ontología controlada de Tesserae, y escribe un manifest de sync:

```text
.tesserae/external/understand-anything-sync.json
```

Mapeo actual:

| Understand Anything | Dirección Tesserae |
|---|---|
| `project` | metadatos de repositorio/proyecto |
| `nodes[type=file]` | nodos `SourceFile` |
| `nodes[type=function]` / `method` | nodos `CodeFunction` |
| `nodes[type=class]` / `component` | nodos `CodeClass` |
| `nodes[type=module]` / `package` | nodos `CodeModule` |
| `nodes[type=concept]` / `topic` | nodos `Concept` canónicos |
| `nodes[type=feature]` / `capability` | nodos `Capability` |
| `edges[type=imports]` | aristas `imports` |
| `edges[type=contains]` | aristas `contains` |
| `edges[type=calls]` | aristas `calls` |
| tipos de arista desconocidos | `shares_concept_with` con metadatos `ua_edge_type` |

La sincronización de conceptos se canonicaliza en lugar de duplicarse a ciegas. Si UA emite `Mermaid Rendering` y Tesserae ya tiene `Mermaid rendering`, la compilación mantiene un solo nodo de concepto y añade la procedencia de UA bajo `metadata.external_refs`.

Tesserae sigue siendo el compilador de memoria; UA sigue siendo un generador de grafo acompañante independiente.

## Principio de colaboración

No plantees Tesserae como un reemplazo de Understand Anything.

Un encuadre mejor:

- Understand Anything ayuda a un desarrollador a entender un codebase ahora.
- Tesserae ayuda a los agentes a recordar, buscar, citar, actualizar y publicar el conocimiento del proyecto a lo largo del tiempo.
