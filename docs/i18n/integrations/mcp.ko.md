# MCP — Tesserae를 Claude Code, Codex, Cursor에 연결하기

<!-- translations:start -->
<p align="center"><a href="../../integrations/mcp.md">English</a> · <a href="mcp.zh.md">中文</a> · <a href="mcp.ja.md">日本語</a> · <a href="mcp.ru.md">Русский</a> · <a href="mcp.es.md">Español</a> · <a href="mcp.fr.md">Français</a> · <a href="mcp.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae는 컴파일된 타입 그래프를 MCP를 지원하는 모든 클라이언트(Claude Code, Codex CLI, Cursor, Claude Desktop 등)에 노출하는 [Model Context Protocol](https://modelcontextprotocol.io) stdio 서버를 함께 제공합니다. 이 서버는 세 가지 MCP 표면(**tools**, **resources**, **prompts**)을 모두 광고하므로, 클라이언트는 필요에 따라 그래프를 쿼리할 수도 있고 정형 URI로부터 저렴하게 컨텍스트를 시드할 수도 있습니다.

## 사전 요구사항

서버는 `.tesserae/graph.json`에서 읽어오므로, 한 번의 컴파일이 필요합니다:

```bash
cd /path/to/your-project
tesserae init    # interactive; or --yes for non-interactive
tesserae compile  # deterministic, no LLM calls, no API keys
```

소스가 변경될 때마다 다시 컴파일하세요. 서버는 재시작 없이 다음 tool 호출에서 새 그래프를 자동으로 인식합니다.

## 1) 클라이언트 설정 생성

```bash
tesserae projects mcp-config
```

