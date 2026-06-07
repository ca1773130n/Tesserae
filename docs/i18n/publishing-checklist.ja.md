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

### デモビルドのスモークテスト（`build-demo` CI ジョブと同一）

リリースフローと CI はどちらも、決定論的エクストラクタ（LLM 呼び出しなし、API キー
不要）で Tesserae を自身のソースツリーに対してコンパイルし、サイトをビルドする:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## リリースフロー

`release` スキル（`.claude/skills/release/SKILL.md`）が主導する。最新タグは `v0.5.0`。

- [ ] `main` 上で作業ツリーがクリーン、`git pull --ff-only origin main` を実行。
- [ ] テスト + デモビルドのスモークテスト（上記）が通る。
- [ ] `pyproject.toml` の `version = "X.Y.Z"` を上げ（`package.json` があれば同期）、`git log v<prev>..HEAD` から作った 1 段落の変更ログとともに `release: vX.Y.Z` をコミット。
- [ ] `git tag -a vX.Y.Z -m "vX.Y.Z"` でタグ付けし、コミットの後にタグをプッシュ。
- [ ] CI のグリーンを待つ（`gh run watch <run-id>`）— 赤いビルドで GitHub リリースを切らない。
- [ ] GitHub リリースを公開する。PyPI 公開は任意（準備ができたとき）。

### GitHub Pages

`build-demo` ワークフロー（`main` への push）は、コンパイル済みの dogfood サイトを常に
検査可能なワークフローアーティファクトとしてアップロードし、Pages が有効な場合は
**さらに** GitHub Pages へデプロイする。Pages のステップは `continue-on-error` だ:
デフォルトの `GITHUB_TOKEN` は Pages サイトを*作成*できないため、初回デプロイには
**Settings → Pages → Source: GitHub Actions** での 1 度の手動切り替えが必要。その
トグルをオンにする前でも、ビルドはグリーンのままでアーティファクトは生成され続ける。

## Self-dogfood

連携のオプトイン（Understand Anything、RAG-Anything、cognee）は、CLI フラグではなく
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
#   - Understand Anything を有効化（プラットフォーム: codex）、インストール: はい
#   - RAG-Anything を有効化、インストール: はい、パーサー: mineru、その後実行: はい
#   - cognee を有効化、インストール: はい
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

完全に非対話的な実行には、`tesserae init --yes`（すべての連携 OFF）を使い、その後
`.tesserae/config.json` で各連携を有効化し（ウィザードは `memory_backends`（cognee）
と `external_tools`（Understand Anything、RAG-Anything）キーの下に書き込みます）、
コンパイル前に各連携に対して `tesserae integrations refresh <name>` を実行します。
正確な設定キーは連携ドキュメントを参照してください。

## デモで話すポイント

- Tesserae は汎用的な名詞句グラフではありません。制御された ontology を使用します。
- 研究コードと開発コードはインフラを共有しますが、別々の schema を保ちます。
- Markdown と HTML は投影であり、権威ある真実の保存場所ではありません。
- デフォルトの経路はローカルで、API key がなくても使いやすいものです。
- エージェント harness と MCP により、コーディングエージェントがグラフを利用できます。
- インポートされた harness セッションページは、transcript の発見を明示的に保ちながら、以前の Claude Code/Codex 作業を検索可能なプロジェクトメモリに変換します。
