# Демо Self-dogfood

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
Этот проект может индексировать сам себя. Поток self-dogfood доказывает, что
Tesserae можно установить, настроить внутри его собственного репозитория,
влить его собственные docs/исходники/тесты/скрипты, опционально обновить
RAG-Anything, скомпилировать артефакты графа и собрать
статический веб-фронтенд.

Тот же поток служит и мультимодальным smoke-тестом. С установленным
RAG-Anything (`tesserae setup --install raganything`) и включённым в
`.tesserae/config.json` (`memory_backends.raganything.enabled: true`)
dogfood-компиляция направляет RAG-Anything на собственный markdown Tesserae в
`docs/` плюс изображения из `docs/assets/` и проектного `assets/`. Это
валидирует мультимодальный конвейер на реальном, принадлежащем проекту
некодовом корпусе — покрывая скриншоты и диаграммы, которые text-first
загрузчики источников пропускают, — не изобретая отдельный набор фикстур.

Он также прогоняет цикл самоулучшения. Каждая компиляция заново выводит
изменяемое состояние памяти — `decay_score`, `access_count`, `confidence` и
флаг `superseded` — в **sidecar-таблицу `node_memory`** внутри
`.tesserae/sqlite.db`. Эти скаляры живут *только* в sidecar и никогда в
`graph.json`, поэтому свежая dogfood-компиляция байт-идентична по графу, пока
sidecar отслеживает затухание и повторяемость. Инсайты, повторяющиеся в `>= 3`
различных сессиях, подкрепляются числовой уверенностью в `(0, 1]`
(3 сессии → `0.5`, 4 → `0.75`, 5+ → `1.0`, с потолком), записываемой в sidecar
и выводимой MCP-инструментом `fresh_insights`, который по умолчанию скрывает
находки, вытесненные более новым почти-дубликатом.

## Команды

Из корня репозитория:

```bash
# Ensure the shell command is installed.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (optional) install the default semantic embedding backend.
pip install 'tesserae[semantic]'

# Set up this repository as an Tesserae project.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts

# (optional) install + enable the heavier companions afterwards:
#   tesserae setup --install raganything
#   then flip memory_backends.*.enabled / external_tools in .tesserae/config.json

# Compile the configured sources.
tesserae compile

# Rebuild the static frontend explicitly.
tesserae export site

# Serve locally (auto-builds the site first if needed).
tesserae serve --port 8765
```

Откройте:

```text
http://127.0.0.1:8765/
```

## Генерируемое рабочее пространство

Self-демо записывает генерируемые артефакты под:

```text
.tesserae/
```

Ключевые артефакты:

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # typed graph + node_memory sidecar + live HarnessSessionsDB
.tesserae/report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
```

Генерируемое рабочее пространство намеренно не коммитится по умолчанию. Оно
воспроизводимо из исходников репозитория командами выше.

## Последний проверенный прогон

Проверено `2026-04-27 11:11:23 KST` из самого репозитория Tesserae.

Подключение интеграций (RAG-Anything) теперь — **интерактивные
подсказки визарда**, а не CLI-флаги. Неинтерактивный эквивалент ниже запускает
`tesserae init --yes` (интеграции ВЫКЛ), включает интеграции в
`.tesserae/config.json` (визард пишет их под ключами `memory_backends` и
`external_tools` — точные ключи см. в документации интеграций), а затем
обновляет каждую перед компиляцией.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # then enable the optional integrations in .tesserae/config.json and run:
                 #   tesserae integrations refresh raganything
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

Итоговые количества артефактов:

```text
nodes:               667
edges:               1020
markdown notes:      684
obsidian notes:      686
agent harness files: 14
graphiti episodes:  1020
temporal facts:      1020
site files:          index.html, nodes/index.html, sources/index.html, graph/index.html, graph.json, search-index.json, llms.txt, llms-full.txt, manifest.json, assets/style.css, assets/app.js
node pages:          687
source pages:        56
```

Топ типов узлов:

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

Браузерная верификация:

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
- Репозиторий может подключить проектное рабочее пространство `.tesserae`.
- Markdown исследований/документации и узлы графа кода разработки могут
  сосуществовать.
- Проекции Markdown, Obsidian, фронтенда, Graphiti, SQLite, отчёта и
  agent-harness производятся из одного графового конвейера.
- Статический HTML-фронтенд может просматривать граф проекта без шага сборки
  JavaScript.
- Цикл самоулучшения работает и персистится: затухание, счётчики доступа,
  уверенность повторяемости и флаги вытеснения ложатся в sidecar `node_memory`,
  не возмущая `graph.json`.
- Гибридный поиск разрешает настоящий семантический бэкенд, когда установлен
  `tesserae[semantic]` (дефолтный порядок `auto`: model2vec →
  sentence-transformers → hash-bucket-заглушка); без него эмбеддинговый поиск
  деградирует до несемантической hash-bucket-заглушки и выводит громкое
  предупреждение.
