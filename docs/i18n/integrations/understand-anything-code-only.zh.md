# Understand-Anything：仅代码图模式

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything-code-only.md">English</a> · <a href="understand-anything-code-only.ko.md">한국어</a> · <a href="understand-anything-code-only.ja.md">日本語</a> · <a href="understand-anything-code-only.ru.md">Русский</a> · <a href="understand-anything-code-only.es.md">Español</a> · <a href="understand-anything-code-only.fr.md">Français</a> · <a href="understand-anything-code-only.de.md">Deutsch</a></p>
<!-- translations:end -->

本文是 [understand-anything.md](../../integrations/understand-anything.md) 的后续。基础文档介绍了如何安装并启用 [Understand-Anything](https://github.com/Lum1104/Understand-Anything)（UA），把它作为配套工具,在 `.understand-anything/knowledge-graph.json` 中生成代码图。**本文则解释如何让 UA 仅贡献代码图,绝不把从你文档中抽取的章节标题污染到 Tesserae 研究图的 Concept 层中。**

如果你启用 UA 后打开类型化图谱,发现 Concept 层里塞满了 `'Quickstart'`、`'2) Paste it into your MCP client'`,或者同一个标题以七种语言重复出现,那么你正好命中了本文要修复的问题。

## 为什么会出现这种情况

同一错误的两层叠加:

1. **UA 默认会遍历你的文档。** UA 的 source loader 开箱即用地会遍历项目根目录下所有可读文件——包括 `docs/`、`docs/i18n/`、各种语言的 README 等。对于它看到的每个 markdown 标题,都会在 `.understand-anything/knowledge-graph.json` 中以标题文本作为实体名记录一个节点。
2. **Tesserae 会原生合并 UA 的整个图。** 当 `external_tools` 列出 UA 并设置 `sync_mode: "native_graph"` 时,`ProjectWiki._merge_configured_understand_anything_graph()` 会读取该产物,并把 UA 的每个节点作为 `Concept` 导入研究图。UA 的"这是一个代码符号"的语义会被压平为"这是一个研究概念",你的文档标题节点也会顺势进入。

最终效果:每个翻译过的标题都会作为重复 Concept 出现(`'Setup'`、`'설정'`、`'安装'`、`'インストール'`、`'Установка'`、`'Configuración'`、`'Configuration'`、`'Einrichtung'`),产生 slug 冲突,投影器只能把它们重命名为 `setup-2.md`、`setup-3.md`、……、`setup-7.md`。

> [!warning] 一看便知
> 在出现该问题的项目上做一次症状自检:
> ```bash
> .venv/bin/python -c "
> import json
> from collections import Counter
> nodes = json.load(open('.tesserae/graph.json'))['nodes']
> srcs = Counter(n.get('source_path','') for n in nodes if n['type']=='Concept')
> print(srcs.most_common(3))
> "
> ```
> 如果排名第一的来源是 `.understand-anything/knowledge-graph.json`,并且伴随成百上千的 Concept 节点,那么你所有的翻译标题都正在作为独立 concept 被导入。

## 三步修复

### 第 1 步——让 Tesserae 这一侧停止把 UA 当作 Concept 导入

编辑 `.tesserae/config.json`,在 UA 工具条目上同时设置 `enabled: false` 和 `sync_mode: "disabled"`。这两个"双保险"标志都会被合并代码路径检查:

```jsonc
{
  "external_tools": [
    {
      "id": "understand-anything",
      "enabled": false,            // ← was true
      "sync_mode": "disabled",     // ← was "native_graph"
      "auto_refresh": false,       // optional: stop refreshing UA on every compile
      // ...the rest of the entry stays as-is
    }
  ]
}
```

`enabled: false` 会让 `_merge_configured_understand_anything_graph()` 完全跳过该工具。`sync_mode: "disabled"` 是次级防线,以防将来某个 bug 忽视 `enabled` 标志。

### 第 2 步——删除残留产物,不留尾巴

如果你之前在启用 UA 的状态下运行过一次 compile,被污染的产物仍然留在磁盘上:

```bash
rm -f .understand-anything/knowledge-graph.json
rm -f .tesserae/external/understand-anything.md
```

Tesserae 仅在该工具启用时才会重新生成 `.tesserae/external/understand-anything.md`,所以在完成第 1 步之后再删除它是安全的。

### 第 3 步——重新编译并清理 Obsidian vault

```bash
tesserae compile
tesserae vault sync --prune-orphans
```

compile 会跳过 UA 合并,让研究图中不再有来源为 UA 的 Concept。prune 步骤会删除 Obsidian vault 中指向已被合并产生的 node_id 的孤儿页面。

## 验证

重新编译后,上面那段审计脚本应当报告来源为 `.understand-anything/knowledge-graph.json` 的 Concept 节点数为零(或接近零)。另一项有用的检查:

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

如果修复生效,应当输出 `0 Concept slug collision(s), 0 duplicate page(s)`。

## 当你确实想恢复代码图导航时

UA 的代码图本身非常有用——调用/导入边、类层级等等——前提是它没有被文档标题噪声淹没。正确地重新启用:

1. **把 UA 自身的扫描范围限定到代码,而非文档。** UA 接受 include/exclude 模式;配置它只遍历 `src/`、`lib/`、`tesserae/` 等,并显式排除 `docs/`、`README*.md` 以及 `docs/i18n/`。具体的配置项请参见 UA 自身的文档 [Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything)。
2. **在 `.tesserae/config.json` 中重新启用**:把 `enabled` 改回 `true`、`sync_mode` 改回 `"native_graph"`、`auto_refresh` 改回 `true`。
3. **重新编译**并再跑一次审计。一次干净的 UA 运行应当产生映射到真实代码符号(函数名、类名、模块)的 Concept,而不是英文章节标题。

这种不对称让人难受——禁用只要翻一个配置开关,而干净地重新启用却需要理解 UA 的 source-scoping——但这恰恰是正确的边界。UA 的职责是代码图,Tesserae 的职责是研究图,两者之间的接缝绝不应让文档标题从一侧越界到另一侧。

## 各项归属

| 层 | 关注点 | 配置位置 |
|---|---|---|
| UA 自身的 walker | UA 一开始读取哪些文件 | UA 的配置(超出 Tesserae 范围) |
| UA 工具上的 `auto_refresh` | `tesserae compile` 是否会重跑 UA | `.tesserae/config.json` external_tools 条目 |
| UA 工具上的 `enabled` | Tesserae 是否考虑 UA | `.tesserae/config.json` external_tools 条目 |
| UA 工具上的 `sync_mode` | UA 的节点是否被合并到研究图中 | `.tesserae/config.json` external_tools 条目 |

`enabled` 与 `sync_mode` 两个开关是两个项目之间的接缝。walker 与 `auto_refresh` 两个开关属于 UA 的内部事务。
