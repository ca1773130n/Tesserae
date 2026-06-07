# Acompañante multimodal RAG-Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.fr.md">Français</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) es un framework RAG multimodal (construido sobre LightRAG) que parsea PDFs, documentos de Office, imágenes y ecuaciones a través de MinerU/Docling/PaddleOCR. Tesserae lo integra tanto como una canalización de ingesta multimodal (proyección de grafo nativa al estilo UA) como un backend de memoria en tiempo de ejecución junto a Cognee.

## ¿Por qué usar ambos?

- Tesserae — memoria de agente duradera, compilación wiki, proyección de grafo.
- RAG-Anything — ingesta multimodal + recuperación en tiempo de ejecución de LightRAG.

Ambos se complementan: RAG-Anything aporta comprensión de PDF/Office/imágenes que los cargadores de fuentes orientados a texto de Tesserae no proporcionan; Tesserae conserva la memoria duradera y consultable que sobrevive entre sesiones.

## Flujo actual de baja fricción

La ruta recomendada es el asistente de configuración:

```bash
tesserae init
```

RAG-Anything es ahora una **pregunta interactiva del asistente** en lugar de un
conjunto de banderas de CLI. Cuando se ejecute el asistente, responde a las
preguntas de integración:

- activa RAG-Anything cuando se te pregunte;
- instálalo cuando se te pida (instala `raganything` + `docling`);
- elige el parser `mineru`;
- activa la ejecución de actualización posterior a la instalación cuando se ofrezca.

Luego compila:

```bash
tesserae compile
```

Para automatización no interactiva (CI), ejecuta el asistente con los valores
predeterminados (todas las integraciones opcionales DESACTIVADAS), después
activa RAG-Anything en `.tesserae/config.json` — el asistente escribe la
configuración de la integración bajo la clave `external_tools` /
`memory_backends` — y ejecuta la actualización administrada:

```bash
tesserae init --yes
# activar raganything en .tesserae/config.json (clave external_tools)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

Tesserae almacena un comando de actualización administrado en lugar de pedir a los usuarios que inventen uno:

```bash
tesserae integrations refresh raganything --parser mineru
```

Durante la compilación, Tesserae:

1. comprueba si `.tesserae/external/raganything/manifest.json` existe y coincide con el commit git actual (mediante el `meta.json#gitCommitHash` almacenado);
2. ejecuta el wrapper de actualización administrado si falta/está obsoleto o si se pasa `--refresh-external-tools`;
3. descubre fuentes no de código (PDFs, documentos de Office, imágenes, markdown) y las parsea con el parser configurado;
4. escribe `manifest.json` + `meta.json`;
5. continúa la compilación normal de memoria.

Puedes forzar todos los comandos externos de actualización configurados antes de compilar:

```bash
tesserae compile --refresh-integrations
```

## Equivalente manual

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## Sincronización nativa de grafos

Tesserae importa de forma nativa el manifest parseado durante compile cuando la herramienta configurada usa `sync_mode: native_graph`.

El adaptador nativo lee `.tesserae/external/raganything/manifest.json`, proyecta cada documento parseado en un `SourceFile` node con metadatos de bloques multimodales y escribe un sync manifest:

```text
.tesserae/external/raganything-sync.json
```

Mapeo actual:

| RAG-Anything | Dirección de Tesserae |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | plegado en `SourceFile.description`; concepts vía el extractor existente |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` y `metadata.equations[]` (LaTeX preservado) |

Se preserva la provenance en cada nodo:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

## Backend de memoria en tiempo de ejecución

`memory_backends.raganything` (predeterminado producido por `default_raganything_backend_config`) coexiste con Cognee. `project ask` prueba los backends por orden de prioridad; la prioridad por proyecto puede establecerse mediante `memory_backends.priority`. RAG-Anything es opcional (predeterminado `enabled: false`); la bandera de configuración `--with-raganything` lo activa.

## Requisitos del sistema

- **Python 3.10+** (requisito de RAG-Anything; Tesserae en sí apunta a 3.9+).
- **LibreOffice** para parsear `.doc/.docx/.ppt/.pptx/.xls/.xlsx` — instálalo por separado mediante el gestor de paquetes de tu plataforma. RAG-Anything omite documentos de Office con una advertencia cuando falta LibreOffice.
- **Los pesos de modelo de MinerU** se descargan en el primer parseo y se almacenan en caché (~GBs). Las ejecuciones siguientes reutilizan la caché.
- **Claves de LLM/embedding/visión compatibles con OpenAI** (`OPENAI_API_KEY`, `OPENAI_BASE_URL`) para el backend de memoria en tiempo de ejecución. El modo solo parser no requiere claves.

## Principio de colaboración

Tesserae sigue siendo el memory compiler. RAG-Anything sigue siendo un acompañante independiente: parser multimodal + motor de recuperación LightRAG.
