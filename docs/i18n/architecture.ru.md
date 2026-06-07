# Архитектура

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae — это **контекстный движок**. Он реконструирует самоулучшающуюся базу знаний из вашего проекта и передаёт её агентам как готовый к использованию контекст. Он работает на трёх столпах: (1) **мониторинг сессий** — наблюдение за живыми сессиями агентов/работы и захват находок по мере их появления; (2) **автономное, проактивное усвоение знаний** — конвейер + цикл супервизора непрерывно подтягивают и переизвлекают знания, улучшая базу, а не ожидая указаний; (3) **документы/контекст по запросу** — запрошенные пользователем артефакты, скомпилированные из той же базы. Типизированный граф, markdown-хранилище (vault) и статический сайт — это *проекции* базы знаний; движок — это цикл, который поддерживает их свежими и питает агентов.

Под капотом Tesserae превращает каталог исходных материалов в контролируемый типизированный граф знаний и проецирует этот граф через долговечный слой markdown-wiki в статический, удобный для ИИ сайт. Редизайн апреля 2026 года реорганизовал сторону проекций вокруг трехслойной модели Karpathy: сырые свидетельства остаются сырыми, типизированный граф управляет онтологией, а слой markdown-wiki находится между графом и любым отрендеренным выводом. Статический сайт является *рендерером* этого wiki-слоя, а не прямой выгрузкой графа; контролируемая онтология в [`tesserae/research_graph.py`](../../tesserae/research_graph.py) служит схемой. Веха **v0.5.0** (июнь 2026) добавила хребет движка, приводящий в действие все три столпа — см. *Хребет движка* и *Контекстный компилятор по запросу* ниже.

## Трехслойная модель Karpathy

Подход Andrej Karpathy к LLM-friendly базам знаний выделяет три слоя, каждый со своей гарантией долговечности:

| Слой | Зона ответственности | Расположение в репозитории | Владелец |
|---|---|---|---|
| L1 — Сырые источники | Буквальные байты, созданные или собранные пользователем. Только добавление. | `data/`, `docs/`, деревья проектов, указанные в `.tesserae/config.json` | пользователь |
| L2 — Wiki | Типизированные markdown-страницы (sources, concepts, entities, papers, repos, topics, syntheses, questions) с YAML frontmatter. Идемпотентный слой: пересоздается при каждой компиляции, но перезаписывается только при изменении хэшей содержимого. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Отрендеренный слой | Статический HTML-сайт, AI-sibling экспорты, поисковый индекс, sitemap, JSON-LD. Очищается и перезаписывается при каждой компиляции, но остается байтово стабильным при повторных запусках. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

Схема проходит через все три слоя как отдельная ось: `ResearchGraph` в `graph.json` — это контролируемая онтология, на которую ссылаются страницы L2, а `ResearchNodeType` / whitelist типов ребер в [`tesserae/research_graph.py`](../../tesserae/research_graph.py) является источником истины о том, какие типы вообще существуют.

Редизайн явно добавил L2. До апреля 2026 года статический сайт проецировался напрямую из `graph.json`; wiki-слой существовал только внутри экспорта Obsidian vault. Его выделение дало нам:

- Единую поверхность, редактируемую человеком (откройте `.tesserae/wiki/` в Obsidian или любом markdown-редакторе).
- Идемпотентные пересборки: повторный запуск `project compile` не дает файловых diff, если исходное содержимое не изменилось.
- Журнал эволюции: страницы синтеза со временем накапливаются и позволяют проекту рассказывать о самом себе.

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

Каждый шаг инкрементален. Экстрактор графа использует хэши содержимого из `manifest.json`, чтобы пропускать неизмененные исходные файлы. `WikiPageStore.write_page` возвращает `False` (и пропускает запись), когда хэш тела совпадает с тем, что уже есть на диске. `StaticSiteBuilder` очищает и заново записывает `.tesserae/site/`, но его вывод детерминирован — см. раздел «История идемпотентности» ниже.

## Поток данных контекстного компилятора

Контекстный компилятор по запросу ([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)) — это флагманский путь Столпа 3. По запросу и/или явным id узлов-семян `compile_context` строит подогнанный, **со ссылками** markdown-пакет прямо из графа и возвращает его в памяти — он ничего не пишет в `.tesserae/`.

