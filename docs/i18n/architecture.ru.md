# Архитектура

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae — это **контекстный движок**. Он реконструирует самоулучшающуюся базу знаний из вашего проекта и передаёт её агентам как готовый к употреблению контекст. Он работает на трёх столпах: (1) **мониторинг сессий** — наблюдать за живыми сессиями агентов/работы и захватывать находки по мере их появления; (2) **автономный, проактивный инжест знаний** — конвейер + цикл супервизора, который непрерывно втягивает и переизвлекает знания, улучшая базу, а не ожидая указаний; (3) **документы/контекст по требованию** — запрошенные пользователем артефакты, скомпилированные из той же базы. Типизированный граф, markdown-vault и статический сайт — *проекции* базы знаний; движок — это цикл, который держит их свежими и питает агентов.

Под капотом Tesserae превращает каталог исходного материала в контролируемый типизированный граф знаний и проецирует этот граф через долговечный слой markdown-wiki в статический AI-дружественный веб-сайт. Редизайн апреля 2026 реорганизовал проекционную сторону вокруг трёхслойной модели Карпатого: сырые свидетельства остаются сырыми, типизированный граф управляет онтологией, а слой markdown-wiki располагается между графом и любым рендеримым выводом. Статический сайт — это *рендерер* этого wiki-слоя, а не прямой дамп графа, с контролируемой онтологией в [`tesserae/research_graph.py`](../../tesserae/research_graph.py) в роли схемы. Веха **v0.5.0** (июнь 2026) добавила хребет движка, движущий все три столпа — см. ниже *Хребет движка* и *Компилятор контекста по требованию*.

## Трёхслойная модель Карпатого

Фрейминг Андрея Карпатого для LLM-дружественных баз знаний различает три слоя, у каждого — своя гарантия долговечности:

| Слой | Забота | Расположение в репо | Владелец |
|---|---|---|---|
| L1 — Сырые источники | Буквальные байты, написанные или собранные пользователем. Только добавление. | `data/`, `docs/`, деревья проекта, указанные в `.tesserae/config.json` | пользователь |
| L2 — Wiki | Типизированные markdown-страницы (sources, concepts, entities, papers, repos, topics, syntheses, questions) с YAML-фронтматтером. Идемпотентно: регенерируется каждой компиляцией, но перезаписывается только при изменении хешей контента. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Рендер | Статический HTML-сайт, AI-sibling-экспорты, поисковый индекс, sitemap, JSON-LD. Стирается и перезаписывается каждой компиляцией, но байт-стабилен между перезапусками. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

Схема пронизывает все три слоя как отдельная ось: `ResearchGraph` в `graph.json` — это контролируемая онтология, на которую ссылаются страницы L2, а `ResearchNodeType` / whitelist рёбер в [`tesserae/research_graph.py`](../../tesserae/research_graph.py) — источник истины о том, какие типы вообще существуют.

Редизайн добавил L2 явно. До апреля 2026 статический сайт проецировался прямо из `graph.json`; wiki-слой существовал только внутри экспорта Obsidian vault. Его выделение дало нам:

- Единую редактируемую человеком поверхность (откройте `.tesserae/wiki/` в Obsidian или любом markdown-редакторе).
- Идемпотентные пересборки: повторный запуск `project compile` даёт ноль файловых диффов, если контент источников не менялся.
- Журнал эволюции: страницы синтезов накапливаются со временем и позволяют проекту рассказывать о самом себе.

## Конвейер

```
data/, docs/, src/                                    (L1 raw)
        │
        ▼  project compile  (tesserae/project.py)
┌───────────────────────────┐
│ ResearchGraphExtractor    │   deterministic + selective Claude
│ + canonicalization        │
└───────────┬───────────────┘
            │
            ▼
┌───────────────────────────┐
│ ResearchGraph (graph.json)│   schema: research_graph.py
└───────────┬───────────────┘
            │
            ├──▶ WikiLayerProjector   (one page per L1/L2 node)
            ├──▶ SynthesisProjector   (pulse, daily, weekly, topic, …)
            │
            ▼
┌───────────────────────────┐
│ .tesserae/wiki/  (L2 md)  │   sources/, concepts/, entities/,
│                            │   papers/, repos/, topics/,
│                            │   syntheses/, questions/
└───────────┬───────────────┘
            │
            ▼  StaticSiteBuilder.write_site
┌───────────────────────────┐
│ .tesserae/site/  (L3 html)│   index.html, <kind>/index.html,
│                            │   <kind>/<slug>.html,
│                            │   per-page .txt + .json siblings,
│                            │   llms.txt, llms-full.txt,
│                            │   graph.json, graph.jsonld,
│                            │   search-index.json,
│                            │   sitemap.xml, rss.xml,
│                            │   robots.txt, ai-readme.md,
│                            │   manifest.json
└───────────────────────────┘
```

