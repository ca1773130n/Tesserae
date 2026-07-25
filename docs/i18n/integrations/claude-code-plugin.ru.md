# Плагин Claude Code

<!-- translations:start -->
<p align="center"><a href="../../integrations/claude-code-plugin.md">English</a> · <a href="claude-code-plugin.ko.md">한국어</a> · <a href="claude-code-plugin.zh.md">中文</a> · <a href="claude-code-plugin.ja.md">日本語</a> · <a href="claude-code-plugin.es.md">Español</a> · <a href="claude-code-plugin.fr.md">Français</a> · <a href="claude-code-plugin.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae поставляется с плагином [Claude Code](https://docs.claude.com/en/docs/claude-code), позволяющим выполнять полный рабочий процесс Tesserae изнутри TUI-сессии — слэш-команды, автоматически зарегистрированный сервер MCP, навык, ориентирующий агента, и четыре хука, замыкающие цикл агент↔память проекта. Плагин находится в репозитории по пути `plugin/`.

## Установка

```bash
# Требование: `tesserae` уже установлен (`pip install tesserae` или `pipx install tesserae`).
/plugin install /path/to/Tesserae/
```

Требование: `tesserae` уже установлен (`pip install tesserae` или `pipx install tesserae`). При установке через pipx убедитесь, что `~/.local/bin` находится в PATH, который Claude Code наследует при запуске.

## Что входит

* **9 слэш-команд** — семь оберток 1:1 над CLI (`/tesserae:compile`, `/tesserae:ask`, `/tesserae:sessions-import`, `/tesserae:build-site`, `/tesserae:serve`, `/tesserae:obsidian-sync`, `/tesserae:setup`) плюс два рабочих макроса (`/tesserae:refresh` цепочкой import + compile + obsidian-sync; `/tesserae:status` показывает счетчики графа и последнюю компиляцию).
* **Автоматическая регистрация сервера `tesserae`** — агент получает всю поверхность инструментов как `mcp__plugin_tesserae_tesserae__<tool>` без ручных правок конфига: запросы графа (`search_nodes`, `node_context`, `graph_ppr`, `search_facts`), компилятор по запросу `compile_context` / `list_communities` / `fresh_insights`, память сессий (`ask`, `list_sessions`, `find_session_findings`, `find_code_symbol_mentions`) и управляемую настройку (`tesserae_setup_plan` / `tesserae_setup_apply`). Полный список см. в [mcp.ru.md](mcp.ru.md).
* **Навык `using-tesserae`** — автозагрузка при запросах о типизированном графе, воспоминаниях из прошлых сессий, контенте wiki/vault или любых рабочих процессах tesserae. Учит агента, какой инструмент MCP использовать vs какую слэш-команду предложить.
* **5 хуков** — `SessionStart` печатает сводку графа; `SessionEnd` фоново выполняет import+compile, чтобы инсайты этого разговора стали узлами графа для следующей сессии; два хука `PostToolUse` срабатывают на `Edit`/`Write`/`MultiEdit` — один делает опциональную инкрементальную перекомпиляцию при правках в docs/, другой выполняет дебаунс (~30 с) синхронизации графа кода; `PreToolUse` (на `Bash`) шлюзует компиляцию большого графа диалогом подтверждения.

> **Компиляция при закрытии сессии — оппортунистическая, а не гарантированная.**
> Хук отсоединяет фоновую задачу через `setsid`, если он есть, и откатывается на
> `nohup`, если нет. В macOS `setsid` отсутствует, а `nohup` лишь игнорирует
> `SIGHUP` — задача остаётся в группе процессов сессии, — поэтому харнесс,
> который убирает группу при закрытии сессии, всё равно может убить компиляцию
> на середине. То, что при этом остаётся, — восстановимо, а не нетронуто:
> `graph.json` пишется атомарным rename и потому никогда не остаётся половиной
> файла, но генерируемые проекции `wiki/` и `site/` очищаются в начале записи
> артефактов, а
> хранилище SQLite пишется после `graph.json`, так что убийство в этом окне
> оставляет их отсутствующими или на одну компиляцию позади. Но молча это не
> происходит: `.tesserae/manifest.json` помечает документ как `graphed` только
> после того, как артефакты легли, поэтому следующая `compile --changed-only`
> откажется от no-op, сообщит `graph.json is not known to cover every tracked
> document` и заново извлечёт весь корпус, попутно пересобрав проекции.
> Не стройте рабочий процесс на допущении, что долгая компиляция переживёт
> сессию, которая её запустила, — запускайте её на переднем плане или через
> `tesserae engine`.

Полные детали, полные таблицы команд/хуков и инструкции по отказу для каждого проекта находятся в собственном [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md) плагина.

## Зачем плагин И сервер MCP?

Разные роли:

- **Инструменты MCP** = запросы графа только для чтения, которые агент вызывает во время разговора. Всегда включены, низкое трение.
- **Слэш-команды** = рабочие действия, которые вы явно вызываете (compile, refresh, obsidian-sync). Высокий рычаг, но должно быть вашим решением.

Можно использовать только сервер MCP (ручное редактирование `claude_desktop_config.json` через `tesserae projects mcp-config`). Плагин просто упаковывает его вместе со слэш-командами, навыком и хуками, делая установку одношаговой.

## Проверка установки

```
/plugin list
/mcp
/tesserae:status
```

## Удаление

```
/plugin uninstall tesserae
```

Обратимо. Не трогает каталог `.tesserae/` ни одного проекта.

## См. также

- [План реализации](../../superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [Спецификация дизайна](../../superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [Интеграция сессий](sessions.ru.md) — функция графа сессий, цикл которой замыкают хуки плагина
