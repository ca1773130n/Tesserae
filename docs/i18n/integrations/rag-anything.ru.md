# Мультимодальный сопутствующий инструмент RAG-Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) — мультимодальный RAG-фреймворк (построен на LightRAG), парсящий PDF, Office-документы, изображения и уравнения через MinerU/Docling/PaddleOCR. Tesserae интегрирует его и как мультимодальный конвейер инжеста (нативная графовая проекция в стиле UA), и как рантайм-бэкенд памяти рядом с Cognee.

## Зачем оба?

- Tesserae — долгоживущая память агентов, компиляция wiki, графовая проекция.
- RAG-Anything — мультимодальный инжест + рантайм-retrieval LightRAG.

Они дополняют друг друга: RAG-Anything приносит понимание PDF/Office/изображений, которого нет у text-first загрузчиков источников Tesserae; Tesserae хранит долгоживущую, запрашиваемую память, переживающую сессии.

## Текущий низкофрикционный рабочий процесс

Рекомендуемый путь — визард настройки:

```bash
tesserae init
```

RAG-Anything теперь — **интерактивная подсказка визарда**, а не набор
CLI-флагов. Когда визард запустится, ответьте на вопросы интеграции:

- включите RAG-Anything, когда спросят;
- установите его по запросу (устанавливает `raganything` + `docling`);
- выберите парсер `mineru`;
- включите post-install refresh-прогон, когда предложат.

Затем компилируйте:

```bash
tesserae compile
```

Для неинтерактивной автоматизации (CI) запустите визард с дефолтами (все
опциональные интеграции ВЫКЛ), затем включите RAG-Anything в
`.tesserae/config.json` — визард пишет конфиг интеграции под ключами
`external_tools` / `memory_backends` (см. ключи, на которые ссылается этот
документ ниже) — и запустите управляемый refresh:

```bash
tesserae init --yes
# enable raganything in .tesserae/config.json (external_tools key)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

Визард настройки устанавливает `raganything` и `docling` вместе. MinerU остаётся opt-in: устанавливайте его через `pip install 'mineru[core]'` только если у вас есть PDF или изображения для инжеста.

Tesserae хранит управляемую команду refresh, а не просит пользователей придумать её:

```bash
tesserae integrations refresh raganything --parser mineru
```

Во время компиляции Tesserae:

1. проверяет, существует ли `.tesserae/external/raganything/manifest.json` и совпадает ли он с текущим git-коммитом (через сохранённый `meta.json#gitCommitHash`);
2. запускает управляемую refresh-обёртку, если он отсутствует/устарел или передан `--refresh-external-tools`;
3. обнаруживает некодовые источники (PDF, Office-документы, изображения, markdown) и парсит их через настроенный парсер;
4. пишет `manifest.json` + `meta.json`;
5. продолжает обычную компиляцию памяти.

Можно принудить все настроенные внешние refresh-команды перед компиляцией:

```bash
tesserae compile --refresh-integrations
```

## Ручной эквивалент

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## Compile-time против runtime

Tesserae чисто разделяет интеграцию:

- **Compile-time парсинг** (`refresh-raganything` и `compile`): запускает парсеры напрямую — нативное чтение для `.md/.txt/.rst`, `docling.DocumentConverter` для всего остального. Полный конвейер RAG-Anything здесь *не* вызывается, поэтому LLM/embedding/vision-ключи для успешной компиляции не нужны.
- **Runtime-запросы** (`project ask`): `raganything_query.py` инстанцирует `RAGAnything` с настроенными в проекте функциями LLM/эмбеддингов/vision и запускает `aquery` против хранилища LightRAG. Этот путь требует API-ключей.

Разделение означает, что `compile` быстр, детерминирован и не требует ключей; LLM-токены стоят только операции времени retrieval.

## Нативная синхронизация графа

Tesserae импортирует распарсенный манифест нативно во время компиляции, когда настроенный инструмент использует `sync_mode: native_graph`.

Нативный адаптер читает `.tesserae/external/raganything/manifest.json`, проецирует каждый распарсенный документ в узел `SourceFile` с метаданными мультимодальных блоков и пишет манифест синхронизации:

```text
.tesserae/external/raganything-sync.json
```

Текущее отображение:

