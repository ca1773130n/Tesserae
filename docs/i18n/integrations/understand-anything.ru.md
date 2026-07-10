# Сопутствующий рабочий процесс Understand Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a> · <a href="understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) и Tesserae — взаимодополняющие проекты.

- Understand Anything отлично производит граф знаний кодовой базы и интерактивный дашборд.
- Tesserae сфокусирован на долгоживущей памяти агентов: документы, компиляция markdown/wiki, статическая публикация, история сессий и агентские экспорты.

Tesserae не должен вендорить или поглощать Understand Anything. Относитесь к нему как к независимому компаньону, способному производить полезные графовые артефакты.

## Зачем оба?

Understand Anything может записать:

```text
.understand-anything/knowledge-graph.json
```

Этот граф захватывает структуру кода: файлы, функции, классы, модули, концепты, зависимости, слои и туры.

Tesserae затем может сохранить этот артефакт рядом с остальной памятью проекта:

- исходные документы и markdown-страницы;
- файлы репозитория;
- исследовательские заметки;
- локальная история сессий Claude Code / Codex;
- генерируемые статические wiki-страницы;
- 2D / 3D виды графа на сайте;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json` и per-page агентские siblings.

## Текущий низкофрикционный рабочий процесс

Рекомендуемый путь — визард настройки:

```bash
tesserae init
```

Выберите Understand Anything на шаге companion-инструментов (он **выключен по умолчанию** — его обновление запускает удалённый install-скрипт). Tesserae записывает управляемую команду refresh в `.tesserae/config.json` под `external_tools`. Авто-обновление при компиляции тоже выключено по умолчанию (`auto_refresh: false`); установите `true`, если хотите, чтобы `tesserae compile` запускал обёртку автоматически, когда UA-граф отсутствует или устарел.

Для неинтерактивной автоматизации запустите `tesserae init --yes` (интеграции ВЫКЛ), включите Understand Anything в `.tesserae/config.json`, затем:

```bash
tesserae integrations refresh understand-anything --platform codex
tesserae compile
```

Сохранённая команда принадлежит Tesserae, а не является чем-то, что пользователь должен придумать:

```bash
tesserae integrations refresh understand-anything --platform codex
```

Во время компиляции Tesserae:

1. проверяет, существует ли `.understand-anything/knowledge-graph.json` и совпадает ли он с текущим git-коммитом, когда метаданные доступны;
2. запускает настроенную агентскую платформу (`codex`, `opencode` или `claude`) только когда её запись `external_tools` имеет `auto_refresh: true` и граф отсутствует/устарел, либо обновление принудительно;
3. проверяет, что граф был записан;
4. материализует `.tesserae/external/understand-anything.md`;
5. продолжает обычную компиляцию памяти.

Можно принудить все настроенные внешние refresh-команды перед компиляцией:

```bash
tesserae compile --refresh-integrations
```

Нужен ещё и Cognee? Cognee тоже opt-in: установите его через `pip install tesserae[cognee]` и задайте `memory_backends.cognee.enabled: true` в `.tesserae/config.json` (запрашивайте явно через `tesserae query --backend cognee`).

## Ручной эквивалент

Управляемый путь настройки предпочтителен. Если вы намеренно хотите использовать UA вне Tesserae, сначала запустите Understand Anything внутри вашего агентского окружения:

```bash
/understand
```

Затем запустите визард настройки и **включите Understand Anything, когда
спросят**, чтобы Tesserae записал источник markdown-проекции. Прямые JSON-файлы
хранятся как сырые companion-артефакты, а не пути источников, введённые
вручную.

```bash
tesserae init
# enable Understand Anything when the wizard prompts
tesserae compile
tesserae export site
```

Для неинтерактивной автоматизации запустите `tesserae init --yes` (интеграции
ВЫКЛ), включите Understand Anything в `.tesserae/config.json` (визард пишет
интеграцию под ключом `external_tools`), затем `tesserae integrations
refresh understand-anything` перед компиляцией.

Если также нужна память локальных агентских сессий:

```bash
tesserae sessions discover --import
tesserae export site
```

## Нативная синхронизация графа

Tesserae теперь хранит markdown-проекцию для читаемости и также импортирует UA-граф нативно во время компиляции, когда настроенный инструмент использует `sync_mode: native_graph`.

Нативный адаптер читает `.understand-anything/knowledge-graph.json`, отображает узлы/рёбра UA в контролируемую онтологию Tesserae и пишет манифест синхронизации:

```text
.tesserae/external/understand-anything-sync.json
```

Текущее отображение:

| Understand Anything | Направление Tesserae |
|---|---|
| `project` | метаданные репозитория/проекта |
| `nodes[type=file]` | узлы `SourceFile` |
| `nodes[type=function]` / `method` | узлы `CodeFunction` |
| `nodes[type=class]` / `component` | узлы `CodeClass` |
| `nodes[type=module]` / `package` | узлы `CodeModule` |
| `nodes[type=concept]` / `topic` | канонические узлы `Concept` |
| `nodes[type=feature]` / `capability` | узлы `Capability` |
| `edges[type=imports]` | рёбра `imports` |
| `edges[type=contains]` | рёбра `contains` |
| `edges[type=calls]` | рёбра `calls` |
| неизвестные типы рёбер | `shares_concept_with` с метаданными `ua_edge_type` |

Синхронизация концептов канонизируется, а не слепо дублируется. Если UA эмитирует `Mermaid Rendering`, а у Tesserae уже есть `Mermaid rendering`, компиляция сохраняет один узел концепта и добавляет UA-provenance под `metadata.external_refs`.

Tesserae остаётся компилятором памяти; UA остаётся независимым генератором companion-графа.

## Принцип сотрудничества

Не подавайте Tesserae как замену Understand Anything.

Лучший фрейминг:

- Understand Anything помогает разработчику понять кодовую базу сейчас.
- Tesserae помогает агентам помнить, искать, цитировать, обновлять и публиковать знания проекта во времени.
