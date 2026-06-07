# Obsidian — открытие скомпилированной wiki как настоящего vault

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.ko.md">한국어</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.fr.md">Français</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

Экспорт Tesserae в Obsidian превращает ваш скомпилированный типизированный граф в настоящий, заранее продуманный vault [Obsidian](https://obsidian.md). Не каталог markdown-файлов — а vault с конфигом `.obsidian/`, типозависимыми [callouts](https://help.obsidian.md/Editing+and+formatting/Callouts), frontmatter, запрашиваемым через [Dataview](https://blacksmithgu.github.io/obsidian-dataview/), дашбордом vault и индексом межхранилищных ссылок `wiki://`.

## Предварительные требования

Сначала скомпилируйте проект:

```bash
cd /path/to/your-project
tesserae project setup
tesserae project compile
```

Компиляция создаёт `.tesserae/graph.json` (источник истины) и обычную markdown-проекцию в `.tesserae/markdown_projection/`. Экспорт Obsidian строится поверх этой проекции, но накладывает на каждую страницу нативные для Obsidian обогащения.

## 1) Экспортируйте vault

```bash
tesserae project export-obsidian --vault ~/Documents/tesserae-vault
```

Каталог создаётся, если его нет. Повторный запуск идемпотентно перезаписывает его — markdown-проекция детерминирована при том же графе.

Что появляется на диске:

```text
tesserae-vault/
  .obsidian/                  # Obsidian config (app.json, graph.json, plugins)
  README.md                   # Vault entry point
  index.md                    # All nodes grouped by section
  _bridges.md                 # Cross-vault wiki:// references, grouped by alias
  _meta/
    dashboard.md              # Dataview overview tables
  papers/                     # Paper / Repository / SourceDocument pages
  concepts/                   # Concept / Topic / Field / Method / Algorithm pages
  claims/                     # Claim / OpenQuestion / Evidence pages
  raw/                        # Optional raw-source attachments (created lazily)
```

## 2) Откройте каталог в Obsidian

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`.

Obsidian обнаружит `.obsidian/`, распознает его как настоящий vault и загрузит. Список community-плагинов включает Dataview, поэтому Obsidian предложит его включить (рекомендуется — без него блоки dataview рендерятся как code fences).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) Экскурсия по vault

### Точки входа

- `README.md` — что это за vault и как его обновлять
- `index.md` — все узлы по секциям (papers, concepts, claims) с wikilinks
- `_meta/dashboard.md` — обзор через dataview: свежие страницы, papers, concepts/claims

### Обогащения на каждой странице

Каждая страница узла теперь содержит:

**Типозависимые callouts.** Семантический callout в верхней части каждой страницы делает тип узла видимым с первого взгляда:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

Сопоставление (ключевое): `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Рёбра, запрашиваемые через Dataview.** Frontmatter теперь содержит типизированные рёбра в виде вложенных map:

```yaml
edges_out:
  uses: [gaussian-splatting, volumetric-rendering]
  part_of: [3d-4d-vision-and-reconstruction]
  supports_claim: [performance-claim-..., comparison-...]
edges_in:
  mentioned_in: [project-pulse, topic-visual-slam]
```

Можно писать запросы вроде:

````markdown
```dataview
LIST FROM "papers" WHERE contains(edges_out.uses, "nerf")
```

```dataview
TABLE edges_out.supports_claim AS "Claims"
FROM "papers"
WHERE length(edges_out.supports_claim) > 3
SORT length(edges_out.supports_claim) DESC
LIMIT 10
```
````

**Межхранилищные мосты.** Любой URI `wiki://<alias>/<kind>/<slug>`, упомянутый в описании или метаданных узла, выводится и как поле frontmatter:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

и как секция `Cross-vault references` в теле страницы. Индекс уровня vault `_bridges.md` агрегирует каждую исходящую ссылку, сгруппированную по алиасу назначения, чтобы вы могли аудировать межхранилищные ссылки с одной страницы.

**Блок Related (dataview).** Каждая страница заканчивается запросом, который автоматически показывает страницы, ссылающиеся на неё:

````markdown
```dataview
LIST
FROM "papers" OR "concepts" OR "claims"
WHERE contains(file.outlinks, this.file.link) AND file.name != this.file.name
SORT file.name
LIMIT 25
```
````

### Дашборд vault

`_meta/dashboard.md` содержит блоки dataview для наиболее полезных агрегированных представлений: недавно обновлённые страницы, все papers со столбцами метаданных, все concepts и claims, отсортированные по типу. Редактируйте его свободно — это отправная точка, а не фиксированный контракт.

### Граф vault

Встроенный graph view Obsidian (`Ctrl/Cmd+G`) уже работает с wikilinks, выводимыми в секциях `## Outgoing` / `## Incoming`. Предпоставляемый `.obsidian/graph.json` подкрашивает пути `papers/`, `concepts/`, `claims/` для ориентации. Поверх можно накладывать представления с dataview-фильтрами для более точных срезов.

## Межхранилищные рабочие процессы

Зарегистрируйте несколько vault Tesserae, чтобы URI `wiki://` разрешались между ними:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

После регистрации заново экспортируйте каждый vault. `_bridges.md` в каждом экспорте теперь будет показывать разрешимые ссылки между vault, сгруппированные по алиасу.

Сам Obsidian не переходит по URI `wiki://` нативно — они рендерятся как обычный текст — но `_bridges.md` плюс секция `Cross-vault references` на каждой странице дают вам ручной индекс до тех пор, пока не появится специализированный плагин Obsidian.

## Рабочий процесс обновления

Чтобы включить новые источники или исправления из ваших исходных файлов:

```bash
# Отредактируйте исходные файлы в каталогах источников проекта, затем:
tesserae project compile
```

`compile` теперь повторно проецирует vault автоматически — отдельный шаг экспорта больше не нужен. (`tesserae project export-obsidian --vault <путь>` по-прежнему существует для разовой повторной проекции без полной перекомпиляции.) Obsidian горячо перезагрузит изменённые файлы на диске.

Если вы добавили внутри vault markdown-заметки, не спроецированные из графа (например, ваши личные аннотации), они сохранятся — проектор перезаписывает только файлы, которыми владеет, под `papers/`, `concepts/`, `claims/`, плюс `index.md`, `_bridges.md`, `_meta/dashboard.md` и `README.md`. Написанные вручную страницы (без frontmatter-ключа `node_id:`) и отдельный блок пользовательских заметок (`<!-- user-notes:start -->` … `<!-- user-notes:end -->`) на каждой спроецированной странице сохраняются между перекомпиляциями.

### Правки в Obsidian возвращаются в граф (двусторонняя синхронизация)

Начиная с v0.5.0, vault — **уже не односторонний экспорт**. Теперь это *двусторонняя проекция*: типизированный граф по-прежнему источник истины, но `project compile` теперь считывает ваши правки из vault и накладывает их на граф **перед** повторной проекцией. Отредактируйте у узла `title`, `aliases`, выноску с описанием или любой несистемный frontmatter-скаляр в Obsidian, перекомпилируйте — и изменение сохранится и распространится на статический сайт, MCP и все прочие проекции.

```bash
tesserae project compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

Что собирает наложение (поля, где *vault побеждает*):

- `title` → `name` узла
- `aliases` → псевдонимы узла
- выноска с описанием в теле (или первый абзац) → `description` узла
- каждый незарезервированный frontmatter-скаляр → `metadata.<key>` (зарезервированные/системные ключи `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault` никогда не считаются пользовательскими переопределениями)

Каждый прогон наложения пишет отчёт `.tesserae/diverged-fields.md` (`## Field overrides — N across M node(s)`), чтобы вы могли точно проверить, что именно было возвращено. Вики-ссылки, добавленные внутри блока пользовательских заметок, становятся рёбрами `user_link`. Передайте `tesserae project compile --no-vault-pull`, чтобы обойти наложение в одном прогоне — это полезно при восстановлении или когда вы намеренно хотите, чтобы исходный markdown победил.

Первая компиляция после включения этой функции получает «бесплатный проход»: пока нет базиса `vault_snapshot.json`, ничего не собирается; снимок, записанный в конце, становится базисом для diff следующей компиляции.

Для отдельного «живого» рабочего процесса `tesserae project obsidian-sync` повторно применяет наложение и проецирует заново без полной перекомпиляции:

```bash
# Предпросмотр того, что компиляция вернула бы обратно, не изменяя граф.
tesserae project obsidian-sync --dry-run

# Следить за vault и переносить правды туда-обратно в реальном времени (Ctrl-C для остановки).
tesserae project obsidian-sync --watch

# После переименования/удаления узлов удалить осиротевшие спроецированные страницы.
tesserae project obsidian-sync --prune-orphans
```

Полную матрицу владения по полям и обоснование дизайна см. в [obsidian-sync.md](obsidian-sync.md).

## Когда использовать это, а когда статический сайт

Скомпилированный HTML-сайт (`tesserae project build-site` → `.tesserae/site/`) — это односторонний экспорт только для чтения, для шеринга: публикуйте на GitHub Pages, S3, любой статический хост. Vault Obsidian — для **чтения, запросов и редактирования** локально через Dataview и graph view Obsidian: это единственная проекция, чьи правки возвращаются в граф (см. раздел о двусторонней синхронизации выше). Оба проецируются из одного графа, поэтому никогда не расходятся — а исправления, внесённые в Obsidian, распространяются на сайт при следующей компиляции.