Каждый шаг инкрементален. Экстрактор графа использует хеши контента из `manifest.json`, чтобы пропускать неизменённые исходные файлы. `WikiPageStore.write_page` возвращает `False` (и пропускает запись), когда хеш тела совпадает с уже лежащим на диске. `StaticSiteBuilder` стирает и перезаписывает `.tesserae/site/`, но его вывод детерминирован — см. «История идемпотентности» ниже.

## Датафлоу компилятора контекста

Компилятор контекста по требованию ([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)) — главный путь столпа 3. По запросу и/или явным id семенных узлов `compile_context` строит подогнанный, цитированный markdown-бандл прямо из графа и возвращает его в памяти — под `.tesserae/` он ничего не пишет.

```
query / seeds
     │
     ▼  1. Seed resolution
        explicit seeds (kept iff they exist) + hybrid_search() hits, deduped, stable order
     │
     ▼  2. PPR expansion
        retrieval.ppr.personalized_pagerank ranks the depth-bounded k-hop neighbourhood;
        empty result (disconnected seeds) → fall back to seed order (bundle is never empty)
     │
     ▼  3. Budget-bound selection
        walk PPR order, include each node's cited body until the next would overflow
        `budget` chars (budget <= 0 = uncapped; over-budget marker on a word boundary)
     │
     ▼  4. Cited markdown assembly
        one section per selected node + a trailing `## Citations` block.
        Body text prefers the projected wiki page (when a store + public wiki kind exist),
        else the node description, else a minimal stub. The no-LLM body embeds NO
        wall-clock timestamp → byte-identical for the same (graph, query, seeds, depth, budget).
     │
     ▼  5. Optional LLM synthesis  (only when synthesize=true AND ANTHROPIC_API_KEY is set)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

Дефолты: `depth=2`, `budget=32000`. Детерминированная сборка (шаги 1–4) — это контракт; LLM-синтез чисто аддитивен. Тот же конвейер стоит за CLI-командой `project context`, MCP-инструментом `compile_context` и тематическими срезами экспорта (`slice_export_context_for_topic`, тематический `llms.txt`).

## Карта модулей

### Wiki + синтез (L2)

| Модуль | Ответственность |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | Датакласс `WikiPage`, `WikiPageStore` для файлового I/O. Stdlib-only парсер YAML-подмножества фронтматтера. Идемпотентность по body-hash. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: отображает каждый узел `ResearchGraph` wiki-слойного типа в markdown-страницу в нужной папке `kind/`. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: детерминированные шаблоны для pulse, daily_digest, weekly, topic, comparison, field_overview. Добавляет узлы `Synthesis` и рёбра `synthesizes` / `summarizes` обратно в граф. |

### Граф + онтология

| Модуль | Ответственность |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | Enum `ResearchNodeType` (вкл. `SYNTHESIS`), whitelist типов рёбер (вкл. `synthesizes`, `summarizes`), валидация. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | Канонизация псевдонимов + очередь ревью почти-дубликатов. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | Детерминированный экстрактор Python AST для среза разработки. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Селективный экстрактор Claude CLI/OAuth. |

### Рендерер сайта (L3)