| RAG-Anything | Направление Tesserae |
|---|---|
| `documents[*]` | Узел `SourceFile`, `metadata.parser="raganything"` |
| `content_list[type=text]` | сворачивается в `SourceFile.description`; концепты через существующий экстрактор |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` и `metadata.equations[]` (LaTeX сохраняется) |

Provenance сохраняется на каждом узле:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

Заметка: интерактивный вид графа скрывает узлы группы `sources` по умолчанию, чтобы сфокусироваться на концептах и сущностях — проецируемые raganything SourceDocument-ы остаются в `graph.json` (MCP, Cognee, поиск, по-страничные wiki-виды по-прежнему их видят), просто не затапливают канву. Установите `graph_view.show_sources = true` в `.tesserae/config.json`, чтобы вернуть плотный вид.

## Рантайм-бэкенд памяти

`memory_backends.raganything` (дефолт, производимый `default_raganything_backend_config`) сосуществует с Cognee. `project ask` пробует бэкенды в порядке приоритета; пер-проектный приоритет можно задать через `memory_backends.priority`. RAG-Anything — opt-in (по умолчанию `enabled: false`); флаг настройки `--with-raganything` включает его.

### LLM-провайдер (API-ключ не нужен)

Рантайм-бэкенду RAG-Anything нужен LLM для ответов на запросы. Tesserae по умолчанию использует свои существующие OAuth-интеграции CLI — API-ключ не требуется:

| Провайдер | Как аутентифицируется | Флаг настройки |
|---|---|---|
| `codex` (дефолт) | OAuth CLI `codex` (вы залогинились один раз через `codex login`) | `--raganything-llm-provider codex` |
| `claude` | CLI `claude -p`; уважает `CLAUDE_CONFIG_DIR` для мультиаккаунтных схем | `--raganything-llm-provider claude --raganything-claude-config-dir ~/.claude-personal2` |

Для мультиаккаунтных схем Claude (например, `~/.claude-personal1`, `~/.claude-personal2`) передайте `--raganything-claude-config-dir <path>` при настройке. Рантайм-бэкенд будет экспортировать `CLAUDE_CONFIG_DIR=<path>` перед каждым вызовом, чтобы использовалась авторизация выбранного аккаунта, не трогая ваш дефолтный `~/.claude`.

### Эмбеддинги

| Провайдер | Когда использовать |
|---|---|
| `deterministic` (дефолт) | Без внешних зависимостей. На хешах; низкое семантическое качество, но достаточно, чтобы LightRAG построил индекс. Хорошая база для доказательства, что интеграция работает. |
| `ollama` | Локальный Ollama с моделью эмбеддингов (например, `nomic-embed-text`). Передайте `--raganything-embedding ollama`; бэкенд по умолчанию использует `http://localhost:11434`. |

Прямая поддержка OpenAI-эмбеддингов через эти флаги в v1 не проведена — пользователи с ключами OpenAI могут задать `OPENAI_API_KEY` и переопределить `memory_backends.raganything.embedding.provider` напрямую в `.tesserae/config.json` (RAGAnything подхватит env-переменную через дефолты LightRAG).

### Вызов из CLI

```bash
# `ask` never enters memory backends: it plans retrieval over the compiled
# graph and synthesizes a cited LLM answer (--no-llm = ranked hits only).
tesserae ask "What does the integration spec say about parser routing?"

# Explicit backends live on `query` — raw retrieval, no LLM synthesis.
tesserae query "..." --backend raganything
tesserae query "..." --backend cognee
tesserae query "..." --backend wiki
```

> **Тип поиска Cognee 1.0.** Cognee 1.0 упразднил старый ретривер `INSIGHTS`,
> поэтому Tesserae по умолчанию ставит бэкенду cognee `GRAPH_COMPLETION`
> (ответ, синтезированный над графом знаний). Для сырого retrieval вместо
> генерируемого ответа передайте `--cognee-search-type CHUNKS` (или
> `SUMMARIES`).

`tesserae query --backend raganything` вызывает `tesserae.raganything_query.query` напрямую. Относительный `working_dir` в `memory_backends.raganything` разрешается относительно корня проекта перед вызовом.

### Верхнеуровневый `ask` (использует мультипроектный реестр)

Для рабочих процессов, где вы хотите спрашивать несколько зарегистрированных проектов Tesserae, не делая `cd` в каждый, верхнеуровневая команда `tesserae ask` разрешает проект через персистентный реестр, общий с MCP-сервером:

