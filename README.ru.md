# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Граф Tesserae: концепции, статьи, репозитории, синтезы и сущности, сгруппированные вокруг фокусного узла" width="100%" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.ko.md">한국어</a> ·
  <a href="./README.zh.md">中文</a> ·
  <a href="./README.ja.md">日本語</a> ·
  <a href="./README.es.md">Español</a> ·
  <a href="./README.fr.md">Français</a> ·
  <a href="./README.de.md">Deutsch</a>
</p>

> Контекстный движок, который поддерживает самоулучшающуюся базу знаний вашего проекта и компилирует готовый к использованию агентами контекст по запросу.

<p align="center">
  <img src="docs/screencasts/showcase.gif" alt="Трёхшаговый скринкаст: tesserae init -> compile -> ask, записанный на демо-корпусе из 135 документов" width="100%" />
</p>

<p align="center">
  <a href="https://ca1773130n.github.io/Tesserae">Живое демо</a> ·
  <a href="docs/">Документация</a> ·
  <a href="docs/release-notes/">Примечания к выпускам</a> ·
  <a href="docs/integrations/mcp.md">Настройка MCP</a> ·
  <a href="docs/tuning.md">Настройка</a> ·
  <a href="docs/integrations/obsidian.md">Экспорт в Obsidian</a>
</p>

## Что это такое

Укажите Tesserae на директорию с Markdown-файлами, исходным кодом и (опционально)
PDF/Office-документами/изображениями. Система реконструирует **типизированный граф знаний**
проекта и поддерживает его в актуальном состоянии — так агенты всегда получают
обоснованный, снабжённый ссылками контекст.
Три опоры:

1. **Мониторинг сессий** — ваши разговоры в Claude Code / Codex о проекте становятся
   полноправными узлами графа (решения, инсайты, вопросы, TODO) в режиме реального времени.
2. **Автономное поглощение** — управляемый движок следит за источниками и сессиями,
   объединяет пакеты изменений, перекомпилирует, а sidecar самоулучшения усиливает
   повторяющиеся находки и вытесняет устаревшие.
3. **Контекст по запросу** — компилятор контекста собирает адаптированный, снабжённый
   ссылками документ для любого запроса или seed-узла (Personalized PageRank в пределах
   символьного бюджета), готовый к вставке в любого агента.

Граф, Obsidian vault и статический сайт — это *проекции* единой базы знаний.
Всё работает локально: это шаг сборки плюс живой движок, а не размещённый сервис.

## Быстрый старт

Требуется **Python 3.10+**.

```bash
pip install tesserae          # добавьте [semantic] для настоящих эмбеддингов
# или: pipx install tesserae   # наиболее надёжная установка в PATH
# или: npx @jokerized/tesserae # обёртка Node вокруг того же CLI

cd /path/to/my-project
tesserae init --yes           # мастер настройки; --yes принимает найденные умолчания
tesserae compile              # построить граф знаний
tesserae ask "Where is Mermaid rendering implemented?"

# Скомпилировать адаптированный, снабжённый ссылками контекстный документ:
tesserae context "How does the parser handle arXiv IDs?" --budget 32000 -o context.md

tesserae serve --port 8765    # просматривать граф и вики локально
```

Возможности на базе LLM по умолчанию используют CLI `codex` / `claude` через OAuth —
**ключи API не требуются** для стандартного сценария. Смотрите
[docs/quickstart.md](docs/quickstart.md) и
[docs/installation.md](docs/installation.md).

<details>
<summary><strong><code>tesserae: command not found</code> после установки? Проблемы на Linux?</strong></summary>

