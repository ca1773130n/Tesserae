# Карта функций

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
Этот документ кратко описывает функции, которые сейчас реализованы в Tesserae, с их статусом, исходными файлами и местом в документации.

Tesserae — это **контекстный движок**, работающий на трёх столпах: (1) мониторинг сессий, (2) автономное проактивное усвоение знаний и (3) документы/контекст по запросу. Типизированный граф, хранилище (vault) и статический сайт — проекции базы знаний. Функции ниже сгруппированы по столпу, которому они служат; веха **v0.5.0** (июнь 2026) выпустила хребет движка и флагманскую функцию Столпа 3 — контекстный компилятор по запросу.

Легенда статуса: ✅ поставлено · ⚠ в работе / частично.

## Контекстный движок — v0.5.0 (июнь 2026)

Хребет движка, приводящий в действие три столпа. Карту модулей хребта движка, sidecar памяти самоулучшения и поток данных контекстного компилятора см. в [`docs/architecture.md`](architecture.ru.md).

### Хребет движка (столпы 1 & 2)

| Функция | Статус | Источник | Примечания |
|---|---|---|---|
| `Pipeline` — переиспользуемая цепочка обновления, возвращающая `List[StepResult]` | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | Один исполнитель шагов, вызываемый CLI, демоном и MCP. Ловит `Exception` на каждом шаге; останавливается на первом сбое. |
| `Daemon` — asyncio-супервизор с единственным владельцем | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | Следит за источниками + хранилищем + каталогом сессий harness; дебаунс «отмена-и-перепланирование» сворачивает серию в один `Pipeline.run()`. pidfile; переживает исключения в полёте. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` — псевдоним `engine`. |
| `project refresh` — прозаическая цепочка (усвоение → компиляция → проекция) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only` (опциональная инкрементальность), `--skip-sessions`. |
| Живой мониторинг сессий → находки | ✅ | `harness_sessions.py` + модули графа сессий | Импортированные сессии питают граф; `fresh_insights` / `find_session_findings` выводят их на поверхность. |

### Память самоулучшения (столп 2)

