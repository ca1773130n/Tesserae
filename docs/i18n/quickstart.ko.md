# 퀵스타트

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
이 페이지는 기존 프로젝트 디렉터리에서 탐색 가능한 Tesserae까지의 최단 경로를 보여줍니다.

## 명령 개요

CLI는 그룹화되어 있습니다: 최상위에 몇 가지 일상 동사, 그리고 나머지를 위한
그룹(`sessions`, `vault`, `export`, `code`, `config`, `projects`,
`integrations`, `lab`)이 있습니다. 전체 트리를 보려면 `tesserae --help`를
실행하세요:

```text
usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  ingest        Ingest a document file or URL into the knowledge base
  context       Compile agent-ready context for a query
  ask           LLM answer over the knowledge graph (planned retrieval)
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         raw retrieval: BM25/semantic + explicit backends
  lint          Graph lint report (--fix-trivial, --severity, --json)
  doctor        Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)
  summary       Daily/weekly activity digest (sessions, findings, commits, PRs, docs)
  decisions     Decisions across projects + time (human AskUserQuestion + agent)

GROUPS
  sessions      import | discover | list | chunk-backfill — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  setup         Machine-wide setup: LLM defaults + optional deps (interactive by default)
  config        llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping
  projects      register | list | unregister | mcp-config — registry
  sources       add | list | remove — manage compile source dirs (local & global)
  federation    status | explain — inspect cross-project federation
  integrations  refresh raganything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

개별 명령의 플래그는 `tesserae <command> --help`(예: `tesserae compile --help`)로
확인하세요.

## 1. 설정 마법사 실행

인덱싱하려는 프로젝트에서:

```bash
cd /path/to/my-project
tesserae init
```

`tesserae init`이 유일한 온보딩 단계입니다. 마법사는 `README.md`, `docs`, `src`, `lib`, `app`, `packages`, `data` 같은 흔한 소스를 감지하고, 어떤 LLM CLI가 설치되어 **로그인까지 되어 있는지** 탐침하고, LLM 프로바이더를 고르게 하고, `.tesserae/config.json`을 기록합니다. 선택적인 RAG-Anything 메모리 백엔드는 **기본적으로 꺼져** 있습니다; 나중에 config의 `memory_backends`에서 활성화하고, `tesserae query --backend raganything`으로 명시적으로 쿼리하세요.

비대화형 설정(CI, 스크립트)의 경우, `--yes`를 전달해 감지된 기본값을 프롬프트
없이 수락하세요(모든 선택적 통합 OFF):

```bash
tesserae init --yes
```

### LLM 프로바이더 설정

마법사의 프로바이더 선택(또는 동등한 플래그)은 다음 config 키를 영속화합니다:

| Config 키 | 플래그 | 설명 |
|---|---|---|
| `llm_provider` | `--llm-provider {claude,codex,anthropic,custom}` | LLM 클라이언트의 백엔드: `claude`/`codex`는 OAuth로 로그인된 CLI 사용; `anthropic`은 API 직접 사용; `custom`은 claude 호환 엔드포인트 대상. |
| `llm_model` | `--llm-model` | synthesis/insights LLM 클라이언트용 모델. |
| `llm_base_url` | `--llm-base-url` | `anthropic`/`custom`용 엔드포인트 기본 URL. |
| `llm_api_key` | `--llm-api-key` | `anthropic`/`custom`용 API 키. |

> **평문 경고.** `llm_api_key`는 `.tesserae/config.json`에 **평문**으로
> 저장됩니다. 대신 환경 변수를 선호하세요: `ANTHROPIC_API_KEY`(키),
> `ANTHROPIC_BASE_URL`(엔드포인트), `TESSERAE_LLM_MODEL`(모델). 해석 순서는
> env → 프로젝트 config → 머신 전역 config(`~/.tesserae/config.json`,
> `tesserae setup`이 기록) → 내장 기본값입니다.

기존 프로젝트에서 `init`을 재실행하면 **병합**됩니다 — 설정된 `sources`와
`memory_backends`는 뭉개지지 않고 보존됩니다.

비대화형 프로바이더 설정 예시:

```bash
tesserae init --yes --llm-provider codex
tesserae init --yes --llm-provider custom \
  --llm-base-url https://llm.internal.example/v1 \
  --llm-model my-model            # key via ANTHROPIC_API_KEY
