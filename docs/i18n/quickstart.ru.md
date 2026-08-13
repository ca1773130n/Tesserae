# Быстрый старт

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
Эта страница показывает кратчайший путь от существующего каталога проекта до просматриваемого Tesserae.

## Обзор команд

CLI сгруппирован: горстка повседневных глаголов на верхнем уровне плюс группы
(`sessions`, `vault`, `export`, `code`, `config`, `projects`, `agents`, `domains`, `integrations`,
`lab`) для остального. Запустите `tesserae --help`, чтобы увидеть всё дерево:

```text
tesserae 0.31.0 — a context engine

usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  ingest        Ingest a document file or URL into the knowledge base
  context       Compile agent-ready context for a query
  ask           LLM answer over the knowledge graph (planned retrieval)
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles, idle 'sleep' consolidation
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query
  distill       Per-agent L1 expertise artifacts (opt-in: TESSERAE_AGENT_DISTILL)

ANALYSIS
  query         raw retrieval: BM25/semantic + explicit backends
  graph-map     Budgeted Descent navigation (the graph_map tool as a CLI verb; JSON out)
  verify-claim  Does the graph license this triple? Deterministic verdict, JSON out
  schema-drift  Propose ResearchNodeType sub-types from clustered nodes (proposals only; promotion is a human edit)
  lint          Graph lint report (--fix-trivial, --severity, --json)
  doctor        Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)
  summary       Daily/weekly activity digest (sessions, findings, commits, PRs, docs)
  decisions     Decisions across projects + time (human AskUserQuestion + agent)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  setup         Machine-wide setup: LLM defaults + optional deps (interactive by default)
  config        llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping
  projects      register | list | unregister | mcp-config — registry
  agents        init | list | tree | show | drill | set-parent | rename — role-grade agent org registry
  domains       status — chartered domain tree (divisions/departments/teams)
  sources       add | list | remove — manage compile source dirs (local & global)
  federation    status | explain — inspect cross-project federation
  integrations  refresh raganything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Запустите `tesserae <command> --help` (например, `tesserae compile --help`),
чтобы увидеть флаги любой отдельной команды.

## 1. Запустите визард настройки

Из проекта, который хотите проиндексировать:

```bash
cd /path/to/my-project
tesserae init
```

`tesserae init` — единственный шаг онбординга. Визард обнаруживает типичные источники, такие как `README.md`, `docs`, `src`, `lib`, `app`, `packages` и `data`, проверяет, какие LLM CLI установлены **и залогинены**, позволяет выбрать LLM-провайдера и пишет `.tesserae/config.json`. Опциональный memory-бэкенд RAG-Anything **выключен по умолчанию**; включите его позже в `memory_backends` в конфиге и запрашивайте явно через `tesserae query --backend raganything`.

Для неинтерактивной настройки (CI, скрипты) передайте `--yes`, чтобы принять
обнаруженные дефолты без запросов (все опциональные интеграции ВЫКЛ):

```bash
tesserae init --yes
```

### Конфигурация LLM-провайдера

Выбор провайдера визардом (или эквивалентные флаги) персистит эти ключи конфига:

| Ключ конфига | Флаг | Что это |
|---|---|---|
| `llm_provider` | `--llm-provider {claude,codex,anthropic,custom}` | Бэкенд LLM-клиента: `claude`/`codex` используют залогиненный CLI через OAuth; `anthropic` — API напрямую; `custom` нацеливается на любой claude-совместимый эндпоинт. |
| `llm_model` | `--llm-model` | Модель для LLM-клиента синтеза/инсайтов. |
| `llm_base_url` | `--llm-base-url` | Базовый URL эндпоинта для `anthropic`/`custom`. |
| `llm_api_key` | `--llm-api-key` | API-ключ для `anthropic`/`custom`. |

> **Предупреждение о хранении в открытом виде.** `llm_api_key` хранится
> **открытым текстом** в `.tesserae/config.json`. Предпочитайте переменные
> окружения: `ANTHROPIC_API_KEY` (ключ), `ANTHROPIC_BASE_URL` (эндпоинт) и
> `TESSERAE_LLM_MODEL` (модель). Порядок разрешения: env → конфиг проекта →
> машинный конфиг (`~/.tesserae/config.json`, записываемый `tesserae setup`)
> → встроенный дефолт.

Повторный запуск `init` на существующем проекте **мёржит** — ваши настроенные
`sources` и `memory_backends` сохраняются, а не затираются.

Примеры неинтерактивных настроек провайдера:

```bash
tesserae init --yes --llm-provider codex
tesserae init --yes --llm-provider custom \
  --llm-base-url https://llm.internal.example/v1 \
  --llm-model my-model            # key via ANTHROPIC_API_KEY
```

> **Пропуск визарда.** `tesserae init --bare` пишет минимальный `.tesserae/config.json`
> без обнаружения источников и проверки бэкендов — удобно, когда вы хотите
> отредактировать конфиг вручную перед первой компиляцией.

## 2. Скомпилируйте граф и проекции

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
```