```
query / seeds
     │
     ▼  1. Разрешение семян
        явные семена (сохраняются, только если есть в графе) + попадания hybrid_search(), дедуп, стабильный порядок
     │
     ▼  2. Расширение PPR
        retrieval.ppr.personalized_pagerank ранжирует k-прыжковую окрестность с ограничением глубины;
        пустой результат (несвязные семена) → откат к порядку семян (пакет никогда не пуст)
     │
     ▼  3. Выбор с ограничением бюджета
        обход в порядке PPR, включая тело со ссылкой каждого узла, пока следующее тело не
        превысит `budget` символов (budget <= 0 = без ограничения; маркер превышения по границе слова)
     │
     ▼  4. Сборка markdown со ссылками
        одна секция на выбранный узел + завершающий блок `## Citations`.
        Текст предпочитает спроецированную wiki-страницу (когда есть store и публичный wiki-вид),
        иначе описание узла, иначе минимальную заглушку. Тело без LLM не встраивает никакой
        отметки времени → байт-идентично для одного и того же (graph, query, seeds, depth, budget).
     │
     ▼  5. Опциональный LLM-синтез  (только когда synthesize=true И задан ANTHROPIC_API_KEY)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

Значения по умолчанию: `depth=2`, `budget=32000`. Детерминированная сборка (шаги 1–4) — это контракт; LLM-синтез чисто дополнителен. Тот же конвейер обслуживает CLI-команду `project context`, MCP-инструмент `compile_context` и срезы экспорта в рамках темы (`slice_export_context_for_topic`, `llms.txt` в рамках темы).

## Карта модулей

### Wiki + синтез (L2)

| Модуль | Ответственность |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | Dataclass `WikiPage`, `WikiPageStore` для файлового I/O. Парсер YAML-subset frontmatter только на stdlib. Идемпотентность по хэшу тела. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: сопоставляет каждый узел `ResearchGraph` wiki-слойного типа с markdown-страницей в соответствующей папке `kind/`. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: детерминированные шаблоны для pulse, daily_digest, weekly, topic, comparison, field_overview. Добавляет узлы `Synthesis` и ребра `synthesizes` / `summarizes` обратно в граф. |

### Граф + онтология

| Модуль | Ответственность |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | Enum `ResearchNodeType` (включая `SYNTHESIS`), whitelist типов ребер (включая `synthesizes`, `summarizes`), валидация. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | Каноникализация алиасов + очередь проверки почти дублирующихся сущностей. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | Детерминированный Python AST extractor для среза разработки. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Селективный extractor через Claude CLI/OAuth. |

### Рендерер сайта (L3)

| Модуль | Ответственность |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: очищает + пересобирает сайт, обходит все маршруты, генерирует экспорты + AI siblings + manifest. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | По одному рендереру на маршрут (home, indexes, detail pages, timeline, graph, about). `SiteContext` передает предварительно рассчитанные индексы, чтобы рендереры оставались чистыми. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML-примитивы: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Design tokens — CSS variables, light + dark themes, layout, typography; здесь стилизованы все компоненты. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | Клиентский JS bundle: search palette, theme toggle, sigma + 3D-force graph view. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Markdown-рендерер только на stdlib (links, autolinks, code, emphasis, headings). Без внешней зависимости. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | Оценка релевантности по четырем сигналам (direct link, source overlap, Adamic-Adar, type affinity), используемая каждым разделом `Related`. |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | Сборщик `search-index.json`. Только wiki-layer kinds. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Рендереры индекса/деталей сессий для импортированной harness history: секции project-memory summary, лента ходов разговора, рендеринг markdown transcript и свернутые блоки tool-use. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, per-page `.txt`/`.json` siblings. |

### Оркестрация конвейера

| Модуль | Ответственность |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: управляет extraction → graph → проходы памяти → wiki layer → site. Владеет `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site` и т. д.). Заранее решает, подходит ли инкрементальная компиляция на основе происхождения (provenance) (управляется `incremental_compile`, по умолчанию OFF). |
| [`tesserae/cli.py`](../../tesserae/cli.py) | Все подкоманды `tesserae project …`, включая `compile`, `refresh`, `context`, `build-site`, `serve`, `watch`, `engine`/`daemon`, `deploy`. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `project deploy`: отправляет `.tesserae/site/` в ветку `gh-pages` через worktree, опционально включает Pages через `gh`. |

### Хребет движка (v0.5.0 — столпы 1 & 2)

