# `tesserae doctor` — проверки здоровья проекта

<!-- translations:start -->
<p align="center"><a href="../doctor.md">English</a> · <a href="doctor.ko.md">한국어</a> · <a href="doctor.zh.md">中文</a> · <a href="doctor.ja.md">日本語</a> · <a href="doctor.ru.md">Русский</a> · <a href="doctor.es.md">Español</a> · <a href="doctor.fr.md">Français</a> · <a href="doctor.de.md">Deutsch</a></p>
<!-- translations:end -->
`tesserae doctor` проверяет рабочее пространство Tesserae от начала до конца —
инициализацию, целостность графа, согласованность реестра, свежесть,
блокировки, вход в LLM и гигиену диска — и печатает чек-лист. По умолчанию
команда **только читает**; `--fix` применяет лишь те исправления, которые
безопасно перезапускать и которые никогда не могут разрушить живое состояние.

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## Что проверяется

Двадцать проверок, сгруппированных по категориям:

| Проверка | Категория | Что проверяет | Действие `--fix` |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` существует и выглядит как рабочее пространство Tesserae | только отчёт (предлагает `tesserae init`) |
| `graph_parse` | core | `graph.json` парсится и имеет ожидаемую форму | только отчёт (предлагает `tesserae compile`) |
| `config_valid` | core | `.tesserae/config.json` парсится и валиден относительно шаблона init | только отчёт |
| `vault_configured` | core | настроенный путь vault разрешается | **SAFE**: создаёт разрешённый каталог vault, если он находится внутри проекта |
| `registry_consistent` | registry | записи `~/.tesserae/registry.json` указывают на реальные корни проектов | **SAFE**: удаляет записи с исчезнувшим корнем, убирает устаревший ключ `active`; отсутствующий граф — только отчёт |
| `graph_staleness` | freshness | git-дельта с момента `git_head`, записанного при последней компиляции | только отчёт (предлагает `tesserae refresh` — компиляции тяжелы) |
| `site_search_index` | freshness | статический сайт / `search-index.json` новее, чем `graph.json` | **SAFE**: пересобирает сайт |
| `backend_artifacts` | freshness | артефакты RAG-Anything актуальны | только отчёт (их обновление тяжёлое по LLM/сети) |
| `session_chunks` | freshness | покрытие [дневных чанков сессий](session-chunks.ru.md) не имеет пропусков в недавнем окне | только отчёт (предлагает `tesserae sessions chunk-backfill`) |
| `wiki_lint` | graph | дрейф графа ⇄ wiki + тривиально исправимые находки линта | **SAFE**: применяет тривиальные исправления линта (`fix_trivial`) |
| `compile_lock` | processes | удерживается ли живая блокировка компиляции и каким pid | только отчёт — doctor **никогда не убивает процесс и не снимает живую блокировку** |
| `daemon_pid` | processes | `daemon.pid` указывает на живой процесс движка | **SAFE**: удаляет pid-файл, если его владелец мёртв |
| `llm_login` | environment | настроенный LLM-бэкенд действительно пригоден (CLI claude/codex залогинен или есть API-ключ) | только отчёт (предлагает `claude /login` / `codex login`) |
| `optional_deps` | environment | статус опциональных зависимостей (memex, raganything) | только отчёт (установки требуют сети) |
| `embedding_backend` | environment | доступен настоящий семантический бэкенд эмбеддингов | только отчёт (предлагает `pip install tesserae[semantic]`) |
| `environment` | environment | сводка полной диагностики окружения | секция только-отчёт |
| `build_history` | hygiene | размер и форма `.build-history` | **SAFE**: обрезает её, всегда сохраняя самую свежую запись `git_head` (от неё зависит проверка staleness) |
| `idempotence` | hygiene | «растяжка» `idempotence_suspect` в output-snapshot | только отчёт (это сигнал о баге, а не то, что нужно автопочинить) |
| `orphan_worktrees` | hygiene | устаревшие регистрации `git worktree` | **SAFE**: `git worktree prune`; удаление каталогов — только отчёт |
| `hook_log_bloat` | hygiene | рост `.tesserae/.session-*-hook.log` | **SAFE**: ротирует/усечает логи больше 10 МБ |

Упавшая проверка сообщается как находка-ошибка — сам doctor никогда не бросает
исключение.

## Политика `--fix`

- `--fix` запускает **только** проверки, помеченные выше как SAFE, а затем
  выполняет повторную диагностику, чтобы отчёт отражал состояние после
  исправлений.
- Каждое исправление идемпотентно: двойной запуск `doctor --fix` оставляет
  второй прогон чистым.
- Doctor **никогда не убивает процесс и никогда не снимает живую блокировку
  компиляции** — удерживаемая блокировка сообщается с pid владельца и не
  трогается.
- Тяжёлые или сетевые операции (перекомпиляции, установка зависимостей,
  обновление бэкендов) никогда не включаются в `--fix`; doctor печатает
  команду, которую вы должны запустить сами.

## Коды выхода

Та же конвенция, что у `tesserae lint`:

| Код выхода | Значение |
|---|---|
| `0` | здорово — нет находок выше OK |
| `1` | есть предупреждения |
| `2` | есть ошибки |

## Артефакты отчёта

Каждый запуск записывает обе формы отчёта в рабочее пространство:

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json` дополнительно печатает JSON-отчёт в stdout вместо markdown-чек-листа.
`--all` перебирает каждый проект из реестра (игнорируя `--project`) и
отчитывается по каждому проекту.

## MCP: `doctor_report`

MCP-сервер выставляет тот же отчёт как инструмент `doctor_report` (зеркально
`lint_report`, включая его байтовый лимит на возвращаемое содержимое), чтобы
агент мог проверить здоровье рабочего пространства посреди диалога без выхода
в шелл. Ему нужен корень проекта — передайте `graph_path`/`project` или
настройте граф по умолчанию.
