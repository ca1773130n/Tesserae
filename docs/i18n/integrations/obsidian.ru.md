# Obsidian — открытие скомпилированной wiki как настоящего vault

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.ko.md">한국어</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.fr.md">Français</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

Экспорт Obsidian в Tesserae превращает ваш скомпилированный типизированный граф в настоящий, «с характером» vault [Obsidian](https://obsidian.md). Не каталог markdown — а vault с конфигом `.obsidian/`, типо-осведомлёнными [callout-ами](https://help.obsidian.md/Editing+and+formatting/Callouts), фронтматтером, запрашиваемым через [Dataview](https://blacksmithgu.github.io/obsidian-dataview/), дашбордом vault и индексом межхранилищных ссылок `wiki://`.

## Предварительные условия

Сначала скомпилируйте проект:

```bash
cd /path/to/your-project
tesserae init
tesserae compile
```

Компиляция производит `.tesserae/graph.json` (источник истины) и простую markdown-проекцию в `.tesserae/markdown_projection/`. Экспорт Obsidian строится поверх этой проекции, но накладывает Obsidian-нативные обогащения на каждую страницу.

## 1) Экспортируйте vault

```bash
tesserae vault export --output ~/Documents/tesserae-vault
```

Каталог создаётся, если не существует. Повторный запуск перезаписывает его идемпотентно — markdown-проекция детерминирована при том же графе.

Что ложится на диск:

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

Obsidian обнаружит `.obsidian/`, распознает его как настоящий vault и загрузится. Список community-плагинов включает Dataview, поэтому Obsidian предложит включить его (рекомендуется — без него dataview-блоки рендерятся как code-фенсы).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) Тур по vault

### Точки входа

- `README.md` — что такое этот vault и как его обновлять
- `index.md` — каждый узел по секциям (papers, concepts, claims) с wikilink-ами
- `_meta/dashboard.md` — dataview-обзор: недавние страницы, статьи, концепты/утверждения

### Обогащения каждой страницы

Каждая страница узла теперь поставляется с:

**Типо-осведомлённые callout-ы.** Семантический callout наверху каждой страницы делает тип узла видимым с одного взгляда:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

Отображение (основное): `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Рёбра, запрашиваемые через Dataview.** Фронтматтер теперь несёт типизированные рёбра как вложенные карты:

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

**Межхранилищные мосты.** Любой URI `wiki://<alias>/<kind>/<slug>`, упомянутый в описании или метаданных узла, отображается и как поле фронтматтера:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

и как секция тела `Cross-vault references`. Индекс уровня vault `_bridges.md` агрегирует каждую исходящую ссылку, сгруппированную по alias назначения, так что аудит межхранилищных ссылок делается с одной страницы.

**Блок Related (dataview).** Каждая страница заканчивается запросом, показывающим страницы со ссылками обратно, заполняемым автоматически:

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

`_meta/dashboard.md` поставляется с dataview-блоками для самых полезных агрегированных видов: недавно обновлённые страницы, все статьи с колонками метаданных, все концепты и утверждения, отсортированные по типу. Редактируйте свободно — это отправная точка, а не фиксированный контракт.

### Вид графа vault

Встроенный вид графа Obsidian (`Ctrl/Cmd+G`) уже работает по wikilink-ам, эмитируемым в секциях `## Outgoing` / `## Incoming`. Предпоставляемый `.obsidian/graph.json` цветокодирует пути `papers/`, `concepts/`, `claims/` для ориентации. Сверху можно наслаивать dataview-фильтрованные виды для более богатых срезов.

## Межхранилищные рабочие процессы

Зарегистрируйте несколько vault-ов Tesserae, чтобы URI `wiki://` разрешались между ними:

```bash
tesserae projects register /path/to/research --name research
tesserae projects register /path/to/notes    --name notes
```

Переэкспортируйте каждый vault после регистрации. `_bridges.md` в каждом экспорте теперь покажет разрешаемые ссылки между vault-ами, сгруппированные по alias.

