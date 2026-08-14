# Карта функций

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
Этот документ подытоживает функции, реализованные сейчас в Tesserae, — со статусом, исходными файлами и местом их документирования.

Tesserae — это **контекстный движок**, работающий на трёх столпах: (1) мониторинг сессий, (2) автономный проактивный инжест знаний и (3) документы/контекст по требованию. Типизированный граф, vault и статический сайт — проекции базы знаний. Функции ниже сгруппированы по столпу, которому они служат; веха **v0.5.0** (июнь 2026) поставила хребет движка и главную фичу столпа 3 — компилятор контекста по требованию.

Легенда статусов: ✅ поставлено · ⚠ в работе / частично.

> **Порядок чтения.** Разделы ниже — это вехи, новые сверху. Версии между
> v0.12.0 и v0.28.7 здесь не пересказываются: детали по каждому выпуску живут в
> [`docs/release-notes/`](../release-notes/), и это авторитетный журнал
> изменений. Эта карта описывает форму системы, а не каждый коммит.

## Память агента, временная глубина и представления поиска — с v0.31.0 (август 2026)

Цикл, который прочитал дизайн agent-memory в Neo4j и взял части, которые выживают в ограничениях Tesserae: вторая временная ось, именованные разделы рёбер, tombstone идентичности и долговечный дом для вердиктов, которые машина не может переизвлечь. Сама база данных осталась вне — см.
`docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md` для того, что было взято, что это стоило, и почему.