Хребет движка — это внутрипроцессный цикл, приводящий в действие мониторинг сессий и автономное переусвоение. Один и тот же `Pipeline.run()` — единственный путь обновления, который вызывают CLI, демон-супервизор и (позже) MCP-сервер.

| Модуль | Ответственность |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: последовательный исполнитель шагов. Кодифицирует прозаическую цепочку обновления (усвоение → компиляция → проекция/публикация) как импортируемый объект, возвращающий структурированный `List[StepResult]` вместо «напечатать-и-выйти», так что каждый вызывающий сам решает, как подать результат. `run()` ловит `Exception` на каждом шаге (пропуская `KeyboardInterrupt`/`SystemExit`) и останавливается на первом сбое. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: asyncio-супервизор с единственным владельцем. Следит за каталогами источников, хранилищем Obsidian и каталогом сессий harness; через дебаунс «отмена-и-перепланирование» сворачивает серию `TriggerEvent` ровно в один `Pipeline.run()`. Повторно использует существующие наблюдатели `watch.py` / `vault_watch.py` (не переписывает их), пишет pidfile и переживает исключения в полёте. Доступен как `project engine` / `project daemon` (`--interval`, `--debounce`, `--once`). |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | Опрашивающие наблюдатели, повторно используемые автономной командой `project watch` и линиями источников/хранилища демона. |

### Память самоулучшения (v0.5.0 — столп 2)

Фаза 5 активировала постоянное самоулучшение. Изменяемое состояние на узел живёт в sidecar `node_memory` SQLite (внутри `.tesserae/sqlite.db`), отдельно от неизменяемой отметки первого появления `node_provenance.first_seen_at` (sidecar Фазы 4). Компиляция запускает набор детерминированных проходов по графу.

| Модуль | Ответственность |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + независимые от хранилища аксессоры (`read_memory`, `write_memory`, `bump_access`) над таблицей `node_memory` — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. Ни одно место вызова не встраивает сырой SQL. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: оценка свежести в стиле Эббингауза (новейшее + чаще всего открываемое первым) для ранжирования находок сессий. |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass` (**по умолчанию ВКЛ**): детерминированный вердикт помечает более старый почти-дубликат инсайта как вытесненный более новым, добавляя ребро `supersedes`. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: связывает инсайты сессий с кодовыми символами, которые они обсуждают, рёбрами `discusses`. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Помощники подкрепления доступа и обнаружения противоречий над тем же sidecar. |

Уверенность повторяемости в выводе числовая: временная проекция проставляет `confidence` каждого факта из `NodeMemoryRow.confidence` (текст в SQLite, выводится через `temporal.py`), откатываясь к `infer_confidence` только когда сохранённого значения нет.

### Поиск (v0.5.0 — столпы 2 & 3)

| Модуль | Ответственность |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: локально-ориентированный гибридный ретривер, сливающий три линии — Okapi BM25 (k1=1.5, b=0.75), регистронезависимое лексическое/FTS-подобное совпадение подстрок и подключаемую линию эмбеддингов — через слияние обратных рангов (RRF, k=60). Полностью детерминирован. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: персонализированный PageRank в стиле HippoRAG-2 (arXiv:2502.14802) по графу для многопрыжкового расширения семян — выводит на поверхность хорошо связанные узлы в нескольких прыжках от семени, а не только 1-прыжковую окрестность. |
| Бэкенд эмбеддингов (Фаза 6, Track B) | Бэкенд по умолчанию для линии эмбеддингов гибрида — детерминированный псевдо-эмбеддинг на хэш-бакетах, не требующий доп. зависимостей; `sentence-transformers` (`all-MiniLM-L6-v2`) предпочтителен и загружается лениво, когда установлена опциональная зависимость. MCP-инструмент `embedding_status` сообщает активный бэкенд. |

### Контекстный компилятор по запросу (v0.5.0 — флагман Столпа 3)

| Модуль | Ответственность |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: флагманская функция Столпа 3. Компилирует подогнанный пакет контекста **со ссылками** для набора запросов/семян прямо из графа — см. *Поток данных контекстного компилятора* ниже. Возвращает `ContextBundle` в памяти (с `ContextCitation`); ничего не пишет на диск. Доступна как CLI-команда `project context` и MCP-инструмент `compile_context`. |

### Порты персистентности + хранилища графа

| Модуль | Ответственность |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | Протокол `GraphStore`: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical` и поверхность удаления Фазы 4 — `delete_node` и `delete_nodes_by_source` (удаляет узлы, чьё множество происхождения становится пустым после удаления заданных путей-источников, так что межфайловые концепции выживают). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: автономное хранилище; владеет sidecar-таблицами `node_provenance` и `node_memory`. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | Разрешает URL хранилища (`sqlite:///…`, `hypepaper-postgres://…`) в нужный `GraphStore`, позволяя MCP-серверу указывать на любое хранилище во время выполнения. |

