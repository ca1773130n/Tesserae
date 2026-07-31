# 公開チェックリスト

<!-- translations:start -->
<p align="center"><a href="../publishing-checklist.md">English</a> · <a href="publishing-checklist.ko.md">한국어</a> · <a href="publishing-checklist.zh.md">中文</a> · <a href="publishing-checklist.ja.md">日本語</a> · <a href="publishing-checklist.ru.md">Русский</a> · <a href="publishing-checklist.es.md">Español</a> · <a href="publishing-checklist.fr.md">Français</a> · <a href="publishing-checklist.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae を公開する前に、このチェックリストを使用してください。

## リポジトリの衛生状態

- [ ] README が、プロジェクトの内容と解決する問題を説明している。
- [ ] インストールコマンドが新しい shell から動作する。
- [ ] Quickstart が `python3 -m` ではなく `tesserae` を使っている。
- [ ] アーキテクチャ文書が raw evidence → graph → projections を説明している。
- [ ] 機能マップが将来作業を誇張せず、実装済み機能を列挙している。
- [ ] セッション履歴ドキュメントが、明示的なインポート、プライバシーレビュー、生成された routes、transcript typography を説明している。
- [ ] Self-dogfood デモが実行され、文書化されている。
- [ ] 生成された成果物が再現可能であり、無視されるか意図的に公開される。

## 検証

```bash
.venv/bin/pytest tests/ -x          # 失敗があれば中止 — 赤いビルドは決して出荷しない
./scripts/install.sh --help
tesserae init --help
tesserae compile --help
tesserae context --help     # オンデマンド・コンテキスト・コンパイラ
```

### デモビルドのスモークテスト（手動 — CI ではカバーされない）

リリースのたびに手で実行する。以前は `main` への push ごとに走る `build-demo` CI
ジョブと同一だったが、そのワークフローは削除された。したがってこのコンパイル経路を
検証するのはここだけになった。`tests.yml` はユニットスイートを実行するだけで、
`init` → `compile` → `export site` を通しでは実行しない。

決定論的エクストラクタ（LLM 呼び出しなし、API キー不要）で Tesserae を自身の
ソースツリーに対してコンパイルし、サイトをビルドする:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## リリースフロー

`release` スキル（`.claude/skills/release/SKILL.md`）が駆動する。スキルがこの
フローの正典であり、両者が食い違ったらスキルが勝つ。直すのはこのリストのほうだ。

- [ ] `main` 上、作業ツリーがクリーン、`git pull --ff-only origin main`。
- [ ] テストがグリーン（`uv run pytest tests/ -x`、約 9 分）。デモビルドの
      スモークは手動であり、もう CI ではカバーされない — 上記参照。
- [ ] **3 つすべての**バージョンファイルを上げる: `pyproject.toml`、
      `.claude-plugin/plugin.json`、`npm/package.json`。互いに、そしてタグと
      一致していること。npm ラッパーは `tesserae==<npm のバージョン>` を固定する。
- [ ] リリースノートと 7 言語の翻訳を書く。`uv run pytest
      tests/test_docs_i18n.py -q` がグリーンであること。
- [ ] `uv lock` を実行し `uv.lock` をステージする — これは `tesserae` を自身の
      バージョンに固定しており、CI は `uv sync --locked` を走らせるため、古い
      ロックでは失敗する。
- [ ] `git log v<prev>..HEAD` から起こした 1 段落の changelog を添えて
      `release: vX.Y.Z` をコミット。
- [ ] **PR を開く — `main` は保護されており直接 push は拒否される**（`GH006`。
      `enforce_admins` が有効、3 つのチェックが必須）。3 レーンすべてグリーンに
      なってからマージする。赤いビルドにタグを打たないこと。
- [ ] マージ済みコミットにタグを打ち（`git tag -a vX.Y.Z -m "vX.Y.Z"`）、タグを
      push する。ここが後戻りできない地点だ: タグ push が npm の OIDC ワーク
      フローを起動し、公開された npm バージョンは二度と再利用できない。
- [ ] GitHub リリースを公開する。
- [ ] **PyPI への公開 — 必須。オプションではない。** タグのクリーンな worktree
      からビルドしてアップロードし、`--no-cache-dir` 付きで新規 venv への
      インストールを検証する（pip はインデックスをキャッシュするため、すでに
      公開済みのバージョンを「見つからない」と報告する）。
- [ ] **npm への公開 — 必須。** タグ push で OIDC により自動実行される。run を
      監視し、`npx -y @jokerized/tesserae@X.Y.Z status` でスモークする。手動で
      publish しないこと — トークンは存在せず、手動公開は provenance 証明を
      スキップしてしまう。

### GitHub Pages

**もはやどのワークフローもサイトをデプロイしない。** `build-demo` ワークフローが
`main` への push ごとに行っていたが、削除された。それが最後にデプロイしたサイトは
今も配信されており、README も引き続きライブデモとしてリンクしている — つまりその
ページは最後の `build-demo` 実行時点で凍結したスナップショットであり、現在の `main`
を映したものではない。

再公開するには手動の `tesserae export site` とアップロード、または新しいワークフローが
必要になる。いずれにせよ意図的に決めること: コードから静かに乖離するデモリンクは、
デモリンクが無いことより悪い。

## Self-dogfood

連携のオプトイン（RAG-Anything）は、CLI フラグではなく
**対話型ウィザードのプロンプト**になりました。ウィザードを実行して答えてください:

```bash
tesserae init \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts
# ウィザードが尋ねたら:
#   - RAG-Anything を有効化、インストール: はい、パーサー: mineru、その後実行: はい
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

完全に非対話的な実行には、`tesserae init --yes`（すべての連携 OFF）を使い、その後
`.tesserae/config.json` で各連携を有効化し（ウィザードは `memory_backends`
と `external_tools`（RAG-Anything）キーの下に書き込みます）、
コンパイル前に各連携に対して `tesserae integrations refresh <name>` を実行します。
正確な設定キーは連携ドキュメントを参照してください。

## デモで話すポイント

- Tesserae は汎用的な名詞句グラフではありません。制御された ontology を使用します。
- 研究コードと開発コードはインフラを共有しますが、別々の schema を保ちます。
- Markdown と HTML は投影であり、権威ある真実の保存場所ではありません。
- デフォルトの経路はローカルで、API key がなくても使いやすいものです。
- エージェント harness と MCP により、コーディングエージェントがグラフを利用できます。
- インポートされた harness セッションページは、transcript の発見を明示的に保ちながら、以前の Claude Code/Codex 作業を検索可能なプロジェクトメモリに変換します。
