# Демо Self-dogfood

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
Этот проект может индексировать сам себя. Поток self-dogfood доказывает, что Tesserae можно установить, настроить внутри собственного репозитория, загрузить собственные docs/source/tests/scripts, при необходимости обновить Understand Anything и Cognee, скомпилировать графовые артефакты и собрать статический веб-фронтенд.

Он также прогоняет цикл самоулучшения. Каждая компиляция заново выводит изменяемое состояние памяти — `decay_score`, `access_count`, `confidence` и флаг `superseded` — в **sidecar-таблицу `node_memory`** внутри `.tesserae/sqlite.db`. Эти скаляры живут *только* в sidecar и никогда в `graph.json`, поэтому повторная dogfood-компиляция byte-identical по графу, тогда как sidecar отслеживает decay и recurrence. Insight, повторяющиеся в `>= 3` различных сессиях, усиливаются числовым confidence в диапазоне `(0, 1]` (3 сессии → `0.5`, 4 → `0.75`, 5+ → `1.0`, с потолком), записываются в sidecar и выводятся MCP-инструментом `fresh_insights`, который по умолчанию скрывает finding, вытесненные (superseded) более новым near-duplicate.

## Команды

Из корня репозитория:

```bash
# Убедитесь, что shell-команда установлена.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (необязательно) установите стандартный семантический бэкенд эмбеддингов.
pip install 'tesserae[semantic]'

# Настройте этот репозиторий как проект Tesserae.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee

# Скомпилируйте настроенные источники.
tesserae compile

# Явно пересоберите статический фронтенд.
tesserae export site

# Запустите локальную раздачу (при необходимости сайт сначала собирается автоматически).
tesserae serve --port 8765
```

Откройте:

```text
http://127.0.0.1:8765/
```

## Сгенерированное рабочее пространство

self-demo записывает сгенерированные артефакты в:

```text
.tesserae/
```

Ключевые артефакты:

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # типизированный граф + sidecar node_memory + живая HarnessSessionsDB
.tesserae/report.md
.tesserae/competitive_report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
.tesserae/cognee_bundle/
```

Сгенерированное рабочее пространство намеренно не коммитится по умолчанию. Оно воспроизводимо из исходников репозитория с помощью команд выше.

## Последний проверенный запуск

Проверено `2026-04-27 11:11:23 KST` из самого репозитория Tesserae.

Подключения интеграций (Understand Anything, cognee) теперь являются
**интерактивными запросами мастера**, а не CLI-флагами. Неинтерактивный
эквивалент ниже выполняет `tesserae init --yes` (интеграции ВЫКЛЮЧЕНЫ),
включает интеграции в `.tesserae/config.json` (мастер записывает их под ключами
`memory_backends` и `external_tools` — точные ключи см. в документах по
интеграциям), затем обновляет каждую перед компиляцией.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # затем включите Understand Anything + cognee в .tesserae/config.json и выполните:
                 #   tesserae integrations refresh understand-anything
                 #   tesserae integrations refresh cognee
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

Итоговые счетчики артефактов:

```text
nodes:               667
edges:               1020
markdown notes:      684
obsidian notes:      686
agent harness files: 14
cognee nodes:        667
cognee edges:        1020
graphiti episodes:  1020
temporal facts:      1020
site files:          index.html, nodes/index.html, sources/index.html, graph/index.html, graph.json, search-index.json, llms.txt, llms-full.txt, manifest.json, assets/style.css, assets/app.js
node pages:          687
source pages:        56
```

Основные типы узлов:

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

Проверка в браузере:

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## Что это демонстрирует

- Публичный путь установки работает.
- Shell-команда `tesserae` работает.
- Репозиторий может подключить локальное для проекта рабочее пространство `.tesserae`.
- Исследовательский/документационный markdown и графовые узлы разработческого кода могут сосуществовать.
- Проекции Markdown, Obsidian, frontend, Graphiti, Cognee, SQLite, report и agent-harness создаются из одного графового конвейера.
- Статический HTML-фронтенд может просматривать граф проекта без шага сборки JavaScript.
- Цикл самоулучшения работает и сохраняется: decay, счётчики доступа, recurrence confidence и флаги supersede попадают в sidecar `node_memory`, не возмущая `graph.json`.
- Гибридный поиск использует настоящий семантический бэкенд, если установлен `tesserae[semantic]` (порядок по умолчанию `auto`: model2vec → sentence-transformers → заглушка hash-bucket); без него поиск по эмбеддингам деградирует до несемантической заглушки hash-bucket и выдаёт громкое предупреждение.