Используйте `--changed-only` после первого запуска, чтобы пропускать неизменённые markdown-файлы, сохраняя предыдущий граф, когда файлы не менялись.

Чтобы влить дополнительные пути ad-hoc, не трогая настроенные источники,
передайте их позиционно: `tesserae compile path/to/extra.md docs/`.

### Ручки интеграций теперь живут в конфиге

`tesserae compile` намеренно ограничен повседневными флагами (позиционные пути
плюс `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions` и три LLM-флага). Каждый прочий бывший флаг
compile переехал в блок `compile_options` в `.tesserae/config.json`; старый
дефолт argparse остаётся запасным значением. Установите ключ там, чтобы
изменить поведение:

| Ключ `compile_options` | Старый флаг | Дефолт | Что делает |
|---|---|---|---|
| `source_kind` | `--source-kind` | (нет) | Переопределить настроенный вид источника. |
| `trends` | `--trends` | `false` | Добавить узлы Trend уровня корпуса. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Минимум источников для узла Trend. |
| `exclude_data` | `--exclude-data` | `false` | Пропустить неявное авто-включение `project_root/data`. |
| `no_vault_pull` | `--no-vault-pull` | `false` | Не подтягивать существующие правки vault перед компиляцией. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Подавать результаты прежнего извлечения обратно в прогон. |
| `sessions_llm` | `--sessions-llm` | (auto) | Режим LLM-извлечения сессий (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (нет) | Переопределить LLM-модель извлечения сессий. |

> **Cognee удалён в 0.19.** Бэкенд cognee был понижен в 0.18 и никогда не
> питал граф. Конфиги, всё ещё несущие секцию `memory_backends.cognee`
> (или compile-опции `cognee_*`), продолжают загружаться — секция
> игнорируется с однострочной заметкой.

> **Однопроходный конвейер.** `tesserae refresh` прогоняет весь цикл внутри процесса — импортирует новые агентские сессии, компилирует и синхронизирует vault одной командой. Передайте `--changed-only` для opt-in инкрементальной компиляции.

## 3. Соберите и поднимите статический фронтенд

`serve` автособирает сайт, если он отсутствует, так что одна команда даёт вам
просматриваемый Tesserae. **Голый `serve` обслуживает каждый зарегистрированный
проект** под одним сервером — лендинг проектов на `/`, каждый проект на
`/<alias>/` и переключатель Projects в шапке для прыжков между ними.
Встроенный **ask-виджет работает вживую в обоих режимах**, маршрутизируясь к
проекту той страницы, на которой вы находитесь:

```bash
tesserae serve --port 8765                 # all registered projects
tesserae serve --project . --port 8765     # just this one
```

Откройте:

```text
http://127.0.0.1:8765/
```

Чтобы собрать сайт явно (например, для деплоя без обслуживания), используйте
`export site`; передайте `--no-build` команде `serve`, когда хотите
просматривать ранее собранный сайт без пересборки:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Автопересборка при сохранении

Спарьте dev-сервер со встроенным наблюдателем, чтобы правки под `data/` и `docs/` запускали инкрементальную перекомпиляцию:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` опрашивает каждые 2 с, дебаунсит 1 с и запускает `compile --changed-only`. Используйте `--once` для cron-стиля пересборок (снапшоты против `.tesserae/.watch-cache.json`), `--paths <dir>` для добавления собственных каталогов наблюдения и `--interval` / `--debounce` для настройки каденса.
<!-- END: subagent-r-watch -->

### Запустите демон обновления

Для всегда работающего движка, который сам держит базу знаний свежей — наблюдая за источниками, коалесцируя всплески правок и автоматически перекомпилируя, — запустите супервизируемый демон:

```bash
tesserae engine
```

`engine` — долгоживущий супервизор: он опрашивает каждые 2 с и выжидает тихое окно в 1 с перед каждой пересборкой. Настройте каденс через `--interval` и `--debounce`, направьте его на другой проект через `--project` или передайте `--once`, чтобы выполнить один детерминированный drain-цикл и выйти (полезно для cron или CI). Это hands-off двойник `export site --watch`: оставьте его работать — и граф, vault и сайт остаются актуальными, пока вы и ваши агенты работаете.

Аннотированный тур по каждому видимому маршруту — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, плюс AI-siblings — см. в [`docs/frontend-redesign.md`](frontend-redesign.ru.md).

Фронтенд лёгок по зависимостям и записывает:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Импортируйте локальную историю агентских сессий

Импорт истории сессий явный: обычные compile/build читают уже нормализованные сессии, но самостоятельно не сканируют приватные хранилища транскриптов Claude Code или Codex.

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

Импортированные сессии появляются в глобальной секции Sessions, поиске сайта и домашних карточках Browse. Детальные страницы сессий рендерят ходы user/assistant как читаемый markdown, прикрепляют блоки tool-use под предыдущим ходом assistant и предоставляют левый рейл ходов для навигации `#turn-N`. Заметки о приватности, форматы импорта и текущую карту типографики транскриптов см. в [`docs/session-history.md`](session-history.ru.md).

## 5. Пролинтуйте wiki

```bash
tesserae lint
```

Обходит скомпилированный граф + wiki + сайт и помечает осиротевшие статьи, устаревшие цитирования, дрейф между графом и wiki/, призрачные входы синтезов и многое другое. Записывает `.tesserae/lint-report.md` и `.tesserae/lint-report.json`. Передайте `--fix-trivial`, чтобы применить безопасные автопочинки (недостающие рёбра `implemented_in`, чистку призрачных входов), и `--severity error`, чтобы код выхода падал только на ошибках.

Для здоровья рабочего пространства за пределами самого графа — согласованность реестра, устаревание, блокировки, вход в LLM, гигиена — запустите `tesserae doctor` (`--fix` применяет только безопасные починки). См. [`docs/doctor.md`](doctor.ru.md).

## 6. Спрашивайте и запрашивайте wiki

```bash
# LLM-planned, cited answer over the compiled graph (the default):
tesserae ask "What is Gaussian Splatting?"

# Raw retrieval — ranked search hits, no LLM:
tesserae query "What is Gaussian Splatting?"
```

`ask` — поверхность ответов: модель планирует retrieval по скомпилированному графу, затем синтезирует ответ с цитированиями. Работает с залогиненным CLI `claude`/`codex` (OAuth) или `ANTHROPIC_API_KEY`; передайте `--no-llm` для только-ранжированных результатов поиска (этот force-off сильнее `TESSERAE_QUERY_LLM=1`). `TESSERAE_QUERY_DRY_RUN=1` прогоняет промпт без вызова API.

`query` — поверхность retrieval: BM25/семантический поиск по `.tesserae/site/search-index.json` с 200-символьным отрывком из совпавшего `wiki/<kind>/<slug>.md`. Передайте `--kind papers` (или `concepts`, `repos` и т.д.) для сужения, `--top-k N` для расширения и `--json` для структурированного вывода; `--interactive` открывает readline-REPL — пустая строка или EOF завершают. Явный memory-бэкенд тоже живёт здесь: `--backend raganything` замыкается на этот бэкенд и показывает его ошибки. На `query` нет LLM-синтеза — для этого есть `ask`.

## 7. Компилируйте agent-ready контекст по требованию

Главная фича v0.5.0 — компилятор контекста по требованию: попросите у скомпилированного графа единый документ контекста с цитированиями, ограниченный запросом и подогнанный под окно агента.

```bash
tesserae context "How does session import work?"
```

Он засевает Personalized PageRank из узлов, совпавших с вашим запросом (используйте `--seeds <node_id>` для явного засева), расширяет окрестность (`--depth`, по умолчанию 2) и собирает документ с цитированиями, ограниченный символьным `--budget` (по умолчанию 32000; передайте `<= 0` для снятия ограничения). Добавьте `--llm` для LLM-написанной сводки сверху (требуется LLM-бэкенд) и `-o/--output <file>`, чтобы записать документ на диск вместо stdout.

Тот же компилятор доступен агентам через MCP как инструмент `compile_context`, так что кодинг-агент может подтянуть ровно-столько-сколько-нужно бюджетно-ограниченного контекста проекта посреди диалога без ручного экспорта.

## 8. Экспортируйте файлы agent harness

```bash
tesserae export harness
```

Поддерживаемые цели:

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

## 9. Экспортируйте Obsidian vault

```bash
tesserae vault export
```

Или запишите в существующий vault:

```bash
tesserae vault export --output "$OBSIDIAN_VAULT_PATH"
```

Vault включает markdown-проекции, дефолты `.obsidian`, раскраску графа, `raw/assets/` и Dataview-дашборд. Используйте `tesserae vault sync`, чтобы согласовать существующий vault с последней компиляцией (добавьте `--prune`, чтобы убрать осиротевшие заметки).

## 10. Настройте MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Вставьте вывод под `mcp_servers` в `~/.hermes/config.yaml`, затем перезапустите Hermes/gateway.

## 11. Экспорт / синхронизация Graphiti

Экспорт эпизодов без зависимостей:

```bash
tesserae export graphiti
```

Smoke dry-run синхронизации без установленного Graphiti:

```bash
tesserae export graphiti --sync --dry-run
```

Живая синхронизация требует `graphiti_core` и доступный бэкенд Neo4j:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Разверните на GitHub Pages

Отправьте скомпилированный сайт из `.tesserae/site/` в ветку `gh-pages` git-origin проекта:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` сперва запускает `compile`, чтобы сайт был свежим. `--enable-pages` включает Pages через `gh` CLI (идемпотентно; пропускается с подсказкой, если `gh` отсутствует). Используйте `--dry-run`, чтобы застейджить и закоммитить без пуша, `--branch` / `--remote`, чтобы переопределить дефолты, и `--force`, чтобы разрешить деплой с грязным рабочим деревом.

Сайт становится доступен по адресу `https://<owner>.github.io/<repo>/`.