| Модуль | Ответственность |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: стирает + пересобирает сайт, обходит каждый маршрут, эмитирует экспорты + AI-siblings + манифест. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | Один рендерер на маршрут (home, индексы, детальные страницы, timeline, graph, about). `SiteContext` несёт предвычисленные индексы, чтобы рендереры оставались чистыми. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML-примитивы: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Дизайн-токены — CSS-переменные, светлая + тёмная темы, layout, типографика, все компоненты стилизуются здесь. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | Клиентский JS-бандл: поисковая палитра, переключатель темы, sigma + 3D-force вид графа. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Stdlib-only markdown-рендерер (ссылки, автоссылки, код, выделение, заголовки). Без внешних зависимостей. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | Четырёхсигнальная оценка релевантности (прямая связь, пересечение источников, Adamic-Adar, типовая близость), используемая каждой секцией `Related`. |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | Билдер `search-index.json`. Только виды wiki-слоя. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Рендереры индекса/деталей сессий для импортированной harness-истории: секции сводки памяти проекта, рейл ходов диалога, markdown-рендеринг транскриптов и свёрнутые блоки tool-use. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, per-page siblings `.txt`/`.json`. |

### Оркестрация конвейера

| Модуль | Ответственность |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: ведёт извлечение → граф → проходы памяти → wiki-слой → сайт. Владеет `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site` и т.д.). Заранее решает, допустима ли provenance-управляемая инкрементальная компиляция (за флагом `incremental_compile`, по умолчанию OFF). |
| [`tesserae/cli.py`](../../tesserae/cli.py) | Диспетчеризация CLI с плоскими глаголами (~2 732 строки после удаления устаревших групп подкоманд `project`/`wiki`). Глаголы — `init`, `compile`, `ingest`, `context`, `ask`, `query`, `doctor`, `summary`, `decisions`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `setup`, `config`, `projects`, `sources`, `federation`, `integrations` — объявлены как метаданные в [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) и подключаются из этого дерева, а не регистрируются вручную. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`: пушит `.tesserae/site/` в ветку `gh-pages` через worktree, опционально включает Pages через `gh`. |

### Хребет движка (v0.5.0 — столпы 1 и 2)

Хребет движка — это внутрипроцессный цикл, который движет мониторинг сессий и автономный ре-инжест. Один и тот же `Pipeline.run()` — единственный путь refresh, который вызывают CLI, демон-супервизор и (позже) MCP-сервер.

| Модуль | Ответственность |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: последовательный прогонщик шагов. Кодифицирует прозаичную цепочку refresh (ingest → compile → project/publish) как импортируемый объект, возвращающий структурированный `List[StepResult]` вместо print-and-exit, так что каждый вызывающий сам решает, как показывать исходы. `run()` ловит `Exception` на шаг (пропуская `KeyboardInterrupt`/`SystemExit`) и останавливается на первом сбое. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: asyncio-супервизор с одним владельцем. Наблюдает каталоги источников, Obsidian vault и каталог harness-сессий; коалесцирует всплеск `TriggerEvent`-ов ровно в один `Pipeline.run()` через дебаунс cancel-and-reschedule. Переиспользует существующие наблюдатели `watch.py` / `vault_watch.py` (не переписывает их), пишет pidfile и переживает исключения в полёте. Доступен как `engine` (`--interval`, `--debounce`, `--once`). |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | Опросные наблюдатели, переиспользуемые и автономной командой `export site --watch`, и линиями источников/vault демона. |

### Память самоулучшения (v0.5.0 — столп 2)

Фаза 5 активировала персистентное самоулучшение. Изменяемое пер-узловое состояние живёт в SQLite-sidecar `node_memory` (внутри `.tesserae/sqlite.db`), отдельно от неизменяемого штампа first-seen `node_provenance.first_seen_at` (sidecar фазы 4). Компиляция прогоняет над графом набор детерминированных проходов.

| Модуль | Ответственность |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + независимые от хранилища аксессоры (`read_memory`, `write_memory`, `bump_access`) над таблицей `node_memory` — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. Ни один вызывающий не встраивает сырой SQL. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: оценка свежести в стиле Эббингауза (сначала новейшие + чаще всего запрашиваемые), используемая для ранжирования находок сессий. |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass` (**включён по умолчанию**): детерминированный вердикт, помечающий более старый почти-дубликат инсайта вытесненным более новым, с добавлением ребра `supersedes`. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: связывает инсайты сессий с обсуждаемыми ими символами кода через рёбра `discusses`. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Хелперы подкрепления доступом и обнаружения противоречий над тем же sidecar. |