| Функция | Статус | Источник | Примечания |
|---|---|---|---|
| sidecar `node_memory` SQLite (затухание / уверенность / вытеснено) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + независимые от хранилища аксессоры; только изменяемое состояние. Первое появление — в отдельном sidecar `node_provenance`. |
| Оценка затухания Эббингауза | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | Ранжирует находки сессий по новизне + частоте доступа (питает `fresh_insights`). |
| Проход вытеснения (**по умолчанию ВКЛ**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | Детерминированный вердикт помечает более старый почти-дубликат инсайта как вытесненный более новым; добавляет ребро `supersedes`. |
| Связь инсайт → кодовый символ | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | Рёбра `discusses` от инсайтов сессий к упоминаемым символам. |
| Проходы подкрепления + противоречий | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Подкрепление доступа + обнаружение противоречий над тем же sidecar. |
| Числовая уверенность повторяемости в выводе | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Временные факты проставляют `confidence` из `NodeMemoryRow.confidence`, иначе откат к `infer_confidence`. |

### Поиск + эмбеддинги (столпы 2 & 3)

| Функция | Статус | Источник | Примечания |
|---|---|---|---|
| Гибридный ретривер (BM25 + лексика + эмбеддинги, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | Локально-ориентированный, полностью детерминирован. |
| Персонализированный PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | Многопрыжковое расширение семян; подграф с ограничением глубины. |
| Реальные эмбеддинги по умолчанию (Track B, Фаза 6) | ✅ | `retrieval/hybrid.py` | По умолчанию = детерминированный псевдо-эмбеддинг на хэш-бакетах (без зависимостей); `sentence-transformers` (`all-MiniLM-L6-v2`) предпочтителен при установке, грузится лениво. MCP-инструмент `embedding_status` сообщает активный бэкенд. |

### Контекстный компилятор по запросу (Столп 3 — флагман)

| Функция | Статус | Источник | Примечания |
|---|---|---|---|
| `compile_context` — `ContextBundle` в памяти со ссылками | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Разрешение семян → расширение PPR → выбор с ограничением бюджета → markdown со ссылками → опциональный LLM-синтез. Детерминирован, если не `synthesize=true`. Ничего не пишет на диск. |
| CLI `project context` | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = без ограничения), `--synthesize`, `--output`. |
| MCP-инструмент `compile_context` | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Тот же конвейер через MCP; `budget=0` = без ограничения. |
| Срезы экспорта в рамках темы | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | `llms.txt` в рамках темы + `render_harness_context` через `compile_context`. |

### Инкрементальная компиляция (Фаза 4 — экспериментально)

| Функция | Статус | Источник | Примечания |
|---|---|---|---|
| sidecar происхождения (`node_provenance`, первое появление) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | Основа для удаления изменённого; пишется всегда. |
| Поверхность удаления `GraphStore` | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (удаляет узлы, чьё множество происхождения опустевает; межфайловые концепции выживают). |
| Диспетчеризация хранилища `url_resolver` во время выполнения | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| Флаг `incremental_compile` | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **По умолчанию OFF / экспериментально.** Байт-паритет доказан для нескольких форм правок, но остаются пробелы с несколькими владельцами/жизненным циклом продюсеров; полная компиляция остаётся по умолчанию. |

## Редизайн фронтенда — апрель 2026

Иерархическая wiki, ориентированная на документы, заменяет прежний дамп графа. См. [`docs/frontend-redesign.md`](frontend-redesign.ru.md) для обзора по маршрутам и [`docs/architecture.md`](architecture.ru.md) для трехслойной модели.

### Wiki-слой (L2 markdown)

| Функция | Статус | Источник | Якорь документации |
|---|---|---|---|
| `WikiPageStore` (идемпотентные записи body-hash, парсер frontmatter) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Карта модулей](architecture.ru.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — одна md-страница на узел wiki-слоя | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.ru.md#pipeline) |
| Страницы `sources/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.ru.md#sources) |
| Страницы `concepts/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.ru.md#concepts) |
| Страницы `entities/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.ru.md#entities) |
| Страницы `papers/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.ru.md#papers) |
| Страницы `repos/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.ru.md#repos) |
| Страницы `topics/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.ru.md#topics) |
| Страницы `questions/` (открытые вопросы) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.ru.md#questions) |
| Страницы `syntheses/` | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.ru.md#syntheses) |

### Виды синтеза (L2 → производные)

`SynthesisProjector` создает семь детерминированных шаблонов и добавляет узлы `Synthesis` плюс ребра `synthesizes` / `summarizes` обратно в граф.

| Вид | Статус | Источник | Примечания |
|---|---|---|---|
| `pulse` (один глобальный, питает `/`) | ✅ | `synthesis.py` | Пересобирается при каждом compile. |
| `daily_digest` | ✅ | `synthesis.py` | Один на `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Один на `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Один на кластер `ResearchTopic` / `ApproachFamily` с ≥ 3 papers. |
| `comparison` | ✅ | `synthesis.py` | Один на пару `ApproachFamily`, конкурирующих в одной задаче. |
| `field_overview` | ✅ | `synthesis.py` | Один на `ResearchField`. |
| Сводки, улучшенные LLM (через env-флаг) | ⚠ | только hook | Эвристическая базовая версия поставляется; hook `TESSERAE_SYNTHESIS_LLM=1` оставлен как stub. |

### Маршруты статического сайта

| Маршрут | Статус | Источник | Примечания |
|---|---|---|---|
| `/` (главная, hero pulse) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | Строка статистики + отобранные точки входа + недавняя активность. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Тепловая карта + список дней + рельс synthesis. |
| `/timeline/<YYYY-MM-DD>.html` (детали по дню) | ⚠ | пока n/a | Ячейки тепловой карты временно ведут на исходную страницу `digest.md` соответствующего дня. Subagent P подключает дневные detail-страницы через `StaticSiteBuilder`. |
| `/graph/` (интерактивные 2D + 3D) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, подсказки при наведении, подписи ребер, zoom с привязкой к курсору. |
| `/about.html` | ✅ | `pages.py::render_about` | Schema, информация о сборке. |

### Экспорты, удобные для ИИ

| Артефакт | Статус | Источник | Назначение |
|---|---|---|---|
| Соседний файл `<page>.txt` для каждой страницы | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | Текстовый вид одной страницы (без навигации и стилей). |
| Соседний файл `<page>.json` для каждой страницы | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | Короткий индекс llmstxt.org. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Тело всех страниц, ограничено 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`, только узлы wiki-слоя. |
| `graph.json` | ✅ | `__init__.py::write_site` | Полный payload графа (вкл. code nodes для tooling). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | Палитра + поиск страниц; только типы wiki-слоя. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Все выпущенные маршруты, `lastmod` из frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Последние 30 syntheses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Разрешительный — crawl + index. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Машиночитаемая карта сайта. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + размер для каждого выпущенного файла (harness идемпотентности). |

### Визуальный дизайн + UX

| Функция | Статус | Источник | Примечания |
|---|---|---|---|
| Design tokens (светлая + темная темы, терракотовый акцент) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Один CSS bundle в `assets/style.css`. |
| Переключатель темы (сохраняется, без вспышки) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` в `localStorage`, применяется до отрисовки. |
| Поисковая палитра (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Нечеткое совпадение по `search-index.json`; список недавних страниц. |
| Липкий правый TOC | ✅ | `pages.py` + `tokens.py` | Только desktop; mobile drawer через `<details>`. |
| Тепловая карта активности с метками месяцев + дней недели | ✅ | `components.py::heatmap_svg` | SVG на 26 недель, ячейки ведут на дневной `digest.md`. |
| Sparkline (по concept/entity) | ✅ | `components.py::sparkline_svg` | Недельные счетчики упоминаний, последние 12 недель. |
| Mobile shell (drawer rail, bottom nav, fluid type) | ✅ | `tokens.py` + `pages.py` | Сенсорные цели ≥ 44 px. |
| Переходы страниц (opacity 120 ms, prefers-reduced-motion) | ✅ | `tokens.py` | |
| 3D + 2D вид графа (hover, подписи ребер, zoom с привязкой к курсору) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, vendored как CDN snapshot. |
| Footer AI-соседей на странице | ✅ | `components.py::ai_siblings_footer` | Inline-ссылки на `.txt` и `.json` текущей страницы. |
| Страницы истории сессий harness | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Явный импорт Claude Code/Codex; индекс `/sessions/` и detail-страницы с markdown turns, левым turn rail, свернутым tool use и search entries. |

### Pipeline + CLI

| Функция | Статус | Источник | Примечания |
|---|---|---|---|
| `project compile` вызывает synthesis + wiki + site по порядку | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | Фаза 3 плана редизайна. |
| `project build-site` standalone | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | Читает `wiki/` + `graph.json`, пишет `site/`. |
| `project serve` локальный HTTP | ✅ | `cli.py` | Простой stdlib server. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Worktree push в `gh-pages`; опциональный `--enable-pages` через `gh` CLI. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Входящая история сессий для Claude Code/Codex; обнаружение явное и ограничено рабочим каталогом проекта. |
| `project watch` пересборка-при-изменении | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | Автономный опрашивающий watcher: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. Многоисточниковый супервизор — в `project engine`/`daemon` (см. Контекстный движок). |
| `project context` — компиляция документа контекста со ссылками | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Флагман Столпа 3; см. раздел Контекстный движок. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | Прозаическая цепочка обновления + цикл супервизора; см. раздел Контекстный движок. |

## Ранее существовавшие функции (перенесены без изменений)

### CLI и установка

- ✅ Устанавливаемый Python package через `pyproject.toml`.
- ✅ Console commands: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` для установки `curl | bash`.
- ✅ Editable installs по умолчанию для быстрой локальной разработки.

### Извлечение

- ✅ Детерминированный extractor исследовательских заметок с контролируемыми словарями nodes/edges.
- ✅ Claude CLI/OAuth extractor для более качественного структурированного извлечения без API keys.
- ✅ Выборочная маршрутизация Claude по glob и budget limit.
- ✅ Детерминированный extractor development-code для Python проектов.
- ✅ Batch ingest с content hashing и поддержкой `--changed-only`.
- ✅ Чтение источников с терпимостью к некорректному UTF-8.

### Управление графом

- ✅ Контролируемый список `ResearchNodeType` — теперь включает `SYNTHESIS`.
- ✅ Контролируемый whitelist edge types — теперь включает `synthesizes`, `summarizes`.
- ✅ Валидация для отклонения schema drift.
- ✅ Каноникализация alias.
- ✅ Review queue для неоднозначных почти-дубликатов nodes.
- ✅ Шаблон review decisions и workflow merge/keep-separate.
- ✅ Сводка трендов корпуса из графов по файлам.

### Персистентность и отчеты

- ✅ Экспорт Graph JSON.
- ✅ SQLite graph store.
- ✅ Опциональный Kuzu graph store.
- ✅ Graph report с counts, evidence coverage, orphan nodes, date buckets, alias-heavy nodes.
- ✅ Competitive report с описанием идей, заимствованных из MegaMem, Graphiti/Zep, MCP graph servers, agentic RAG.

### Project-local workflow

- ✅ `tesserae project init`
- ✅ `tesserae project ingest`
- ✅ `tesserae project compile`
- ✅ `tesserae project mcp-config`
- ✅ `tesserae project build-site`
- ✅ `tesserae project serve`
- ✅ `tesserae project deploy` (GitHub Pages)
- ✅ `tesserae project sessions discover/import/list` (явный импорт локальной agent-history)
- ✅ `tesserae project watch` (автономный опрашивающий watcher)
- ✅ `tesserae project engine` / `tesserae project daemon` (цикл супервизора — v0.5.0)
- ✅ `tesserae project refresh` (прозаическая цепочка усвоение → компиляция → проекция — v0.5.0)
- ✅ `tesserae project context` (контекстный компилятор по запросу — v0.5.0)
- ✅ `tesserae project export-agent-harness`
- ✅ `tesserae project export-obsidian`
- ✅ `tesserae project export-graphiti`
- ✅ `tesserae project sync-graphiti`

### Obsidian

- ✅ Готовый к открытию vault export.
- ✅ `.obsidian/app.json` и настройки графа.
- ✅ Markdown projection.
- ✅ Структура `raw/assets/`.
- ✅ `_meta/dashboard.md` с Dataview query.

### Agent harnesses

Создаваемые target files для:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering and MCP settings
- ✅ Cursor: project rules and MCP config
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / temporal facts

- ✅ Проекция temporal facts с полями provenance, currentness, confidence и invalidation.
- ✅ Dependency-free экспорт Graphiti episode JSONL.
- ✅ Smoke `sync-graphiti --dry-run` без установленного Graphiti.
- ✅ Опциональная live sync с `graphiti_core` и Neo4j.

### Cognee

- ✅ Cognee JSONL bundle (`nodes.jsonl`, `edges.jsonl`, `manifest.json`).
- ✅ Опциональный add-only direct import.
- ✅ Опциональный Cognee cognify adapter на базе Codex CLI/OAuth.
- ✅ Детерминированный и Ollama embedding adapter paths для no-API-key smoke/quality workflows.

### MCP server

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` поверх stdio JSON-RPC.
- ✅ Инструменты поиска/графа: `schema`, `graph_summary`, `search_nodes`, `node_context` (с `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`.
- ✅ Инструменты контекстного движка (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (ранжирование по затуханию), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ Инструменты настройки: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Реестр нескольких проектов: `list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`. Диспетчеризация URL хранилища через `url_resolver`.

## Тесты

Текущий набор покрывает:

- ✅ ontology guardrails (вкл. новый узел `Synthesis` + ребра `synthesizes` / `summarizes`);
- ✅ deterministic extraction;
- ✅ parsing/validation обертки Claude CLI;
- ✅ selective Claude routing;
- ✅ workflow canonicalization/review;
- ✅ batch ingest;
- ✅ reports;
- ✅ SQLite/Kuzu persistence;
- ✅ Cognee bundles/import patches;
- ✅ Graphiti export/sync dry-run;
- ✅ project CLI workflow;
- ✅ agent harness export;
- ✅ Obsidian export;
- ✅ frontend generation + link integrity (без `nodes/codeclass-*.html`);
- ✅ wiki store idempotence;
- ✅ synthesis projector golden + idempotence;
- ✅ site components, pages, exports, relevance;
- ✅ форма AI-sibling (`.txt` + `.json` на страницу);
- ✅ end-to-end compile-twice idempotence;
- ✅ хребет движка: конвейер, цепочка обновления, ядро демона + источники, CLI `project engine`;
- ✅ память самоулучшения: sidecar, затухание/вытеснение, подавление вытеснения (вкл. MCP), подкрепление/противоречия;
- ✅ поиск + эмбеддинги: гибридный поиск, PPR, реальные эмбеддинги по умолчанию (Фаза 6);
- ✅ контекстный компилятор: форма/целостность ссылок/детерминизм/бюджет/откат PPR, CLI `project context`, MCP `compile_context`;
- ✅ инкрементальная компиляция (экспериментально): диффер, проверки паритета, готовность происхождения, происхождение SQLite;
- ✅ package install и installer contract.
