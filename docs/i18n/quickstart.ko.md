# 빠른 시작

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
이 페이지는 기존 프로젝트 디렉터리에서 탐색 가능한 Tesserae까지 가는 가장 짧은 경로를 보여줍니다.

## 명령 개요

CLI는 그룹으로 구성됩니다. 최상위에 몇 개의 일상 동사가 있고, 나머지는 그룹
(`sessions`, `vault`, `export`, `code`, `config`, `projects`, `integrations`,
`lab`)으로 묶여 있습니다. 전체 트리를 보려면 `tesserae --help`를 실행하세요.

```text
usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  context       Compile agent-ready context for a query
  ask           Ask the project memory a question
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         Raw retrieval over the graph (top-k, kind filters)
  lint          Graph lint report (--fix-trivial, --severity, --json)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  config        llm | show — machine-wide defaults (~/.tesserae/config.json)
  projects      register | list | activate | unregister | mcp-config — registry
  integrations  refresh raganything|understand-anything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

개별 명령의 flag를 보려면 `tesserae <command> --help`(예: `tesserae compile --help`)를 실행하세요.

## 1. 설정 마법사 실행

색인화하려는 프로젝트에서:

```bash
cd /path/to/my-project
tesserae init
```

마법사는 `README.md`, `docs`, `src`, `lib`, `app`, `packages`, `data` 같은 일반적인 source를 감지한 뒤 `.tesserae/config.json`을 씁니다. 또한 기본 Cognee backend를 설정하여 `tesserae ask`가 Cognee를 시도하고 compiled wiki search로 fallback할 수 있게 합니다.

비대화형 설정(CI, 스크립트)을 원하면 `--yes`를 전달해 프롬프트 없이 감지된 기본값을 그대로 수락합니다.

```bash
tesserae init --yes
```

Understand Anything과 Cognee runtime memory를 활성화한 완전 자동 설정:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
```

수행 내용:

| Flag | Effect |
|---|---|
| `--with-understand-anything` | UA graph projection을 source로 추가합니다. |
| `--install-understand-anything` | UA companion skills를 설치/업데이트합니다. |
| `--understand-anything-platform codex` | Codex를 사용해 Tesserae의 managed UA refresh wrapper를 실행합니다. |
| `--with-raganything` | RAG-Anything을 통한 multimodal ingestion을 활성화합니다. |
| `--install-raganything` | 설정 중 raganything[all]을 설치합니다. |
| `--raganything-parser` | 파서 선택: mineru(기본값), docling, paddleocr. |
| `--run-raganything` | 모든 compile에서 RAG-Anything을 자동 refresh합니다. |
| `--run-cognee` | compile 중 best-effort Cognee runtime cognify를 실행합니다. |
| `--install-cognee` | Cognee가 없으면 현재 Python으로 설치합니다. |

사용자는 UA install path를 알거나 `/understand`를 입력할 필요가 없습니다. UA graph가 없거나 오래되면 `tesserae compile`이 `tesserae integrations refresh understand-anything`을 실행합니다.

> **마법사 건너뛰기.** `tesserae init --bare`는 source 감지나 backend 프로빙 없이 최소한의 `.tesserae/config.json`만 씁니다. 첫 compile 전에 config를 직접 편집하고 싶을 때 유용합니다.

## 2. 그래프와 projection 컴파일

```bash
tesserae compile
```

`compile`은 durable artifact를 씁니다.

```text
.tesserae/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  competitive_report.md
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
  cognee_bundle/
```

첫 실행 후에는 `--changed-only`를 사용해 변경되지 않은 markdown 파일을 건너뛰면서, 파일 변경이 없을 때 이전 graph를 보존할 수 있습니다. Understand Anything이 활성화되어 있으면 compile은 먼저 `.tesserae/external/understand-anything.md`를 refresh/materialize합니다. Cognee runtime이 활성화되어 있으면 `.tesserae/cognee_bundle/` 작성 후 best-effort로 Cognee도 업데이트합니다.

구성된 source를 건드리지 않고 추가 경로를 ad-hoc으로 ingest하려면 위치 인자로 전달하세요: `tesserae compile path/to/extra.md docs/`.

### 통합 옵션은 이제 config에 있습니다

`tesserae compile`은 일상 flag(위치 인자 paths, `--project`, `--changed-only`,
`--limit`, `--refresh-integrations`, `--sessions`/`--no-sessions`, 그리고 3개의
LLM flag)로 의도적으로 제한됩니다. 그 외 이전의 모든 compile flag는
`.tesserae/config.json`의 `compile_options` 블록으로 이동했습니다. 이전 argparse
기본값이 여전히 fallback입니다. 동작을 바꾸려면 해당 키를 설정하세요.