| Возможность | Статус | Исходник | Заметки |
|---|---|---|---|
| Transaction time (`observed_as_of`) | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | Вторые часы: `as_of` отвечает «что было ИСТИННО тогда» из временных меток самих источников; `observed_as_of` отвечает «что мы УЗНАЛИ к тому моменту» из таблицы `fact_observed`, помечаемой один раз за компиляцию. Они составляют друг друга. Это живет только в `sqlite.db` — настенные часы внутри `graph.json` заставили бы одни и те же источники компилироваться в разные байты завтра. Раньше `as_of` рекламировал себя как «bitemporal» пока существовала только одна ось. |
| Факты ищут как контент; `dated` как предикат | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | `search_facts` ранжирует по subject / predicate / object / evidence, никогда по сериализованному факту, поэтому id или фрагмент метаданных больше не совпадает. `dated` (`any`/`dated`/`undated`) делает датированность фильтром вместо того, чтобы быть чем-то, что вызывающий должен был вывести из `undated_included`. |
| `resolved_by` закрывает интервал | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Проход противоречий судит loser, но temporal projector его игнорировал, так что arbitrated loser продолжал читать `current: true`. Он закрывает со стороны **проигравшей** — `resolved_by` работает source→winner, противоположно инвалидирующим предикатам — плюс Graphiti's overlap guard: winner observed в момент или раньше своего loser не может сказать, когда loser перестал быть истинным. |
| Timeline считает свои совпадения | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | `timeline` date-сортирует **весь** набор совпадений перед пагинацией, и `total_events` считает каждое совпадение. Раньше он сортировал rank-selected 100-row слайс и сообщал об этом ограничении как покрытие корпуса — поэтому самые ранние события, что такое timeline и есть, были теми, которые скорее всего выпадали. |
| Реестр представлений + мультивью-слияние | ✅ | [`tesserae/retrieval/views.py`](../../tesserae/retrieval/views.py), [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Одна память, проходимая как четыре ортогональных графа — `semantic` / `temporal` / `causal` / `entity`, каждое — именованное подмножество словаря рёбер. Не новый алгоритм ранжирования: представление разрешает в нулевые веса для каждого типа рёбер вне представления, и обход окрестности фильтрует на том же наборе, так что узел только-вне-представления никогда не допускается. Несколько представлений сплавляются взвешенным RRF, и каждая цитата сообщает `via_views`. |
| Persistent vector cache | ✅ | [`tesserae/retrieval/vector_cache.py`](../../tesserae/retrieval/vector_cache.py) | Каждый сайт вызова `.embed()` переэмбеддировал весь корпус при каждом вызове. Таблица `node_vectors` теперь поддерживает все три, ключ — `(backend, dim, sha256(embedded_text))` — **не** id узла, поэтому неизменённый узел попадает после полной переиндексации или перемещения, переописанный промахнётся, и векторы двух моделей никогда не встречаются. `embedding_status` сообщает `vectors_cached` плюс попадания/промахи/ошибки в масштабе процесса. |
| Per-lane retrieval profiling | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `explain: true` на `search_nodes` / `compile_context` возвращает вес per-lane, корпус, вызовы embed, попадания/промахи кэша и wall time, плюс какие ряды внесли каждого победителя. Opt-in, как Neo4j's `PROFILE`, потому что измерение стоит — и оно никогда не может сдвинуть ранжирование, поскольку каждое число читается из таблиц, которые слияние уже произвело. |
| Merge ledger — мёртвый id разрешает в его survivor | ✅ | [`tesserae/merge_ledger.py`](../../tesserae/merge_ledger.py) | Каждая компиляция коллапсирует дубликаты тремя способами и использовалась выбросить каждый ответ, так что агент, держащий id узла из последней компиляции, получил простой not-found. `merge-ledger.json` — loser→survivor tombstone, consulted только после того, как граф не найдёт (живой id никогда не может быть перенаправлен); `node_context` сообщает `status: merged` с `merged_from` / `merged_into`. Производная информация, не история: loser, вернувшийся, выпадает. |
| Retraction (`retracts`) | ✅ | [`tesserae/research_graph.py`](../../tesserae/research_graph.py), [`tesserae/graph_filters.py`](../../tesserae/graph_filters.py) | Агент может сказать «это неправильно» без изобретения замены: `retracts` ребро указанное на узел **по id** убирает его из обнаружения (`search_nodes`, `fresh_insights`), из выбора контекста (`compile_context`) и из списков соседей, которые возвращает `node_context`. Точный поиск `node_context` по id или имени по-прежнему возвращает узел себя, помеченный `retracted` — название узла не является его открытием. `include_superseded` восстанавливает его к поверхностям открытия; ничего не удаляется. |
| Candidate same-as verdict ledger | ✅ | [`tesserae/candidate_ledger.py`](../../tesserae/candidate_ledger.py) | Рецензент, который ответил «это разные» раньше спрашивался один и тот же вопрос вечно — `apply_decisions` потреблял `keep_separate` и ничего не делал прочно. `.tesserae/candidate-same-as.json` ключит вердикт на отсортированную пару id узлов и ничего больше, поэтому переписанное описание, новый источник или другой бэкенд эмбеддинга его все оставляют. Аккумулируемая, никогда не очищаемая: вердикт — единственное здесь, что машина не может переизвлечь. Surfaced как `PENDING_REVIEW`. |
| Один слой блокировки для обоих попарных проходов | ✅ | [`tesserae/blocking.py`](../../tesserae/blocking.py) | Канонизация имела встроенный индекс; `supersede` сравнивал каждую пару в группе находок без всяких ограничений. Обе теперь делят один слой, с двумя свойствами тесты закрепляют: кэп обрезает по **отсортированному id**, так что кэпированный запуск не зависит от порядка прихода, и вызывающий поставляет свой собственный tokenizer, потому что блокер грубее скорера молча удаляет истинные совпадения. Каждый проход сообщает о ударе кэпа вместо тихо возврата более короткой очереди. |
| Узлы доказательства артефакта достигают сайта | ✅ | [`tesserae/raganything_adapter.py`](../../tesserae/raganything_adapter.py), [`tesserae/site/raw_view.py`](../../tesserae/site/raw_view.py) | Фигуры, таблицы и уравнения становятся узлами `Artifact` первого класса, каждый id посеян из вида артефакта и хеша контента и ничего больше — не документ, не путь, не подпись, не страница. Фигура дополнительно получает сырую страницу и адресованные по контенту байты под `raw-assets/` (таблицы и уравнения не несут актива — их контент *есть* описание), и для фигуры, чей актив находится внутри проекта, `drill_down` возвращает `asset_path` / `asset_sha256` / `asset_site_path`. Per-owner факты — вид, страница, подпись, порядок — ездят на ребре `part_of`, потому что узел doc-agnostic по построению и два документа, печатающие одну фигуру, иначе потеряли бы страницу второго. Доказательство **остаётся вне canvas графа**: весь слой утверждения исключён, постоянно. См. [rag-anything](integrations/rag-anything.ru.md). |
| Planner идёт по графу и предлагает записи | ✅ | [`tesserae/ask_planner.py`](../../tesserae/ask_planner.py) | Каталог держал семь примитивов проекции и не было способа идти по графу; `compile_context` его присоединяет, с union представления интерполированным из реестра, чем перепечатано. Planner также может возвращать `proposed_write` — узлы и рёбра обоснованные только в том, что *вопрос* утверждал. **Предлагать, никогда не выполнять**: происхождение всегда null, поэтому `graph_write` отказывает, пока вызывающий с ключом агента и внешней якорью его не предоставит. |
| Read audit — кто читал граф | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py), [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Счётчики доступа ведут забывание-по-неиспользованию, но ничего не записано *кто* их вызвал. `TESSERAE_READ_AUDIT=1` записывает `{tool, actor, node_ids, at, tesserae_version}` везде, где возрастает счётчик доступа — одна строка с названием каждого узла, которую вызов считал, кроме `fresh_insights`, который возрастает в собственном цикле и поэтому пишет одну строку в узел; вызов, не открывший ничего, не пишет — читаемое обратно через `read_audit` с per-actor-суммой. **По умолчанию выключено**, и ворота сидят впереди открытия store — создание таблицы само по себе запись. См. [agent memory](agent-memory.ru.md#забвение--никогда-не-удаление). |
| `tesserae schema-drift` как первоклассный глагол | ✅ | [`tesserae/schema_drift.py`](../../tesserae/schema_drift.py) | Sub-type предложения были доступны только через `lab`. Предложения живут в `.tesserae/schema-drift-proposals.json`, не в метаданных узла — out-of-band ключ метаданных выживал бы incremental compile и испарялся на полном, слепое пятно byte-idempotence, в которое этот repo попал четыре раза. Surfaced как `SUGGESTED_SUBTYPE`; **promotion остаётся человеческой правкой** на `ResearchNodeType`, потом `"approved": true` и `TESSERAE_SCHEMA_DRIFT_APPLY=1`. |
| Portable compile + agent-write locks | ✅ | [`tesserae/locking.py`](../../tesserae/locking.py) | Блокировка была `if fcntl is None: yield` — на Windows она ничего не блокировала, и overlay записи агента единственный путь, где два unsynchronized append рвут JSONL-строку. Теперь `flock(2)`, где она есть, `msvcrt.locking` иначе (закреплено к one-byte range, так как msvcrt блокирует от позиции файла). Платформа ни с каким примитивом предупреждает один раз за процесс. Пропущенная строка replay теперь находка lint (`AGENT_WRITE_SKIPPED`), не только stderr-предупреждение. |
| Sidecar registry | ✅ | [`tesserae/sidecars.py`](../../tesserae/sidecars.py) | Каждая запись `.tesserae/` объявляет своего владельца, свой вид (`derived` / `accumulated` / `cache` / `scratch`) и что стоит удаления — и `safe_to_delete` отдельное поле, потому что `cache` чей ответ пришёл из модели небезопасен уронить и `derived` файл может носить человеческие одобрения. Check Doctor's `sidecars` читает ваш реальный каталог против него. См. [sidecars](sidecars.ru.md). |
| Kuzu является экспортом, никогда не хранилищем | ✅ | [`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) | Управлял one-way: `tesserae export kuzu` пишет `graph.kuzu`, и никакой путь компиляции или рантайма его не читает обратно — `read_graph` хранится только так, чтобы экспорт можно было проверить против графа, из которого он пришёл. См. [architecture § Kuzu export](architecture.ru.md#экспорт-kuzu). |

## Когнитивная память и область охвата — v0.29.0 → v0.31.0 (август 2026)

Цикл, после которого граф *знает, что произошло*, а не только что было написано:
исходы переживают загрузку, из них выводится одно причинное ребро, а деградации,
прежде молчаливые, теперь заявляют о себе.

| Возможность | Статус | Исходники | Заметки |
|---|---|---|---|
| Слой кода включается явно | ✅ | `cli.py`, [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | `compile` больше не загружает символы кода по умолчанию. В большом репозитории они численно подавляли всё остальное и вытесняли выдачу; `tesserae code ingest` по-прежнему подключает CodeGraph осознанно. См. [ingest](ingest.ru.md). |
| Открытая поверхность выдачи | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Битемпоральные и вью-селективные параметры были написаны и покрыты тестами, но недостижимы через MCP. Теперь `search_facts` принимает `as_of` (ответ на прошлую дату) наряду с `current_only` — **вместе они отвергаются**, это разные часы, — и сообщает `undated_included`, чтобы вызывающий знал, сколько строк без даты он получил. |
| Громкие деградации | ✅ | [`tesserae/lint.py`](../../tesserae/lint.py), [`tesserae/ingest/fetch.py`](../../tesserae/ingest/fetch.py), [`tesserae/ingest/orchestrator.py`](../../tesserae/ingest/orchestrator.py) | Три молчаливых отказа сделаны явными: бинарная загрузка, не давшая ничего; недатированное покрытие интервалов (`INTERVAL_COVERAGE`); отброшенное нетекстовое содержимое. Молчание читалось как успех — больше нет. |
| `first_seen_at` из источника | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/session_graph.py`](../../tesserae/session_graph.py) | Дата узла берётся из пути, по которому загружался источник, а не с настенных часов во время компиляции, — поэтому повторный запуск датирует его так же, и байтовая идемпотентность выживает. |
| Процедурный пул выдачи | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py), [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `context` резервирует место под процедурную память — что запускалось и чем кончилось, — и это место **зарабатывается происхождением**, а не выдаётся по умолчанию. Линт `PROCEDURAL_POOLS` сообщает, когда его нечем честно заполнить. |
| Результат инструмента — ход | ✅ | [`tesserae/session_event.py`](../../tesserae/session_event.py), [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Коды возврата и флаги ошибок переживают загрузку и оседают на узлах `Event`. Граф отличает упавшую команду от просто запущенной. Домашние каталоги вырезаются на входе. |
| Ребро `recovers` | ✅ | [`tesserae/session_recovery.py`](../../tesserae/session_recovery.py) | Единственное причинное ребро: «это удалось после того, как то упало», выведенное из двух **наблюдённых** исходов одной сессии, совпадающих по инструменту, семейству программ, рабочему каталогу и операнду. В `CAUSAL_EDGE_TYPES` намеренно один элемент. См. [историю сессий](session-history.ru.md). |
| Уставная структура доменов | ⚠ | [`tesserae/charter.py`](../../tesserae/charter.py), `cli.py` | Обнаружение сообществ *предлагает* словарь доменов; устав *владеет* им между явными реорганизациями, потому что обнаружение детерминировано, но не устойчиво (один документ из 15 узлов сдвигает около 29 % участников). `tesserae domains status` его читает. **`compile` его пока не создаёт** — до тех пор команда сообщает «no charter yet». |
| Несколько хостов на общем диске | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | `TESSERAE_HOST_ID` ограничивает удаление и перезапись по тому, *кто записал* запись, так что N серверов на одном диске перестают стирать историю сессий друг друга. См. [историю сессий](session-history.ru.md). |

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
- ✅ Инструменты контекстного движка (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (ранжированные затуханием), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
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