```bash
# One-time: register your projects (saved to ~/.tesserae/registry.json).
tesserae projects register ~/Developer/Projects/Tesserae --name tesserae
tesserae projects register ~/Developer/Projects/Other --name other

# List registered projects.
tesserae projects list

# Ask from inside a project (the smart router picks the target otherwise).
tesserae ask "How does the parser routing work?"

# Ask a specific registered project by name.
tesserae ask "What is the architecture?" --name other

# Pass a direct path, or hit a memory backend explicitly via `query`.
tesserae ask "..." --project /tmp/somewhere
tesserae query "..." --backend raganything --json
```

Логика диспетчеризации — `--project > --name > router` — реализована в верхнеуровневом обработчике ask, а форматирование ответа разделяется с MCP-инструментом `ask` через `tesserae.query.ask_project` (memory-бэкенды достижимы только через `tesserae query --backend …`). Реестр файловый (`~/.tesserae/registry.json` по умолчанию), поэтому он персистентен между сессиями и остаётся синхронным со списком проектов MCP-сервера.

#### Запросы по нескольким vault (`--scope all-registered`)

Ставка B2 — когда у вас несколько зарегистрированных проектов (research-vault, рабочий vault, vault сайд-проекта) и вы хотите задать один вопрос всем сразу, используйте `--scope all-registered`:

```bash
# Fan out across every registered project. The aggregated envelope is
# {"scope": "all-registered", "question": "...", "by_project": {"<alias>": <envelope>}}.
tesserae ask "What did I write about RLHF?" --scope all-registered --json

# Restrict to a hand-picked subset of aliases.
tesserae ask "..." --scope all-registered --scope-aliases research side-projects
```

Обработчик перебирает зарегистрированные проекты в алфавитном порядке, вызывает `ask_project` для каждого и агрегирует пер-проектные конверты. Отказ одного проекта — отсутствующий конфиг, невключённый RAG-Anything, лежащий Cognee — захватывается как `{"error": "..."}` в слоте этого alias и никогда не прерывает остальной fan-out. Тот же аргумент `scope` принимает MCP-инструмент `ask`, поэтому кодинг-агенты через MCP получают тот же fan-out без лишней обвязки.

### Мультипроектный реестр (`tesserae projects`)

| Команда | Назначение |
| --- | --- |
| `tesserae projects list [--json]` | Показать зарегистрированные проекты (все равны — «активного» нет). |
| `tesserae projects register <path> [--name <alias>]` | Добавить проект в реестр; alias по умолчанию — санитизированное имя каталога. |
| `tesserae projects unregister <name>` | Удалить запись из реестра. |

Эти команды работают напрямую с `tesserae.mcp_server.ProjectRegistry` — без MCP-обхода — поэтому их можно скриптовать без запущенного MCP-сервера.

### Вызов из MCP