Сам Obsidian не переходит по URI `wiki://` нативно — они рендерятся как inline-текст — но `_bridges.md` плюс секция `Cross-vault references` на каждой странице дают ручной индекс, пока не появится специальный Obsidian-плагин.

## Рабочий процесс обновления

Чтобы включить новые источники или исправления из ваших исходных файлов:

```bash
# Edit source files under your project's source dirs, then:
tesserae compile
```

`compile` перепроецирует vault автоматически — отдельный шаг экспорта больше не нужен. (`tesserae vault export --output <path>` по-прежнему существует для одноразовой перепроекции без полной перекомпиляции.) Obsidian горячо перезагружает изменённые файлы на диске.

Если вы добавили внутри vault markdown-заметки, не проецируемые из графа (например, ваши личные аннотации), они выживают — проектор перезаписывает только файлы, которыми владеет, под `papers/`, `concepts/`, `claims/`, плюс `index.md`, `_bridges.md`, `_meta/dashboard.md` и `README.md`. Написанные вручную страницы (без фронтматтера `node_id:`) и выделенный блок пользовательских заметок (`<!-- user-notes:start -->` … `<!-- user-notes:end -->`) на каждой проецируемой странице сохраняются между перекомпиляциями.

### Правки в Obsidian текут обратно (двусторонняя синхронизация)

Начиная с v0.5.0 vault **больше не односторонний экспорт**. Это *двусторонняя проекция*: типизированный граф по-прежнему источник истины, но `project compile` теперь считывает ваши Obsidian-правки обратно из vault и накладывает их на граф **до** перепроекции. Отредактируйте `title` узла, `aliases`, callout описания или любой несистемный скаляр фронтматтера в Obsidian, перекомпилируйте — и изменение выживает, распространяясь на статический сайт, MCP и любую другую проекцию.

```bash
tesserae compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

Что собирает оверлей (поля *vault-wins*):

- `title` → `name` узла
- `aliases` → псевдонимы узла
- callout описания в теле (или первый абзац) → `description` узла
- каждый незарезервированный скаляр фронтматтера → `metadata.<key>` (зарезервированные/системные ключи `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault` никогда не трактуются как пользовательские переопределения)

Каждый прогон оверлея записывает отчёт `.tesserae/diverged-fields.md` (`## Field overrides — N across M node(s)`), чтобы можно было проверить, что именно было подтянуто обратно. Wikilink-и, добавленные внутри блока пользовательских заметок, становятся рёбрами `user_link`. Запустите `tesserae compile` (с `compile_options.no_vault_pull = true` в `.tesserae/config.json`), чтобы обойти оверлей на один прогон — полезно для восстановления или когда вы намеренно хотите, чтобы победил исходный markdown.

Первая компиляция после включения этой фичи получает «бесплатный проход»: пока нет базового `vault_snapshot.json`, ничего не собирается; снапшот, записанный в конце, становится базой для диффа следующей компиляции.

Для выделенного живого рабочего процесса `tesserae vault sync` повторно применяет оверлей и перепроецирует без полной перекомпиляции:

```bash
# Preview what a compile would pull back, without mutating the graph.
tesserae vault sync --dry-run

# Watch the vault and round-trip edits live (Ctrl-C to stop).
tesserae vault sync --watch

# After renaming/removing nodes, delete projected pages left orphaned.
tesserae vault sync --prune-orphans
```

Полную матрицу владения полями и обоснование дизайна см. в [obsidian-sync.md](obsidian-sync.ru.md).

## Когда использовать это вместо статического сайта

Скомпилированный HTML-сайт (`tesserae export site` → `.tesserae/site/`) — односторонний, только для чтения экспорт для шаринга — пушьте на GitHub Pages, S3, любой статический хостинг. Obsidian vault — для **чтения, запросов и редактирования** локально с Dataview и видом графа Obsidian: это единственная проекция, чьи правки текут обратно в граф (см. секцию двусторонней синхронизации выше). Оба проецируются из одного графа, поэтому они никогда не расходятся — а исправления, сделанные в Obsidian, распространяются на сайт при следующей компиляции.
