# Граф сессий

<!-- translations:start -->
<p align="center"><a href="../../integrations/sessions.md">English</a> · <a href="sessions.ko.md">한국어</a> · <a href="sessions.zh.md">中文</a> · <a href="sessions.ja.md">日本語</a> · <a href="sessions.es.md">Español</a> · <a href="sessions.fr.md">Français</a> · <a href="sessions.de.md">Deutsch</a></p>
<!-- translations:end -->

Граф сессий Tesserae превращает ваши разговоры Claude Code / Codex о проекте в первоклассные узлы типизированного графа знаний, связанные с документами, которые упоминались. После компиляции вы можете спросить `tesserae project ask "что мы решили о 3D Gaussian Splatting?"` и получить конкретные узлы Insight / Decision / Question / TODO / Hypothesis / Takeaway с провенансом обратно к сессии, которая их произвела.

## Как это работает

Два прохода на сессию:

1. **Структурный** (всегда работает, без LLM). Читает нормализованные записи `HarnessSession`, которые `tesserae sessions discover --import` записывает в `.tesserae/harness_sessions/`. Для каждой сессии создает узел-конверт `Session`, испускает ребра `discussed_in` от каждого документа, который открыл агент, и превращает существующее поле `decisions` в узлы `SessionDecision`.
2. **LLM** (опционально, выполняется при настроенном `ANTHROPIC_API_KEY`). Отправляет нормализованные ходы разговора (поле `metadata["turns"]` — не исходный файл расшифровки) в Claude с JSON-только схемой находок. Возвращает шесть видов находок, каждая ссылается на конкретные ходы и конкретные ID узлов документов в текущем графе. Кэшируется по content_hash + project_root_hash, поэтому неизмененные сессии пропускают вызов при следующей компиляции.

## Настройка

```bash
# Импортируйте сессии для этого проекта в `.tesserae/harness_sessions/`. Фильтрует по cwd, поэтому импортируются только сессии, которые запускались внутри этого проекта.
tesserae sessions discover --import

# Компиляция. Структурный проход работает бесплатно; проход LLM запускается, когда установлен `ANTHROPIC_API_KEY`.
tesserae project compile
```

Чтобы скомпилировать без сессий (например, на сервере без какой-либо истории harness):

```bash
tesserae project compile --no-sessions
```

Чтобы принудительно использовать только структурный (пропустить вызов LLM, даже когда ключ установлен):

```bash
tesserae project compile --sessions-llm=false
```

## Конфигурация

`.tesserae/config.json` принимает блок `sessions`:

```jsonc
{
  "sessions": {
    "enabled": true,
    "llm_enabled": "auto",
    "max_turns_per_chunk": 30,
    "model": "claude-sonnet-4-7-20251201",
    "include_doc_id_context": 200
  }
}
```

Флаги CLI переопределяют конфигурацию. `llm_enabled = "auto"` (по умолчанию) запускает проход LLM только при настроенном бэкенде; без него выполняется только структурный проход (без ошибки, без исходящих вызовов).

## Запросы

Два инструмента MCP добавляются поверх существующих инструментов поиска/wiki:

* `list_sessions(since?, limit?)` — конверты Session для активного проекта (id, started_at, title, количество находок).
* `find_session_findings(node_id, kinds?)` — каждая находка, производная от сессии, связанная с `node_id` через `discussed_in` или `references`, опционально отфильтрованная до insight / decision / question / todo / hypothesis / takeaway.

Из CLI:

```bash
tesserae sessions list
tesserae project ask "what did we decide about extractor dedup?"
```

## Конфиденциальность

* Без `ANTHROPIC_API_KEY` (или с `--sessions-llm=false`) исходящих сетевых вызовов нет. Выполняется только структурный проход.
* Когда выполняется проход LLM, отправляются **полные нормализованные ходы разговора** для еще не кэшированных сессий. Сам файл расшифровки остается на диске; только JSON-вывод LLM сохраняется в графе и кэше каждой сессии.
* Файлы кэша находятся в `.tesserae/session_findings/<session_id>.findings.json` с `content_hash` и `project_root_hash`. Файл кэша, скопированный между проектами, отклоняется при чтении — нет межпроектного воспроизведения.
* Сессии фильтруются через `session_matches_project` после загрузки, поэтому расшифровка, чей `cwd` был соседним проектом, никогда не производит узлы в графе этого проекта.

## Структура хранилища

Находки отображаются под хранилищем Obsidian как одна страница на находку, сгруппированные по сессии:

```
<vault>/
  sessions/
    <session-id-slug>/
      cache-findings-by-content-hash.md
      path-index-needs-basename-suppression.md
      ...
```

Пользовательские заметки внутри блока `<!-- user-notes:start -->` … `<!-- user-notes:end -->` на любой странице находки переживают перекомпиляцию — тот же контракт, что и у каждой другой страницы хранилища.

## Устранение неполадок

* **После компиляции не появляются узлы Session.** Запускали ли вы сначала `tesserae sessions discover --import`? Путь компиляции потребляет только `.tesserae/harness_sessions/`; он НЕ сканирует `~/.claude/projects/` автоматически (это сканирование может занять минуты на машинах с тысячами исторических сессий).
* **Опасения по поводу стоимости LLM.** Кэш означает, что каждая сессия отправляется в LLM не более одного раза на content-hash. Длинные сессии разбиваются на куски при `max_turns_per_chunk` (по умолчанию 30) с 5-ходовым перекрытием. Чтобы ограничить общую стоимость, уменьшите `max_turns_per_chunk`, уменьшите `include_doc_id_context` или установите `--sessions-llm=false`.
* **Находка цитирует несуществующий ID узла.** Оркестратор проверяет каждую цитируемую ссылку на живом графе документов и молча отбрасывает неизвестные. Если вы видите предупреждение в логах, LLM галлюцинировал цитату — выжившие ссылки все еще заслуживают доверия.

## Спецификация

Полный дизайн находится в [docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md](../../superpowers/specs/2026-05-19-session-graph-extractor-design.md). План реализации — [docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md](../../superpowers/plans/2026-05-19-session-graph-extractor-plan.md).
