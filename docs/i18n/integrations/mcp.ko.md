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
| `search_nodes` | 공개 그래프 노드를 `query`, `type`/`types`, `kind`, `limit`, 하이브리드 `mode`/`weights`로 필터링. `include_superseded`로 폐기된 노드도 노출; `explain`은 검색 `profile`을 더합니다 (아래 참고) |
| `node_context` | 노드 + 인접 엣지 + 이웃 노드. `use_ppr`는 1-홉 탐색 대신 개인화 PageRank로 이웃을 랭킹하며 `include_superseded`, `limit`로 결과를 한정. 나중 컴파일에서 병합에 진 `node_id`는 **미스가 아닙니다**: 병합 원장을 통해 그것을 흡수한 노드로 해결되고, 응답은 `status: "merged"`와 `merged_from` / `merged_into`를 얻어 지금부터 들고 있을 id를 배웁니다. 원장은 그래프가 미스한 후에만 참조되므로, 살아있는 id는 절대 리다이렉트될 수 없습니다 |
| `embedding_status` | 하이브리드 검색을 구동하는 활성 임베딩 백엔드와 그 영속화된 벡터 캐시를 보고 — 이 백엔드/차원 키에 대한 `vectors_cached`, 그리고 프로세스 전역의 `cache_hits` / `cache_misses` / `cache_errors`. 차갑거나 쓸 수 없는 캐시를 빠른 경로로 오인할 수 없게 합니다. 어느 프로젝트의 사이드카를 보고할지 고르는 `graph_path` / `project`를 받습니다 |
| `search_facts` | 그래프에서 투영된 시간적 사실(Graphiti 스타일). 순위는 사실의 내용(주어·술어·목적어·근거)만으로 매기며 직렬화된 사실 전체는 보지 않으므로 id나 메타데이터 조각은 일치로 잡히지 않습니다. `dated`(`any`, `dated`, `undated`)는 사용 가능한 `valid_from`이 있는지로 걸러냅니다. `current_only`는 현재 사실만 필터, `as_of`는 과거 시점 기준으로 응답. 두 옵션의 동시 사용은 거부됩니다(서로 다른 시계를 뜻하므로). `undated_included`는 반환된 행 중 날짜가 없는 건수를 보고합니다 |
| `timeline` | 종단적 관점을 위해 파싱된 `valid_from` 기준으로 정렬된 사실. 날짜 없는 사실은 날짜 있는 사실 뒤에 따로 모이며 섞이지 않고 `undated_events`로 건수가 보고됩니다. `dated`(`any`, `dated`, `undated`)는 사용 가능한 `valid_from`이 있는지로 걸러냅니다; `as_of`는 과거 시점 기준으로 응답합니다(유효 구간에 대한 시점 지정이며 범위 하한이 아닙니다). `undated_included`는 반환된 행 중 날짜가 없는 건수를 보고합니다. 날짜 없는 사실은 `as_of`에서도 유지되므로, 이 수치가 빈약한 답과 완전한 답을 구분해 줍니다. `total_events`는 주어진 페이지가 아니라 **일치하는 모든** 사실을 셉니다 — 전체 매치 집합이 페이지가 잘리기 전에 날짜별로 정렬되므로, 가장 이른 이벤트들은 timeline이 실제로 반환하는 것이고, `total_events > len(events)`가 가득 찬 페이지와 완전한 답을 구분해 줍니다 |
| `graph_ppr` | 하나 이상의 `seed_node_id`에서 시드된 개인화 PageRank로 가장 관련성 높은 top-K 노드 반환; `alpha`, `directed`, `edge_type_weights` 조정 가능 |
| `wiki_page` | 노드에 대해 컴파일된 markdown 페이지 본문과 참조하는 내부 링크. 오래된 `node_id`는 같은 병합 원장 리다이렉트를 조용히 따릅니다 — 흡수된 노드의 이름은 생존자의 alias이므로, 생존자의 페이지 *는* 요청한 페이지입니다 |
| `raw_source` | 원본 소스 markdown (16 KB로 제한). 바이트를 절대 반환하지 않습니다: `Artifact` 노드의 경우 `drill_down`으로 가리키는데, 그것이 대신 asset의 경로와 사이트 주소를 보고합니다 |
| `verify_claim` | 트리플 하나를 그래프에 대해 검증합니다 — 정확한 조회이며 LLM도, 퍼지 매칭도, 순위 결과도 없습니다. `{verdict, reason, triple, citation, provenance, advisory}`를 반환하며 `verdict`는 `SUPPORTED`(엣지가 존재하고 **그 증거가 문서의 축자 구간**), `PRESENT_UNEVIDENCED`, 또는 거부입니다. 산문만 있다면 `search_nodes` → `verify_claim`으로 이어 부르세요 |
| `doctor_run` | 헬스 체크를 실행하고 보고서를 JSON(`findings`, `exit_code` 0/1/2)으로 반환합니다. **항상 읽기 전용** — MCP에서는 수정이 실행되지 않으며, 복구는 CLI의 `tesserae doctor --fix`를 쓰세요 |
| `doctor_report` | `.tesserae/doctor-report.md`의 내용(64 KB 제한). `tesserae doctor`를 실행하기 전까지는 비어 있습니다 |
| `charter_route` | 한 번의 호출로 태스크 하나를 차터된 도메인 트리에 배치합니다. 이름으로 고를 수 있는 카드가 없을 때 `graph_map` 카드를 페이징하는 대신 쓰는 경로입니다. 살아 있는 모든 도메인(slug, 앵커 이름, 캐시된 brief가 있으면 그것까지)에 순위를 매기고, 서브트리가 가장 좋은 근거를 가진 도메인까지 beam-1로 내려가 `{routed, path, brief, parent, siblings, route_quality}`를 반환합니다. 도메인 slug는 ingest를 넘겨 살아남는 스코프이고, community id는 그렇지 않습니다. **베스트에포트이며, 그 사실을 스스로 밝힙니다**: `charter.json`의 바이트는 멱등이지만 이 순위는 아닙니다 — embedding 레인은 머신의 백엔드에 따라 달라지고 brief가 데워질수록 코퍼스가 두꺼워지므로, `route_quality`가 `{backend, semantic, corpus_rows, warm_rows, evidenced_rows}`를 보고하고, 각 카드는 `evidence`를 함께 싣습니다 — `lexical`(용어 일치. 백엔드가 바뀌어도 남습니다), `semantic`(임베딩 유사도뿐. 남지 않습니다), `none`(지나쳤을 뿐). 배치할 수 없는 태스크는 `routed: false`로 돌아오며 도메인을 **하나도** 지목하지 않습니다. 추측을 읽어낼 저신뢰 후보 자체가 없습니다. `tesserae compile`이 쓰는 `.tesserae/charter/charter.json`이 필요합니다 |
| `lint_report` | 가장 최근의 컴파일 시점 lint 결과 (64 KB로 제한) |