Уверенность повторяемости в выводе числовая: темпоральная проекция штампует `confidence` каждого факта из `NodeMemoryRow.confidence` (текст в SQLite, выводится через `temporal.py`), откатываясь к `infer_confidence` только когда сохранённого значения нет.

### Retrieval (v0.5.0 — столпы 2 и 3)

| Модуль | Ответственность |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: локальный прежде всего гибридный ретривер, сплавляющий три линии — Okapi BM25 (k1=1.5, b=0.75), case-folded лексический/FTS-подобный поиск подстрок и подключаемую эмбеддинговую линию — через reciprocal-rank fusion (RRF, k=60). Полностью детерминирован. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: Personalized PageRank в стиле HippoRAG-2 (arXiv:2502.14802) над графом для мультихопового расширения семян — выводит хорошо связанные узлы в нескольких хопах от семени, а не только 1-хоповую окрестность. |
| Бэкенд эмбеддингов (фаза 6, Track B) | Дефолтный бэкенд эмбеддинговой линии гибрида — детерминированный hash-bucket-псевдоэмбеддинг без дополнительных зависимостей; `sentence-transformers` (`all-MiniLM-L6-v2`) предпочитается и загружается лениво, когда установлена опциональная зависимость. MCP-инструмент `embedding_status` сообщает, какой бэкенд активен. |

### Компилятор контекста по требованию (v0.5.0 — главная фича столпа 3)

| Модуль | Ответственность |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: главная фича столпа 3. Компилирует подогнанный, **цитированный** контекстный бандл для запроса/набора семян прямо из графа — см. *Датафлоу компилятора контекста* выше. Возвращает in-memory `ContextBundle` (с `ContextCitation`); на диск ничего не пишет. Доступен как CLI-команда `project context` и MCP-инструмент `compile_context`. |

### Порты персистентности + хранилища графа

| Модуль | Ответственность |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | Протокол `GraphStore`: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical` и поверхность удаления фазы 4 — `delete_node` и `delete_nodes_by_source` (удаляет узлы, чей набор provenance опустел после удаления заданных путей источников, поэтому межфайловые концепты выживают). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: автономное хранилище; владеет sidecar-таблицами `node_provenance` и `node_memory`. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | Разрешает store-URL (`sqlite:///…`, `hypepaper-postgres://…`) в нужный `GraphStore`, позволяя MCP-серверу указывать на любое хранилище в рантайме. |

### Внешние адаптеры (в этом раунде без изменений)