### Внешние адаптеры (без изменений в этом раунде)

| Модуль | Ответственность |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Проекция Obsidian vault (раскраска графа, Dataview dashboard, raw assets). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Экспорты harness для Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Обнаружение входящих сессий Claude Code/Codex, нормализация, хранение в `.tesserae/harness_sessions/` и редактированные markdown-сводки. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | Temporal-fact JSONL + опциональная live Graphiti sync. |
| [`tesserae/cognee_adapter.py`](../../tesserae/cognee_adapter.py) | JSONL bundle узлов/ребер Cognee и прямой путь add/cognify. |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP stdio-сервер. Поиск/граф: `schema`, `graph_summary`, `search_nodes`, `node_context` (с `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`. Контекстный движок (v0.5.0): `compile_context` (контекстный компилятор по запросу), `embedding_status`, `fresh_insights` (находки сессий, ранжированные по затуханию), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Плюс `ask`, инструменты реестра нескольких проектов (`list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`) и `tesserae_setup_plan` / `tesserae_setup_apply`. |

## Структура рабочего пространства проекта

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; также владеет sidecar-таблицами node_provenance
                              (первое появление, Фаза 4) и node_memory (затухание / уверенность /
                              вытеснено, Фаза 5)
  temporal_facts.jsonl        Graphiti-style temporal projection (числовая уверенность повторяемости)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  cognee_bundle/              Cognee nodes/edges/manifest JSONL
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

Каждый файл можно редактировать вручную; следующая компиляция уважает пользовательские правки, пока хэш тела отличается от того, что записал бы projector. (Правка только body выигрывает; правка frontmatter проигрывает при следующей компиляции, потому что frontmatter генерируется заново.) Пользователи Obsidian могут открывать `.tesserae/wiki/` напрямую; существующий адаптер `obsidian_vault/` — отдельная проекция, а не замена.

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

## Что намеренно исключено

Редизайн провел явную границу: узлы code-class и code-function остаются в `graph.json` (поэтому потребители MCP, Cognee и Graphiti все еще их видят), но никогда не получают HTML-страниц, не появляются в `search-index.json` и не появляются в навигации. Это пользовательский контракт — wiki является knowledge base, ориентированной на документы, а не браузером функций.

Конкретно, `StaticSiteBuilder` пропускает любой узел, тип которого отсутствует в карте L2 wiki kinds (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`):

- Исключены из L2 + L3: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, все варианты `Claim` (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Где они все еще появляются: как bullets, badges, neighbor counts или evidence excerpts inline на связанных wiki-страницах, а также в `graph.json` для downstream tooling.

Если нужен просмотр на уровне кода, направьте LSP / call-graph tool прямо на дерево исходников — это другая задача, чем «wiki о том, что знает этот проект».

## История идемпотентности

Редизайн стремится к **байтово идентичному выводу при двух последовательных запусках `project compile` на неизмененных входных данных**. Составные части:

1. **Извлечение источников** использует хэши содержимого `manifest.json`; неизмененные файлы пропускаются, поэтому граф остается стабильным.
2. **Запись wiki-слоя** идемпотентна на уровне тела. `WikiPageStore.write_page` читает существующий файл, удаляет frontmatter, вычисляет sha256 тела и быстро завершает работу, если новое тело хэшируется так же — даже если новый frontmatter имеет другой timestamp `generated_at`. Это ключевой прием, который сохраняет git diff компактным при пересборке.
3. **Вывод синтеза** несет `content_hash: sha256-…` во frontmatter. Хэш тела вычисляется без `generated_at`, поэтому повторные компиляции на том же графе дают тот же хэш, а узлы `Synthesis` несут тот же `content_hash` в метаданных графа.
4. **Рендеринг сайта** очищает `site/` в начале `write_site`, затем пишет детерминированно: маршруты сортируются, словари выгружаются с `sort_keys=True`, `manifest.json` обходится через `sorted(rglob("*"))`. Два запуска создают байтово идентичные файлы, включая manifest.

Это проверяется в `tests/test_site_pages.py` и end-to-end smoke в `tests/test_project_e2e_redesign.py` (две компиляции, сравнение сайтов, ожидается нулевой file delta).

## Заметки о масштабировании

- **Лимит узлов graph view.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) ограничивает встроенный в страницу payload для интерактивной force layout. После примерно 1500 узлов браузерная симуляция становится медленной на среднем железе, поэтому при превышении лимита страница сначала отбрасывает wiki-layer узлы с наименьшей степенью. Экспортированный `graph.json` не затрагивается — он всегда содержит полный граф. Code nodes фильтруются до применения лимита.
- **Лимит `llms-full.txt`.** В [`tesserae/site/exports.py`](../../tesserae/site/exports.py) действует защитный лимит 5 MB; если лимит достигнут, файл заканчивается маркером `[TRUNCATED — see graph.jsonld for the full set]`. `graph.jsonld` не ограничен, потому что consumers JSON-LD ожидают полный набор.
- **Поисковый индекс.** Только wiki-layer kinds. Code-graph nodes никогда не попадают в `search-index.json`; цель редизайна для dogfood corpus — < 500 KB, и сегодня мы значительно ниже этого значения.
- **Бюджет байтов на страницу (эмпирическое правило).** Каждая detail page < 60 KB gz HTML, shared CSS < 30 KB, shared JS < 25 KB, sigma vendor только на graph page (~60 KB). Graph view использует 3D-force-graph + Three.js, загруженные один раз; все остальные страницы остаются vanilla.
- **Время компиляции на dogfood.** ~300 markdown-файлов извлекаются менее чем за 5 s на современной dev-машине; рендер сайта добавляет еще ~2 s. Идемпотентность wiki-слоя означает, что последующие компиляции затрагивают только измененные пути.

## Поверхность frontend-взаимодействий

- **Search palette** — `cmd+k` / `ctrl+k` / `/`. Fuzzy match по `search-index.json`, ограниченный wiki kinds. Последние страницы сохраняются в `localStorage`.
- **Theme toggle** — кнопка справа сверху; `data-theme="dark"` хранится в `localStorage` и применяется до paint, чтобы избежать flash.
- **Sticky right TOC** — только desktop; на mobile сворачивается в drawer `<details>`. Генерируется из `<h2>` / `<h3>` в body страницы.
- **Activity heatmap** — 26-недельный SVG с month + weekday labels. Ячейки ссылаются на source page `digest.md` соответствующего дня, если она существует. (Per-day timeline detail pages — `/timeline/<YYYY-MM-DD>.html` — явный follow-up; inline notice в `render_timeline` отмечает это. ⚠ in-progress.)
- **Graph view** — `/graph/`. 3D force layout (3d-force-graph + Three.js) с hover tooltips, edge labels, cursor-anchored zoom и 2D fallback view. Цвета узлов берутся из `ResearchNodeType`.
- **Mobile shell** — drawer rail, bottom nav, fluid type, touch-safe hit targets (≥ 44 px).

## Стратегия тестирования

- **Unit** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **Хребет движка** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **Память самоулучшения** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Поиск + эмбеддинги** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **Контекстный компилятор** — `tests/test_context_compiler.py` (форма, целостность ссылок, детерминизм, бюджет, откат PPR), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **Инкрементальная компиляция (экспериментально)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **Идемпотентность** — `tests/test_project_e2e_redesign.py` компилирует дважды и проверяет нулевые diff в `wiki/` и `site/`.
- **Целостность ссылок** — `tests/test_frontend.py` парсит все выпущенные HTML для hrefs и проверяет, что каждая внутренняя ссылка разрешается в сгенерированный файл. `nodes/codeclass-*.html` не создается.
- **AI siblings** — для каждого `path/foo.html` test suite проверяет наличие `path/foo.txt` и `path/foo.json`; JSON парсится и содержит `{title, kind, body, links}`.
- **Без Playwright** — vanilla pytest при `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

## Связанные документы

- [Быстрый старт](quickstart.ru.md) — минимальный путь от `project init` до browsable site.
- [Обзор frontend-редизайна](frontend-redesign.ru.md) — аннотированный tour каждого маршрута.
- [Карта функций](feature-map.ru.md) — что shipped, что in-progress, с указателями на файлы.
- [Self-dogfood demo](self-dogfood.ru.md) — запуск Tesserae против собственного репозитория.