Stdio MCP-сервер выставляет инструмент `ask` с тем же селектором бэкенда:

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "tesserae"
  }
}
```

Порядок диспетчеризации (`raganything` → `cognee` → поиск по скомпилированной wiki) и разрешение `working_dir` в точности зеркалят CLI-обработчик, поэтому кодинг-агенты и люди-операторы сходятся на одних и тех же ответах.

## Системные требования

- **Python 3.10+** требуется для RAG-Anything (апстрим-пакет `raganything` ≥1.3.0 транзитивно зависит от `mineru[core]`, который Python 3.10+). На более старых Python Tesserae отключает интеграцию с ясным предупреждением, а не молча устанавливает сломанную заглушку.
- **LibreOffice** для парсинга `.doc/.docx/.ppt/.pptx/.xls/.xlsx` — устанавливается отдельно через пакетный менеджер вашей платформы. RAG-Anything пропускает Office-документы с предупреждением, когда LibreOffice отсутствует.
- **Веса моделей MinerU** скачиваются при первом парсе и кешируются (~ГБ). Последующие запуски переиспользуют кеш.
- **OpenAI-совместимые LLM/embedding/vision-ключи** для рантайм-бэкенда памяти (`OPENAI_API_KEY`, `OPENAI_BASE_URL`). Режим «только парсер» ключей не требует.

## Маршрутизация парсеров

Tesserae автоматически направляет источники к нужному парсеру по расширению файла:

| Расширение | Парсер | Причина |
|---|---|---|
| `.md`, `.markdown`, `.txt`, `.rst` | `docling` | Лёгкий; без скачивания моделей MinerU. |
| `.doc`, `.docx`, `.ppt`, `.pptx`, `.xls`, `.xlsx` | `docling` | Лучшее сохранение структуры Office согласно апстриму. |
| `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp` | настроенный дефолт (`--raganything-parser`, по умолчанию `mineru`) | OCR + извлечение таблиц. |

Управляемая обёртка `tesserae integrations refresh raganything` выставляет `--parser` (настроенный дефолт для PDF/изображений), `--parse-method {auto,ocr,txt}`, `--root` (повторяемый, ограничение поддеревом), `--force` и `--full`. Пер-бакетная маршрутизация текста/Office фиксирована (обе по умолчанию `docling`). Чтобы явно переопределить парсер текста или Office, вызовите нижележащий модуль напрямую — `python -m tesserae.raganything_refresh --text-parser <p> --office-parser <p>` — который выставляет эти два дополнительных флага. Настроенный дефолт по-прежнему применяется к PDF и изображениям.

Перед циклом парсинга Tesserae пробует, импортируем ли Python-пакет каждого требуемого парсера (`importlib.import_module(...)`), и быстро выходит с одной агрегированной ошибкой, перечисляющей каждый отсутствующий парсер и команду его установки. Мы сознательно не используем апстримовский `RAGAnything.check_parser_installation()`, потому что он инспектирует только парсер, настроенный на экземпляре, и подмешивает проверки готовности весов моделей, не подходящие для pre-flight-этапа.

Tesserae также выбирает construction-time парсер `RAGAnything` из фактического распределения маршрутизации (побеждает чаще всего выбираемый парсер), а не напрямую из `--raganything-parser`. Это избегает сценария отказа, когда `RAGAnything.__init__` пытается инициализировать тяжёлый парсер (например, `mineru`), чьих весов ещё нет на диске, и валит весь прогон до того, как per-call переопределения `parser=` могут вступить в силу. Флаг `--raganything-parser` по-прежнему управляет дефолтом для не-текстовых, не-Office источников (PDF, изображения).

### Пакеты парсеров

Compile-time путь парсинга использует `docling.DocumentConverter` напрямую для каждого не-текстового источника; установите его один раз — и вы покрыты:

| Парсер | Команда установки |
|---|---|
| `docling` (compile-time дефолт для всего, кроме нативного текста) | идёт в комплекте при запуске `--with-raganything --install-raganything` (или `pip install docling` отдельно) |
| `paddleocr` (опциональная OCR-альтернатива) | `pip install 'raganything[paddleocr]>=1.3.0'` и `pip install paddlepaddle` (платформо-специфичный wheel) |

> Заметка: `mineru` сейчас **не вызывается на этапе компиляции**. Compile-путь обходит полный конвейер RAG-Anything (который потребовал бы LLM/embedding/vision-коллаблов) и направляет каждый не-текстовый источник через docling напрямую. Поддержка MinerU зарезервирована для будущего пути прямого импорта, вливающего произведённый извне `content_list.json`.

Когда настроенный парсер отсутствует, `refresh-raganything` быстро выходит — перечисляя каждый отсутствующий парсер в одной ошибке с правильной командой установки — вместо каскада пофайловых сбоев.

### Виджет ask на странице

Каждая детальная страница (концепт, статья, репо, синтез, сущность, топик, вопрос, источник) включает inline-виджет «спросить об этой странице». Он POST-ит на `/api/ask` локального экземпляра `tesserae serve`, который вызывает `tesserae.query.ask_project` и рендерит ответ inline. В отличие от CLI (где `tesserae ask` — LLM-по-умолчанию), `/api/ask` по умолчанию делает **не-LLM retrieval** ради латентности виджета; пошлите `{"llm": true}` в payload, чтобы включить планируемый/синтезируемый ответ. Виджет добавляет имя узла текущей страницы перед вопросом пользователя как естественноязыковую контекстную подсказку (например `` About `<NodeName>`: <question> ``); будущий PR может провести настоящий subgraph-скоупинг в сам `ask_project`.

Виджет определяет доступность бэкенда через `/api/ask/health` при загрузке. Когда wiki обслуживается статически (GitHub Pages, `file://`, S3, любой простой статический хостинг), виджет сворачивается в однострочную заметку, указывающую читателям на `tesserae serve` для локального интерактивного использования. Ни один запрос не падает и ничто не блокирует рендер страницы — виджет является отложенным JS-островом, отдельным от более тяжёлого граф-бандла.

Спарьте это с мультипроектным реестром (`tesserae projects register`) — и вы сможете спрашивать wiki любого зарегистрированного проекта с любой из его детальных страниц.

## Принцип сотрудничества

Tesserae остаётся компилятором памяти. RAG-Anything остаётся независимым компаньоном: мультимодальный парсер + retrieval-движок LightRAG.
