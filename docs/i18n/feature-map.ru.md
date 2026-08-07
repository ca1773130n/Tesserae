# Карта функций

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
Этот документ подытоживает функции, реализованные сейчас в Tesserae, — со статусом, исходными файлами и местом их документирования.

Tesserae — это **контекстный движок**, работающий на трёх столпах: (1) мониторинг сессий, (2) автономный проактивный инжест знаний и (3) документы/контекст по требованию. Типизированный граф, vault и статический сайт — проекции базы знаний. Функции ниже сгруппированы по столпу, которому они служат; веха **v0.5.0** (июнь 2026) поставила хребет движка и главную фичу столпа 3 — компилятор контекста по требованию.

Легенда статусов: ✅ поставлено · ⚠ в работе / частично.

## Межпроектность и UX — v0.11.0 (июнь 2026)

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| Межпроектная федерация | ✅ | [`tesserae/federation.py`](../../tesserae/federation.py) | `ask --scope federated` собирает ОДИН граф из нескольких зарегистрированных проектов — слияние по идентичности (один arxiv/repo/hash/symbol) + opt-out эмбеддинговые связи `shares_concept_with` — и возвращает единый перекрёстно-сослан­ный, цитированный ответ по объединению (PPR + `compile_context`). Пер-проектный `graph.json` только читается; детерминировано для identity-only. |
| Умный роутер `ask` (без активного проекта) | ✅ | [`tesserae/ask_router.py`](../../tesserae/ask_router.py) | Понятие «активного проекта» удалено — все зарегистрированные проекты равны. Голый `ask` маршрутизируется сам (называет проект → в него; сравнительный → федеративно; follow-up → сохраняет маршрут; иначе — федеративный fallback), с опциональным LLM-разрешителем и непрерывностью в рамках диалога. Пер-проектные операции разрешают проект из cwd. |
| Инспекция федерации | ✅ | `tesserae/federation.py`, `cli.py` | `tesserae federation status` (пер-проектные счётчики узлов, слияния по идентичности, семантические связи) и `federation explain <node>` (почему узел мостит проекты). |
| Мультипроектный serve | ✅ | [`tesserae/serve.py`](../../tesserae/serve.py), `cli.py` | Голый `tesserae serve` обслуживает КАЖДЫЙ зарегистрированный проект под одним сервером (лендинг на `/`, каждый на `/<alias>/`, переключатель Projects в шапке, пути изолированы); `--project X` обслуживает один с живым ask-виджетом. |
| LLM-слой концептов в `compile` | ✅ | `cli.py`, [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py), [`tesserae/selective_extractor.py`](../../tesserae/selective_extractor.py) | `tesserae compile` строит слой концептов/утверждений **по умолчанию** (`--extractor llm`) через настроенного провайдера (codex/claude/api согласно `llm_provider`); `--extractor deterministic` — структурный, байт-стабильный opt-out; `selective-llm --llm-include … --llm-limit N` — с учётом стоимости. |
| `tesserae setup` (интерактивный) | ✅ | `cli.py`, [`tesserae/deps.py`](../../tesserae/deps.py) | Верхнеуровневый `tesserae setup` — интерактивный по умолчанию (LLM-провайдер/усилие + какие опциональные зависимости); флаги пропускают запросы. Установки работают в uv-tool-окружениях без pip (uv-pip fallback). |

## Взаимодействие, поиск и настройка — v0.10.0 (июнь 2026)

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| Импорт/экспорт Google **OKF v0.1** | ✅ | [`tesserae/okf.py`](../../tesserae/okf.py) | `tesserae export okf [--import DIR]`. Бандл Markdown + YAML-фронтматтер; собственные бандлы Tesserae проходят круговой путь без потерь через неймспейс `x_tesserae`, чужие — по мере возможности. |
| Быстрый поиск по транскриптам (memex) | ✅ | [`tesserae/memex_search.py`](../../tesserae/memex_search.py) | BM25-индекс `nicosuave/memex` по транскриптам Claude/Codex, подключённый к дашборду сессий `tesserae serve` через `GET /api/transcript-search`. Опционален + мягко деградирует при отсутствии. |
| Хэндлы дисциплины чтения | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | `compile_context` `preview=N` возвращает ограниченное превью + хэндл по ключу контента; `get_handle` подгружает остальное постранично. Держит огромные payload вне контекста агента. |
| Сигналы качества извлечения | ✅ | [`tesserae/session_graph_llm.py`](../../tesserae/session_graph_llm.py) | По-находочные `confidence` + `confidence_rationale` + `revisit_signals` (байт-стабильны; выводятся в `fresh_insights`). |
| Машинная настройка + зависимости | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py), `cli.py` | `tesserae setup` пишет глобальные LLM-дефолты + устанавливает опциональные зависимости (memex, raganything); `tesserae config deps` показывает/устанавливает; `tesserae init` предлагает memex. Проектный конфиг по-прежнему переопределяет. |