| Модуль | Ответственность |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Проекция Obsidian vault (раскраска графа, Dataview-дашборд, сырые ассеты). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Экспорты харнесов Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Входящее обнаружение сессий Claude Code/Codex, нормализация, хранение под `.tesserae/harness_sessions/` и редактированные markdown-сводки. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | JSONL темпоральных фактов + опциональная живая синхронизация Graphiti. |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP stdio-сервер. Retrieval/граф: `schema`, `graph_summary`, `search_nodes`, `node_context` (с `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`. Контекстный движок (v0.5.0): `compile_context` (компилятор контекста по требованию), `embedding_status`, `fresh_insights` (находки сессий, ранжированные затуханием), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Плюс `ask`, инструменты мультипроектного реестра (`list_projects`, `register_project`, `unregister_project`, `list_sessions`) и `tesserae_setup_plan` / `tesserae_setup_apply`. |

## Раскладка рабочего пространства проекта

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; also owns the node_provenance
                              (first-seen, Phase 4) and node_memory (decay /
                              confidence / superseded, Phase 5) sidecar tables
  temporal_facts.jsonl        Graphiti-style temporal projection (numeric recurrence confidence)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  wiki/                       L2 markdown wiki — see below
  site/                       L3 static site — see below
```

### `.tesserae/wiki/` (L2)

```text
wiki/
  sources/<slug>.md           raw documents from data/ + docs/, with frontmatter
  concepts/<slug>.md          Concept / TechnicalTerm / Algorithm / etc.
  entities/<slug>.md          Model / Dataset / Benchmark / Metric / Org / Person
  papers/<slug>.md            Paper hub
  repos/<slug>.md             Repository / Project / CodeProject
  topics/<slug>.md            ResearchField / ResearchTopic / ApproachFamily / Trend
  syntheses/<slug>.md         pulse, daily_digest, weekly, topic, comparison, field_overview
  questions/<slug>.md         OpenQuestion
```

Каждый файл можно редактировать вручную; следующая компиляция уважает пользовательские правки, пока хеш тела отличается от того, что записал бы проектор. (Редактирование только тела выигрывает; редактирование фронтматтера проигрывает на следующей компиляции, потому что фронтматтер регенерируется.) Пользователи Obsidian могут открыть `.tesserae/wiki/` напрямую; существующий адаптер `obsidian_vault/` — отдельная проекция, а не замена.

### `.tesserae/site/` (L3)

```text
site/
  index.html                  home + project pulse
  about.html                  schema, build info
  assets/{style.css,app.js}   single CSS bundle + single JS bundle
  sources/index.html
  sources/<slug>.html
  sources/<slug>.txt          AI sibling — plain text
  sources/<slug>.json         AI sibling — structured record
  concepts/…  entities/…  papers/…  repos/…  topics/…  syntheses/…  questions/…
  sessions/index.html          imported harness-session index
  sessions/<project>/<id>.html session detail: summary, metadata, turn rail, markdown turns, collapsed tools
  timeline/index.html
  graph/index.html            interactive 2D + 3D force layout
  graph.json                  full graph payload (incl. code nodes, for tooling)
  graph.jsonld                schema.org Dataset, wiki-layer nodes only
  search-index.json           palette + page search; wiki-layer kinds only
  llms.txt                    llmstxt.org — short index
  llms-full.txt               llmstxt.org — every page body, capped 5MB
  sitemap.xml                 every emitted route
  rss.xml                     last 30 syntheses
  robots.txt                  permissive (crawl + index)
  ai-readme.md                machine-readable site map
  manifest.json               sha256 + size for every emitted file
```

## Что сознательно исключено

Редизайн провёл явную черту: узлы code-class и code-function остаются в `graph.json` (так что потребители MCP и Graphiti их по-прежнему видят), но никогда не получают HTML-страниц, никогда не попадают в `search-index.json` и никогда не появляются в навигации. Это контракт для пользователя — wiki есть документо-ориентированная база знаний, а не браузер функций.

Конкретно, `StaticSiteBuilder` пропускает любой узел, чей тип не входит в карту видов L2-wiki (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`):

- Исключены из L2 + L3: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, все варианты `Claim` (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Поверхности, где они всё же появляются: как буллеты, бейджи, счётчики соседей или отрывки свидетельств inline на связанных wiki-страницах, и в `graph.json` для инструментов ниже по течению.

Если нужен просмотр на уровне кода, направьте LSP / call-graph-инструмент на дерево исходников напрямую — это другая задача, нежели «wiki того, что знает этот проект».

## История идемпотентности

Редизайн стремится к **байт-идентичному выводу между двумя последовательными запусками `project compile` над неизменёнными входами**. Составляющие:

1. **Извлечение источников** использует хеши контента из `manifest.json`; неизменённые файлы пропускаются, поэтому граф остаётся стабильным.
2. **Записи wiki-слоя** идемпотентны на уровне тела. `WikiPageStore.write_page` читает существующий файл, срезает фронтматтер, вычисляет sha256 тела и коротко замыкается, если новое тело хешируется так же — даже если новый фронтматтер имеет другой timestamp `generated_at`. Это ключевой трюк, который держит git-диффы тесными при пересборке.
3. **Вывод синтезов** несёт `content_hash: sha256-…` в своём фронтматтере. Хеш тела вычисляется без `generated_at`, поэтому повторные компиляции над тем же графом дают тот же хеш, а узлы `Synthesis` несут тот же `content_hash` в метаданных графа.
4. **Рендеринг сайта** стирает `site/` в начале `write_site`, а затем пишет детерминированно: маршруты отсортированы, словари сериализуются с `sort_keys=True`, `manifest.json` обходится через `sorted(rglob("*"))`. Два запуска дают байт-идентичные файлы, включая манифест.

Это верифицируется `tests/test_site_pages.py` и сквозным smoke в `tests/test_project_e2e_redesign.py` (компилировать дважды, сравнить сайты, ожидать ноль файловых дельт).

## Заметки о масштабировании

- **Кап узлов вида графа.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) ограничивает встроенный в страницу payload интерактивного force-layout. За ~1500 узлами браузерная симуляция становится вялой на среднем железе, поэтому страница сбрасывает wiki-слойные узлы с наименьшей степенью первыми, когда счётчик превышает кап. Экспортируемый `graph.json` не затронут — он всегда содержит полный граф. Узлы кода отфильтровываются до применения капа.
- **Кап `llms-full.txt`.** Страховочный кап 5 МБ применяется в [`tesserae/site/exports.py`](../../tesserae/site/exports.py); файл заканчивается маркером `[TRUNCATED — see graph.jsonld for the full set]`, если кап достигнут. `graph.jsonld` без капа, потому что потребители JSON-LD ожидают полный набор.
- **Поисковый индекс.** Только виды wiki-слоя. Узлы графа кода никогда не попадают в `search-index.json`; целевой размер редизайна — < 500 КБ для dogfood-корпуса, и сегодня мы существенно ниже.
- **Байтовый бюджет страницы (эвристика).** Каждая детальная страница < 60 КБ gz HTML, общий CSS < 30 КБ, общий JS < 25 КБ, вендор sigma только на странице графа (~60 КБ). Вид графа использует 3D-force-graph + Three.js, загружаемые один раз; все прочие страницы остаются vanilla.
- **Время компиляции на dogfood.** ~300 markdown-файлов извлекаются менее чем за 5 с на свежей dev-машине; рендер сайта добавляет ещё ~2 с. Идемпотентность wiki-слоя означает, что последующие компиляции трогают только изменённые пути.

