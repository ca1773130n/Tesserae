# チューニングリファレンス — 環境変数

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae が環境から読み取るすべてのノブ、そのデフォルト値、
および実際に変更する時期について説明します。ここのすべてが必須ではありません：
デフォルト値は、単純な `tesserae compile` が正しく機能するように選択されています。

LLM バックエンド設定は `.tesserae/config.json` および
`~/.tesserae/config.json` にも存在します。下記の環境変数はこれらの両方に優先し、[LLM バックエンド](#llm-バックエンド)は優先順位をまとめて示します。

---

## お金を費やすフック

Claude Code プラグインはコンパイルをバックグラウンドできるフックを付属します。
費用をかけるものはすべて**デフォルトではオフ**です：

```sh
export TESSERAE_HOOK_AUTOCOMPILE=1   # 自動リコンパイルにオプトイン
```

ゲート：`posttooluse-edit.sh`（毎回の Edit/Write で起火）および `session-end.sh`。
ゲートされていません。費用がかからないため：`session-start.sh` は決定的な
`tesserae code sync` を実行し、`pretooluse-compile.sh` はあなたが自分で入力した
`tesserae compile` をインターセプトするだけです。

このデフォルトが存在する理由は、代替案が測定されたからです。`~/.tesserae` の
ナレッジベースは `$HOME` をプロジェクトルートのように見せかけ、フックリゾルバーは
作業ディレクトリから**上へ**歩んで、最初に見つかった `.tesserae/` に到達しました。
そのため、登録されたプロジェクト外のセッションはすべて `$HOME` に解決され、
ホームディレクトリ全体をコンパイルしました：15k ファイル、795 MB のグラフ、
**およそ 10 時間の LLM 費用**、それを開始したセッションより長く生き残った
デタッチされたプロセスから。

`resolve_project_root()` は現在、どちらのパスでも `$HOME` を拒否し、作業ディレクトリに
フォールバックする代わりに空を返します。そのため、呼び出し側は推測する代わりに
何もしません。バックグラウンドでモデルワークを行うフックは、請求が到着した後に
オフにスイッチするのではなく、意図的にオンに切り替えるべきです。

## 抽出

### `TESSERAE_EXTRACT_TIMEOUT`

**デフォルト `1800`（秒）、試行ごと。** 各 codex/claude 抽出呼び出しを制限し、
wedged CLI 子プロセスがコンパイルをハングさせないようにします。

これは実際に起こりました：コンパイルが **5 h 43 m** の間 0% CPU で
観察され、**4 h 6 m** の間アイドル状態の `codex exec` 子プロセスが背後にあり、
`.tesserae/compile.lock` をずっと保持していました。
すでにメモリ内に 32 個のコミュニティサマリーを構築していましたが、
それらを永続化することはできませんでした。

試行ごと、ドキュメントごとではありません。タイムアウト時に、クライアントは次の
`CODEX_HOME` / claude 設定ディレクトリにローテーションするため、
1 つのドキュメントの最悪のケースは `timeout × 設定されたプロファイル` です。

```sh
export TESSERAE_EXTRACT_TIMEOUT=3600   # 非常に大きなドキュメントのためのより多くの余裕
export TESSERAE_EXTRACT_TIMEOUT=0      # 制限なし—完了まで実行
```

設定されているが使用不可な値（`10m`、`600s`、負、`inf`）は stderr に警告し、
デフォルトを保つ。タイプミスがセーフティバルブをサイレントに無効にしてはいけません。

### `TESSERAE_EXTRACT_CONCURRENCY`

**デフォルト `4`、LLM エンドポイントがこのマシン上にある場合は `1`。** 並行して抽出されたドキュメント。各々はおよそ 1 分かかる
ブロッキング CLI サブプロセスであるため、シーケンシャルループは壁時計を
すべてのモデルラウンドトリップの文字通りの合計にします—
161 個のドキュメントで ~2 h 40 m として測定されました。

上限はあなたのマシンではなく、プロバイダーアカウントのレート制限です。
これが デフォルト値が控えめである理由です。
厳密にシーケンシャルな動作のために `1` に設定します。

ローカルのモデルサーバーは例外です。Ollama、llama.cpp、LM Studio は一度に 1 つのリクエストしか
処理しないため、4 つのワーカーはすべての呼び出しの後ろに 3 つのリクエストを並ばせ、サーバーが
落としたキュー内のリクエストは `TESSERAE_EXTRACT_TIMEOUT` の間ずっとそのワーカーをブロックします—
これはメモリの問題とまったく同じように見えます。解決された `llm_base_url` が `localhost`、
`127.0.0.1`、または `::1` を指していて、この変数が未設定の場合、抽出は一度に 1 つのドキュメントを
処理し、stderr にそう伝えます。クラウド API に転送するループバックプロキシ（LiteLLM、バッチ処理
付きの vLLM）はもっと受け付けられます：変数を明示的に設定すれば、常にそれが優先されます。

並行性は出力を変更することはありません：作業リストは経路順で固定され、
結果はインデックスで収集されるため、並行実行はシーケンシャル実行と
バイト単位で同じです。

### `TESSERAE_LLM_CACHE`

**デフォルトオン。** CLI プロバイダー応答のコンテンツアドレス指定キャッシュは、
`~/.tesserae/llm_cache` の下にあり、実際に送信されたプロンプトのダイジェスト、
およびモデルと推論努力によってキー付けされます—したがって、異なる質問は
再度質問され、モデルを切り替えると、前のモデルの回答を返す代わりに
再度質問します。解析可能な応答のみが保存されるため、
1 つの悪い生成も永続的になることはできません。

旧エントリは設計上到達不可能です：かつてキーはプロンプトのダイジェストではなく
呼び出しステージが提供するラベルだったため、関連のない質問が 1 つのエントリを
共有することがありました。移行するものはありません—ディレクトリは
削除しても問題ありません。コンパイルすれば再び埋まります。

```sh
export TESSERAE_LLM_CACHE=0   # 常に再度質問
```

### `TESSERAE_LLM_CHUNK_CHARS`

ドキュメントが 1 回の呼び出しに対して大きすぎる場合のチャックあたりの文字数。
コンテキスト制限に達していない限り、設定されていないままにしておきます。

---

## LLM バックエンド

バックエンド、ワイヤ、認証情報が何かを決定します。下記のすべてのキーは同じ方法で解決され、この方法でのみ解決されます。

**`TESSERAE_*` 環境変数 → プロジェクト `.tesserae/config.json` → `~/.tesserae/config.json` → ビルトインデフォルト。**

| 設定キー | 環境変数 | デフォルト | 注記 |
|---|---|---|---|
| `llm_provider` | `TESSERAE_LLM_PROVIDER` | `claude` | 以下のいずれか：`claude`、`codex`、`anthropic`、`openai`、`custom`。それ以外は名前で拒否されます — typo はかつて黙って `claude` として扱われたため、`openrouter` という設定は Anthropic に対して実行され、あなたが選ばなかったモデルに関するエラーが報告されました |
| `llm_api_style` | `TESSERAE_LLM_API_STYLE` | `llm_provider` が `openai` のとき `openai`、それ以外は `anthropic` | ワイヤプロトコル。バックエンドとは異なる問題です。`anthropic` は Anthropic SDK を通じて `{base_url}/v1/messages` に POST し、`openai` は `{base_url}/chat/completions` に POST します |
| `llm_model` | `TESSERAE_LLM_MODEL` | `sonnet`（claude CLI）、`gpt-5.6-luna`（codex CLI）、`claude-sonnet-4-6`（anthropic ワイヤ）、`gpt-4o-mini`（openai ワイヤ） | 2 つの CLI バックエンドではプロバイダーでスコープされているため、claude 型のモデルが codex パスに落ちることはありません。設定されたエンドポイントプロバイダーは、プロバイダーとモデルが異なる config レイヤーで設定された場合でも、そのモデルを保持します |
| `llm_base_url` | `TESSERAE_LLM_BASE_URL`、その後 `ANTHROPIC_BASE_URL` | `https://api.anthropic.com`（anthropic ワイヤ）、`https://api.openai.com/v1`（openai ワイヤ） | エンドポイント。各ワイヤが追加するものにトリムされます — [カスタムエンドポイント](#カスタムエンドポイント)を参照してください |
| `llm_api_key` | `TESSERAE_LLM_API_KEY`、その後 `ANTHROPIC_API_KEY` | — | API キー認証情報：anthropic ワイヤでは `X-Api-Key`、openai ワイヤでは `Authorization: Bearer` |
| `llm_auth_token` | `TESSERAE_LLM_AUTH_TOKEN`、その後 `ANTHROPIC_AUTH_TOKEN` | — | ベアラー認証情報。両方のワイヤで `Authorization: Bearer`。**`llm_api_key`** のいずれかを設定してください。anthropic ワイヤでは、トークンは SDK に `auth_token=` として渡され、API キーは設定されないため、この 2 つは決して衝突しません |
| `llm_allow_fallback` | `TESSERAE_LLM_ALLOW_FALLBACK` | オフ | 設定されたエンドポイントプロバイダーが失敗せずに別のバックエンドにフォールスルーすることを許可します — [エンドポイントプロバイダーは契約です](#エンドポイントプロバイダーは契約です)を参照してください。環境変数の任意の空でない値がオンにします |
| `llm_claude_config_dirs` | `TESSERAE_CLAUDE_CONFIG_DIRS` | CLI 独自のデフォルト | ローテーション順の Claude 設定ディレクトリ、環境変数では `os.pathsep` で区切られます — 繰り返される `--claude-config-dir` の環境変数チャネルです。*設定された*リストのみが権威を持ちます。周囲の `CLAUDE_CONFIG_DIR` は意図的に権威を持ちません。それに固定するとマルチアカウント rotation が 1 つのアカウントに潰れるからです |
| `llm_codex_homes` | `TESSERAE_CODEX_HOMES` | CLI 独自のデフォルト | Codex ホーム。上記と同じ形状と同じ理由です。古い単数形の `llm_codex_home` は引き続き機能し、1 つのホームリストを意味します |
| `llm_codex_reasoning_effort` | `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | 構造化抽出は、対話的な作業のために設定される可能性のある `xhigh` を必要としません — `xhigh` はマルチドキュメント compile を何倍も遅くします |

`ANTHROPIC_*` の名前はまだ機能し、Tesserae 所有のものの 1 段下です。これらは ambient です — Claude Code セッションはそれらをエクスポートします — そのため、Tesserae に特に設定した値より上位にランク付けされてはいけませんが、それでも両方の config ファイルを上回ります。

`tesserae config llm` はマシン全体のファイルを書き込みます。1 つのプロジェクトの場合は、その `.tesserae/config.json` に同じ `llm_*` キーを入れてください。いずれかのファイルに書き込まれた認証情報は**平文**で保存されるため、これらの 2 つについては `TESSERAE_LLM_API_KEY` /
`TESSERAE_LLM_AUTH_TOKEN` を優先してください。

### カスタムエンドポイント

`llm_provider` はバックエンドを示します。`llm_api_style` はそれに話しかける HTTP 方言を示します。これらを分離しておくことが、非 Anthropic エンドポイントに到達可能にするものです。`custom` は以前は Anthropic ワイヤを意味していたため、OpenAI 互換サーバーは設定する場所がありませんでした。未設定のままにすると、`llm_api_style` は引き続き `custom` について `anthropic` に解決され、このエンドポイントは以前と同じように動作し続けます。

**OpenAI 互換エンドポイント** — vLLM、LiteLLM、OpenRouter、Together、Ollama、LM Studio：

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=openai
export TESSERAE_LLM_BASE_URL=http://localhost:8000/v1
export TESSERAE_LLM_MODEL=qwen2.5-coder-32b-instruct
export TESSERAE_LLM_AUTH_TOKEN=sk-...   # or TESSERAE_LLM_API_KEY — same header here
tesserae config status
```

リクエストは `POST {base_url}/chat/completions` です。このワイヤは stdlib `urllib` であるため、追加のインストールは必要なく、keyless local server はまったく認証情報を必要としません — 両方を未設定のままにしても、それでも構築されます。

**Anthropic 互換エンドポイント** — Messages API を話すゲートウェイ：

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=anthropic
export TESSERAE_LLM_BASE_URL=https://gateway.internal.example   # no /v1
export TESSERAE_LLM_MODEL=claude-sonnet-4-6
export TESSERAE_LLM_API_KEY=...         # X-Api-Key; TESSERAE_LLM_AUTH_TOKEN for a bearer gateway
tesserae config status
```

リクエストは Anthropic SDK を通じて `POST {base_url}/v1/messages` です。このワイヤの `llm_provider: custom` と `llm_provider: anthropic` の両方が必要です。

```bash
pip install "tesserae[synthesis-llm]"
```

**`/v1` は装飾ではありません。** SDK は `/v1/messages` 自体を追加するため、すべてのゲートウェイ README が示す `https://host/v1` は `/v1/v1/messages` を生成しました — 誤ったモデルのように見えるエラー 404。1 つの末尾の `/v1` は anthropic ワイヤで削除され、openai ワイヤで保証されるようになりました。その 1 つの末尾セグメントだけが常にタッチされ、それに先行するものに関係なくトリムされます — `/anthropic/v1` を実際に提供する proxy はその `/v1` も失います — したがって、書き直しは黙って行われるのではなく INFO でログされ、ログ行は実際に使用される URL を見つけるところです。

### エンドポイントプロバイダーは契約です

`anthropic`、`openai`、`custom` はあなたが選んだエンドポイント — URL、モデル名、認証情報を持ちます。それらのいずれかが設定されると、単独で構築され、失敗すると `LLMProviderConfigError` が発生し、プロバイダー、ワイヤ、ベース URL、モデル、およびどのような種類の認証情報が解決されたかを名指しします。

以前はむしろ優先選択でした。構築できないカスタムエンドポイントは Claude CLI にフォールスルーし、その後あなた独自のベース URL に対して `--model sonnet` で起動され、設定されなかったモデルに関する unsupported エラーが報告され、実際の原因を名指しするものは何もありませんでした。`llm_allow_fallback: true` に設定してそのチェーニングを戻してください。

2 つの OAuth CLI プロバイダーはチェーンし続けます — 相互に、およびそれらの背後にある API クライアントに。`claude` と `codex` はベース URL を取らず、それらのモデルはプロバイダーごとにスコープされているため、どちらもあなたが選ばなかったバックエンドに選択したエンドポイントを持ってくることはできません。これは、契約が存在する唯一のものです。

### 実際に動作しているものを見る

```bash
tesserae config status                 # resolved backend + a live probe
tesserae config status --project .     # as this project's config.json sees it
tesserae config status --no-ping       # skip the probe, spend nothing
```

プロバイダー、ワイヤ、モデル、ベース URL、および解決された認証情報の*種類* — `api_key`、`auth_token`、または none（秘密は決して解決されない）を出力し、各レイヤが勝ったものでタグ付けされ、応答したクライアントのクラスと ID が続きます。そのクライアントは実行が使用するのと同じ settings dict から構築され、probe は決してキャッシュされないため、渡す行は、バックエンドがここ数秒ではなく過去のある時点で答えたことを意味します。

呼び出しが失敗すると、失敗は平坦化されるのではなく分類されます。`401` と `403` は auth として報告され、`404` — およびモデルを命名する `400` — はエンドポイントとして、各レイヤが生成したエンドポイントを命名します。それ以前は、misconfigured URL を LLM がインストールされていないことと区別することはできませんでした。

---

## コンパイルパス

| 変数 | デフォルト | 何を制御するか |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **オン** | GraphRAG スタイルのサマリーパス。≥ 5 メンバーのクラスタあたり LLM 呼び出し 1 回、メンバーシップダイジェストでキャッシュ。`false`/`0`/`no`/`off` で無効化 |
| `TESSERAE_ENABLE_LLM_PASSES` | オフ | 抽出以外のオプションの LLM 拡張パス |
| `TESSERAE_AGENT_DISTILL` | オフ | エージェントごとの L1 専門知識アーティファクト（`tesserae distill`） |
| `TESSERAE_RUNBOOK_DISTILLATION` | オフ | Runbook/Gotcha 蒸留メモリノード |
| `TESSERAE_SESSION_EVENT_PASS` | **オン** | セッショントランスクリプトからターンごとに生成する `Event` ノード。LLM を使わずバイト単位で決定的ですが、有意なターンごとに 1 ノード — 長いコーパスでは規模が大きくなります。`false`/`0`/`no`/`off` で無効化 |
| `TESSERAE_INSIGHT_SYMBOL_LINK` | オン | セッション洞察をコードシンボルにリンク |
| `TESSERAE_SUPERSEDE_PASS` | オン | 修正されたクレーム間の `superseded_by` エッジ |
| `TESSERAE_PROMPT_SIGNATURES` | オフ | ドリフト検出のためのプロンプトシグネチャの記録 |
| `TESSERAE_COMPILE_LOCK_WAIT` | — | `.tesserae/compile.lock` を待つまでの秒数 |

**コミュニティサマリーについて：** コンパイルパスは最も粗い粒度を積極的にカバーします；
`graph_map` はさらに、冷たいスコープに最初に下降するときに、サマリーを遅延して具体化し、
レベルごとにキャッシュします。パスをオフにすることは正当なコスト戦略です—
実際にアクセスするブランチにのみ料金を支払います—ただし 1 つの注意があります：
**連合下降は遅延具体化を行いません。** 兄弟プロジェクトのカードは、
そのインビデオサマリーまたは既にウォームなキャッシュからのみ命名できるため、
クロスプロジェクトナビゲートするプロジェクトはイーグルパスをオンにしたいです。

---

## クエリと合成

| 変数 | デフォルト | 注記 |
|---|---|---|
| `TESSERAE_QUERY_LLM` | オフ | `tesserae query` の LLM プランナー |
| `TESSERAE_QUERY_DRY_RUN` | オフ | モデルを呼び出さずにプラン |
| `TESSERAE_SYNTHESIS_LLM` | オフ | `tesserae ask` での散文合成 |
| `TESSERAE_SYNTHESIS_MODEL` | — | 合成モデルのオーバーライド |
| `TESSERAE_SYNTHESIS_WORKERS` | — | 並行合成ワーカー |
| `TESSERAE_SYNTHESIS_DRY_RUN` | オフ | モデルをスキップ、パイプラインを実行 |
| `TESSERAE_VERIFY_BAND` | オン | `ask` の不確かなレビュー印を、実測の 0.30–0.70 帯でモデルに判定し直させる。`lo-hi` はその帯を上書きする。`off` はトークンもネットワークも使わない無料の印だけを残す |
| `TESSERAE_EMBEDDING_PREFER` | auto | 密ベクトルレーンのエンコーダ: `model2vec`(同梱、静的、torch 不要)、`st`(学習済み sentence-transformers モデル)、`openai`、`hash`。未設定なら、インストール済みのものを順に最初の一つ選ぶ |
| `TESSERAE_ST_MODEL` | `BAAI/bge-base-en-v1.5` | `st` が読み込む sentence-transformers モデル。Hugging Face の任意の名前 |

### `TESSERAE_VERIFY_BAND`

`ask` の答えはどれも、費用のかからない文ごとのレビュー印を携えている。この印はモデル
に尋ねるより正確ではない — 保留した 755 文で 0.870 対 0.926 — そして差のほとんどは、
忠実な言い換えに対する誤報である。そうした文は出典と共有する語彙がほとんどない。

両者は別々の文で誤るので、無料の検査が確信を持てないところだけモデルに払えば、わずか
な費用で精度が戻る。カバレッジ 0.30–0.70 を委ねると、呼び出しの 42% で 0.932 だった。
すべての文を尋ねる場合と区別がつかず（McNemar p=0.52）、支出は 42% である。

```bash
export TESSERAE_VERIFY_BAND=on          # 実測の 0.30-0.70 帯
export TESSERAE_VERIFY_BAND=0.40-0.60   # より狭く: 呼び出しの 22%、0.914
```

`ask` の内側では既定でオンである。モデルのクライアントはすでに手元にあり、答えにトーク
ンはすでに使われているので、正確な印の追加コストは小さい。モデルなしの検査の変種は、ど
れも単独では差を埋めない — 語幹処理、文字 n-gram、希少度の重み付け、ローカル埋め込みを
それぞれ測り、素朴なカバレッジに勝つものはなかった — だから既定はより賢い無料検査では
なくカスケードである。ライブラリ関数 `check_against_evidence` は手つかずで、いまも費用は
かからない。エンベロープは `adjudicated` を報告する。カスケードが動かなかったときは
`null`、動いたときは件数だ。答えられなかったモデルは無料の判定をそのまま残す — 失敗した
呼び出しが、印の付いた文をきれいにすることは決してない。

### `TESSERAE_EMBEDDING_PREFER`

`hybrid_search` の密ベクトルレーンは、`active_embedding_backend` が最初に見つけた
もので埋め込む: 同梱の `model2vec` 静的モデル(8 MB、torch 不要、オフライン)、
次に sentence-transformers、次にハッシュのスタブ。静的モデルのおかげで
`pip install tesserae` は小さく保たれ、小さなコーパスでは測定できるほどの
コストはない。大きなコーパスではこれがボトルネックになる: 論文 148 本で、
文書単位の再現率は同梱モデルで 0.754 @10 / 0.914 @50、同じ融合に
`BAAI/bge-base-en-v1.5` を入れると 0.791 / 0.962 だった — 密ベクトルレーン単体では
0.473 から 0.680 @10 に上がった。同じチャンク上の素のベクトルストアは nomic-embed-text で
0.784 / 0.942、同じ bge-base で 0.775 / 0.944 である。グラフの優位は 57 問では
ノイズの範囲内だ(対応のある符号検定 p=1.0 @10、0.51 @50)。学習済みエンコーダは
グラフをそれに劣らせず、同等に並べるものである。

```bash
uv pip install sentence-transformers          # torch, ~2 GB with the model
export TESSERAE_EMBEDDING_PREFER=st
export TESSERAE_ST_MODEL=BAAI/bge-base-en-v1.5   # the default; any Hugging Face name
```

`auto` は今も静的モデルを最初に選ぶので、この変数を一度も設定していない
インストールは以前とまったく同じに振る舞う。設定値はバックエンドが最初に
解決されるときに一度だけ読まれる。どのバックエンドも指さない値は、黙って
ハッシュのスタブに落ちるのではなく、報告されたうえで無視される。学習済み
エンコーダはベクトルをキャッシュしない限り、クエリのたびに全ノードを
埋め込み直す — `compile_context` と MCP サーバーはすでにプロジェクトの
`VectorCache` を渡しており、これはバックエンドをキーにしているので、モデルを
切り替えても古いベクトルが返されることはない。

---

## パスとインフラストラクチャ

| 変数 | デフォルト | 注記 |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | プロジェクトレジストリの場所。**すべての**コマンドが尊重します — 0.28.7 までは engine のフリートモードだけがこれを読んでいたため、他の場所で設定しても黙って効果がなく、コマンドは実在のレジストリを使い続けていました |
| `TESSERAE_HOST_ID` | `~/.tesserae/host_id` に一度だけ生成 | このマシンの識別子。[複数マシンでの運用](#複数のマシンで単一プロジェクトを運用する)を参照 |
| `TESSERAE_DISCOVERY_CACHE` | — | セッション発見キャッシュ |
| `TESSERAE_ARXIV_CACHE` | — | arXiv メタデータキャッシュ |
| `TESSERAE_NO_FEDERATION_CACHE` | オフ | フェデレーション グラフ LRU を無効化 |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | オフ | 結合されたクロスプロジェクトグラフを出力 |
| `TESSERAE_FLEET_PIDFILE` | — | エンジンフリート pidfile |
| `TESSERAE_CLIP_TOKEN` | — | Web クリッパーの共有シークレット |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | オフ | `.tesserae/schema-drift-proposals.json` の**承認済み**レコードをコンパイル時に適用（決定論的、LLM なし）。`tesserae schema-drift` で提案を記述し、承認は `ResearchNodeType` を先に編集してから `"approved": true` に設定することを意味する — 解決不可能な名前は何も再型付けしない。 |

---

## グラフを読んだのは誰か

| 変数 | デフォルト | 備考 |
|---|---|---|
| `TESSERAE_READ_AUDIT` | **オフ** | アクセスカウントを動かす読み取りを記録 — `{tool, actor, node_ids, at, tesserae_version}` — `.tesserae/sqlite.db` 内の `read_audit` テーブルに、`read_audit` ツール経由で読み戻し可能で、アクター単位のカウント付き。アクセスカウントがバンプされるところどこにでも 1 行が書かれるので、行数は呼び出しではなくサーフェスに従う: ノードのリストを表面化するツール（`search_nodes`, `node_context`, `compile_context`, `graph_map`, `graph_ppr`, `ask` / `query`, `drill_down`, `find_session_findings`）は**呼び出しごとに 1 行**を書き、そこで数えたすべてのノードを名指しします。いっぽう `fresh_insights` は自身のループの中でバンプするため、表面化したノード 1 つにつき **1 行**を書きます。何も表面化しない呼び出しは 1 行も書かず、ノードを 1 つも読まないツール — `schema`, `graph_summary` — は監査に到達しません。ノードを名指ししない行はアクセスカウントを説明しないからです。既定でオフなのは常時オンの監査があらゆる読み取りを書き込みに変えるからで、ゲートはストアを開く前に置かれます — テーブルを作ること自体が書き込みだからです。この記録が `graph.json` に届くことは決してありません |
| `TESSERAE_ACTOR` | — | 呼び出しがエージェントビューを伴わないとき、その読み取りを誰に帰属させるか。アクターは、呼び出しが解決できた場合は `agent` 引数、そうでなければこの値です; 未設定なら名前をでっち上げず、匿名の読み取りとして記録します |

`TESSERAE_READ_AUDIT` をオフにすると記録は止まりますが、すでに記録された
ものが消えるわけではなく、サーバーを再起動しなくても反映されます。この監査が
*何のため*にあるかというと、[不使用による忘却](agent-memory.ja.md#忘却--削除されない)
のためです: アクセス回数が何を吸収し何を降格させるかを駆動しており、アクターが
なければ、あるノードをポーリングし続けるおしゃべりなエージェントと、それを一度
読んだ人間とが同じ入力になってしまいます。

---

## 複数のマシンで単一プロジェクトを運用する

想定している形：複数のサーバーがそれぞれコーディングエージェントを実行し、それぞれが自分の
ローカルセッショントランスクリプトを持ち、ディスクを共有している — つまり同じプロジェクト
ディレクトリと同じ `.tesserae/` を見ている、という状態です。

**コンパイルは 1 台のホストに任せ、残りは収集だけをさせます。**

```bash
# on the compiling host
tesserae engine

# on every other host
tesserae engine --harvest-only
```

`--harvest-only` は、そのマシンのローカルトランスクリプトを共有セッションストアへ流し込むだけで、
プロジェクトのコンパイルロックを取得することは決してありません。これは競合を調停するのではなく
取り除くやり方であり、タイムアウトの調整に勝るのはそのためです。

**失敗させるのではなく順番待ちさせたい場合**は、`--wait` を渡します：

```bash
tesserae compile --wait          # up to 30 min, reporting every 5s
tesserae compile --wait 120      # or name your own bound
```

これがないと、ロックが保持されているのを見つけたコンパイルは終了コード 2 で終わります — フックには
正しい挙動ですが、人間には腹立たしい挙動です。`--wait` が、stdout が端末かどうかから推測される
ものではなくフラグであるのは、同じコマンドが `tee` の下でも、tmux のキャプチャでも、CI でも
挙動を変えてはならないからです。`TESSERAE_COMPILE_LOCK_WAIT=<seconds>` はプロセスツリー全体に
対して同じことを行います。

**すべてのプロジェクトを最新に保つ**のを 1 回の呼び出しで：

```bash
tesserae refresh --all               # every registered project, sequentially
tesserae refresh --all --jobs 3      # three at a time
tesserae compile --all --name alpha --name beta
```

1 つのプロジェクトが失敗しても、他は止まりません。1 つでも失敗すれば終了コードは `2`、1 つでも
別の実行にロックされていれば `1`、すべて実行できれば `0` です。`--jobs` のデフォルトが 1 なのは、
コンパイルが LLM 負荷の高い処理であり、値を上げるとクォータを並列に消費するからです。

### これを安全にしているもの

マシンごとの状態は、かつて 1 つの共有された名前の下に保存され、どのホストからも読まれていました。
以下はそれぞれホスト ID で分割されるようになりました：

| 状態 | 場所 | なぜホストごとでなければならないか |
|---|---|---|
| セッションレコード | `.tesserae/harness_sessions/` | ホストは自分が収集したものだけを削除します。さもなければホスト B がホスト A のセッションを削除して成功したと報告します — どのホストのスキャンも同じ生産者をスタンプし、それぞれの `~/.claude` は同一に解決されるため、他にそれらを区別するものがありません |
| エンジン pidfile | `.tesserae/daemon.<host>.pid` | 生存確認は**ローカルの**プロセステーブルに対する `os.kill(pid, 0)` です。別のマシンが書き込んだ pid は、無関係なローカルプロセスに照らして判定されてしまいます |
| Codex スキャンの下限 | `.tesserae/harness_sessions.db` | 共有のウォーターマークが 1 つしかないと、最後に実行したホストが、もう一方がまだ読んでいないトランスクリプトを飛び越えて下限を進めてしまい — それらは一度もインポートされませんでした |

ホスト ID は `~/.tesserae/host_id` に一度だけ生成され（共有プロジェクトディレクトリでは**なく**
マシンごとです）、`TESSERAE_HOST_ID` で固定できます。ホスト名ではなく永続化された ID を使うのは、
1 つのイメージから構築されたフリートはホスト名を使い回すためであり、衝突すればあるマシンの
レコードが別のマシンに引き渡されてしまうからです。

### テストすべき前提

以上のすべては、`.tesserae/` を保持するファイルシステムが `flock(2)` を**強制する**ことを前提に
しています。NFS や SMB ではそれは設定次第であり、動作するロックデーモンがなければ `flock` は
黙って no-op に退化しうる — その時点で 2 つのホストが、それぞれ排他ロックを保持していると
信じたまま、同じプロジェクトを同時にコンパイルします。

`tesserae doctor` はプロジェクトがネットワークファイルシステム上にあるときに警告しますが、
単一のホストではホスト間での強制を証明することは**できません**。実機で直接テストしてください：
ホスト A でロックを保持し、ホスト B が拒否されることを確認します。

---

## 低下したコーパスの復旧

ドキュメントの抽出が失敗すると、決定論的ベースラインによって提供され、
`.tesserae/manifest.json` で**マークされます**。マークなしでは、
クリーン抽出と区別できないため、`--changed-only` はそれを永遠にスキップし、
低下はファイル自体のコンテンツが変更されるまで永続的になります。

```sh
tesserae compile --changed-only --retry-fallbacks
```

マークされたドキュメントのみ再試行；クリーンなものはスキップのままです。

### 後処理パスより前にコンパイルされたグラフ

二つの修正は、モデルが抽出した内容を変えずに、コンパイル済みグラフの姿を変える。チャンク
ごとではなく文書ごとに一つのアンカー（チャンクでコンパイルした論文は 9.4 個を抱えてい
た）、そして綴りと型ごとではなくエンティティ名ごとに一つのノードである。どちらも `compile`
の内側で走るので、すでにディスクにあるグラフは再コンパイルするまでどちらも持たない。
`graph-repair` は同じ規則をグラフのバイトに適用する — モデルもネットワークも使わず数秒 —
そして修復したグラフは再コンパイルしたグラフと一致する。

```sh
tesserae graph-repair --dry-run     # 何が変わるかを報告するだけで、何も書かない
tesserae graph-repair               # .tesserae/graph.json をその場で書き直す
```

サイトとボールトは投影であり、ここでは再構築しない。公開しているなら、あとで
`export site` を実行すること。

## 階層構造の検査

```sh
tesserae graph-map                          # ルートマップ
tesserae graph-map --scope <scope_id>       # 下降
tesserae graph-map --scope '<alias>::'      # 兄弟登録プロジェクト
```

各カードは階層サイドカーから `size` と `leaf_member_count` を報告し、
さらに `live_member_count`—*現在の*グラフが実際に保持するメンバーの数。
`0` があるところはスコープが死んでいます（sidecar/グラフスキュー）：
下降するのではなくスキップしてください。

## エージェントがグラフに書き込む

`graph_write` (MCP)はスキーマ検証済みの型付きノードとエッジを必須の出所と共に受け取るため、エージェントは抽出器が型を推測する必要がある散文ではなく*構造*として発見を記録します。

強制ではなく拒否します。型なしエッジ、制御語彙外のノードまたはエッジ型、ダングリングエンドポイント、および出所がない書き込みはすべて拒否されます。重複書き込みはべき等です。エージェントが書いたノードは、完全な再コンパイル、削除された `graph.json`、`--limit`、および完全なコーパス削除に耐えます。

## グラフに対して主張を検証する

`verify_claim` (MCP) はグラフがトリプルをライセンスするかどうかに答えます。`(subject, predicate, object)` を取ります — **自然言語パラメータがありません**、設計上、パーサーが以前のバージョンに、それが支持した主張の否定に対して SUPPORTED で答えるようにさせたからです。

判定はグラフバイトの純粋な関数です：LLM、埋め込み、決定パスのどこにも曖昧なマッチングがありません。

| 判定 | 意味 |
|---|---|
| `SUPPORTED` | エッジが存在し、それ自体の証拠を持ち、そのテキストはソースファイルに対して再接地されました |
| `PRESENT_UNEVIDENCED` | エッジが存在しますが、文書がそれを支持していません |
| `CONTRADICTED` | 同じ 2 つのエンドポイント間のドキュメント裏付き `contradicts_claim` |
| `DISPUTED_UNEVIDENCED` | 主張された不一致、なし証拠 |
| `CONFLICTING` | 両極ともドキュメント裏付き — ツールが判定を辞退 |
| `ABSENT` | このグラフはトリプルを主張していません。反論ではありません |
| `NOT_RESOLVABLE` | エンドポイントまたは述語を正確に解決できません |

意図的にしないことが 2 つあります。`supersedes` を反論として扱いません — その関係は*ノード*が置き換えられたことを言う、トリプルが偽であることではありません。エージェント書き込みは出処クラスを*弱める*ことしかできず、1 つをアップグレードできない、そのため、エージェントが主張するものは文書接地として提示できません。

結果を読むときに値するという知識：15,284 エッジの実際のグラフで、約 40% の `SUPPORTED` 判定は同語反復です — `evidenced_by` エッジ、その引用されたスパンはエッジ自体のターゲットです。真ですが、情報がありません。

## 質問をルーティングする

`tesserae ask` は質問の形状によって検索パスを選択します：単一エンティティルックアップは安いバックエンドに行き、マルチホップ / "何が変わったか" / "なぜ" / コーパス全体の質問はグラフに行きます。この分岐が符号化しているのは**測定ではなく仮説**です：走査はマルチホップ、時間的、統合的な質問ではコストに見合い、単純な事実検索では無駄になると予想しています。このリポジトリにそれを検証するものはありません — 検索ベンチマークも、ルーティング表を裏づける公開された数値もないので、これは結論ではなく上書きすべきデフォルトとして扱ってください。

決定は返された封筒に表示されるため、安い回答は監査可能です。CLI では `--route` で、または MCP ツールでは `route` パラメータでオーバーライドします。
