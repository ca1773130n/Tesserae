# Мультимодальный сопутствующий инструмент RAG-Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) — мультимодальный RAG-фреймворк (на базе LightRAG), который разбирает PDF, Office-документы, изображения и формулы через MinerU/Docling/PaddleOCR. Tesserae интегрирует его и как мультимодальный конвейер загрузки (UA-стиль нативной проекции графа), и как runtime-бэкенд памяти наряду с Cognee.

## Зачем использовать оба?

- Tesserae — долговременная память агентов, компиляция wiki, проекция графа.
- RAG-Anything — мультимодальная загрузка + runtime-поиск LightRAG.

Они дополняют друг друга: RAG-Anything приносит понимание PDF/Office/изображений, которого нет у текстоориентированных загрузчиков Tesserae; Tesserae сохраняет долговременную, запрашиваемую память, переживающую сессии.

## Текущий рабочий процесс с низким трением

Рекомендуемый путь — мастер настройки:

```bash
tesserae init
```

Теперь RAG-Anything — это **интерактивный запрос мастера**, а не набор
CLI-флагов. Когда мастер запускается, ответьте на запросы об интеграциях:

- включите RAG-Anything, когда вас спросят;
- установите его, когда попросят (устанавливает `raganything` + `docling`);
- выберите парсер `mineru`;
- включите запуск обновления после установки, когда будет предложено.

Затем выполните компиляцию:

```bash
tesserae compile
```

Для неинтерактивной автоматизации (CI) запустите мастер со значениями по
умолчанию (все необязательные интеграции ВЫКЛЮЧЕНЫ), затем включите
RAG-Anything в `.tesserae/config.json` — мастер записывает конфигурацию
интеграции под ключом `external_tools` / `memory_backends` — и выполните
управляемое обновление:

```bash
tesserae init --yes
# включить raganything в .tesserae/config.json (ключ external_tools)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

Tesserae хранит управляемую команду обновления, а не просит пользователей придумывать ее самим:

```bash
tesserae integrations refresh raganything --parser mineru
```

Во время компиляции Tesserae:

1. проверяет, существует ли `.tesserae/external/raganything/manifest.json` и совпадает ли он с текущим git-коммитом (через сохраненный `meta.json#gitCommitHash`);
2. запускает управляемую обертку обновления, если файл отсутствует/устарел или передан `--refresh-external-tools`;
3. обнаруживает источники, не относящиеся к коду (PDF, Office-документы, изображения, markdown), и разбирает их настроенным парсером;
4. записывает `manifest.json` + `meta.json`;
5. продолжает обычную компиляцию памяти.

Можно принудительно выполнить все настроенные внешние команды обновления перед компиляцией:

```bash
tesserae compile --refresh-integrations
```

## Ручной эквивалент

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## Нативная синхронизация графа

Tesserae нативно импортирует разобранный manifest во время compile, когда настроенный инструмент использует `sync_mode: native_graph`.

Нативный адаптер читает `.tesserae/external/raganything/manifest.json`, проецирует каждый разобранный документ в `SourceFile` node с метаданными мультимодальных блоков и записывает sync manifest:

```text
.tesserae/external/raganything-sync.json
```

Текущее сопоставление:

| RAG-Anything | Направление Tesserae |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | сворачивается в `SourceFile.description`; concepts через существующий извлекатель |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` и `metadata.equations[]` (LaTeX сохраняется) |

В каждом узле сохраняется provenance:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

## Runtime-бэкенд памяти

`memory_backends.raganything` (значение по умолчанию, создаваемое `default_raganything_backend_config`) сосуществует с Cognee. `project ask` пробует бэкенды в порядке приоритета; приоритет для каждого проекта можно задать через `memory_backends.priority`. RAG-Anything подключается явно (по умолчанию `enabled: false`); флаг настройки `--with-raganything` его включает.

## Системные предварительные требования

- **Python 3.10+** (требование RAG-Anything; сам Tesserae ориентирован на 3.9+).
- **LibreOffice** для разбора `.doc/.docx/.ppt/.pptx/.xls/.xlsx` — устанавливается отдельно через пакетный менеджер вашей платформы. При отсутствии LibreOffice RAG-Anything пропускает Office-документы с предупреждением.
- **Веса моделей MinerU** скачиваются при первом разборе и кешируются (~ГБ). Последующие запуски используют кеш повторно.
- **OpenAI-совместимые ключи LLM/эмбеддингов/vision** (`OPENAI_API_KEY`, `OPENAI_BASE_URL`) для runtime-бэкенда памяти. Режим только парсинга ключей не требует.

## Принцип сотрудничества

Tesserae остается memory compiler. RAG-Anything остается независимым сопутствующим инструментом: мультимодальный парсер + поисковый движок LightRAG.
