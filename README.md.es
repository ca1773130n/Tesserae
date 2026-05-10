<h1 align="center">LLM-Wiki</h1>

<p align="center">
  <strong>El compilador de memoria para agentes de programación.</strong>
  <br />
  <em>Compila repositorios, documentación, notas de investigación, sesiones de Claude/Codex y herramientas gráficas complementarias en memoria validada para Cognee, MCP, Kuzu, SQLite, llms.txt y documentación estática.</em>
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.md.ko">한국어</a> ·
  <a href="./README.md.zh">中文</a> ·
  <a href="./README.md.ja">日本語</a> ·
  <a href="./README.md.ru">Русский</a> ·
  <a href="./README.md.es">Español</a> ·
  <a href="./README.md.fr">Français</a>
</p>

<p align="center">
  <a href="#inicio-rápido"><img src="https://img.shields.io/badge/start-project_setup-blue" alt="Configuración del proyecto" /></a>
  <a href="#cognee--llm-wiki"><img src="https://img.shields.io/badge/Cognee-memory_backend-d4a574" alt="Backend de memoria Cognee" /></a>
  <a href="#por-qué-lo-usan-los-agentes"><img src="https://img.shields.io/badge/agents-MCP%20%7C%20llms.txt%20%7C%20harness-38bdf8" alt="Exportaciones para agentes" /></a>
  <a href="#canalización-de-memoria"><img src="https://img.shields.io/badge/graph-validated%20ontology-8A2BE2" alt="Grafo validado" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/ca1773130n/LLM-Wiki" alt="Licencia" /></a>
</p>

<p align="center">
  <img src="./docs/assets/wiki-graph-screenshot.png" alt="Sitio estático de LLM-Wiki que muestra un grafo compilado de memoria del proyecto y un explorador de fuentes" width="100%" />
</p>

---

## La propuesta

La mayoría de las herramientas wiki para LLM crean otra página de notas generadas.

**LLM-Wiki construye la capa de memoria desde la que empieza tu próximo agente.** Toma la realidad desordenada de un proyecto — archivos fuente, documentación en Markdown, notas de investigación, transcripciones locales de Claude/Codex y artefactos gráficos externos — y la compila en un sistema de memoria tipado y portátil.

El sitio web es solo la ventana de cristal. El producto es el artefacto de memoria compilada.

<table>
  <tr>
    <td width="33%" valign="top">
      <h3>🧬 Validar la memoria</h3>
      <p>Restringe nodos y aristas antes de que lleguen a la recuperación. Evita una sopa aleatoria de <code>related_to</code>, entidades duplicadas y esquemas que derivan.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🧠 Preservar el trabajo del agente</h3>
      <p>Convierte sesiones de Claude Code y Codex en memoria de proyecto consultable: decisiones, comandos, archivos, resúmenes y trazas de herramientas.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🔌 Exportar a todas partes</h3>
      <p>Envía la misma memoria a Cognee, MCP, Kuzu, SQLite, episodios estilo Graphiti, <code>llms.txt</code>, Markdown y un sitio web estático.</p>
    </td>
  </tr>
</table>

---

## Por qué lo usan los agentes

| Si solo tienes... | Tu agente todavía tiene que... | LLM-Wiki le da... |
|---|---|---|
| Un README | redescubrir la arquitectura y las decisiones | memoria de proyecto tipada + procedencia del código fuente |
| Un sitio de documentación | buscar páginas como una persona | herramientas MCP, `llms.txt`, grafo JSON, contexto por página |
| Una base de datos vectorial | adivinar relaciones a partir de fragmentos | nodos, aristas, alias, afirmaciones y evidencias validados |
| Un visualizador de grafos | admirar una imagen | artefactos de grafo portátiles que los sistemas de recuperación pueden usar |
| Historial de chat | olvidar el trabajo anterior | sesiones de agentes importadas como memoria duradera |

---

## Canalización de memoria

```mermaid
flowchart TB
  A["Raw project sources<br/>README · docs · code · research notes"]
  B["Agent sessions<br/>Claude Code · Codex · subagents"]
  C["Companion artifacts<br/>Understand Anything · external graphs"]
  D["LLM-Wiki compiler<br/>detect · refresh · extract · validate"]
  E["Typed memory graph<br/>ontology · aliases · evidence · temporal facts"]
  F["Runtime memory backends<br/>Cognee · MCP · Kuzu · SQLite"]
  G["Agent context exports<br/>llms.txt · harness · JSON · markdown"]
  H["Inspectable projections<br/>static wiki · 2D/3D graph · source pages"]

  A --> D
  B --> D
  C --> D
  D --> E
  E --> F
  E --> G
  E --> H
```

---

## Cognee + LLM-Wiki

**LLM-Wiki compila memoria. Cognee la recupera.**

Cognee es potente como backend de memoria de IA: recuperación por grafo + vectores, memoria semántica y hooks conscientes de la ontología. Pero la ingesta sin procesar de repositorios/documentación puede volverse ruidosa si la memoria que entra no está restringida.

LLM-Wiki actúa como el paso de compilación antes de Cognee:

| Capa | Rol de LLM-Wiki | Rol de Cognee |
|---|---|---|
| Captura de fuentes | rastrea documentación, código, investigación, sesiones y artefactos complementarios | puede ingerir muchos tipos de datos |
| Estructura | valida tipos de nodos/aristas, alias, evidencia y procedencia | almacena y recupera memoria semántica |
| Runtime | exporta paquetes Cognee limpios o flujos cognify de Codex/OAuth | sirve memoria híbrida grafo/vectorial a los agentes |
| Seguridad | mantiene disponibles rutas deterministas/local-first | añade recuperación de memoria más rica cuando se desea |