| `compile_options` 키 | 이전 flag | 기본값 | 설명 |
|---|---|---|---|
| `source_kind` | `--source-kind` | (없음) | 구성된 source kind를 재정의합니다. |
| `trends` | `--trends` | `false` | corpus 수준 Trend 노드를 추가합니다. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Trend 노드에 필요한 최소 source 수. |
| `exclude_data` | `--exclude-data` | `false` | 암시적 `project_root/data` 자동 포함을 건너뜁니다. |
| `no_vault_pull` | `--no-vault-pull` | `false` | compile 전에 기존 vault 편집을 다시 pull하지 않습니다. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | 이전 extraction 결과를 다시 입력으로 사용합니다. |
| `sessions_llm` | `--sessions-llm` | (auto) | LLM 세션 추출 모드(`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (없음) | 세션 추출에 사용하는 LLM 모델을 재정의합니다. |
| `cognee_add` | `--cognee-add` | `false` | Cognee bundle을 dataset에 추가합니다(cognify 없음). |
| `cognee_cognify` | `--cognee-cognify` | `false` | bundle을 추가하고 Cognee cognify를 실행합니다. |
| `cognee_codex_cognify` | `--cognee-codex-cognify` | `false` | Cognee의 LLM client를 Codex로 패치해 cognify를 실행합니다. |
| `cognee_codex_model` | `--cognee-codex-model` | `gpt-5.4` | `cognee_codex_cognify`용 Codex CLI 모델. |
| `cognee_codex_timeout` | `--cognee-codex-timeout` | `300` | Codex CLI 호출당 timeout(초). |
| `cognee_dataset` | `--cognee-dataset` | `tesserae_research_graph` | Cognee dataset 이름. |
| `cognee_embedding_provider` | `--cognee-embedding-provider` | `deterministic` | Cognee lane의 embedding provider. |
| `cognee_ollama_embedding_model` | `--cognee-ollama-embedding-model` | `qwen3-embedding:0.6b` | Ollama embedding 모델. |
| `cognee_ollama_embedding_endpoint` | `--cognee-ollama-embedding-endpoint` | `http://127.0.0.1:11434/api/embed` | Ollama `/api/embed` endpoint. |
| `cognee_ollama_embedding_timeout` | `--cognee-ollama-embedding-timeout` | `120` | Ollama embedding 요청 timeout(초). |
| `cognee_local_embedding_dimensions` | `--cognee-local-embedding-dimensions` | `128` | 로컬 embedding 차원 수. |
| `cognee_system_root` | `--cognee-system-root` | (없음) | 격리된 Cognee system root 디렉터리. |
| `cognee_data_root` | `--cognee-data-root` | (없음) | 격리된 Cognee data root 디렉터리. |

> **원샷 파이프라인.** `tesserae refresh`는 전체 루프를 in-process로 실행합니다. 즉, 새 agent session을 import하고, compile하고, vault를 sync하는 작업을 하나의 명령으로 처리합니다. opt-in 증분 compile에는 `--changed-only`를 사용하세요.

## 3. 정적 frontend 빌드 및 제공

`serve`는 site가 없으면 자동으로 빌드하므로, 단일 명령으로 탐색 가능한 Tesserae를 얻을 수 있습니다.

```bash
tesserae serve --port 8765
```

열기:

```text
http://127.0.0.1:8765/
```

site를 명시적으로 빌드하려면(예: 제공 없이 배포할 때) `export site`를 사용하세요. 이미 빌드된 site를 다시 빌드하지 않고 탐색하려면 `serve`에 `--no-build`를 전달합니다.

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### 저장 시 자동 재빌드

개발 서버를 내장 watcher와 함께 사용하면 `data/` 및 `docs/` 아래 편집이 incremental recompile을 트리거합니다.

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch`는 2초마다 polling하고 1초 debounce 후 `compile --changed-only`를 실행합니다. cron 스타일 rebuild에는 `--once`(snapshots vs `.tesserae/.watch-cache.json`), custom watch dir 추가에는 `--paths <dir>`, cadence 조정에는 `--interval` / `--debounce`를 사용하세요.
<!-- END: subagent-r-watch -->

### refresh daemon 실행

소스를 감시하고, 편집 burst를 합치며, 자동으로 recompile하여 지식 베이스를 스스로 항상 최신으로 유지하는 상시 엔진을 원한다면 supervised daemon을 시작하세요.

```bash
tesserae engine
```

`engine`은 장기 실행 supervisor입니다. 2초마다 polling하고, 각 rebuild 전에 1초의 정적 구간을 기다립니다. cadence는 `--interval`과 `--debounce`로 조정하고, 다른 프로젝트를 대상으로 하려면 `--project`를, 단일 결정적 drain 사이클을 실행하고 종료하려면(cron이나 CI에 유용) `--once`를 사용하세요. 이는 `export site --watch`의 무인 버전입니다. 계속 실행해 두면 작업하는 동안 graph, vault, site가 최신 상태로 유지됩니다.

보이는 모든 route(home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, AI siblings)에 대한 주석 달린 tour는 [`docs/frontend-redesign.md`](frontend-redesign.ko.md)를 참고하세요.

Frontend는 dependency-light이며 다음을 씁니다.

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. 로컬 agent 세션 기록 가져오기

세션 기록 import는 명시적입니다. 일반 compile/build는 이미 정규화된 세션을 읽지만 private Claude Code 또는 Codex transcript store를 자체적으로 스캔하지 않습니다.

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae sessions discover --import

# Confirm the imported set:
tesserae sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae export site
```

가져온 세션은 global Sessions section, site search, home Browse cards에 나타납니다. 세션 detail page는 user/assistant turn을 읽기 쉬운 markdown으로 렌더링하고, tool-use block을 앞선 assistant turn 아래 붙이며, `#turn-N` navigation을 위한 왼쪽 turn rail을 제공합니다. privacy notes, import formats, 현재 transcript typography map은 [`docs/session-history.md`](session-history.ko.md)를 참고하세요.

## 5. Wiki lint

```bash
tesserae lint
```

Compiled graph + wiki + site를 순회하면서 orphan papers, stale citations, graph와 wiki/ 간 drift, ghost synthesis inputs 등을 표시합니다. `.tesserae/lint-report.md`와 `.tesserae/lint-report.json`을 씁니다. 안전한 auto-fix(missing `implemented_in` edges, ghost-input pruning)를 적용하려면 `--fix-trivial`, error일 때만 exit code 실패를 원하면 `--severity error`를 전달하세요.

## 6. Wiki 질의

```bash
tesserae query "What is Gaussian Splatting?"
```

기본은 search-only입니다. `.tesserae/site/search-index.json` 위의 BM25와 일치하는 `wiki/<kind>/<slug>.md`에서 가져온 200자 excerpt를 사용합니다. 좁히려면 `--kind papers`(또는 `concepts`, `repos` 등), 넓히려면 `--top-k N`, structured output은 `--json`을 전달하세요. `[node_id]` citation이 있는 합성 답변을 Claude에 요청하려면 `--llm`을 추가하거나 `TESSERAE_QUERY_LLM=1`을 설정합니다. `--interactive`는 readline REPL을 열며 blank line 또는 EOF로 종료합니다. `TESSERAE_QUERY_DRY_RUN=1`은 API 호출 없이 prompt를 실행해 봅니다.

## 7. 온디맨드로 agent용 context 컴파일

v0.5.0의 핵심 기능은 On-Demand Context Compiler입니다. 컴파일된 graph에 query 범위로 한정된 단일 인용 context 문서를 요청하고, agent의 context window에 맞게 크기를 조절합니다.

```bash
tesserae context "How does session import work?"
```

query와 일치하는 노드에서 Personalized PageRank를 seed로 시작하고(명시적으로 seed하려면 `--seeds <node_id>` 사용), 이웃을 확장한 뒤(`--depth`, 기본값 2), 문자 `--budget`(기본값 32000, `<= 0`이면 무제한)으로 제한된 인용 문서를 조립합니다. 그 위에 LLM이 작성한 요약을 추가하려면 `--synthesize`(LLM 백엔드 필요)를, 문서를 stdout 대신 파일로 쓰려면 `-o/--output <file>`을 사용하세요.

같은 compiler가 MCP를 통해 `compile_context` 도구로 agent에게 노출되므로, 코딩 agent는 수동 export 없이 대화 도중에 budget으로 제한된, 딱 필요한 만큼의 프로젝트 context를 가져올 수 있습니다.

## 8. Agent harness 파일 export

```bash
tesserae export harness
```

지원 target:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

부분 예시:

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Obsidian vault export

```bash
tesserae vault export
```

또는 기존 vault에 쓰기:

```bash
tesserae vault export --vault "$OBSIDIAN_VAULT_PATH"
```

Vault에는 markdown projections, `.obsidian` defaults, graph coloring, `raw/assets/`, Dataview dashboard가 포함됩니다. 기존 vault를 최신 compile과 맞추려면 `tesserae vault sync`를 사용하세요(고아 노트를 제거하려면 `--prune` 추가).

## 10. MCP 설정

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

출력을 `~/.hermes/config.yaml`의 `mcp_servers` 아래 붙여 넣은 뒤 Hermes/gateway를 재시작하세요.

## 11. Graphiti export / sync

Dependency-free episode export:

```bash
tesserae export graphiti
```

Graphiti 설치 없이 dry-run sync smoke:

```bash
tesserae export graphiti --sync --dry-run
```

Live sync에는 `graphiti_core`와 접근 가능한 Neo4j backend가 필요합니다.

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. GitHub Pages에 배포

`.tesserae/site/`의 compiled site를 프로젝트 git origin의 `gh-pages` branch로 push합니다.

```bash
tesserae export site --deploy --build --enable-pages
```

`--build`는 먼저 `compile`을 실행해 site를 최신으로 만듭니다. `--enable-pages`는 `gh` CLI를 통해 Pages를 켭니다(idempotent, `gh`가 없으면 hint와 함께 skip). push 없이 stage/commit하려면 `--dry-run`, default override에는 `--branch` / `--remote`, dirty working tree에서 배포 허용에는 `--force`를 사용하세요.

사이트는 `https://<owner>.github.io/<repo>/`에서 접근할 수 있습니다.