Наиболее надёжное решение на любой платформе — [`pipx`](https://pipx.pypa.io/):

```bash
# macOS: brew install pipx · Ubuntu/Debian: sudo apt install pipx
pipx ensurepath          # добавляет ~/.local/bin в PATH; откройте новый терминал после
pipx install tesserae
```

Типичные проблемы Ubuntu при `pip install tesserae`:

| Ошибка | Причина | Решение |
|---|---|---|
| `error: externally-managed-environment` | PEP 668 — системный Python заблокирован | Используйте `pipx` (выше) или venv |
| `command not found` после `pip install --user …` | `~/.local/bin` не в `PATH` | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
| `ModuleNotFoundError` на старых дистрибутивах | системный `python3` < 3.10 | `sudo apt install python3.11 python3.11-venv`, затем установите с `python3.11 -m pip` |

</details>

<details>
<summary><strong>Анимированные демонстрации</strong> — каждый шаг быстрого старта на встроенном демо-корпусе из 135 документов</summary>

<details>
<summary>1. Настройка — указать на директорию с материалами, получить заготовку вики проекта</summary>
<br/>
<img src="docs/screencasts/setup.gif" alt="tesserae init --source ./research выполняется в неинтерактивном режиме и создаёт .tesserae/" width="100%" />
</details>

<details>
<summary>2. Компиляция + сборка сайта — детерминированно, без вызовов LLM</summary>
<br/>
<img src="docs/screencasts/compile.gif" alt="tesserae compile и tesserae export site, создающие graph.json и дерево статического сайта" width="100%" />
</details>

<details>
<summary>3. Ask — запрашивать скомпилированную вики из CLI</summary>
<br/>
<img src="docs/screencasts/ask.gif" alt="tesserae ask --backend wiki возвращает топ-3 результата с оценкой, типом и исходящими связями" width="100%" />
</details>

Пересоберите любой GIF командой `vhs docs/screencasts/<name>.tape`.

</details>

## Повседневные команды

Запустите `tesserae --help` для полного сгруппированного списка, `tesserae <cmd> --help` для флагов.

| Команда | Что делает |
|---|---|
| `tesserae init` | Мастер настройки → `.tesserae/config.json`. `--yes` неинтерактивный, `--bare` минимальный. |
| `tesserae compile` | Перестроить граф знаний и все артефакты. `compile <paths>` для точечного добавления файлов. |
| `tesserae ingest <file\|url>` | Добавить один документ или веб-страницу в базу знаний без полной перекомпиляции (инкрементальный быстрый путь). |
| `tesserae context "<query>"` | **Компилятор контекста по запросу**: снабжённый ссылками документ через PPR-расширение с `--budget`; `--synthesize` добавляет резюме LLM. |
| `tesserae ask "<question>"` | Задать вопрос скомпилированной базе знаний (`--scope all-registered` охватывает все проекты). |
| `tesserae engine` | Управляемый демон обновления для текущего проекта: наблюдение, дебаунс, перекомпиляция. |
| `tesserae engine --all` | **Флотский режим**: один процесс поддерживает актуальность *всех* зарегистрированных проектов — горячая перезагрузка реестра, регулирование `--compile-slots`. |
| `tesserae refresh` | Однократный конвейер: импорт новых сессий → компиляция → синхронизация vault. |
| `tesserae sessions discover --import` | Найти и импортировать историю локальных сессий Claude Code / Codex для данного проекта. |
| `tesserae export site` | Собрать статический сайт (`--deploy`, `--watch`). |
| `tesserae serve` | Запустить сайт локально со встроенным виджетом ask (`/api/ask`). |
| `tesserae projects …` | Реестр нескольких проектов: `register`, `list`, `activate`, `mcp-config`. |
| `tesserae integrations refresh …` | Перезапустить сопутствующие инструменты (Understand-Anything, RAG-Anything). |

## Автоматическое поддержание актуальности

Движок — это то, что делает базу знаний *самоулучшающейся*, а не однократной сборкой:

```bash
# Один проект: наблюдение за источниками + живыми сессиями, перекомпиляция при изменениях.
tesserae engine

# Все зарегистрированные проекты, один процесс (v0.8.0):
tesserae engine --all --compile-slots 1
```

Флотский режим каждые 10 с сверяется с `~/.tesserae/registry.json` —
регистрация или удаление проекта вступает в силу без перезапуска — и
сериализует компиляции по проектам, чтобы одновременное извлечение через LLM
не исчерпывало лимиты аккаунта. Первый запуск единожды сканирует историю сессий
(это отражается в логе); при перезапуске возобновление идёт с сохранённой отметки.

## Что вы получаете после компиляции

```text
.tesserae/
  graph.json              # типизированные узлы/рёбра (база знаний)
  sqlite.db               # доступное для запросов хранилище графа
  markdown_projection/    # читабельные вики-страницы
  obsidian_vault/         # готово к использованию в Obsidian
  site/                   # статический сайт (граф + вики + поиск)
  harness_sessions/       # импортированная память сессий Claude/Codex
  agent_harness/          # конфигурация контекста для каждого агента (Claude/Codex/Gemini/...)
  cognee_bundle/          # JSONL, готовый для поглощения Cognee
  config.json · manifest.json · report.md · …
```

## MCP-сервер

`tesserae projects mcp-config` выводит запись сервера для Claude Code, Codex или
любого MCP-клиента. Основные инструменты:

- **`compile_context`** — адаптированный, снабжённый ссылками документ контекста для запроса или seed-узлов
  (детерминированный, если не задан `synthesize=true`), основан на `graph_ppr`.
- **Граф + вики**: `search_nodes`, `node_context`, `graph_summary`,
  `wiki_page`, `raw_source`, `timeline`, `search_facts`, `lint_report`, `ask`.
- **Память сессий**: `list_sessions`, `find_session_findings`,
  `find_code_symbol_mentions`, `fresh_insights` (ранжирование по убыванию свежести, дедупликация).
- **Реестр**: `list_projects`, `register_project`, `activate_project`.

## Несколько проектов

Реестр в `~/.tesserae/registry.json` разрешает имена проектов везде —
в CLI, MCP и флотском движке:

```bash
tesserae projects register /path/to/my-project --name myproj
tesserae projects activate myproj
tesserae ask "..." --scope all-registered        # охватить все проекты
```

Markdown в одном проекте может содержать глубокую ссылку на узел в другом через
`wiki://<alias>/<kind>/<slug>`; при компиляции они превращаются в мостовые узлы в
представлении графа. Подробнее в [документации](docs/).

## Интеграции (все опциональные)

- **Плагин Claude Code** — slash-команды, хуки сессий, skill и автоматическая
  регистрация MCP через один `/plugin install`.
  [docs/integrations/claude-code-plugin.md](docs/integrations/claude-code-plugin.md)
- **Граф сессий** — разговоры Claude Code / Codex → узлы Insight / Decision /
  Question / TODO, связанные с затронутыми документами. Ключ API не нужен.
  [docs/integrations/sessions.md](docs/integrations/sessions.md)
- **Understand-Anything** — поглощение графа знаний кода.
  [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md)
- **RAG-Anything** — мультимодальное поглощение (PDF/Office/изображения через
  MinerU/Docling) и бэкенд вопросов LightRAG.
  [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)
- **Cognee** — бэкенд граф+вектор памяти; компиляция всегда записывает
  пакет, готовый для Cognee, runtime cognify — по возможности.
- **Obsidian** — двунаправленная синхронизация vault с оверлеем пользовательских правок.
  [docs/integrations/obsidian.md](docs/integrations/obsidian.md)

## Сравнение

<details>
<summary>Матрица функций по сравнению с Quartz, Logseq, Cognee, Foam</summary>

| Функция | Tesserae | Quartz | Logseq | Cognee | Foam |
|---|---|---|---|---|---|
| Вывод в статический HTML | да | да | частично (экспорт) | нет | частично (publish) |
| Встроенный вид графа | да | да | да | да (отдельный UI) | да (VSCode) |
| Типизированная схема узлов | да (41 тип) | нет | частично (теги) | да | нет |
| Извлечение концепций из источников | да (LLM) | нет | нет | да | нет |
| Мультимодальное поглощение (PDF/изображение) | да (через RAG-Anything) | нет | частично (вставки) | да | нет |
| Поглощение графа кода | да | нет | нет | частично | нет |
| MCP-сервер | да | нет | нет | да | нет |
| Компилятор контекста по запросу со ссылками | да (PPR + бюджет) | нет | нет | нет | нет |
| Мониторинг живых сессий → граф | да | нет | нет | нет | нет |
| Реестр нескольких проектов | да | нет | да (графы) | частично | нет |
| Флотский демон для нескольких проектов | да | нет | нет | нет | нет |
| Работает без ключа API (OAuth) | да | н/п | н/п | нет | н/п |
| Детерминированная побайтовая компиляция | да | да | н/п | нет | н/п |
| Живое редактирование | нет | частично | да | н/п | да |
| Совместная работа в реальном времени | нет | нет | да (DB beta) | нет | нет |

</details>

Tesserae выбирает компиляцию из источников вместо живого редактирования. Если вы хотите
редактировать заметки в UI — используйте Logseq или Obsidian. Если вам нужен инструмент
сборки *и живой движок* для вашего графа знаний — это тот проект.

**Подходит вам**, если вы хотите долговечный, пригодный для проверки граф знаний над
текстовыми источниками проекта, локальный MCP-сервер, основанный на ваших собственных файлах,
или чистые пакеты для Cognee/Obsidian без написания связующего кода.

**Не подходит вам**, если вам нужен только векторный поиск по небольшой директории, вы хотите
размещённую вики с UI для редактирования или ожидаете готового агента «спроси что угодно» —
Tesserae строит субстрат; вы сами подключаете его к агенту на свой выбор.

## Аутентификация и поставщики LLM

Стандартный сценарий не использует **никаких ключей API**:

- **Codex CLI** (по умолчанию) и **Claude Code CLI** через OAuth с
  ротацией нескольких аккаунтов.
- **Эмбеддинги**: нативное гибридное извлечение использует офлайн-семантический
  канал без torch через `pip install "tesserae[semantic]"` (`model2vec`). Бэкенды Cognee/RAG-Anything
  по умолчанию используют детерминированного поставщика; переключитесь на Ollama или любой
  совместимый с OpenAI эндпоинт для лучшего качества поиска.

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` подхватываются, если заданы, но никогда не обязательны.

## Статус и ограничения

Текущий выпуск: смотрите [примечания к выпускам](docs/release-notes/). Известные ограничения:

- Первичная компиляция больших корпусов (тысячи файлов) занимает несколько минут;
  время компиляции растёт примерно линейно. Инкрементальная компиляция (`--changed-only`)
  включена, но экспериментальна и отключена по умолчанию.
- Без дополнения `semantic` гибридное извлечение деградирует до несемантической
  заглушки (с явным предупреждением).
- Описание изображений в RAG-Anything ещё не подключено end-to-end.
- Runtime cognify в Cognee — по возможности: отсутствующие поставщики логируются и
  пропускаются, никогда не являются фатальными.
- Набор инструментов MCP стабилен; схема графа может ещё получить новые типы узлов.

## Структура проекта

```text
tesserae/        # пакет (CLI, компилятор, движок, MCP-сервер, адаптеры)
docs/            # английская документация + docs/i18n/ для семи других языков
ontology/        # схемы узлов/рёбер, против которых валидирует компилятор
prompts/         # промпты для извлечения и синтеза
tests/           # тесты pytest
evals/           # обвязки для оценки качества графа
examples/        # демо-корпус, используемый в скринкастах
```

## Локализованная документация

[한국어](./README.ko.md) ·
[中文](./README.zh.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md) ·
[Deutsch](./README.de.md)

Подробная документация продублирована в `docs/i18n/`.

## Лицензия

MIT. Смотрите [LICENSE](LICENSE).
