# Быстрый старт

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
Эта страница показывает самый короткий путь от существующего каталога проекта до просматриваемого Tesserae.

## Обзор команд

CLI сгруппирован: на верхнем уровне несколько повседневных глаголов, а остальное
собрано в группы (`sessions`, `vault`, `export`, `code`, `config`, `projects`,
`integrations`, `lab`). Запустите `tesserae --help`, чтобы увидеть всё дерево:

```text
usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  context       Compile agent-ready context for a query
  ask           Ask the project memory a question
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         Raw retrieval over the graph (top-k, kind filters)
  lint          Graph lint report (--fix-trivial, --severity, --json)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  config        llm | show — machine-wide defaults (~/.tesserae/config.json)
  projects      register | list | activate | unregister | mcp-config — registry
  integrations  refresh raganything|understand-anything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Чтобы увидеть флаги отдельной команды, запустите `tesserae <command> --help` (например, `tesserae compile --help`).

## 1. Запуск мастера настройки

В проекте, который вы хотите проиндексировать:

```bash
cd /path/to/my-project
tesserae init
```

Мастер обнаруживает распространённые source, такие как `README.md`, `docs`, `src`, `lib`, `app`, `packages` и `data`, а затем записывает `.tesserae/config.json`. Он также настраивает Cognee backend по умолчанию, чтобы `tesserae ask` мог попробовать Cognee и при необходимости перейти на скомпилированный поиск по wiki.

Для неинтерактивной настройки (CI, скрипты) передайте `--yes`, чтобы принять обнаруженные значения по умолчанию без запросов:

```bash
tesserae init --yes
```

Для полностью автоматической настройки с включёнными Understand Anything и Cognee runtime memory:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
```

Что это делает:

| Flag | Effect |
|---|---|
| `--with-understand-anything` | Добавляет UA graph projection как source. |
| `--install-understand-anything` | Устанавливает/обновляет UA companion skills. |
| `--understand-anything-platform codex` | Использует Codex для запуска управляемого Tesserae UA refresh wrapper. |
| `--with-raganything` | Включает мультимодальный ingestion через RAG-Anything. |
| `--install-raganything` | Устанавливает raganything[all] во время настройки. |
| `--raganything-parser` | Выбор парсера: mineru (по умолчанию), docling, paddleocr. |
| `--run-raganything` | Автоматически refresh RAG-Anything при каждом compile. |
| `--run-cognee` | Запускает по возможности Cognee runtime cognify во время compile. |
| `--install-cognee` | Устанавливает Cognee текущим Python, если он отсутствует. |

Пользователям не нужно знать путь установки UA или вводить `/understand`; когда UA graph отсутствует или устарел, `tesserae compile` запускает `tesserae integrations refresh understand-anything`.

> **Пропустить мастер.** `tesserae init --bare` записывает минимальный `.tesserae/config.json` без обнаружения source и проверки backend — удобно, когда нужно вручную отредактировать config до первого compile.

## 2. Компиляция графа и проекций

```bash
tesserae compile
```

`compile` записывает долговечные артефакты:

```text
.tesserae/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  competitive_report.md
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
  cognee_bundle/
```

После первого запуска используйте `--changed-only`, чтобы пропускать неизменённые markdown-файлы, сохраняя предыдущий граф, когда файлы не менялись. Если включён Understand Anything, compile сначала refresh/materialize `.tesserae/external/understand-anything.md`; если включён Cognee runtime, он также по возможности обновляет Cognee после записи `.tesserae/cognee_bundle/`.

Чтобы выполнить ad-hoc ingest дополнительных путей, не трогая настроенные source, передайте их позиционно: `tesserae compile path/to/extra.md docs/`.

### Переключатели интеграций теперь в config

`tesserae compile` намеренно ограничен повседневными флагами (позиционные paths
плюс `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions` и три LLM-флага). Все остальные прежние флаги compile
переехали в блок `compile_options` в `.tesserae/config.json`; прежнее значение
argparse по-прежнему служит fallback. Установите там ключ, чтобы изменить поведение:

| Ключ `compile_options` | Старый flag | По умолчанию | Что делает |
|---|---|---|---|
| `source_kind` | `--source-kind` | (нет) | Переопределяет настроенный source kind. |
| `trends` | `--trends` | `false` | Добавляет Trend-узлы уровня корпуса. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Минимум source для создания Trend-узла. |
| `exclude_data` | `--exclude-data` | `false` | Пропускает неявное авто-включение `project_root/data`. |
| `no_vault_pull` | `--no-vault-pull` | `false` | Не подтягивает существующие правки vault перед compile. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Подаёт прежние результаты extraction обратно в прогон. |
| `sessions_llm` | `--sessions-llm` | (auto) | Режим LLM-извлечения сессий (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (нет) | Переопределяет LLM-модель для извлечения сессий. |
| `cognee_add` | `--cognee-add` | `false` | Добавляет Cognee bundle в dataset (без cognify). |
| `cognee_cognify` | `--cognee-cognify` | `false` | Добавляет bundle и запускает Cognee cognify. |
| `cognee_codex_cognify` | `--cognee-codex-cognify` | `false` | Запускает cognify с LLM client Cognee, пропатченным на Codex. |
| `cognee_codex_model` | `--cognee-codex-model` | `gpt-5.4` | Модель Codex CLI для `cognee_codex_cognify`. |
| `cognee_codex_timeout` | `--cognee-codex-timeout` | `300` | Таймаут одного вызова Codex CLI (секунды). |
| `cognee_dataset` | `--cognee-dataset` | `tesserae_research_graph` | Имя dataset Cognee. |
| `cognee_embedding_provider` | `--cognee-embedding-provider` | `deterministic` | Embedding provider для lane Cognee. |
| `cognee_ollama_embedding_model` | `--cognee-ollama-embedding-model` | `qwen3-embedding:0.6b` | Embedding-модель Ollama. |
| `cognee_ollama_embedding_endpoint` | `--cognee-ollama-embedding-endpoint` | `http://127.0.0.1:11434/api/embed` | Endpoint Ollama `/api/embed`. |
| `cognee_ollama_embedding_timeout` | `--cognee-ollama-embedding-timeout` | `120` | Таймаут embedding-запроса Ollama (секунды). |
| `cognee_local_embedding_dimensions` | `--cognee-local-embedding-dimensions` | `128` | Размерность локальных embedding. |
| `cognee_system_root` | `--cognee-system-root` | (нет) | Изолированный каталог system root Cognee. |
| `cognee_data_root` | `--cognee-data-root` | (нет) | Изолированный каталог data root Cognee. |

> **Конвейер в один заход.** `tesserae refresh` выполняет весь цикл внутри процесса — импортирует любые новые agent-сессии, компилирует и синхронизирует vault одной командой. Передайте `--changed-only` для опционального инкрементального compile.

## 3. Сборка и обслуживание статического фронтенда

`serve` автоматически собирает site, если он отсутствует, поэтому одной командой вы получаете просматриваемый Tesserae:

```bash
tesserae serve --port 8765
```

Откройте:

```text
http://127.0.0.1:8765/
```

Чтобы собрать site явно (например, для деплоя без обслуживания), используйте `export site`; передайте `--no-build` команде `serve`, когда хотите просматривать ранее собранный site без пересборки:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Авто-пересборка при сохранении

Свяжите dev-сервер со встроенным watcher, чтобы правки в `data/` и `docs/` запускали инкрементальный recompile:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` опрашивает каждые 2 с, делает debounce 1 с и запускает `compile --changed-only`. Используйте `--once` для пересборок в стиле cron (снимки против `.tesserae/.watch-cache.json`), `--paths <dir>` для добавления своих каталогов наблюдения и `--interval` / `--debounce` для настройки темпа.
<!-- END: subagent-r-watch -->

### Запуск демона refresh

Если вам нужен всегда работающий движок, который сам поддерживает базу знаний свежей — наблюдает за вашими source, объединяет всплески правок и автоматически recompile — запустите управляемый демон:

```bash
tesserae engine
```

`engine` — это долгоживущий supervisor: он опрашивает каждые 2 с и ждёт окно тишины в 1 с перед каждой пересборкой. Настройте темп с помощью `--interval` и `--debounce`, нацельте его на другой проект через `--project` или передайте `--once`, чтобы выполнить один детерминированный цикл drain и выйти (полезно для cron или CI). Это автономный аналог `export site --watch`: оставьте его работать, и граф, vault и site будут оставаться актуальными, пока вы и ваши агенты работаете.

Аннотированный обзор каждого видимого маршрута (home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, а также AI siblings) см. в [`docs/frontend-redesign.md`](frontend-redesign.ru.md).

Фронтенд лёгкий по зависимостям и записывает:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Импорт локальной истории agent-сессий

Импорт истории сессий явный: обычный compile/build читает уже нормализованные сессии, но не сканирует приватные хранилища транскриптов Claude Code или Codex самостоятельно.

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae sessions discover --import

# Confirm the imported set:
tesserae sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae export site
```

Импортированные сессии появляются в глобальном разделе Sessions, в поиске по site и в карточках Browse на главной. Страницы деталей сессии отображают ходы user/assistant как читаемый markdown, прикрепляют блоки tool-use под предыдущим ходом assistant и предоставляют левую панель ходов для навигации `#turn-N`. Замечания о приватности, форматы импорта и текущую карту типографики транскриптов см. в [`docs/session-history.md`](session-history.ru.md).

## 5. Lint wiki

```bash
tesserae lint
```

Проходит по скомпилированному graph + wiki + site и отмечает orphan papers, stale citations, drift между graph и wiki/, ghost synthesis inputs и прочее. Записывает `.tesserae/lint-report.md` и `.tesserae/lint-report.json`. Передайте `--fix-trivial`, чтобы применить безопасные авто-исправления (отсутствующие edges `implemented_in`, обрезку ghost-input), и `--severity error`, чтобы код выхода падал только на ошибках.

## 6. Запрос к wiki

```bash
tesserae query "What is Gaussian Splatting?"
```

По умолчанию только поиск — BM25 по `.tesserae/site/search-index.json` с выдержкой в 200 символов из совпавшего `wiki/<kind>/<slug>.md`. Передайте `--kind papers` (или `concepts`, `repos` и т. д.) для сужения, `--top-k N` для расширения и `--json` для структурированного вывода. Добавьте `--llm` (или задайте `TESSERAE_QUERY_LLM=1`), чтобы попросить Claude синтезированный ответ со ссылками `[node_id]`; `--interactive` открывает readline REPL — пустая строка или EOF завершает. `TESSERAE_QUERY_DRY_RUN=1` прогоняет prompt без вызова API.

## 7. Компиляция context для агента по запросу

Главная новинка v0.5.0 — On-Demand Context Compiler: запросите у скомпилированного графа единый цитируемый документ context, ограниченный запросом и подогнанный под окно агента.

```bash
tesserae context "How does session import work?"
```

Он задаёт seed для Personalized PageRank из узлов, совпавших с вашим запросом (для явного seed используйте `--seeds <node_id>`), расширяет окрестность (`--depth`, по умолчанию 2) и собирает цитируемый документ с ограничением по символам `--budget` (по умолчанию 32000; передайте `<= 0` для без ограничения). Добавьте `--synthesize` для сводки, написанной LLM, поверх (требуется LLM backend), и `-o/--output <file>`, чтобы записать документ на диск вместо stdout.

Тот же compiler доступен агентам через MCP как инструмент `compile_context`, поэтому кодинг-агент может вытянуть ровно столько ограниченного по budget context проекта, сколько нужно, прямо посреди разговора, без ручного export.

## 8. Экспорт файлов agent harness

```bash
tesserae export harness
```

Поддерживаемые target:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

Пример подмножества:

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Экспорт хранилища Obsidian

```bash
tesserae vault export
```

Или записать в существующее хранилище:

```bash
tesserae vault export --vault "$OBSIDIAN_VAULT_PATH"
```

Хранилище включает markdown projections, `.obsidian` defaults, раскраску графа, `raw/assets/` и Dataview dashboard. Используйте `tesserae vault sync`, чтобы согласовать существующее хранилище с последним compile (добавьте `--prune`, чтобы удалить осиротевшие заметки).

## 10. Настройка MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Вставьте вывод под `mcp_servers` в `~/.hermes/config.yaml`, затем перезапустите Hermes/gateway.

## 11. Экспорт / sync Graphiti

Экспорт эпизодов без зависимостей:

```bash
tesserae export graphiti
```

Dry-run smoke синхронизации без установленного Graphiti:

```bash
tesserae export graphiti --sync --dry-run
```

Живая sync требует `graphiti_core` и доступного Neo4j backend:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Деплой на GitHub Pages

Запушьте скомпилированный site из `.tesserae/site/` в ветку `gh-pages` git origin проекта:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` сначала запускает `compile`, чтобы site был свежим. `--enable-pages` включает Pages через `gh` CLI (идемпотентно; пропускается с подсказкой, если `gh` отсутствует). Используйте `--dry-run`, чтобы сделать stage и commit без push, `--branch` / `--remote` для переопределения значений по умолчанию и `--force`, чтобы разрешить деплой при грязном рабочем дереве.

Сайт станет доступен по адресу `https://<owner>.github.io/<repo>/`.