## Интерактивная поверхность фронтенда

- **Поисковая палитра** — `cmd+k` / `ctrl+k` / `/`. Fuzzy-поиск по `search-index.json`, ограничен видами wiki. Недавние страницы персистятся в `localStorage`.
- **Переключатель темы** — кнопка справа сверху; `data-theme="dark"` хранится в `localStorage` и применяется до отрисовки, чтобы избежать вспышки.
- **Липкий правый TOC** — только десктоп; на мобильном сворачивается в `<details>`-drawer. Генерируется из `<h2>` / `<h3>` тела страницы.
- **Хитмап активности** — 26-недельный SVG с подписями месяцев и дней недели. Ячейки ведут на страницу источника `digest.md` этого дня, когда она существует. (Детальные страницы таймлайна за день — `/timeline/<YYYY-MM-DD>.html` — явный follow-up; inline-уведомление в `render_timeline` это помечает. ⚠ в работе.)
- **Вид графа** — `/graph/`. 3D force-layout (3d-force-graph + Three.js) с hover-тултипами, подписями рёбер, зумом с якорем на курсоре и 2D-запасным видом. Цвета узлов берутся из `ResearchNodeType`.
- **Мобильный каркас** — drawer-рейл, нижняя навигация, флюидная типографика, тач-безопасные таргеты (≥ 44 px).

## Стратегия тестирования

- **Unit** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **Хребет движка** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **Память самоулучшения** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Retrieval + эмбеддинги** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **Компилятор контекста** — `tests/test_context_compiler.py` (форма, целостность цитирований, детерминизм, бюджет, PPR-fallback), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **Инкрементальная компиляция (экспериментально)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **Идемпотентность** — `tests/test_project_e2e_redesign.py` компилирует дважды и утверждает ноль диффов в `wiki/` и `site/`.
- **Целостность ссылок** — `tests/test_frontend.py` парсит каждый эмитированный HTML на href-ы и утверждает, что каждая внутренняя ссылка разрешается в сгенерированный файл. `nodes/codeclass-*.html` не производится.
- **AI-siblings** — для каждого `path/foo.html` набор тестов утверждает существование `path/foo.txt` и `path/foo.json`; JSON парсится и содержит `{title, kind, body, links}`.
- **Без Playwright** — vanilla pytest под `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

## Связанные документы

- [Quickstart](quickstart.ru.md) — минимальный путь от `project init` до просматриваемого сайта.
- [Разбор редизайна фронтенда](frontend-redesign.ru.md) — аннотированный тур по каждому маршруту.
- [Карта функций](feature-map.ru.md) — что поставлено, что в работе, с указателями на файлы.
- [Демо self-dogfood](self-dogfood.ru.md) — запуск Tesserae на его собственном репо.