```

> **마법사 건너뛰기.** `tesserae init --bare`는 소스 감지나 백엔드 탐침 없이
> 최소한의 `.tesserae/config.json`을 기록합니다 — 첫 compile 전에 config를
> 직접 편집하고 싶을 때 편리합니다.

## 2. 그래프와 프로젝션 compile

```bash
tesserae compile
```

`compile`은 내구성 있는 아티팩트를 기록합니다:

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
```

첫 실행 이후에는 `--changed-only`를 사용해 변경되지 않은 markdown 파일을 건너뛰세요 — 변경된 파일이 없으면 이전 그래프가 보존됩니다.

설정된 소스를 건드리지 않고 임시로 추가 경로를 ingest하려면 위치 인자로
전달하세요: `tesserae compile path/to/extra.md docs/`.

### 통합 노브는 이제 config에 있습니다

`tesserae compile`은 의도적으로 일상 플래그로 제한되어 있습니다(위치 인자
paths와 `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions`, 그리고 세 개의 LLM 플래그). 나머지 모든 예전
compile 플래그는 `.tesserae/config.json`의 `compile_options` 블록으로
이동했습니다; 예전 argparse 기본값이 여전히 폴백입니다. 동작을 바꾸려면
거기에 키를 설정하세요:

| `compile_options` 키 | 예전 플래그 | 기본값 | 하는 일 |
|---|---|---|---|
| `source_kind` | `--source-kind` | (없음) | 설정된 소스 종류를 재정의. |
| `trends` | `--trends` | `false` | 코퍼스 수준 Trend 노드 추가. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Trend 노드에 필요한 최소 소스 수. |
| `exclude_data` | `--exclude-data` | `false` | 암묵적인 `project_root/data` 자동 포함을 건너뜀. |
| `no_vault_pull` | `--no-vault-pull` | `false` | compile 전에 기존 vault 편집을 되돌려 받지 않음. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | 이전 추출 결과를 실행에 피드백. |
| `sessions_llm` | `--sessions-llm` | (auto) | LLM 세션 추출 모드(`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (없음) | 세션 추출에 사용되는 LLM 모델을 재정의. |

> **Cognee는 0.19에서 제거되었습니다.** cognee 백엔드는 0.18에서 강등되었고
> 그래프에 실제로 데이터를 공급한 적이 없습니다. `memory_backends.cognee`
> 섹션(또는 `cognee_*` compile 옵션)을 아직 갖고 있는 config는 계속
> 로드됩니다 — 해당 섹션은 한 줄짜리 안내와 함께 무시됩니다.

> **원샷 파이프라인.** `tesserae refresh`는 전체 루프를 프로세스 내에서 실행합니다 — 새 에이전트 세션을 가져오고, compile하고, vault를 하나의 명령으로 동기화합니다. 옵트인 증분 compile은 `--changed-only`를 전달하세요.

## 3. 정적 프론트엔드 빌드 및 서빙

`serve`는 사이트가 없으면 자동으로 빌드하므로, 명령 하나로 탐색 가능한
Tesserae를 얻습니다. **인자 없는 `serve`는 등록된 모든 프로젝트**를 하나의
서버 아래에서 서빙합니다 — `/`에 프로젝트 랜딩, 각 프로젝트는 `/<alias>/`,
헤더에는 프로젝트 간 이동을 위한 Projects 스위처. 페이지 내 **ask 위젯은 두
모드 모두에서 라이브로 동작**하며, 현재 보고 있는 페이지의 프로젝트로
라우팅됩니다:

```bash
tesserae serve --port 8765                 # all registered projects
tesserae serve --project . --port 8765     # just this one
```

열기:

```text
http://127.0.0.1:8765/
```

사이트를 명시적으로 빌드하려면(예: 서빙 없이 배포용) `export site`를
사용하세요; 재빌드 없이 이전에 빌드된 사이트를 탐색하고 싶을 때는 `serve`에
`--no-build`를 전달하세요:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### 저장 시 자동 재빌드

내장 감시자와 dev 서버를 함께 쓰면 `data/`와 `docs/` 아래의 편집이 증분 재compile을 트리거합니다:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch`는 2초마다 폴링하고, 1초 디바운스하며, `compile --changed-only`를 실행합니다. cron 스타일 재빌드에는 `--once`(`.tesserae/.watch-cache.json` 대비 스냅샷), 사용자 지정 감시 디렉터리 추가에는 `--paths <dir>`, 주기 조정에는 `--interval` / `--debounce`를 사용하세요.
<!-- END: subagent-r-watch -->

### refresh 데몬 실행

지식 베이스를 스스로 신선하게 유지하는 상시 가동 엔진 — 소스를 감시하고, 편집 버스트를 병합하고, 자동 재compile — 을 원하면 감독(supervised) 데몬을 시작하세요:

```bash
tesserae engine
```

`engine`은 장수 슈퍼바이저입니다: 2초마다 폴링하고 각 재빌드 전에 1초의 조용한 윈도우를 기다립니다. `--interval`과 `--debounce`로 주기를 조정하고, `--project`로 다른 프로젝트를 가리키고, `--once`를 전달하면 단일 결정적 drain 사이클을 실행하고 종료합니다(cron이나 CI에 유용). 이는 `export site --watch`의 핸즈오프 대응물입니다: 켜 두면 사용자와 에이전트가 작업하는 동안 그래프, vault, 사이트가 최신으로 유지됩니다.

보이는 모든 라우트 — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, 그리고 AI 시블링 — 의 주석 달린 투어는 [`docs/frontend-redesign.md`](frontend-redesign.ko.md)를 참조하세요.

프론트엔드는 의존성이 가볍고 다음을 기록합니다:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. 로컬 에이전트 세션 이력 가져오기

세션 이력 가져오기는 명시적입니다: 일반 compile/build는 이미 정규화된 세션을 읽을 뿐, 스스로 비공개 Claude Code나 Codex 트랜스크립트 저장소를 스캔하지 않습니다.

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

가져온 세션은 글로벌 Sessions 섹션, 사이트 검색, 홈 Browse 카드에 나타납니다. 세션 상세 페이지는 user/assistant 턴을 읽기 좋은 markdown으로 렌더링하고, tool-use 블록을 직전 assistant 턴 아래에 부착하며, `#turn-N` 내비게이션을 위한 왼쪽 턴 레일을 노출합니다. 프라이버시 참고 사항, 가져오기 형식, 현재 트랜스크립트 타이포그래피 맵은 [`docs/session-history.md`](session-history.ko.md)를 참조하세요.

## 5. 위키 lint

```bash
tesserae lint
```

컴파일된 그래프 + 위키 + 사이트를 순회하며 고아 paper, 오래된 citation, 그래프와 wiki/ 사이의 드리프트, 유령 synthesis 입력 등을 플래그합니다. `.tesserae/lint-report.md`와 `.tesserae/lint-report.json`을 기록합니다. 안전한 자동 수정(누락된 `implemented_in` 엣지, 유령 입력 정리)을 적용하려면 `--fix-trivial`을, 오류에서만 종료 코드를 실패시키려면 `--severity error`를 전달하세요.

그래프 자체를 넘어선 워크스페이스 건강 상태 — 레지스트리 일관성, staleness, lock, LLM 로그인, 위생 — 는 `tesserae doctor`를 실행하세요(`--fix`는 안전한 복구만 적용). [`docs/doctor.md`](doctor.ko.md)를 참조하세요.

## 6. 위키에 ask와 query

```bash
# LLM-planned, cited answer over the compiled graph (the default):
tesserae ask "What is Gaussian Splatting?"

# Raw retrieval — ranked search hits, no LLM:
tesserae query "What is Gaussian Splatting?"
```

`ask`는 답변 표면입니다: 모델이 컴파일된 그래프 위에서 retrieval을 계획한 뒤 citation이 달린 답변을 합성합니다. 로그인된 `claude`/`codex` CLI(OAuth) 또는 `ANTHROPIC_API_KEY`로 동작합니다; 순위 매겨진 검색 결과만 원하면 `--no-llm`을 전달하세요(이 강제 off는 `TESSERAE_QUERY_LLM=1`을 이깁니다). `TESSERAE_QUERY_DRY_RUN=1`은 API 호출 없이 프롬프트를 연습합니다.

`query`는 retrieval 표면입니다: `.tesserae/site/search-index.json` 위의 BM25/시맨틱 검색이며, 매칭되는 `wiki/<kind>/<slug>.md`에서 200자 발췌를 가져옵니다. 좁히려면 `--kind papers`(또는 `concepts`, `repos` 등)를, 넓히려면 `--top-k N`을, 구조화된 출력에는 `--json`을 전달하세요; `--interactive`는 readline REPL을 엽니다 — 빈 줄이나 EOF로 종료합니다. 명시적 메모리 백엔드도 여기에 있습니다: `--backend raganything`은 해당 백엔드로 바로 가서 그 오류를 노출합니다. `query`에는 LLM 합성이 없습니다 — 그건 `ask`입니다.

## 7. 온디맨드로 에이전트 준비 컨텍스트 compile

v0.5.0의 헤드라인은 온디맨드 컨텍스트 컴파일러입니다: 컴파일된 그래프에 질의 범위로 한정되고 에이전트의 윈도우에 맞게 크기가 조정된, citation이 달린 단일 컨텍스트 문서를 요청하세요.

```bash
tesserae context "How does session import work?"
```

질의에 매칭되는 노드에서 Personalized PageRank를 시드하고(명시적으로 시드하려면 `--seeds <node_id>` 사용), 이웃을 확장하고(`--depth`, 기본 2), 문자 `--budget`(기본 32000; 무제한은 `<= 0` 전달)으로 상한이 정해진 citation 문서를 조립합니다. 그 위에 LLM 작성 요약을 원하면 `--llm`을 추가하고(LLM 백엔드 필요), stdout 대신 디스크에 문서를 기록하려면 `-o/--output <file>`을 사용하세요.

같은 컴파일러가 MCP를 통해 `compile_context` 도구로 에이전트에 노출되므로, 코딩 에이전트가 수동 export 없이 대화 중에 딱 필요한 만큼의 예산 제한 프로젝트 컨텍스트를 가져올 수 있습니다.

## 8. 에이전트 harness 파일 내보내기

```bash
tesserae export harness
```

지원 타깃:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

부분 집합 예시:

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Obsidian vault 내보내기

```bash
tesserae vault export
```

또는 기존 vault에 기록:

```bash
tesserae vault export --output "$OBSIDIAN_VAULT_PATH"
```

vault에는 markdown 프로젝션, `.obsidian` 기본값, 그래프 색상, `raw/assets/`, Dataview 대시보드가 포함됩니다. 기존 vault를 최신 compile과 조정하려면 `tesserae vault sync`를 사용하세요(고아 노트를 제거하려면 `--prune` 추가).

## 10. MCP 설정

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

출력을 `~/.hermes/config.yaml`의 `mcp_servers` 아래에 붙여넣고 Hermes/gateway를 재시작하세요.

## 11. Graphiti 내보내기 / 동기화

의존성 없는 에피소드 내보내기:

```bash
tesserae export graphiti
```

Graphiti 설치 없이 dry-run 동기화 스모크:

```bash
tesserae export graphiti --sync --dry-run
```

라이브 동기화에는 `graphiti_core`와 접근 가능한 Neo4j 백엔드가 필요합니다:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. GitHub Pages에 배포

`.tesserae/site/`의 컴파일된 사이트를 프로젝트 git origin의 `gh-pages` 브랜치에 푸시하세요:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build`는 먼저 `compile`을 실행해 사이트를 신선하게 합니다. `--enable-pages`는 `gh` CLI를 통해 Pages를 켭니다(멱등적; `gh`가 없으면 힌트와 함께 건너뜀). 푸시 없이 스테이징과 커밋만 하려면 `--dry-run`을, 기본값 재정의에는 `--branch` / `--remote`를, 더러운 워킹 트리로도 배포를 허용하려면 `--force`를 사용하세요.

사이트는 `https://<owner>.github.io/<repo>/`에서 접근 가능해집니다.