대략 다음과 같은 JSON 스니펫을 출력합니다:

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "python3",
      "args": [
        "-m", "tesserae.mcp_server",
        "--graph", "/path/to/your-project/.tesserae/graph.json"
      ]
    }
  }
}
```

정확한 경로는 현재 프로젝트에 맞춰 채워집니다. 서버 항목 이름을 `tesserae`가 아닌 다른 이름으로 지정하고 싶다면 `--name <alias>`를 전달하세요.

## 2) MCP 클라이언트에 붙여넣기

| 클라이언트 | 설정 위치 |
|---|---|
| Claude Code | `~/.claude/mcp-servers.json` (or `~/.config/claude-code/mcp-servers.json`) |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex CLI | `~/.config/codex/mcp-servers.json` |
| Cursor | Settings → MCP Servers → paste JSON |
| Hermes | `~/.hermes/config.toml` (use the TOML-equivalent block printed by `mcp-config --format hermes`) |

편집 후 클라이언트를 재시작하세요. 다음 세션에서 연결되어 Tesserae 표면이 인식됩니다.

## 3) 클라이언트가 보는 것

### Tools — 모델이 호출하는 도구

모든 tool은 선택적 `graph_path` 또는 `project`(레지스트리 별칭)를 받아 하나의 서버가 호출마다 등록된 어떤 vault든 해석할 수 있습니다. 생략 시 활성 프로젝트로 폴백합니다.

**그래프 쿼리 및 검색**

| Tool | 용도 |
|---|---|
| `graph_map` | **여기서 시작하세요.** 그래프 계층의 예산 지도 — Descent의 진입점. 스코프 없이 호출하면 루트 카드 집합(카운트, 상위 허브, 가장 거친 커뮤니티당 카드 하나), `scope='<카드의 scope_id>'`는 덴드로그램을 한 단계 내려가고, `org:root`는 에이전트 조직 트리를 걷습니다. 검색어를 추측하지 않고도 에이전트의 방향을 잡아 줍니다 |
| `schema` | 통제된 node, edge, wiki-kind 어휘 |
| `graph_summary` | 활성 프로젝트의 노드 및 엣지 개수와 타입 분포 |
| `search_nodes` | 공개 그래프 노드를 `query`, `type`/`types`, `kind`, `limit`, 하이브리드 `mode`/`weights`로 필터링. `include_superseded`로 폐기된 노드도 노출 |
| `node_context` | 노드 + 인접 엣지 + 이웃 노드. `use_ppr`는 1-홉 탐색 대신 개인화 PageRank로 이웃을 랭킹하며 `include_superseded`, `limit`로 결과를 한정 |
| `embedding_status` | 하이브리드 검색을 구동하는 활성 임베딩 백엔드 보고 |
| `search_facts` | 그래프에서 투영된 시간적 사실(Graphiti 스타일); `current_only`는 현재 사실만 필터, `as_of`는 과거 시점 기준으로 응답. 두 옵션의 동시 사용은 거부됩니다(서로 다른 시계를 뜻하므로). `undated_included`는 반환된 행 중 날짜가 없는 건수를 보고합니다 |
| `timeline` | 종단적 관점을 위해 `valid_from` 기준으로 정렬된 사실 |
| `graph_ppr` | 하나 이상의 `seed_node_id`에서 시드된 개인화 PageRank로 가장 관련성 높은 top-K 노드 반환; `alpha`, `directed`, `edge_type_weights` 조정 가능 |
| `wiki_page` | 노드에 대해 컴파일된 markdown 페이지 본문과 참조하는 내부 링크 |
| `raw_source` | 원본 소스 markdown (16 KB로 제한) |
| `verify_claim` | 트리플 하나를 그래프에 대해 검증합니다 — 정확한 조회이며 LLM도, 퍼지 매칭도, 순위 결과도 없습니다. `{verdict, reason, triple, citation, provenance, advisory}`를 반환하며 `verdict`는 `SUPPORTED`(엣지가 존재하고 **그 증거가 문서의 축자 구간**), `PRESENT_UNEVIDENCED`, 또는 거부입니다. 산문만 있다면 `search_nodes` → `verify_claim`으로 이어 부르세요 |
| `doctor_run` | 헬스 체크를 실행하고 보고서를 JSON(`findings`, `exit_code` 0/1/2)으로 반환합니다. **항상 읽기 전용** — MCP에서는 수정이 실행되지 않으며, 복구는 CLI의 `tesserae doctor --fix`를 쓰세요 |
| `doctor_report` | `.tesserae/doctor-report.md`의 내용(64 KB 제한). `tesserae doctor`를 실행하기 전까지는 비어 있습니다 |
| `lint_report` | 가장 최근의 컴파일 시점 lint 결과 (64 KB로 제한) |

**온디맨드 컨텍스트 컴파일러** (Phase 7)

| Tool | 용도 |
|---|---|
| `compile_context` | `query` 또는 명시적 `seeds`에 대해 맞춤형 **인용 포함** 컨텍스트 문서를 컴파일. 깊이 제한 서브그래프(`depth`, 1–10, 기본 2)를 탐색하고 PPR로 랭킹한 뒤 문자 `budget`(기본 32000; `0`이면 무제한)를 채움. 기본은 결정론적이며 `synthesize: true`면 LLM이 작성한 서사형 "topic" 슬라이스를 생성. `body`, `citations`, `selected_node_ids`, `char_budget_used` 반환. `view`는 walk를 명명된 edge partition으로 제한합니다 — `semantic`, `temporal`, `causal` 또는 `entity`; names의 배열을 전달하여 view당 한 번의 walk를 실행하고 이들을 fuse합니다 (weighted RRF). view를 요청하면 — 이름 하나든 여러 개든 — 각 citation은 `via_views`(그에 도달한 walk의 views)를 가집니다 |
| `get_handle` | 앞서 `handle`로 반환된 큰 페이로드(예: `preview`를 쓴 `compile_context`)를 조각(`offset`, `limit`)으로 페이징합니다 — 전부를 컨텍스트에 쏟아 넣는 대신 필요할 때 더 가져옵니다 |
| `list_communities` | 후처리 패스가 생성한 `COMMUNITY_SUMMARY` 노드를 멤버 수 기준으로 나열(`min_size`, `limit`); `node_context`로 `summarizes` 엣지를 따라 멤버로 회귀 |
| `fresh_insights` | 에빙하우스 스타일 감쇠 점수(최신 + 최다 접근 우선)로 랭킹된 세션 발견; 폐기된 근사 중복은 제외. 선택적 `kind`, `limit`, `include_superseded` |

**세션 메모리** ([sessions.md](sessions.ko.md) 참조)

| Tool | 용도 |
|---|---|
| `list_sessions` | 활성 프로젝트의 세션 엔벨로프(id, started_at, title, files_touched, 발견 개수); `since`, `limit` |
| `find_session_findings` | `discussed_in` / `references`를 통해 `node_id`에 연결된 모든 세션 발견. `kinds`(insight / decision / question / todo / hypothesis / takeaway)로 필터 가능 |
| `find_code_symbol_mentions` | 세션 발견을 그것이 언급하는 `CodeFunction`/`CodeClass`/`CodeMethod` 심볼로 확장(옵트인 insight↔symbol 연결 패스의 `discusses` 엣지 사용). 코드 레이어는 옵트인입니다. `codegraph`에 대한 `external_tools` 항목이 없으면 아무것도 반환하지 않습니다 |
| `activity_summary` | 등록된 프로젝트 전반의 일간/주간 다이제스트 — 세션, 발견, git 커밋, PR, 수집된 문서. 각 항목은 세션의 `started_at`이 아니라 **자기 자신의** 타임스탬프로 창을 잡습니다. 결정적 마크다운이며, 끄지 않는 한 LLM 서사가 앞에 붙습니다 |
| `query_decisions` | 기간 내 등록된 프로젝트들의 결정: Claude Code의 `AskUserQuestion`에서 결정적으로 파싱한 명시적 **인간** 선택(질문과 고른 선택지), 그리고 대화에서 캐낸 에이전트 결정 |

**에이전트 메모리와 되쓰기** ([agent-memory.ko.md](../agent-memory.ko.md) 참고)

| 도구 | 용도 |
|---|---|
| `agent_view_explain` | 에이전트 스코프 뷰를 *로드하지 않고* 설명합니다: 해석 모드(worker / manager / org), 구성원 에이전트, 각 L1 아티팩트의 경로와 노드 수, 그리고 `distilled_through` 신선도 워터마크 |
| `drill_down` | 증류본의 `member_ref`를 원본 L0 노드로 되돌립니다 — 증류된 가시성을 넘어서는 관리자의 명시적이고 감사 기록되는 에스컬레이션. 상태는 `alive` / `changed` / `absorbed` / `gone`이며 모든 호출이 사이드카에 기록됩니다 |
| `graph_write` | 타입 지정 노드와 엣지를 그래프에 직접 씁니다 — 마크다운도, 추출 패스도 없습니다. append-only 오버레이에 추가되어 컴파일 생산자로 재생되므로 **재컴파일을 견딥니다**. 엄격합니다: 알 수 없는 타입, 증거 없는 엣지, 이 페이로드에도 없고 기존 노드 id도 아닌 엔드포인트는 모두 거부됩니다. 그냥 틀린 것을 대체물을 지어내지 않고 **철회하려면**: `retracts` 엣지를 틀린 노드에 **id로** 겨누십시오 — 대상은 모든 기본 읽기(`search_nodes`, `fresh_insights`, `node_context`, `compile_context`)에서 억제되지만 `include_superseded: true`로는 여전히 닿을 수 있고, 아무것도 삭제되지 않습니다 |

**Q&A 및 레지스트리**

| Tool | 용도 |
|---|---|
| `ask` | 구성된 메모리 백엔드(raganything, cognee, 또는 컴파일된 위키)를 통한 자연어 Q&A. `backend`, `top_k`; `scope`/`scope_aliases`로 다중 vault 팬아웃; 다중 계정 라우팅용 `claude_config_dir` |
| `query` | LLM 없는 원시 검색 — `tesserae query`를 그대로 반영합니다. `backend='wiki'`(기본)는 컴파일된 위키에 대한 결정적 BM25/시맨틱 검색으로 발췌가 달린 순위 결과를 돌려주고, `backend='raganything'`은 프로젝트가 활성화한 경우 선택형 멀티모달 RAG 인덱스에 질의합니다. 합성된 인용 답변은 `ask`를 쓰세요 |
| `ingest` | 원시 웹/텍스트 콘텐츠(예: 브라우저 클립)를 해석된 프로젝트의 지식 그래프로 수집합니다 |
| `list_projects` | 등록된 프로젝트 목록 |
| `register_project` | 레지스트리에 프로젝트 추가 |
| `unregister_project` | 레지스트리에서 프로젝트 제거 (특권적인 "활성" 프로젝트는 없습니다) |

**가이드 설정**

| Tool | 용도 |
|---|---|
| `tesserae_setup_plan` | 환경을 감지하고 설정 계획을 JSON으로 제안. 읽기 전용 — `.tesserae/`를 절대 건드리지 않음 |
| `tesserae_setup_apply` | (수정 가능한) 계획 적용: `.tesserae/config.json` 작성 및 게이트된 설치/실행 액션 수행. `confirm_install_actions` / `confirm_run_actions`로 게이트 |

### Resources — 모델 컨텍스트에 자동 로드

클라이언트가 tool 턴을 소비하지 않고도 리소스 선택기를 통해 가져올 수 있는 URI:

- `tesserae://graph/schema` — `schema` tool과 동일한 페이로드를 정적 컨텍스트로 제공
- `tesserae://graph/summary` — 활성 프로젝트의 요약
- `tesserae://lint-report` — markdown 형식의 최신 lint 보고서

