# Web Clipper (extensión de Chrome)

<!-- translations:start -->
<p align="center"><a href="../../integrations/chrome-extension.md">English</a> · <a href="chrome-extension.ko.md">한국어</a> · <a href="chrome-extension.zh.md">中文</a> · <a href="chrome-extension.ja.md">日本語</a> · <a href="chrome-extension.ru.md">Русский</a> · <a href="chrome-extension.fr.md">Français</a> · <a href="chrome-extension.de.md">Deutsch</a></p>
<!-- translations:end -->

Captura cualquier página web — o solo el texto que seleccionaste — directamente en tu
base de conocimiento de Tesserae. El clipper POST-ea la página a una instancia local de `tesserae
serve`, que escribe un archivo markdown con marca de procedencia en el
corpus del proyecto e inicia una compilación incremental para que el clip aparezca como
nodos tipados en tu grafo, bóveda y sitio.

Este es el pilar de "ingesta de conocimiento autónoma y proactiva" hecho
un clic: ve algo que vale la pena guardar, clípalo, y se convierte en
contexto listo para agentes.

---

## Qué hace

1. Navegas a una página e haces clic en el clipper (botón de la barra de herramientas o atajo
   de teclado).
2. La extensión agarra la URL de la página, el `title`, metadatos de la página, y ya sea
   el **contenido legible completo** o, si tienes texto destacado, solo tu
   **selección**. Puedes añadir una **nota** opcional y **etiquetas**, y
   alternar la generación de **TL;DR**.
3. POST-ea ese payload a `http://localhost:<port>/api/clip` en tu
   `tesserae serve` en ejecución.
4. El servidor resuelve el proyecto siendo servido, escribe
   `data/ingested/<slug>.md`, opcionalmente prepende un TL;DR de LLM de una sola llamada,
   y llama a la misma ruta de ingesta que usa el CLI (`ingest_sources`),
   que compila incrementalmente la nueva fuente en el grafo.
5. Obtienes un informe JSON (`status`, `path`, `tldr`, `node_count`,
   `edge_count`).

El markdown clipeado se ve así:

```markdown
---
clipped_at: 2026-06-13T00:00:00Z
note: read later
source: web-clip
tags: python, web
title: An Article
url: https://example.com/article
---

## TL;DR

A two-sentence summary (only present when TL;DR is enabled and succeeds).

## Note

read later

## Content

The clipped page text (or your selection).
```

El TL;DR es **mejor esfuerzo**: usa la capa Claude respaldada por CLI (sin API
key necesaria). Si el CLI `claude` no está disponible o la llamada falla, el
clip se ingiere de todas formas — solo sin la sección `## TL;DR`.

---

## Instalar (cargar sin empaquetar)

> La extensión viene en el repo bajo `extension/` (cargada sin empaquetar durante
> el desarrollo; un listado de Chrome Web Store está en revisión).

1. Abre `chrome://extensions`.
2. Alterna el **Modo de desarrollador** (arriba a la derecha) activado.
3. Haz clic en **Cargar extensión sin empaquetar** y selecciona el directorio `extension/`.
4. Fija el clipper de Tesserae a tu barra de herramientas.

La extensión se comunica con `http://localhost:8765` por defecto; configura el puerto en
las opciones de la extensión para que coincida con el puerto que pasas a `tesserae serve`.

---

## Ejecutar el servidor

Compila tu proyecto y luego sírvelo:

```bash
python3 -m tesserae serve --project /path/to/project --port 8765
```

`tesserae serve` expone el sitio estático **más** dos rutas JSON en el
mismo origen:

- `POST /api/ask`  — preguntas y respuestas (ver [mcp.md](mcp.es.md))
- `POST /api/clip` — ingesta de web-clip (esta característica)

Mantenlo en ejecución mientras navegas; cada clip golpea `/api/clip`.

---

## El contrato `/api/clip`

`POST /api/clip` con un cuerpo JSON:

