# История сессий Harness

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae может импортировать локальные transcript AI-agent и отображать их как память проекта в разделе `sessions/` статического сайта.

Эта функция намеренно отделена от `export-agent-harness`:

- `export-agent-harness` — исходящий контекст для инструментов вроде Claude Code, Codex, Gemini, Cursor, Kiro и OpenCode.
- `project sessions ...` — входящая история: она нормализует предыдущие сессии Claude Code/Codex для текущего проекта, сохраняет их в `.tesserae/harness_sessions/` и позволяет `project build-site` публиковать страницы index/detail сессий.

## Два входа: пакетный импорт и живой мониторинг

Приём сессий больше не только пакетный. В одно и то же нормализованное хранилище ведут два пути:

- **Пакетный импорт** — `project sessions discover/import` сканирует корни transcript по запросу и пишет за один проход. Этот поток описан ниже.
- **Живой мониторинг** — supervisor daemon (`project engine`, алиас `project daemon`) запускает `SessionTailer`, который наблюдает за *собственными* transcript проекта (Claude Code и Codex) и принимает новые turn по мере их появления. На каждом tick он делает seek к сохранённому пофайловому byte offset, читает только поступившие новые байты и записывает полные turn в SQLite `HarnessSessionsDB` (`.tesserae/sqlite.db`) **до** постановки в очередь debounce-перекомпиляции, поэтому компиляция всегда читает согласованное состояние. Tailer ограничен собственными сессиями проекта (Claude `projects/<slug>/*.jsonl`; Codex фильтруется по cwd) и после перезапуска возобновляется с сохранённых offset, не повторяя turn.

Запуск живого цикла:

```bash
tesserae project engine        # наблюдать источники, объединять всплески, авто-перекомпиляция
tesserae project engine --once # один цикл drain и выход (детерминированно)
```

`tesserae project refresh` запускает тот же конвейер ingest → compile → project один раз, in-process, не поднимая долгоживущий watcher (передайте `--skip-sessions`, чтобы пропустить сканирование discovery harness-сессий).

## Модель приватности

Оба пути приёма явные: живой tailer работает только пока вы держите `project engine`/`daemon` запущенным, а пакетный discovery пишет только с `--import`. Обычный `project compile` или `project build-site` читает уже нормализованные сессии из `.tesserae/harness_sessions/` и живые записи в `.tesserae/sqlite.db`, но сам по себе не выполняет неожиданное scrape приватных каталогов harness transcript.

Импортированные записи сессий — локальные артефакты проекта. Проверьте их перед публикацией публичного сайта, особенно если transcript могут содержать secrets, приватные пути, данные клиентов или не выпущенный код.

## Обнаружение и импорт локальных сессий

Из корня проекта:

```bash
tesserae project sessions discover --import
```

Discovery сканирует локальные корни transcript Claude Code и Codex, относящиеся к рабочей директории текущего проекта. Используйте `--root`, чтобы сканировать конкретный config-каталог, и повторяйте `--harness`, чтобы ограничить discovery:

```bash
tesserae project sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

Без `--import` discovery печатает найденное, не записывая нормализованные записи сессий.

## Прямой импорт нормализованного JSON

Если другой инструмент уже создал нормализованный JSON `HarnessSession`, импортируйте один файл или список файлов:

```bash
tesserae project sessions import path/to/session.json path/to/more-sessions.json
```

Каждый вход может содержать один объект сессии или список объектов сессий.

## Список импортированных сессий

```bash
tesserae project sessions list
```

Сессии хранятся здесь:

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

Сессии, отслеживаемые в живом режиме, дополнительно учитываются в SQLite `HarnessSessionsDB` (`.tesserae/sqlite.db`), где также сохраняются пофайловые read offset, с которых возобновляется tailer. `project sessions list` показывает объединённое представление.

## Сборка статических страниц сессий

После импорта сессий пересоберите сайт:

```bash
tesserae project build-site
```

Сайт создаёт:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

Сгенерированный сайт ссылается на Sessions из global rail, карточек Browse на главной, поисковых записей и breadcrumb trail каждой страницы detail сессии.

## Макет страницы detail сессии

Страницы detail сессий используют общий static-site shell, а не отдельный transcript dump. Они включают:

- hero и stat strip;
- high-level summary;
- timeline и size metadata;
- decisions, files, commands, tools и errors, если есть;
- свёрнутое subagent tree;
- user/assistant conversation по turn;
- свёрнутые tool-use blocks, прикреплённые под предыдущим assistant turn;
- левый conversation rail со ссылками на anchors `#turn-N`.

Markdown разговора рендерится через site markdown renderer. Семантические поверхности вроде inline code, явной command/tag markup, paths, filenames и hashtags оформляются как компактные chips; случайные существительные с заглавной буквы не chip-ятся автоматически.

Текущая transcript typography:

| Surface | Selector | Size |
|---|---|---|
| Conversation markdown prose | `.session-turn-text`, prose children | `8px` |
| Generic conversation code fences | `.session-turn-text pre` | `10px` |
| Bash/shell fenced code content | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool details/summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-use header | `.session-tool-use-header` | `8px` |
| Tool payload text | `.session-tool-use-text` | `6px` |

## Чеклист публикации сессий

Перед деплоем публичного сайта с сессиями:

1. Запустите `tesserae project sessions list` и подтвердите ожидаемое количество.
2. Проверьте `.tesserae/harness_sessions/` на чувствительное содержимое.
3. Пересоберите через `tesserae project build-site`.
4. Локально откройте `sessions/index.html` и хотя бы одну страницу detail сессии.
5. Убедитесь, что tool blocks свёрнуты по умолчанию и raw tool payloads допустимы к публикации.
6. После commit исходного дерева выполните деплой через `tesserae project deploy --build`.
