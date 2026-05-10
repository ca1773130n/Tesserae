# Acompañante multimodal RAG-Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.fr.md">Français</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) es un framework RAG multimodal (construido sobre LightRAG) que parsea PDFs, documentos de Office, imágenes y ecuaciones a través de MinerU/Docling/PaddleOCR. LLM-Wiki lo integra tanto como una canalización de ingesta multimodal (proyección de grafo nativa al estilo UA) como un backend de memoria en tiempo de ejecución junto a Cognee.

## ¿Por qué usar ambos?

- LLM-Wiki — memoria de agente duradera, compilación wiki, proyección de grafo.
- RAG-Anything — ingesta multimodal + recuperación en tiempo de ejecución de LightRAG.

Ambos se complementan: RAG-Anything aporta comprensión de PDF/Office/imágenes que los cargadores de fuentes orientados a texto de LLM-Wiki no proporcionan; LLM-Wiki conserva la memoria duradera y consultable que sobrevive entre sesiones.

## Flujo actual de baja fricción

La ruta recomendada es el asistente de configuración:

```bash
llm_wiki project setup
```

Para automatización:

```bash
llm_wiki project setup \
  --yes \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything
llm_wiki project compile
```

LLM-Wiki almacena un comando de actualización administrado en lugar de pedir a los usuarios que inventen uno:

```bash
llm_wiki project refresh-raganything --parser mineru
```

Durante la compilación, LLM-Wiki:

1. comprueba si `.llm-wiki/external/raganything/manifest.json` existe y coincide con el commit git actual (mediante el `meta.json#gitCommitHash` almacenado);
2. ejecuta el wrapper de actualización administrado si falta/está obsoleto o si se pasa `--refresh-external-tools`;
3. descubre fuentes no de código (PDFs, documentos de Office, imágenes, markdown) y las parsea con el parser configurado;
4. escribe `manifest.json` + `meta.json`;
5. continúa la compilación normal de memoria.

Puedes forzar todos los comandos externos de actualización configurados antes de compilar:

```bash
llm_wiki project compile --refresh-external-tools
```

## Equivalente manual

```bash
pip install 'raganything[all]'
python -m llm_wiki.raganything_refresh --project . --parser mineru
llm_wiki project compile
```

## Sincronización nativa de grafos

LLM-Wiki importa de forma nativa el manifest parseado durante compile cuando la herramienta configurada usa `sync_mode: native_graph`.

El adaptador nativo lee `.llm-wiki/external/raganything/manifest.json`, proyecta cada documento parseado en un `SourceFile` node con metadatos de bloques multimodales y escribe un sync manifest:

```text
.llm-wiki/external/raganything-sync.json
```

Mapeo actual:

| RAG-Anything | Dirección de LLM-Wiki |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | plegado en `SourceFile.description`; concepts vía el extractor existente |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` y `metadata.equations[]` (LaTeX preservado) |

Se preserva la provenance en cada nodo:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".llm-wiki/external/raganything/manifest.json"}
```

## Backend de memoria en tiempo de ejecución

`memory_backends.raganything` (predeterminado producido por `default_raganything_backend_config`) coexiste con Cognee. `project ask` prueba los backends por orden de prioridad; la prioridad por proyecto puede establecerse mediante `memory_backends.priority`. RAG-Anything es opcional (predeterminado `enabled: false`); la bandera de configuración `--with-raganything` lo activa.

## Requisitos del sistema

- **Python 3.10+** (requisito de RAG-Anything; LLM-Wiki en sí apunta a 3.9+).
- **LibreOffice** para parsear `.doc/.docx/.ppt/.pptx/.xls/.xlsx` — instálalo por separado mediante el gestor de paquetes de tu plataforma. RAG-Anything omite documentos de Office con una advertencia cuando falta LibreOffice.
- **Los pesos de modelo de MinerU** se descargan en el primer parseo y se almacenan en caché (~GBs). Las ejecuciones siguientes reutilizan la caché.
- **Claves de LLM/embedding/visión compatibles con OpenAI** (`OPENAI_API_KEY`, `OPENAI_BASE_URL`) para el backend de memoria en tiempo de ejecución. El modo solo parser no requiere claves.

## Principio de colaboración

LLM-Wiki sigue siendo el memory compiler. RAG-Anything sigue siendo un acompañante independiente: parser multimodal + motor de recuperación LightRAG.
