# Сопутствующий рабочий процесс Understand Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a> · <a href="understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) и Tesserae — взаимодополняющие проекты.

- Understand Anything хорошо создает граф знаний кодовой базы и интерактивную панель.
- Tesserae сосредоточен на долговременной памяти агентов: документах, компиляции markdown/wiki, статической публикации, истории сессий и экспортах для агентов.

Tesserae не должен встраивать или поглощать Understand Anything. Рассматривайте его как независимый сопутствующий инструмент, который может создавать полезные графовые артефакты.

## Зачем использовать оба?

Understand Anything может записывать:

```text
.understand-anything/knowledge-graph.json
```

Этот граф фиксирует структуру кода: файлы, функции, классы, модули, концепции, зависимости, слои и туры.

Затем Tesserae может сохранить этот артефакт вместе с остальной памятью проекта:

- исходные документы и markdown-страницы;
- файлы репозитория;
- исследовательские заметки;
- локальную историю сессий Claude Code / Codex;
- сгенерированные статические wiki-страницы;
- 2D / 3D представления сайта графа;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json` и агентские sibling-файлы для каждой страницы.

## Текущий рабочий процесс с низким трением

Рекомендуемый путь — мастер настройки:

```bash
tesserae init
```

Выберите Understand Anything на шаге сопутствующих инструментов. Tesserae установит/обновит сопутствующие skills по запросу и запишет управляемую команду обновления в `.tesserae/config.json`. Последующие вызовы `tesserae compile` будут автоматически запускать эту обертку, когда граф UA отсутствует или устарел.

Для неинтерактивной автоматизации используйте:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
tesserae compile
```

Сохраненная команда принадлежит Tesserae, а не является тем, что пользователь должен придумать сам:

```bash
tesserae integrations refresh understand-anything --platform codex
```

Во время компиляции Tesserae:

1. проверяет, существует ли `.understand-anything/knowledge-graph.json` и совпадает ли он с текущим git-коммитом, когда доступны метаданные;
2. запускает настроенную агентскую платформу (`codex`, `opencode` или `claude`) только когда граф отсутствует/устарел или обновление принудительное;
3. проверяет, что граф был записан;
4. материализует `.tesserae/external/understand-anything.md`;
5. продолжает обычную компиляцию памяти.

Можно принудительно выполнить все настроенные внешние команды обновления перед компиляцией:

```bash
tesserae compile --refresh-integrations
```

Нужен также Cognee? Добавьте флаги runtime-памяти в ту же команду setup:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## Ручной эквивалент

Предпочтителен управляемый путь настройки. Если вы намеренно хотите использовать UA вне Tesserae, сначала запустите Understand Anything в вашей агентской среде:

```bash
/understand
```

Затем запустите мастер настройки и **включите Understand Anything по
соответствующему запросу**, чтобы Tesserae записал источник markdown-проекции.
Прямые JSON-файлы сохраняются как сырые сопутствующие артефакты, а не как
вручную введенные пути источников.

```bash
tesserae init
# включите Understand Anything, когда мастер спросит
tesserae compile
tesserae export site
```

Для неинтерактивной автоматизации выполните `tesserae init --yes` (интеграции
ВЫКЛЮЧЕНЫ), включите Understand Anything в `.tesserae/config.json` (мастер
записывает интеграцию под ключом `external_tools`), затем выполните `tesserae
integrations refresh understand-anything` перед компиляцией.

Если также нужна локальная память агентских сессий:

```bash
tesserae sessions discover --import
tesserae export site
```

## Нативная синхронизация графа

Tesserae по-прежнему сохраняет markdown projection для читаемости, но также нативно импортирует граф UA во время compile, когда настроенный инструмент использует `sync_mode: native_graph`.

Нативный адаптер читает `.understand-anything/knowledge-graph.json`, сопоставляет узлы/ребра UA с контролируемой ontology Tesserae и записывает sync manifest:

```text
.tesserae/external/understand-anything-sync.json
```

Текущее сопоставление:

| Understand Anything | Направление Tesserae |
|---|---|
| `project` | repository/project metadata |
| `nodes[type=file]` | `SourceFile` nodes |
| `nodes[type=function]` / `method` | `CodeFunction` nodes |
| `nodes[type=class]` / `component` | `CodeClass` nodes |
| `nodes[type=module]` / `package` | `CodeModule` nodes |
| `nodes[type=concept]` / `topic` | canonical `Concept` nodes |
| `nodes[type=feature]` / `capability` | `Capability` nodes |
| `edges[type=imports]` | `imports` edges |
| `edges[type=contains]` | `contains` edges |
| `edges[type=calls]` | `calls` edges |
| unknown edge types | `shares_concept_with` с metadata `ua_edge_type` |

Concept synchronization выполняет canonicalization, а не создает дубликаты вслепую. Если UA выдает `Mermaid Rendering`, а в Tesserae уже есть `Mermaid rendering`, compile сохраняет один concept node и добавляет UA provenance в `metadata.external_refs`.

Tesserae остается memory compiler; UA остается независимым companion graph generator.

## Принцип сотрудничества

Не представляйте Tesserae как замену Understand Anything.

Лучшее позиционирование:

- Understand Anything помогает разработчику понять кодовую базу сейчас.
- Tesserae помогает агентам со временем помнить, искать, цитировать, обновлять и публиковать знания проекта.