또한 클라이언트가 필요에 따라 구성할 수 있는 URI 템플릿:

- `tesserae://wiki/{kind}/{slug}` — 컴파일된 모든 위키 페이지 본문
- `tesserae://raw/{source_path}` — 모든 원본 소스 markdown

### Prompts — 원클릭 리서치 템플릿

이 항목들은 클라이언트의 슬래시 메뉴(예: Claude Code의 `/` 팔레트)에 표시됩니다:

| Prompt | 인자 | 동작 |
|---|---|---|
| `summarize-paper` | `slug` (필수) | `node_context` + `wiki_page` + 선택적 `raw_source`를 호출한 뒤 기여, 방법 스케치, 핵심 결과, 한계, 관련 노드로 구조화된 요약을 반환합니다 |
| `find-related-work` | `topic` (필수), `limit` | `search_nodes` + `node_context`를 연결하여 관련성 근거와 함께 상위 K개 관련 항목을 반환합니다 |
| `compare-approaches` | `a`, `b` (둘 다 필수) | 양쪽 모두에 대해 `node_context`를 가져오고 성능 주장에 대해 `search_facts`를 가져온 뒤 합성과 함께 나란히 비교한 결과를 반환합니다 |
| `gap-analysis` | `topic` (선택) | 해결되지 않은 미해결 질문, 누락된 벤치마크, 근거가 부족한 주장을 표면화합니다 |
| `triage-open-questions` | _없음_ | 모든 `OpenQuestion` 노드를 나열하고 주제별로 묶은 뒤 우선순위 순서를 제안합니다 |