```mermaid
flowchart LR
  A["Messy project context"] --> B["LLM-Wiki<br/>validated memory graph"]
  B --> C["Cognee<br/>hybrid graph + vector retrieval"]
  C --> D["Coding agents<br/>ask better questions with durable context"]
```

Usa Cognee cuando quieras que la memoria compilada se convierta en un sustrato de recuperación en vivo para agentes. Usa LLM-Wiki cuando quieras controlar, validar, exportar e inspeccionar esa memoria antes de que se convierta en contexto de ejecución.

---

## Inicio rápido

```bash
pip install llm-wiki

llm_wiki project setup
llm_wiki project compile
llm_wiki project ask "Which files implement Mermaid rendering?"
llm_wiki project build-site
llm_wiki project serve --port 8765
```

Si quieres usar Understand Anything y Cognee juntos, configúralos una vez:

```bash
llm_wiki project setup \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
llm_wiki project compile
```

Abre:

```text
http://127.0.0.1:8765/
```

El asistente de setup detecta fuentes comunes como `README.md`, `docs`, `src`, `data` y artefactos complementarios. Si seleccionas Understand Anything, LLM-Wiki instala las skills complementarias cuando lo pides y guarda un wrapper de refresh gestionado, de modo que `project compile` puede actualizar `.understand-anything/knowledge-graph.json` sin que el usuario sepa dónde está instalado UA ni cómo invocar `/understand`. Cognee queda habilitado como backend de preguntas por defecto; el cognify en runtime se activa explícitamente con `--run-cognee`.

```text
◆ LLM-Wiki project setup
Choose sources and companion tools. Press Enter to accept defaults.

Sources
  ✓ README.md
  ✓ docs
  ✓ src
  ✓ .llm-wiki/external/understand-anything.md

External tools
  ◆ Understand Anything → .llm-wiki/external/understand-anything.md

Memory backends
  ◆ Cognee → my_project_memory (codex_cognify, manual cognify)
```

---

## Qué exporta

| Salida | Por qué importa |
|---|---|
| `cognee_bundle/` | artefactos de grafo limpios para flujos de trabajo de memoria estilo Cognee |
| `graph.json` / `graph.jsonld` | grafo de memoria tipado y portátil |
| `sqlite.db` / salida Kuzu | almacenamiento local de grafos consultable |
| `llms.txt` / `llms-full.txt` | paquetes de contexto directos para agentes |
| Servidor MCP | `search_nodes`, `node_context`, `timeline` y herramientas de grafo |
| `agent_harness/` | configuración para Claude Code, Codex, Gemini, Cursor, Kiro y OpenCode |
| `markdown_projection/` | archivos wiki legibles para personas y editores |
| `.llm-wiki/site/` | sitio web estático para inspección, uso compartido y depuración |

---

## Herramientas complementarias, sin dependencia cautiva

LLM-Wiki está diseñado para situarse entre herramientas, no para reemplazarlas.

| Herramienta | Relación |
|---|---|
| Understand Anything | artefacto independiente de grafo de código → proyección Markdown → memoria compilada |
| Cognee | backend de memoria para recuperación híbrida grafo/vectorial |
| Sistemas estilo Graphiti | ruta de exportación de episodios/hechos temporales |
| Obsidian / markdown | proyección legible, no la única fuente de verdad |
| Claude Code / Codex | fuente de memoria de sesiones y consumidores del contexto compilado |

Usa el setup gestionado: LLM-Wiki instala las skills complementarias, guarda el wrapper de refresh y puede activar la memoria runtime de Cognee en un solo comando:

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
llm_wiki project compile
```

Durante la compilación, LLM-Wiki ejecuta `project refresh-understand-anything` cuando el grafo de UA falta o está obsoleto, materializa `.llm-wiki/external/understand-anything.md`, escribe `.llm-wiki/cognee_bundle/` y, si está configurado, refresca la memoria runtime de Cognee en modo best-effort. El usuario no necesita saber dónde están instalados UA o Cognee.

---

## Cuándo LLM-Wiki es la herramienta adecuada

| Quieres... | Usa LLM-Wiki porque... |
|---|---|
| mejor continuidad para agentes de programación | las sesiones antiguas de Claude/Codex se convierten en memoria consultable |
| entradas GraphRAG más seguras | la validación de esquemas ocurre antes de la recuperación |
| flujos de trabajo local-first | la extracción determinista y las rutas CLI/OAuth evitan el gasto obligatorio en claves API |
| memoria de proyecto portátil | una compilación emite artefactos para Cognee, MCP, SQLite, Kuzu, Markdown, JSON y sitio web |
| inspección humana | el sitio estático te permite depurar lo que recuperarán los agentes |

---

## Documentación

| Guía | Qué obtienes |
|---|---|
| [Inicio rápido](./docs/quickstart.md) | primera compilación de memoria de proyecto |
| [Instalación](./docs/installation.md) | opciones de instalación y wrappers |
| [Arquitectura](./docs/architecture.md) | detalles internos de la canalización y modelo de grafo |
| [Historial de sesiones](./docs/session-history.md) | importación de transcripciones de Claude/Codex |
| [Flujo de trabajo complementario de Understand Anything](./docs/integrations/understand-anything.md) | actualización y proyección del grafo complementario |
| [Lista de verificación de publicación](./docs/publishing-checklist.md) | despliega el sitio estático generado |

---

<p align="center">
  <strong>No le des a tu próximo agente un repositorio en blanco. Dale memoria compilada.</strong>
</p>