| Campo       | Tipo      | Requerido | Notas |
|-------------|-----------|----------|-------|
| `url`       | string    | sí       | URL de la página fuente (procedencia + slug de nombre de archivo). |
| `title`     | string    | no       | Título de la página; regresa a un título derivado. |
| `content`   | string    | sí\*     | Texto completo de la página. |
| `selection` | string    | no       | Si está presente, **anula** `content` — clipea solo el texto destacado. |
| `meta`      | object    | no       | Metadatos de página extra pasados a través. |
| `note`      | string    | no       | Tu anotación de texto libre → `## Note`. |
| `tags`      | string[]  | no       | Etiquetas de front-matter. |
| `tldr`      | boolean   | no       | Predeterminado `true`. Establece `false` para saltar la generación de TL;DR. |

\* `content` o `selection` debe ser no-vacío.

**Respuesta** `200 OK`:

```json
{
  "status": "ok",
  "path": "/path/to/project/data/ingested/example-com-article.md",
  "tldr": "A two-sentence summary…",
  "node_count": 142,
  "edge_count": 287
}
```

Los errores retornan `400` (solicitud incorrecta / cuerpo vacío) o `500` (falla de ingesta)
con `{"error": "..."}`.

### CORS

Porque el clipper es una extensión de navegador golpeando `localhost`, el
endpoint habla CORS — pero solo para llamadores de confianza, así que un sitio web arbitrario
que visites no puede POST en tu grafo:

- `OPTIONS /api/clip` retorna los encabezados de preflight.
- El servidor valida el `Origin` de la solicitud y **refleja solo**
  orígenes de extensión de navegador (`chrome-extension://…`) y loopback
  (`http://localhost`, `http://127.0.0.1`). Un origen de sitio web extranjero
  es rechazado con `403` y nunca alcanza la ruta de ingesta.
- Las respuestas permitidas envían `Access-Control-Allow-Origin: <that origin>`,
  `Access-Control-Allow-Methods: POST, OPTIONS`, y
  `Access-Control-Allow-Headers: Content-Type`.
- El preflight de **Acceso a Red Privada** de Chrome es honrado: cuando la
  solicitud lleva `Access-Control-Request-Private-Network: true`, el
  servidor responde `Access-Control-Allow-Private-Network: true` así que una
  extensión de Web Store puede alcanzar `localhost`.
- El cuerpo de la solicitud está limitado (5 MB) antes de ser leído.

---

## La herramienta MCP `ingest`

La misma ruta de ingesta se expone a agentes a través del servidor MCP de Tesserae
como la herramienta `ingest`, así que un agente puede clipear contenido que encontró sin
un navegador:

| Entrada   | Requerido | Notas |
|-----------|----------|-------|
| `content` | sí       | El texto a ingerir. |
| `url`     | no       | URL fuente (procedencia + slug). |
| `title`   | no       | Título del documento. |
| `note`    | no       | Anotación → `## Note`. |
| `tags`    | no       | Etiquetas de front-matter. |
| `tldr`    | no       | Predeterminado `true`. |

Ingiere al **proyecto activo** (resuelve con `activate_project`
o pasa `project`) y retorna el mismo informe `{status, path, tldr, node_count,
edge_count}`. Ver [mcp.md](mcp.es.md) para configuración de MCP.

---

## Alternar TL;DR

TL;DR está activado por defecto. Desactívalo por-clip en el popup de la extensión (o
envía `"tldr": false`) cuando quieres un clip rápido y determinista sin una llamada LLM — p. ej.
clipeando a un proyecto aislado del aire o cuando `claude` no está en
PATH. Con está activado, un resumen fallido/ausente nunca bloquea el clip; simplemente
no obtienes una sección `## TL;DR`.

---

## Atajo de teclado

El clipper registra un comando que puedes vincular bajo
`chrome://extensions/shortcuts`. El predeterminado es:

- **Clipear página actual / selección:** `Ctrl+Shift+S` (macOS:
  `Cmd+Shift+S`)

Reasígnalo allí si choca con otra extensión.
