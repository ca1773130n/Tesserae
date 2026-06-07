# Understand-Anything: code-graph-only モード

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything-code-only.md">English</a> · <a href="understand-anything-code-only.ko.md">한국어</a> · <a href="understand-anything-code-only.zh.md">中文</a> · <a href="understand-anything-code-only.ru.md">Русский</a> · <a href="understand-anything-code-only.es.md">Español</a> · <a href="understand-anything-code-only.fr.md">Français</a> · <a href="understand-anything-code-only.de.md">Deutsch</a></p>
<!-- translations:end -->

これは [understand-anything.md](../../integrations/understand-anything.md) の続編です。ベースドキュメントでは、[Understand-Anything](https://github.com/Lum1104/Understand-Anything) (UA) をコンパニオンとしてインストール・有効化し、`.understand-anything/knowledge-graph.json` にコードグラフを生成させる方法を説明しています。**本ドキュメントでは、UA が code graph のみを提供し、ドキュメントから抽出された見出しテキストで Tesserae の research-graph の Concept レイヤーを汚染しないようにする方法を解説します。**

UA を有効化した後に型付きグラフを開いて、Concept レイヤーが `'Quickstart'`、`'2) Paste it into your MCP client'`、あるいは同じ見出しが 7 言語分並んでいる――そんな状態を見たことがあるなら、まさに本ドキュメントが解決する問題に直面しています。

## なぜこうなるのか

同じ過ちが 2 つのレイヤーで重なって発生します:

1. **UA はデフォルトでドキュメントも巡回します。** 初期状態では、UA のソースローダーはプロジェクトルート配下の読み取り可能なすべてのファイルを巡回します――`docs/`、`docs/i18n/`、各言語の README なども対象です。markdown の見出しを見つけるたびに、その見出しテキストをエンティティ名として `.understand-anything/knowledge-graph.json` にノードを記録します。
2. **Tesserae は UA のグラフ全体をネイティブに取り込みます。** `external_tools` に UA が `sync_mode: "native_graph"` で登録されていると、`ProjectWiki._merge_configured_understand_anything_graph()` がその成果物を読み込み、UA のすべてのノードを `Concept` として research graph にインポートします。UA の「これはコードシンボルである」という意図は「これは研究 concept である」に平坦化され、ドキュメント見出しのノードもまとめて入り込んできます。

正味の効果: 翻訳済みの見出しがすべて重複した Concept として現れ (`'Setup'`、`'설정'`、`'安装'`、`'インストール'`、`'Установка'`、`'Configuración'`、`'Configuration'`、`'Einrichtung'`)、slug 衝突が発生して projector が `setup-2.md`、`setup-3.md`、…、`setup-7.md` とリネームしてしまいます。

> [!warning] 見ればすぐ分かる症状
> この問題が発生したプロジェクトでの症状確認:
> ```bash
> .venv/bin/python -c "
> import json
> from collections import Counter
> nodes = json.load(open('.tesserae/graph.json'))['nodes']
> srcs = Counter(n.get('source_path','') for n in nodes if n['type']=='Concept')
> print(srcs.most_common(3))
> "
> ```
> 最上位のソースが `.understand-anything/knowledge-graph.json` で、しかも数百もの Concept ノードを持っているなら、翻訳済み見出しがすべて個別の concept としてインポートされていることになります。

## 3 ステップで修正する

### Step 1 — Tesserae 側で UA を Concept としてインポートするのをやめる

`.tesserae/config.json` を編集し、UA ツールエントリで `enabled: false` と `sync_mode: "disabled"` の両方を設定します。マージ処理のコードパスでは、ベルト＆サスペンダー（二重安全）として両方のフラグがチェックされます:

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

`enabled: false` により `_merge_configured_understand_anything_graph()` はそのツールを完全にスキップします。`sync_mode: "disabled"` は、将来のバグで `enabled` フラグが無視された場合に備えた二次的なガードです。

### Step 2 — 古い成果物を削除して取り残しをなくす

以前に UA を有効化した状態で compile を実行していた場合、汚染された成果物がディスク上に残っています:

```bash
rm -f .understand-anything/knowledge-graph.json
rm -f .tesserae/external/understand-anything.md
```

Tesserae は当該ツールが有効な場合にのみ `.tesserae/external/understand-anything.md` を再生成するので、Step 1 が完了していれば削除しても問題ありません。

### Step 3 — 再コンパイルして Obsidian vault を整理する

```bash
tesserae compile
tesserae vault sync --prune-orphans
```

このコンパイルでは UA のマージがスキップされ、research graph は UA 由来の Concept から解放されます。prune ステップは、マージ処理が作成した node_id を指していた Obsidian vault 内のオーファンページを削除します。

## 検証

再コンパイル後、上記の監査スクリプトは `.understand-anything/knowledge-graph.json` をソースとする Concept ノードがゼロ（あるいはほぼゼロ）であることを報告するはずです。もう 1 つ有用なチェック:

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

修正が効いていれば `0 Concept slug collision(s), 0 duplicate page(s)` と出力されるはずです。

## それでもやはり code-graph ナビゲーションが欲しい場合

UA の code graph は本当に有用です――call/import エッジ、クラス階層など――ただし、ドキュメント見出しのノイズに埋もれていない限りにおいて。きちんと再有効化するには:

1. **UA 自身のスコープをコードに限定し、ドキュメントを除外する。** UA は include/exclude パターンを受け付けます。`src/`、`lib/`、`tesserae/` などのみを巡回し、`docs/`、`README*.md`、`docs/i18n/` を明示的に除外するよう設定してください。具体的な設定項目は UA 自身のドキュメント [Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything) を参照してください。
2. **`.tesserae/config.json` で再有効化する**: `enabled` を `true` に、`sync_mode` を `"native_graph"` に、`auto_refresh` を `true` に戻します。
3. **再コンパイル** して監査を再実行します。クリーンな UA 実行であれば、英語のセクション見出しではなく、実際のコードシンボル（関数名、クラス名、モジュール）にマッピングされる Concept が生成されるはずです。

この非対称さは少々辛いところです――無効化は設定 1 つの切り替えで済むのに、クリーンに再有効化するには UA のソーススコーピングを理解する必要があります――しかし、これは正しい境界です。UA の役割は code graph、Tesserae の役割は research graph であり、その継ぎ目をドキュメント見出しが越えて両側を行き来することは決してあってはなりません。

## 全体マップにおける位置づけ

| レイヤー | 関心事 | 設定箇所 |
|---|---|---|
| UA 自身のウォーカー | そもそも UA がどのファイルを読むか | UA の config（Tesserae の範囲外） |
| UA ツールの `auto_refresh` | `tesserae compile` が UA を再実行するかどうか | `.tesserae/config.json` の external_tools エントリ |
| UA ツールの `enabled` | Tesserae が UA を考慮するかどうか | `.tesserae/config.json` の external_tools エントリ |
| UA ツールの `sync_mode` | UA のノードが research graph にマージされるかどうか | `.tesserae/config.json` の external_tools エントリ |

`enabled` と `sync_mode` のノブが 2 つのプロジェクト間の継ぎ目です。ウォーカーと `auto_refresh` のノブは UA の内部的な関心事です。