**검색 프로파일링.** `search_nodes`와 `compile_context`는 `explain: true`를 
받아 `profile`로 응답합니다 — `bm25`, `lexical`, `embedding` 차선 각각의 
가중치, `candidates_in`, 채점한 개수, `embed_calls` / `cache_hits` / 
`cache_misses`와 벽 시간, 그리고 총 `candidates_in` / `admitted` / 
`returned`, 그리고 그것이 세는 각 노드에 어느 차선이 기여했는지. `returned`와
그 노드별 차선 귀속은 **예산 적용 전** 값입니다: 융합이 그 자신의 top-`k`
슬라이스 위에서 둘을 고정하고, 구속력 있는 `budget_chars`가 그 뒤에 MCP
계층에서 프로파일을 다시 쓰지 않은 채 그 슬라이스를 잘라냅니다. 그래서
빡빡한 예산 아래에서 `returned`는 응답의 행이 아니라 검색기가 생산한
슬라이스를 기술하고, 그 차이를 보고하는 것은 `continuation` 줄입니다.
`search_nodes`는 한 개의 프로파일을 반환하고, `compile_context`는 
실행한 각 seed 검색당 하나씩의 목록을 반환합니다.

기본값이 off이고, 그것이 형식만은 아닙니다: 측정에는 시간이 들기 때문에, 
이것은 계속 켜 둘 것이 아니라 진단입니다. 순위를 옮길 수 없습니다 — 모든 
수가 융합이 이미 생산한 점수와 순위 테이블에서 읽혀 나옵니다 — 그리고 
플래그가 unset일 때 응답은 항상 가지고 있던 정확한 키를 전달합니다. 
`cache_hits` / `cache_misses` 카운터는 `embedding_status`를 나중에 검사하는 
대신 현재 질의에서 따뜻한 벡터 캐시를 차가운 것과 구분해 주는 방법입니다.

**온디맨드 컨텍스트 컴파일러** (Phase 7)

