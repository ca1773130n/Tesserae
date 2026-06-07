# Understand-Anything: режим только code-graph

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything-code-only.md">English</a> · <a href="understand-anything-code-only.ko.md">한국어</a> · <a href="understand-anything-code-only.zh.md">中文</a> · <a href="understand-anything-code-only.ja.md">日本語</a> · <a href="understand-anything-code-only.es.md">Español</a> · <a href="understand-anything-code-only.fr.md">Français</a> · <a href="understand-anything-code-only.de.md">Deutsch</a></p>
<!-- translations:end -->

Это продолжение [understand-anything.md](../../integrations/understand-anything.md). Базовый документ объясняет, как установить и включить [Understand-Anything](https://github.com/Lum1104/Understand-Anything) (UA) как сопутствующий инструмент, который создает code-graph в `.understand-anything/knowledge-graph.json`. **Этот документ объясняет, как заставить UA вносить ТОЛЬКО code graph и никогда не загрязнять слой Concept research-graph Tesserae заголовками разделов, извлеченными из ваших документов.**

Если вы когда-либо открывали типизированный граф после включения UA и обнаруживали слой Concept, заполненный записями вроде `'Quickstart'`, `'2) Paste it into your MCP client'` или одним и тем же заголовком на семи языках, — вы столкнулись с проблемой, которую решает этот документ.

## Почему так происходит

Два слоя одной и той же ошибки накладываются друг на друга:

1. **UA обходит ваши документы по умолчанию.** Из коробки source loader UA обходит каждый читаемый файл под корнем вашего проекта — включая `docs/`, `docs/i18n/`, README на каждом языке и т. д. Для каждого markdown-заголовка он записывает узел в `.understand-anything/knowledge-graph.json` с текстом заголовка в качестве имени сущности.
2. **Tesserae нативно сливает весь граф UA.** Когда `external_tools` содержит UA с `sync_mode: "native_graph"`, `ProjectWiki._merge_configured_understand_anything_graph()` читает артефакт и импортирует каждый узел UA в research graph как `Concept`. Намерение UA «это символ кода» сводится к «это исследовательский concept», а ваши узлы-заголовки документов едут вместе с ним.

Итог: каждый переведенный заголовок появляется как дублирующий Concept (`'Setup'`, `'설정'`, `'安装'`, `'インストール'`, `'Установка'`, `'Configuración'`, `'Configuration'`, `'Einrichtung'`), создавая коллизии slug, которые проектор переименовывает в `setup-2.md`, `setup-3.md`, …, `setup-7.md`.

> [!warning] Вы узнаете это, когда увидите
> Проверка симптомов на проекте, где это произошло:
> ```bash
> .venv/bin/python -c "
> import json
> from collections import Counter
> nodes = json.load(open('.tesserae/graph.json'))['nodes']
> srcs = Counter(n.get('source_path','') for n in nodes if n['type']=='Concept')
> print(srcs.most_common(3))
> "
> ```
> Если верхний источник — `.understand-anything/knowledge-graph.json` с сотнями узлов Concept, то каждый имеющийся у вас переведенный заголовок импортируется как отдельный concept.

## Исправление в три шага

### Шаг 1 — запретите стороне Tesserae импортировать UA как Concept'ы

Отредактируйте `.tesserae/config.json` и установите одновременно `enabled: false` и `sync_mode: "disabled"` в записи инструмента UA. Оба флага «с подстраховкой» проверяются в коде слияния:

```jsonc
{
  "external_tools": [
    {
      "id": "understand-anything",
      "enabled": false,            // ← было true
      "sync_mode": "disabled",     // ← было "native_graph"
      "auto_refresh": false,       // опционально: прекратить обновлять UA при каждой компиляции
      // ...остальная часть записи остается как есть
    }
  ]
}
```

`enabled: false` заставляет `_merge_configured_understand_anything_graph()` полностью пропускать инструмент. `sync_mode: "disabled"` — вторичный предохранитель на случай, если будущий баг проигнорирует флаг `enabled`.

### Шаг 2 — удалите устаревшие артефакты, чтобы ничего не осталось

Если вы ранее запускали компиляцию с включенным UA, загрязненные артефакты все еще лежат на диске:

```bash
rm -f .understand-anything/knowledge-graph.json
rm -f .tesserae/external/understand-anything.md
```

Tesserae регенерирует `.tesserae/external/understand-anything.md` только когда инструмент включен, поэтому удалять его безопасно после выполнения шага 1.

### Шаг 3 — перекомпилируйте и подрежьте Obsidian vault

```bash
tesserae compile
tesserae vault sync --prune-orphans
```

Компиляция пропустит слияние UA, оставив research graph без Concept'ов, источником которых является UA. Шаг prune удаляет любые сиротские страницы в Obsidian vault, которые ссылались на node_id, созданные слиянием.

## Проверка

После перекомпиляции скрипт аудита выше должен сообщить о нуле (или близком к нулю) количестве узлов Concept, источником которых является `.understand-anything/knowledge-graph.json`. Еще одна полезная проверка:

```bash
.venv/bin/python -c "
import json, re
from collections import defaultdict
nodes = json.load(open('.tesserae/graph.json'))['nodes']
concepts = [n for n in nodes if n['type']=='Concept']
def slug(s): return re.sub(r'[^a-z0-9가-힣]+','-',s.lower()).strip('-')
buckets = defaultdict(list)
for n in concepts: buckets[slug(n['name'])].append(n)
collisions = {s: ns for s, ns in buckets.items() if len(ns)>1}
print(f'{len(collisions)} Concept slug collision(s), {sum(len(ns)-1 for ns in collisions.values())} duplicate page(s)')
"
```

Должен напечатать `0 Concept slug collision(s), 0 duplicate page(s)`, если исправление сработало.

## Когда вы действительно хотите вернуть навигацию по code graph

Code graph UA по-настоящему полезен — рёбра call/import, иерархии классов и т. д. — когда он не утопает в шуме от заголовков документов. Чтобы корректно включить его снова:

1. **Ограничьте сам UA кодом, а не документами.** UA принимает include/exclude паттерны; настройте его обходить только `src/`, `lib/`, `tesserae/` и т. д. и явно исключить `docs/`, `README*.md` и `docs/i18n/`. Точный конфигурационный параметр описан в собственной документации UA по адресу [Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything).
2. **Включите снова в `.tesserae/config.json`**: верните `enabled` в `true`, `sync_mode` в `"native_graph"`, `auto_refresh` в `true`.
3. **Перекомпилируйте** и снова запустите аудит. Чистый прогон UA должен производить Concept'ы, которые отображаются на реальные символы кода (имена функций, имена классов, модули), а не на заголовки разделов на английском.

Эта асимметрия неприятна — отключение это один переключатель конфигурации, а чистое повторное включение требует понимания source-scoping в UA, — но это правильная граница. Задача UA — графы кода, задача Tesserae — research graphs, и шов между ними никогда не должен позволять заголовкам документов перетекать с одной стороны на другую.

## Куда это вписывается

| Слой | За что отвечает | Конфигурируется через |
|---|---|---|
| Собственный walker UA | Какие файлы UA читает в первую очередь | Конфиг UA (вне области Tesserae) |
| `auto_refresh` на инструменте UA | Перезапускает ли `tesserae compile` сам UA | запись external_tools в `.tesserae/config.json` |
| `enabled` на инструменте UA | Учитывает ли Tesserae UA вообще | запись external_tools в `.tesserae/config.json` |
| `sync_mode` на инструменте UA | Сливаются ли узлы UA в research graph | запись external_tools в `.tesserae/config.json` |

Переключатели `enabled` + `sync_mode` — это шов между двумя проектами. Переключатели walker + `auto_refresh` — внутренние дела UA.