각 prompt는 모델이 어떤 Tesserae tool을 어떤 순서로 연결해야 하는지를 정확히 알려주는 단일 사용자 메시지로 렌더링되므로, 모델이 매번 표면을 다시 발견할 필요가 없습니다.

## 다중 프로젝트: 하나의 서버에 여러 vault 등록하기

`~/.tesserae/registry.json`에 저장되는 영속 레지스트리를 통해 동일한 MCP 서버가 이름으로 등록된 모든 프로젝트를 해결할 수 있습니다:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

이후 `project` 또는 `graph_path`를 받는 모든 tool은 전체 경로 대신 `project: "research"`를 레지스트리에 대해 해결합니다. 서버는 등록된 `graph_path`가 여전히 존재하는지 검증하고, 재컴파일이 필요한 경우 명확한 오류를 반환합니다.

### 등록된 모든 vault에 대한 팬아웃

`ask` tool은 `scope: "all-registered"`를 받아 등록된 모든 프로젝트를 병렬로 쿼리하고 집계된 결과를 반환합니다:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "Where is splatting used?",
    "scope": "all-registered"
  }
}
```

`scope_aliases: ["research", "notes"]`로 부분집합으로 제한할 수 있습니다.

## 다중 계정 Claude CLI

`ask` tool이 Claude CLI를 통해 라우팅되고 여러 계정(예: `~/.claude`와 `~/.claude-personal2`)이 있다면, 호출마다 `claude_config_dir`을 전달하세요:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "...",
    "claude_config_dir": "/Users/you/.claude-personal2"
  }
}
```

서버는 해당 호출 동안에만 `CLAUDE_CONFIG_DIR`을 export하고 이후 이전 값을 복원합니다. 호출 간 누수가 없습니다.

## 검증

MCP 클라이언트를 재시작한 뒤 연결을 확인하세요:

- Claude Code: `/mcp`에 `tesserae`가 tool 개수와 함께 나열되어야 합니다.
- Cursor: 채팅 바의 MCP 아이콘에 `tesserae: connected`와 tool/resource/prompt 개수가 표시되어야 합니다.
- Codex / Hermes: 이름으로 임의의 tool(예: `schema`)을 호출하고 응답을 확인하세요.

아무것도 나타나지 않는다면 `--graph`가 기존 `.tesserae/graph.json`을 가리키는지 다시 확인하세요 — 서버는 이제 시작 시점과 모든 tool 호출 시점에 이를 검증하므로, 조용한 500 대신 명확한 오류 메시지를 보게 됩니다.

## 어디에 적합한가

MCP 서버는 타입 그래프에 대한 **읽기 인터페이스**입니다. **쓰기 경로**(소스 수집, 재컴파일, RAG-Anything 같은 동반 도구 갱신)에는 CLI를 직접 사용하세요. 둘은 분리되어 있습니다: CLI는 `.tesserae/`를 업데이트하고, MCP 서버는 다음 tool 호출 시 그곳에 있는 것을 읽습니다.