| Tool | 용도 |
|---|---|
| `compile_context` | `query` 또는 명시적 `seeds`에 대해 맞춤형 **인용 포함** 컨텍스트 문서를 컴파일. 깊이 제한 서브그래프(`depth`, 1–10, 기본 2)를 탐색하고 PPR로 랭킹한 뒤 문자 `budget`(기본 32000; `0`이면 무제한)를 채움. 기본은 결정론적이며 `synthesize: true`면 LLM이 작성한 서사형 "topic" 슬라이스를 생성. `body`, `citations`, `selected_node_ids`, `char_budget_used` 반환. `view`는 walk를 명명된 edge partition으로 제한합니다 — `semantic`, `temporal`, `causal` 또는 `entity`; names의 배열을 전달하여 view당 한 번의 walk를 실행하고 이들을 fuse합니다 (weighted RRF). view를 요청하면 — 이름 하나든 여러 개든 — 각 citation은 `via_views`(그에 도달한 walk의 views)를 가집니다. `explain`은 `profile`을 더하며 seed 검색당 하나씩입니다 |
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
| `drill_down` | 증류본의 `member_ref`를 원본 L0 노드로 되돌립니다 — 증류된 가시성을 넘어서는 관리자의 명시적이고 감사 기록되는 에스컬레이션. 상태는 `alive` / `changed` / `absorbed` / `gone`이며 모든 호출이 사이드카에 기록됩니다. **그림** `Artifact`를 drilling하고 그 자산이 프로젝트 안에 해결되었으면 다른 노드는 절대 가지지 않는 세 개의 키가 추가됩니다: `asset_path`(바이트가 디스크의 어디 있는지), `asset_sha256`(그 바이트의 다이제스트이며, 종류와 함께 노드 id를 시종함), `asset_site_path`(구축된 사이트의 `raw-assets/` 아래의 콘텐츠 주소). 표와 방정식 결과물은 자산이 전혀 없습니다 — 그들의 콘텐츠 *는* 설명입니다 — 그리고 프로젝트 루트 바깥에서 해결된 그림은 절대 경로를 저장하지 않았습니다; 둘 다 일반 키로 drilling됩니다. 잘못된 선언 hash는 주소를 지어내지 않고 `asset_site_path`를 떨어뜨립니다 |
| `read_audit` | 누가 이 그래프를 읽었는가. 기록된 읽기 이벤트(`tool`, `actor`, `node_ids`, `at`, `tesserae_version`)를 최신순으로 돌려주고 액터별 집계를 함께 제공하므로, 미사용에 의한 망각을 움직이는 접근 횟수를 읽은 주체에게 귀속시킬 수 있습니다. **옵트인** — 서버 프로세스에 `TESSERAE_READ_AUDIT=1`을 설정하지 않으면 아무것도 기록되지 않습니다. 항상 켜진 감사는 모든 읽기를 쓰기로 만들기 때문입니다. 플래그를 꺼도 이미 기록된 행은 계속 읽을 수 있으며, `enabled`가 현재 설정을 알려줍니다. `actor`, `tool`, `node_id`로 필터링합니다 |
| `graph_write` | 타입 지정 노드와 엣지를 그래프에 직접 씁니다 — 마크다운도, 추출 패스도 없습니다. append-only 오버레이에 추가되어 컴파일 생산자로 재생되므로 **재컴파일을 견딥니다**. 엄격합니다: 알 수 없는 타입, 증거 없는 엣지, 이 페이로드에도 없고 기존 노드 id도 아닌 엔드포인트는 모두 거부됩니다. 그냥 틀린 것을 대체물을 지어내지 않고 **철회하려면**: `retracts` 엣지를 틀린 노드에 **id로** 겨누십시오 — 대상은 발견(`search_nodes`, `fresh_insights`)에서, 컨텍스트 선택(`compile_context`)에서, `node_context`가 반환하는 모든 이웃 목록과 incident edge에서 떨어져 나갑니다. 그것이 하지 않는 것은 노드의 이름을 지정하는 사람에게 숨기는 것입니다: id나 이름으로 정확한 `node_context` 조회는 여전히 노드 자체를 반환하며, `"retracted": true` 플래그가 붙습니다. 호출자가 그것을 요청했기 때문입니다. `include_superseded: true`는 그것을 발견 표면에 다시 넣고, 아무것도 삭제되지 않습니다 |

**Q&A 및 레지스트리**

| Tool | 용도 |
|---|---|
| `ask` | 자연어 Q&A. `scope`를 생략하면 스마트 라우터가 등록된 프로젝트 전반에서 대상을 고르고(연합 폴백), 연속적 질문 간에 라우트를 다시 겨냅니다(`conversation_id`로 스레드 격리). 명시적 `scope`: `current`(프로젝트 하나), `all-registered`(프로젝트당 답 하나), `federated`(하나의 병합되고 상호 참조된 답, 기본값 `semantic` 활성화). 플러스 `backend`, `top_k`, `scope_aliases`, `claude_config_dir`. 그래프 라우트된 질문에서 envelop은 `plan`(플래너의 추론, 선택한 단계, `executed` — 실제로 실행된 것)을 전달하며, `proposed_write`를 전달할 수도 있습니다: 플래너가 기록할 가치가 있다고 생각하는 노드와 엣지로, *질문이* 주장한 것으로만 접지됩니다. 이것은 **제안이지, 결코 쓰기가 아닙니다** — 그것의 provenance는 항상 null이므로, `graph_write`는 agent 키와 외부 anchor를 가진 호출자가 하나를 공급할 때까지 그것을 거부합니다. 변이는 절대 질의의 부수 효과가 아닙니다 |
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
