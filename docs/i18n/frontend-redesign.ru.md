# Редизайн фронтенда — аннотированный обзор маршрутов

<!-- translations:start -->
<p align="center"><a href="../frontend-redesign.md">English</a> · <a href="frontend-redesign.ko.md">한국어</a> · <a href="frontend-redesign.zh.md">中文</a> · <a href="frontend-redesign.ja.md">日本語</a> · <a href="frontend-redesign.ru.md">Русский</a> · <a href="frontend-redesign.es.md">Español</a> · <a href="frontend-redesign.fr.md">Français</a> · <a href="frontend-redesign.de.md">Deutsch</a></p>
<!-- translations:end -->
Этот документ — пошаговый обзор всех видимых маршрутов на переработанном статическом сайте Tesserae. Он дополняет высокоуровневую модель в [`architecture.md`](architecture.ru.md) и таблицу статусов в [`feature-map.md`](feature-map.ru.md).

После `tesserae compile` сайт находится в `.tesserae/site/`. Чтобы открыть его локально:

```bash
tesserae serve --port 8765
# open http://127.0.0.1:8765/
```

Каждый маршрут — это статический HTML-файл с двумя соседними файлами (`<page>.txt`, `<page>.json`) для потребителей на базе ИИ. Экспорты уровня всего сайта (`llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, `manifest.json`) описаны в конце этого документа.

Легенда статусов: ✅ поставлено · ⚠ в работе.

## Общие соглашения для всех страниц

Каждая конечная страница имеет одинаковую структуру (§3.3 дизайн-спецификации):

```
breadcrumbs
eyebrow (type · last updated · ≈ reading time)
TITLE
right-rail TOC (sticky on desktop, drawer on mobile)
lead paragraph
rendered markdown body
Mentions in the corpus  — bullets with badges + counts
Related (4-signal ranked) — card grid
Source provenance       — file path, line excerpt
Activity                — sparkline, weekly mentions
AI siblings footer      — links to the .txt and .json
```

Общая оболочка сайта:

- **Верхняя панель.** Логотип + имя проекта слева, вызов поиска + переключатель темы справа.
- **Левая панель** (desktop ≥ 1024 px): иерархическое дерево — Home, Recent activity, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Open questions, Sessions, Timeline, Graph view, About. Счётчики берутся из `manifest.json`.
- **Нижняя навигация** (mobile): выдвижная панель сворачивается; нижняя навигация показывает самые используемые типы.
- **Поисковая палитра.** `cmd+k` / `ctrl+k` / `/` — нечёткий поиск по `search-index.json`, ограниченный типами wiki. Недавние страницы сохраняются в `localStorage`.
- **Тема.** По умолчанию светлая; переключатель сохраняет `data-theme="dark"` в `localStorage`. Применяется до отрисовки, чтобы избежать вспышки.

## Home

### `/` ✅

> _Screenshot: home.png_

Главная страница — это пульс проекта. Она управляется глобальным синтезом `pulse` (`wiki/syntheses/pulse.md`), который пересоздаётся при каждой компиляции. Hero-блок представляет собой строку статистики — sources, concepts, papers, open questions — за которой идут 1-3 карточки "what's new this week", взятые из самого свежего тела `pulse`. Ниже hero-блока курируемые точки входа ведут на индексную страницу каждого типа, чтобы новый посетитель мог углубиться без необходимости читать навигацию.

Это первая страница, на которую стоит отправлять LLM-агента; она поставляет самое высокое соотношение сигнала к шуму по корпусу. Карточки ведут на конечные страницы, а не обратно к индексам.

**Заметные взаимодействия.** Клики по строке статистики прокручивают к соответствующему индексу типа или переходят на него. Текст hero-блока редактируемый — если вручную написать `wiki/overview.md`, главная страница подхватит его при следующей компиляции.

**Связанные маршруты.** [Timeline](#timeline) для журнала активности, [Syntheses](#syntheses) для развёрнутой формы, [Graph](#graph-view) для пространственного вида.

## Sources

Это исходные документы L1 — файлы в `data/`, `docs/` и дереве проекта, на которые ссылается `.tesserae/config.json`. Каждый источник становится узлом `SourceDocument` (или `Paper` / `Repository`) и получает wiki-страницу, спроецированную `WikiLayerProjector`.

### `/sources/` ✅

> _Screenshot: sources-index.png_

Таблица всех исходных документов, известных корпусу. Колонки: бейдж типа (Document / Paper / Repository / Project), заголовок, число упоминаний, последнее обновление. Таблица с зебра-полосами; при наведении показывается однострочный предпросмотр, а бейдж типа можно фильтровать через поисковую палитру (`/ kind:papers`).

Это индекс для агента, когда ему нужно перечислить буквальные свидетельства, на которых построена wiki.

**Связанные маршруты.** [Papers](#papers) для среза только по статьям, [Repos](#repos) для среза только по репозиториям, [Concepts](#concepts) для вида извлечённых сущностей.

### `/sources/<slug>.html` ✅

> _Screenshot: source-detail.png_

Один исходный документ, отрендеренный через stdlib markdown pipeline (`tesserae/site/markdown.py`). Тело — исходный markdown с безопасной отрисовкой ссылок и изображений. Ниже тела:

- **Mentions** — все concept / entity / paper, извлечённые из этого источника, с бейджами типов рёбер.
- **Backlinks** — все остальные wiki-страницы, которые ссылаются сюда.
- **Related** — ранжирование по четырём сигналам (direct link 3.0 + source overlap 4.0 + Adamic-Adar 1.5 + type affinity 1.0).
- **Source provenance** — исходный путь файла + первые 12 строк сырого файла как свидетельство.
- **Activity** — sparkline еженедельных упоминаний за последние 12 недель.
- **AI siblings footer** — plain-text вид `<page>.txt`, структурированная запись `<page>.json`.

**Заметные взаимодействия.** Автоматически связанные URL и arXiv ID в теле; click-to-copy для блоков кода; правый TOC отслеживает прокрутку.

## Concepts

Concepts — это повторяющиеся идеи, термины, алгоритмы, архитектуры и методологии, извлечённые по всему корпусу. Они охватывают `Concept`, `TechnicalTerm`, `Algorithm`, `MathematicalConcept`, `MethodologicalConcept`, `ArchitecturePattern`, `TrainingParadigm`, `InferenceStrategy`, `EvaluationProtocol`, `Task`, `Capability`, `ObjectiveFunction`.

### `/concepts/` ✅

> _Screenshot: concepts-index.png_

Карточная сетка с фасетной фильтрацией. Каждая карточка содержит бейдж типа, заголовок, однострочное определение, число упоминаний и дату последнего обновления. Страница поддерживает фильтры по типу через tag chips (Algorithm, Architecture, Training paradigm, …) и по умолчанию сортируется по числу упоминаний.

Сюда идут, чтобы спросить: "о чём говорит этот корпус?"

**Связанные маршруты.** [Papers](#papers) — концепты обычно вводятся в статьях, [Topics](#topics) — концепты группируются в темы.

### `/concepts/<slug>.html` ✅

> _Screenshot: concept-detail.png_

Богатая страница концепта с синтезированным определением (или первым абзацем самого авторитетного источника, если синтеза нет), списком всех упоминаний в корпусе, ранжированными связанными соседями и sparkline активности.

Раздел "Mentions in the corpus" является ключевым для агентов — он перечисляет каждую статью / источник / репозиторий, где упоминается концепт, с однострочным фрагментом для контекста.

**Заметные взаимодействия.** Правый TOC отслеживает `<h2>` / `<h3>` в теле; сетка связанных карточек уважает четырёхсигнальную оценку, поэтому соседи ощущаются релевантными, а не просто смежными.

## Entities

Entities — это именованные, идентифицируемые вещи в корпусе: `Model`, `Dataset`, `Benchmark`, `Metric`, `Organization`, `Person`. Они проецируются из графовых узлов, а их страницы делают акцент на утверждениях и результатах, а не на прозе.

### `/entities/` ✅

> _Screenshot: entities-index.png_

Таблица с фасетами по типу. Колонки: бейдж типа, имя, summary (первое предложение frontmatter `description`, если есть, иначе первый абзац тела), число упоминаний. Фильтруется по type chip.

### `/entities/<slug>.html` ✅

> _Screenshot: entity-detail.png_

Страница деталей выводит на первый план три раздела:

- **Claims** — рёбра `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`, которые касаются этой сущности, со встроенными фрагментами свидетельств. (Сами узлы Claim не получают собственных URL — они всплывают здесь как пункты.)
- **Reported results** — все связанные с этой сущностью `Result` / `evaluated_on` / `reports_result`, перечисленные с метрикой + score + происхождением из статьи.
- **Mentions in the corpus** — та же форма, что на страницах концептов.

Это правильная страница, когда агенту нужно ответить "что мы знаем о model X?" или "для каких datasets сообщается benchmark Y?"

## Papers

Papers — исследовательская литература, рассматриваемая как первоклассное свидетельство. Редизайн вывел их из общего пула источников и дал им отдельный тип, чтобы можно было отрисовывать специфичные для статей возможности.

### `/papers/` ✅

> _Screenshot: papers-index.png_

Карточная сетка с фасетной фильтрацией по year, topic и family chips. Каждая карточка: заголовок, авторы (первые три + "et al."), однострочный фрагмент abstract, бейдж года, число упоминаний. По умолчанию сортировка по году по убыванию.

### `/papers/<slug>.html` ✅

> _Screenshot: paper-detail.png_

Макет карточки статьи: заголовок, авторы, год, фрагмент abstract, затем разделы:

- **Contributions** — рёбра `ContributionClaim` из статьи.
- **Results** — рёбра `reports_result` с метрикой + score.
- **Comparisons** — рёбра `compares_against`.
- **Related concepts** — рёбра `introduces` / `extends` / `uses`.
- **Open questions** — `OpenQuestion`, связанные через статью.

Ссылки arXiv автоматически линкуются через `tesserae/site/markdown.py`; правый TOC отражает список разделов выше.

## Repos

Repos — программные проекты: `Repository`, `Project`, `CodeProject`. Редизайн явно не отрисовывает HTML-страницы для каждого класса / функции; страницы репозиториев суммируют поверхность проекта и ссылаются на дерево исходников.

### `/repos/` ✅

> _Screenshot: repos-index.png_

Карточная сетка с бейджами языков. Каждая карточка: имя, однострочный фрагмент README, основной язык(и), число звёзд, если известно, последнее обновление.

### `/repos/<slug>.html` ✅

> _Screenshot: repo-detail.png_

Страница репозитория показывает:

- **README excerpt** — первые ~600 символов `README.md` репозитория, если он есть в корпусе.
- **Dependencies** — исходящие рёбра типа `depends_on` / `imports` / `uses` к другим repos / models / concepts.
- **Implements** — рёбра `implemented_in` из статей (то есть "этот repo реализует paper X").
- **Module overview** — счётчики модулей / классов / функций, но без ссылок на отдельные функции — так задумано.
- **Related** — ранжирование по четырём сигналам.

Это правильная страница для агента, которому нужно выбрать репозиторий из семейства подходов.

## Topics

Topics группируют концепты в более широкие области: `ResearchField`, `ResearchTopic`, `ProblemArea`, `ApproachFamily`, `Trend`. Страницы тем частично проецируются из графовых узлов, частично синтезируются — см. [Syntheses](#syntheses) про страницы обзора областей, которые формируют вводную часть темы.

### `/topics/` ✅

> _Screenshot: topics-index.png_

Карточная сетка, ключом которой служит type chip. Каждая карточка показывает имя темы, родительскую область и статистику "X papers · Y concepts · Z repos".

### `/topics/<slug>.html` ✅

> _Screenshot: topic-detail.png_

Страница темы начинается с синтезирующего абзаца (если он существует в `wiki/syntheses/topic-<slug>.md`) и перечисляет:

- **Papers in this topic** — таблица.
- **Approach families** — подтемы типа `ApproachFamily`.
- **Concepts in scope** — облако chips.
- **Open questions** — связанные узлы `OpenQuestion`.
- **Related fields** — соседи `belongs_to` / `rising_in`.

**Связанные маршруты.** [Syntheses → topic-…](#syntheses) для развёрнутого нарратива, [Concepts](#concepts) для составляющих атомов.

## Syntheses

Syntheses — страницы более высокого порядка, создаваемые `SynthesisProjector`. Семь типов (pulse, daily_digest, weekly, topic, comparison, field_overview) покрывают временные и структурные срезы корпуса. Сегодня тела синтезов — детерминированные шаблоны; `TESSERAE_SYNTHESIS_LLM=1` — hook для LLM-апгрейда (stub).

### `/syntheses/` ✅

> _Screenshot: syntheses-index.png_

Индекс перечисляет каждый синтез, сгруппированный по типу, с сортировкой по `generated_at` по убыванию внутри каждой группы. Каждая строка: kind badge, заголовок, однострочный lead, timestamp generated-at.

### `/syntheses/<slug>.html` ✅

> _Screenshot: synthesis-detail.png_

Страница синтеза отрисовывает markdown-тело как есть. Frontmatter содержит `synthesis_kind`, `slug`, `sources`, `inputs`, `generated_at`, `generator`, `content_hash` — страница показывает `synthesis_kind` и `generated_at` в eyebrow. Ниже тела:

- **Sources consumed** — цели рёбер `summarizes` — по одному на каждый источник, использованный синтезом.
- **Inputs (graph nodes)** — цели рёбер `synthesizes` — каждый узел, который был входом.
- **Related syntheses** — тот же тип / пересекающиеся входы.

Синтез `pulse` является главной страницей; daily / weekly синтезы закрепляют панель [Timeline](#timeline).

## Questions

Open questions извлекаются из корпуса как узлы `OpenQuestion` — места, где статья, источник или синтез явно отмечает нерешённую проблему.

### `/questions/` ✅

> _Screenshot: questions-index.png_

Списковое представление, одна строка на открытый вопрос. Колонки: текст вопроса, источники, которые его подняли, связанные концепты, возраст (с момента первого появления). По умолчанию сортируется по новизне.

### `/questions/<slug>.html` ✅

> _Screenshot: question-detail.png_

Сфокусированная страница одного открытого вопроса с:

- Дословным текстом вопроса.
- **Raised in** — sources / papers / syntheses, где появляется вопрос.
- **Related concepts** — о чём этот вопрос.
- **Adjacent questions** — тот же источник или общие концепты.

Это страница, на которую стоит попадать, когда агента спрашивают: "что в этой области всё ещё не отвечено?"

## Sessions

Sessions — импортированные локальные transcripts AI-harness, нормализованные в `.tesserae/harness_sessions/`, затем отрендеренные как поисковая память проекта. Импорт выполняется явно через `tesserae sessions discover --import` или `tesserae sessions import ...`; обычные сборки сайта потребляют только уже нормализованные записи.

### `/sessions/` ✅

> _Screenshot: sessions-index.png_

Индекс sessions группирует top-level Claude Code и Codex sessions для проекта. Карточки/таблицы показывают title, harness, model, project path, start/end timestamps, message count, tool count, token counts when known, files touched, commands и preview text. Страница связана из глобальной панели, Browse cards на главной и записей поисковой палитры типа `session`.

### `/sessions/<project>/<session>.html` ✅

> _Screenshot: session-detail.png_

Страница деталей session использует общую оболочку, а не сырой дамп transcript. Макет включает hero, stat strip, High-Level Summary, Timeline & size, decisions/files/commands/tools/errors, свёрнутое дерево subagent и блок разговора по ходам.

Левая панель, специфичная для session, заменяет общую file-tree rail якорями ходов user/assistant (`#turn-N`). Ходы пользователя и ассистента отрисовываются через markdown-рендерер сайта; семантические поверхности вроде inline code, command/tag markup, paths, filenames и hashtags превращаются в компактные chips. Tool calls сворачиваются под предшествующим ходом ассистента, с тёмными фонами code/tool как в светлой, так и в тёмной теме.

Текущая типографика деталей держит обычную прозу разговора компактной на 8 px, обычные code fences разговора на 10 px, содержимое fenced bash/shell code на 11 px, tool details/summary на 10 px, tool headers на 8 px и tool payload text на 6 px. См. [`session-history.md`](session-history.ru.md) для карты селекторов и privacy checklist публикации.

## Timeline

Страница timeline — это журнал активности: когда рос корпус, какие типы узлов добавлялись, как это выглядит по дням и неделям?

### `/timeline/` ✅

> _Screenshot: timeline.png_

У страницы три панели:

- **Activity heatmap** — 26-недельный SVG с метками месяцев + дней недели, ячейки окрашены по node-add-count. Каждая ячейка ссылается на страницу источника `digest.md` этого дня, если она существует.
- **Days** — последние 60 датированных дней, каждая строка показывает `<date> · X activity · Y papers`. Если у даты есть `digest.md`, дата является ссылкой.
- **Syntheses rail** — все синтезы, отсортированные по новизне, kind badge + title + timestamp.

Эту страницу стоит добавить в закладки для вопроса "что происходило в последнее время".

### `/timeline/<YYYY-MM-DD>.html` ✅

> _Screenshot: timeline-day.png_

Детальные страницы по дням — список каждой paper / repo / concept / synthesis, привязанной к этому календарному дню — теперь выпущены. `render_timeline_day` (`tesserae/site/pages.py`) emit'ится по каждому датированному дню через `StaticSiteBuilder` (`tesserae/site/__init__.py`), и каждая страница записывается в `manifest.json` как маршрут `timeline_day`. Ячейки heatmap ссылаются напрямую на страницу дня (откатываясь на страницу источника `digest.md` дня только когда маршрута по дню нет).

## Graph view

### `/graph/` ✅

> _Screenshot: graph.png_

Интерактивный вид графа — это 3D force layout (3d-force-graph + Three.js, vendored как CDN snapshot в `assets/`) с 2D fallback. Узлы окрашены по `ResearchNodeType`. Рёбра показывают свой тип как метку при наведении.

**Заметные взаимодействия.**

- Навести на узел → tooltip с именем, типом, числом упоминаний.
- Кликнуть узел → перейти на его wiki-страницу.
- Навести на ребро → метка с отношением (`uses` / `extends` / `compares_against` / …).
- Колесо мыши → зум с привязкой к курсору (приближает к курсору, а не к центру).
- Перетаскивание → orbit (3D) или pan (2D).
- Переключение 2D/3D в правом верхнем углу.

Встроенная в страницу полезная нагрузка ограничена `MAX_GRAPH_NODES = 1500` (см. [`pages.py`](../../tesserae/site/pages.py)). Полный граф всегда доступен по `/graph.json` для инструментов. Узлы code-graph (`CodeClass`, `CodeFunction`, `Dependency`, …) по замыслу фильтруются из визуализации.

**Связанные маршруты.** Каждая wiki-страница ссылается на сфокусированный вид подграфа.

## About

### `/about.html` ✅

> _Screenshot: about.png_

Статическая страница, объясняющая схему (каждый `ResearchNodeType` и whitelist рёбер с однострочными описаниями), информацию о сборке (commit SHA, generator version, timestamp generated-at) и инвентарь AI exports.

Это правильная страница, чтобы дать новому участнику опору: какие типы существуют и для чего каждый нужен.

## AI siblings — как каждая страница также является данными

Tesserae поставляет каждую страницу в трёх формах: человеческий HTML, соседний plain-text файл и соседний структурированный JSON. Плюс экспорты уровня всего сайта для crawlers и agents.

### Per-page `<page>.txt` ✅

Plain text view одной страницы — без nav, без styling, без markdown decoration сверх того, что явно использует body. Подходит, когда агент хочет загрузить одну страницу как контекст без написания HTML scraper.

```bash
curl http://127.0.0.1:8765/concepts/diffusion-model.txt
```

### Per-page `<page>.json` ✅

Структурированная запись:

```json
{
  "title": "...",
  "kind": "concepts",
  "body": "raw markdown body",
  "body_text": "plain-text body",
  "links": ["..."],
  "source_path": "data/...",
  "frontmatter": { ... }
}
```

Подходит, когда инструменту нужен типизированный доступ — список ссылок, frontmatter, kind tag — без markdown parser.

### `/llms.txt` ✅

Короткий индекс llmstxt.org. Одна страница, перечисляющая каждый тип с наиболее релевантными entries для этого типа. Подходит для первого запроса LLM-агента при обнаружении сайта.

### `/llms-full.txt` ✅

Длинная форма llmstxt.org: все wiki-страницы, сконкатенированные вместе. Ограничено 5 MB; если лимит достигнут, файл заканчивается маркером `[TRUNCATED — see graph.jsonld for the full set]`. Подходит, когда у агента есть бюджет загрузить весь корпус одним запросом и контекст 5 MB.

### `/graph.json` ✅

Полная полезная нагрузка `ResearchGraph` — включая узлы code-graph, у которых нет HTML-страниц. Подходит, когда инструменту нужен полный граф для собственного анализа (потребители MCP, Cognee, Graphiti читают это).

### `/graph.jsonld` ✅

JSON-LD `Dataset` schema.org. Только узлы wiki-layer (без code nodes). Подходит для crawlers, понимающих structured data — Google Knowledge Graph, search indexers, schema.org-aware aggregators.

### `/search-index.json` ✅

Индекс палитры + поиска по страницам. Только wiki-layer kinds. Подходит для интеграции стороннего search UI; схема — список entries `{kind, title, slug, body, score_hints}`.

### `/sitemap.xml` ✅

Каждый emitted route с `lastmod`, полученным из frontmatter (`generated_at`, `updated_at`, `published_at`, `date`). Подходит для поисковых систем.

### `/rss.xml` ✅

Последние 30 синтезов, отсортированные от новых к старым. Подходит для "subscribe to this wiki" — RSS readers, IFTTT, mailing lists.

### `/robots.txt` ✅

Разрешающий — crawl + index everything. Wiki предназначена для чтения агентами.

### `/ai-readme.md` ✅

Машиночитаемая карта сайта для AI agents, которые плохо понимают HTML. Перечисляет каждый artifact выше с его purpose и однострочным описанием, когда каждый format уместен.

### `/manifest.json` ✅

Запись sha256 + size для каждого emitted file на сайте. Используется:

- Тестом compile-twice idempotence (`tests/test_project_e2e_redesign.py`).
- Downstream tooling, которому нужно определить "изменился ли этот сайт с прошлого визита?" без полного diff.
- Командой deploy, чтобы пропускать pushes, когда ничего не изменилось.

## Выбор правильного формата

| Если вы… | Читайте |
|---|---|
| Человек, впервые посетивший сайт | `/`, затем углубитесь в [Concepts](#concepts) или [Papers](#papers) |
| Человек, которому нужно "что нового" | [Timeline](#timeline) и недавние [Syntheses](#syntheses) |
| Человек, которому нужна структура | [Graph view](#graph-view) |
| LLM-агент, выполняющий один запрос | `<page>.json` для типизированного доступа |
| LLM-агент, выполняющий один запрос при ограниченном бюджете | `<page>.txt` |
| LLM-агент, загружающий всё | `/llms-full.txt` (≤ 5 MB) или `/graph.jsonld` (без ограничения) |
| Инструмент, строящий кастомный UI | `/search-index.json` + `/graph.json` |
| Поисковая система | `/sitemap.xml` + `/graph.jsonld` |
| Подписчик | `/rss.xml` |
| Детектор изменений | `/manifest.json` |

## Связанные документы

- [Architecture](architecture.ru.md) — трёхслойная модель, карта модулей, история идемпотентности.
- [Feature map](feature-map.ru.md) — каждая функция со статусом, исходными файлами и ссылками сюда.
- [Quickstart](quickstart.ru.md) — минимальный путь от `project init` до просматриваемого сайта.
- [Self-dogfood demo](self-dogfood.ru.md) — запуск Tesserae на собственном репозитории, включая этот сайт.