## Контекстный движок — v0.5.0 (июнь 2026)

Хребет движка, движущий три столпа. Карту модулей хребта движка, sidecar памяти самоулучшения и датафлоу компилятора контекста см. в [`docs/architecture.md`](architecture.ru.md).

### Хребет движка (столпы 1 и 2)

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| `Pipeline` — переиспользуемая цепочка refresh, возвращающая `List[StepResult]` | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | Один прогонщик шагов, который вызывают CLI, демон и MCP. Ловит `Exception` на шаг; останавливается на первом сбое. |
| `Daemon` — asyncio-супервизор с одним владельцем | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | Наблюдает источники + vault + каталог harness-сессий; дебаунс cancel-and-reschedule коалесцирует всплеск в один `Pipeline.run()`. Pidfile; переживает исключения в полёте. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` — псевдоним `engine`. |
| `project refresh` — прозаичная цепочка (ingest → compile → project) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only` (opt-in инкрементальность), `--no-sessions`. |
| Живой монитор сессий → находки | ✅ | `harness_sessions.py` + модули session-graph | Импортированные сессии питают граф; `fresh_insights` / `find_session_findings` выводят их. |

### Память самоулучшения (столп 2)

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| SQLite-sidecar `node_memory` (затухание / уверенность / вытеснение) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + независимые от хранилища аксессоры; только изменяемое состояние. First-seen живёт в отдельном sidecar `node_provenance`. |
| Оценка затухания по Эббингаузу | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | Ранжирует находки сессий: сначала новейшие + чаще всего запрашиваемые (движет `fresh_insights`). |
| Проход вытеснения (**включён по умолчанию**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | Детерминированный вердикт помечает более старый почти-дубликат инсайта вытесненным более новым; добавляет ребро `supersedes`. |
| Связывание инсайт → символ кода | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | Рёбра `discusses` от инсайтов сессий к символам, на которые они ссылаются. |
| Проходы подкрепления + противоречий | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Подкрепление доступом + обнаружение противоречий над тем же sidecar. |
| Числовая уверенность повторяемости в выводе | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Темпоральные факты штампуют `confidence` из `NodeMemoryRow.confidence`, откатываясь к `infer_confidence`. |

### Retrieval + эмбеддинги (столпы 2 и 3)

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| Гибридный ретривер (BM25 + лексический + эмбеддинги, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | Локальный прежде всего, полностью детерминированный. |
| Personalized PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | Мультихоповое расширение семян; подграф с ограничением глубины. |
| Настоящие дефолтные эмбеддинги (Track B, фаза 6) | ✅ | `retrieval/hybrid.py` | Дефолт = детерминированный hash-bucket-псевдоэмбеддинг (без зависимостей); `sentence-transformers` (`all-MiniLM-L6-v2`) предпочитается и загружается лениво, когда установлен. MCP-инструмент `embedding_status` сообщает активный бэкенд. |

### Компилятор контекста по требованию (столп 3 — главная фича)

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| `compile_context` — цитированный in-memory `ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Разрешение семян → PPR-расширение → выбор в пределах бюджета → цитированный markdown → опциональный LLM-синтез. Детерминирован, если не `synthesize=true`. Ничего не пишет на диск. |
| CLI `project context` | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = без ограничения), `--llm`, `--output`. |
| MCP-инструмент `compile_context` | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Тот же конвейер по MCP; `budget=0` — без ограничения. |
| Тематические срезы экспорта | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | Тематический `llms.txt` + `render_harness_context` через `compile_context`. |

### Инкрементальная компиляция (фаза 4 — экспериментально)

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| Sidecar provenance (`node_provenance`, first-seen) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | Фундамент для changed-only удалений; записывается всегда. |
| Поверхность удаления `GraphStore` | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (сбрасывает узлы, чей набор provenance опустел; межфайловые концепты выживают). |
| Диспетчеризация рантайм-хранилища `url_resolver` | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| Флаг `incremental_compile` | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **По умолчанию ВЫКЛ / экспериментально.** Байтовый паритет доказан для нескольких форм правок, но остаются пробелы multi-owner/producer-lifecycle; полная компиляция остаётся дефолтом. |

## Редизайн фронтенда — апрель 2026

Документо-ориентированная иерархическая wiki заменяет старый дамп графа. Помаршрутный тур см. в [`docs/frontend-redesign.md`](frontend-redesign.ru.md), трёхслойную модель — в [`docs/architecture.md`](architecture.ru.md).

### Слой wiki (L2 markdown)

| Функция | Статус | Исходник | Якорь документации |
|---|---|---|---|
| `WikiPageStore` (идемпотентные записи по body-hash, парсер фронтматтера) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Module map](architecture.ru.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — одна md-страница на узел wiki-слоя | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.ru.md#pipeline) |
| Страницы `sources/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.ru.md#sources) |
| Страницы `concepts/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.ru.md#concepts) |
| Страницы `entities/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.ru.md#entities) |
| Страницы `papers/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.ru.md#papers) |
| Страницы `repos/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.ru.md#repos) |
| Страницы `topics/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.ru.md#topics) |
| Страницы `questions/` (открытые вопросы) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.ru.md#questions) |
| Страницы `syntheses/` | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.ru.md#syntheses) |

### Виды синтезов (L2 → производные)

`SynthesisProjector` производит семь детерминированных шаблонов и добавляет узлы `Synthesis` + рёбра `synthesizes` / `summarizes` обратно в граф.

| Вид | Статус | Исходник | Заметки |
|---|---|---|---|
| `pulse` (один глобальный, движет `/`) | ✅ | `synthesis.py` | Пересобирается каждой компиляцией. |
| `daily_digest` | ✅ | `synthesis.py` | Один на `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Один на `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Один на кластер `ResearchTopic` / `ApproachFamily` ≥ 3 статей. |
| `comparison` | ✅ | `synthesis.py` | Один на пару `ApproachFamily`, конкурирующих на одной задаче. |
| `field_overview` | ✅ | `synthesis.py` | Один на `ResearchField`. |
| LLM-улучшенные сводки (по env-флагу) | ⚠ | только хук | Эвристическая база поставляется; хук `TESSERAE_SYNTHESIS_LLM=1` оставлен заглушкой. |

### Маршруты статического сайта

| Маршрут | Статус | Исходник | Заметки |
|---|---|---|---|
| `/` (home, hero pulse) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | Строка статистики + курируемые точки входа + недавняя активность. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Хитмап + список дней + рейл синтезов. |
| `/timeline/<YYYY-MM-DD>.html` (детали за день) | ⚠ | пока нет | Ячейки хитмапа временно ведут на страницу источника `digest.md` этого дня. Субагент P проводит по-дневные детальные страницы через `StaticSiteBuilder`. |
| `/graph/` (интерактивный 2D + 3D) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, hover-тултипы, подписи рёбер, зум с якорем на курсоре. |
| `/about.html` | ✅ | `pages.py::render_about` | Схема, информация о сборке. |

### AI-дружественные экспорты

| Артефакт | Статус | Исходник | Назначение |
|---|---|---|---|
| Per-page sibling `<page>.txt` | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | Простотекстовый вид одной страницы (без навигации, без стилей). |
| Per-page sibling `<page>.json` | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | Короткий индекс llmstxt.org. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Тело каждой страницы, ограничено 5 МБ. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`, только узлы wiki-слоя. |
| `graph.json` | ✅ | `__init__.py::write_site` | Полный payload графа (вкл. узлы кода для инструментов). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | Палитра + поиск страниц; только виды wiki-слоя. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Каждый эмитированный маршрут, `lastmod` из фронтматтера. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Последние 30 синтезов. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Разрешительный — crawl + index. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Машиночитаемая карта сайта. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + размер каждого эмитированного файла (харнес идемпотентности). |

### Визуальный дизайн + UX

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| Дизайн-токены (светлая + тёмная темы, терракотовый акцент) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Один CSS-бандл в `assets/style.css`. |
| Переключатель темы (персистентный, без вспышки) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` в `localStorage`, применяется до отрисовки. |
| Поисковая палитра (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Fuzzy-совпадение по `search-index.json`; список недавних страниц. |
| Липкий правый TOC | ✅ | `pages.py` + `tokens.py` | Только десктоп; на мобильном — drawer через `<details>`. |
| Хитмап активности с подписями месяцев и дней недели | ✅ | `components.py::heatmap_svg` | 26-недельный SVG, ячейки ведут на `digest.md` дня. |
| Sparkline (на концепт/сущность) | ✅ | `components.py::sparkline_svg` | Недельные счётчики упоминаний, последние 12 недель. |
| Мобильный каркас (drawer-рейл, нижняя навигация, флюидная типографика) | ✅ | `tokens.py` + `pages.py` | Тач-таргеты ≥ 44 px. |
| Переходы страниц (120 мс opacity, prefers-reduced-motion) | ✅ | `tokens.py` | |
| 3D + 2D вид графа (hover, подписи рёбер, зум с якорем на курсоре) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, вендорены как снапшот CDN. |
| Футер AI-siblings на каждой странице | ✅ | `components.py::ai_siblings_footer` | Inline-ссылки на `.txt` и `.json` текущей страницы. |
| Страницы истории harness-сессий | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Явный импорт Claude Code/Codex; индекс `/sessions/` и детальные страницы с markdown-ходами, левым рейлом ходов, свёрнутым tool-use и записями поиска. |

### Конвейер + CLI

| Функция | Статус | Исходник | Заметки |
|---|---|---|---|
| `project compile` вызывает синтез + wiki + сайт по порядку | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | Фаза 3 плана редизайна. |
| `project build-site` автономно | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | Читает `wiki/` + `graph.json`, пишет `site/`. |
| `project serve` локальный HTTP | ✅ | `cli.py` | Простой stdlib-сервер. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Worktree-пуш в `gh-pages`; опциональный `--enable-pages` через `gh` CLI. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Входящая история сессий для Claude Code/Codex; обнаружение явное и ограничено рабочим каталогом проекта. |
| `project watch` пересборка при изменении | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | Автономный опросный наблюдатель: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. Мультиисточниковый супервизор живёт под `project engine`/`daemon` (см. Контекстный движок). |
| `project context` — компиляция цитированного документа контекста | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Главная фича столпа 3; см. секцию Контекстный движок. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | Прозаичная цепочка refresh + цикл супервизора; см. секцию Контекстный движок. |

## Ранее существовавшие функции (перенесены без изменений)

### CLI и установка

- ✅ Устанавливаемый Python-пакет через `pyproject.toml`.
- ✅ Консольные команды: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` для установки `curl | bash`.
- ✅ Editable-установки по умолчанию для быстрой локальной разработки.

### Извлечение

- ✅ Детерминированный экстрактор исследовательских заметок с контролируемыми словарями узлов/рёбер.
- ✅ Экстрактор Claude CLI/OAuth для более качественного структурированного извлечения без API-ключей.
- ✅ Селективная маршрутизация Claude по glob и лимиту бюджета.
- ✅ Детерминированный экстрактор кода разработки для Python-проектов.
- ✅ Пакетный инжест с хешированием контента и поддержкой `--changed-only`.
- ✅ Чтение источников, устойчивое к некорректному UTF-8.

### Управление графом

- ✅ Контролируемый список `ResearchNodeType` — теперь включает `SYNTHESIS`.
- ✅ Контролируемый whitelist типов рёбер — теперь включает `synthesizes`, `summarizes`.
- ✅ Валидация, отклоняющая дрейф схемы.
- ✅ Канонизация псевдонимов.
- ✅ Очередь ревью для неоднозначных узлов — почти-дубликатов.
- ✅ Шаблон решений ревью и рабочий процесс merge/keep-separate.
- ✅ Сводка трендов корпуса из пофайловых графов.

### Персистентность и отчёты

- ✅ Экспорт графа в JSON.
- ✅ SQLite-хранилище графа.
- ✅ Опциональное Kuzu-хранилище графа.
- ✅ Отчёт по графу со счётчиками, покрытием свидетельствами, осиротевшими узлами, датовыми корзинами, узлами с обилием псевдонимов.
- ✅ Конкурентный отчёт, описывающий впитанные идеи из MegaMem, Graphiti/Zep, MCP-графовых серверов, агентного RAG.

### Проектно-локальный рабочий процесс

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy` (GitHub Pages)
- ✅ `tesserae sessions discover/import/list` (явный импорт локальной истории агентов)
- ✅ `tesserae export site --watch` (автономный опросный наблюдатель)
- ✅ `tesserae engine` (цикл супервизора — v0.5.0)
- ✅ `tesserae refresh` (прозаичная цепочка ingest → compile → project — v0.5.0)
- ✅ `tesserae context` (компилятор контекста по требованию — v0.5.0)
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ Экспорт vault, готового к открытию.
- ✅ `.obsidian/app.json` и настройки графа.
- ✅ Markdown-проекция.
- ✅ Структура `raw/assets/`.
- ✅ `_meta/dashboard.md` с Dataview-запросом.

### Агентские харнесы

Генерируемые файлы целей для:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering и настройки MCP
- ✅ Cursor: правила проекта и конфиг MCP
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / темпоральные факты

- ✅ Проекция темпоральных фактов с полями provenance, актуальности, уверенности и инвалидации.
- ✅ Экспорт JSONL эпизодов Graphiti без зависимостей.
- ✅ Smoke `sync-graphiti --dry-run` без установленного Graphiti.
- ✅ Опциональная живая синхронизация с `graphiti_core` и Neo4j.

### MCP-сервер

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` по stdio JSON-RPC.
- ✅ Инструменты retrieval/графа: `schema`, `graph_summary`, `search_nodes`, `node_context` (с `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`.
- ✅ Инструменты контекстного движка (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (ранжированные затуханием), `list_communities`, `find_session_findings`, `ask`.
- ✅ Инструменты настройки: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Мультипроектный реестр: `list_projects`, `register_project`, `unregister_project`, `list_sessions`. Диспетчеризация store-URL через `url_resolver`.

## Тесты

Текущий набор покрывает:

- ✅ ограждения онтологии (вкл. новый узел `Synthesis` + рёбра `synthesizes` / `summarizes`);
- ✅ детерминированное извлечение;
- ✅ парсинг/валидацию обёртки Claude CLI;
- ✅ селективную маршрутизацию Claude;
- ✅ рабочий процесс канонизации/ревью;
- ✅ пакетный инжест;
- ✅ отчёты;
- ✅ персистентность SQLite/Kuzu;
- ✅ экспорт Graphiti/dry-run синхронизации;
- ✅ рабочий процесс CLI проекта;
- ✅ экспорт агентских харнесов;
- ✅ экспорт Obsidian;
- ✅ генерацию фронтенда + целостность ссылок (нет `nodes/codeclass-*.html`);
- ✅ идемпотентность wiki-хранилища;
- ✅ golden + идемпотентность проектора синтезов;
- ✅ компоненты сайта, страницы, экспорты, релевантность;
- ✅ форму AI-siblings (`.txt` + `.json` на страницу);
- ✅ сквозную идемпотентность compile-дважды;
- ✅ хребет движка: pipeline, цепочка refresh, ядро демона + источники, CLI `project engine`;
- ✅ память самоулучшения: sidecar, decay/supersede, подавление supersede (вкл. MCP), reinforce/contradiction;
- ✅ retrieval + эмбеддинги: гибридный поиск, PPR, настоящие дефолтные эмбеддинги (фаза 6);
- ✅ компилятор контекста: форма/целостность цитирований/детерминизм/бюджет/PPR-fallback, CLI `project context`, MCP `compile_context`;
- ✅ инкрементальную компиляцию (экспериментально): differ, гейты паритета, готовность provenance, SQLite provenance;
- ✅ установку пакета и контракт инсталлера.
