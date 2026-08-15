# 기능 맵

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
이 문서는 현재 Tesserae에 구현된 기능을 상태, 소스 파일, 문서 위치와 함께 요약합니다.

Tesserae는 세 개의 기둥 위에서 동작하는 **컨텍스트 엔진**입니다: (1) 세션 모니터링, (2) 자율적·능동적 지식 수집, (3) 온디맨드 문서/컨텍스트. 타입 그래프, vault, 정적 사이트는 지식 베이스의 프로젝션입니다. 아래 기능은 어느 기둥에 봉사하는지에 따라 그룹화되어 있습니다; **v0.5.0** 마일스톤(2026년 6월)이 엔진 스파인과 기둥 3의 헤드라인 기능인 온디맨드 컨텍스트 컴파일러를 출시했습니다.

상태 범례: ✅ 출시됨 · ⚠ 진행 중 / 부분적.

> **읽는 순서.** 아래 섹션들은 마일스톤이며 최신순입니다. v0.12.0에서
> v0.28.7 사이의 버전은 여기서 다시 서술하지 않습니다 — 릴리스별 상세는
> 권위 있는 변경 이력인 [`docs/release-notes/`](../release-notes/)에 있습니다.
> 이 맵은 모든 커밋이 아니라 시스템의 형태를 다룹니다.

## 에이전트 메모리, 시간적 깊이 & 검색 뷰 — v0.31.0 이래로 (2026년 8월)

Neo4j의 agent-memory 설계를 읽고 Tesserae의 자체 제약을 견디는 부분들을 가져간
사이클: 두 번째 시간 축, 명명된 엣지 분할, 아이덴티티 묘비, 그리고 기계가 다시
유도할 수 없는 판정을 위한 견고한 홈. 데이터베이스 자체는 빠졌습니다 — 무엇이
가져갔는지, 비용이 무엇인지, 왜인지는
`docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md`를 참고하세요.

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| 트랜잭션 시간 (`observed_as_of`) | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | 두 번째 시계: `as_of`는 소스들의 자체 타임스탬프에서 "그때 무엇이 참이었는가"에 답하고, `observed_as_of`는 컴파일마다 한 번씩 타임스탐프된 `fact_observed` 테이블에서 "그때까지 무엇을 배웠는가"에 답합니다. 둘은 합성합니다. `sqlite.db` 안에서만 생존합니다 — `graph.json` 안의 벽 시계는 같은 소스들이 내일 다른 바이트로 컴파일되게 합니다. 이전에는 `as_of`가 축 하나만 존재하는데도 자신을 "bitemporal"이라고 광고했습니다. |
| 사실이 콘텐츠로 검색됨; `dated` 술부 | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | `search_facts`는 주어 / 술부 / 목적어 / 증거를 순위 매기고, 직렬화된 사실은 절대 아니므로 id나 메타데이터 조각은 더 이상 매치가 아닙니다. `dated` (`any`/`dated`/`undated`)는 dated 여부를 호출자가 `undated_included`에서 추론해야 했던 것에서 필터로 만듭니다. |
| `resolved_by`가 간격을 닫음 | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 모순 통과가 패자를 중재하지만 시간 투영기가 무시했으므로 중재된 패자는 계속 `current: true`로 읽혔습니다. **패는 쪽**에서 닫혀 — `resolved_by`는 source→winner를 실행하고, 이는 무효화하는 술부의 반대 — 플러스 Graphiti의 겹침 가드: 패자와 같은 시점이나 그 이전에 관측된 우승자는 패자가 참인 것을 멈춘 시기를 말할 수 없습니다. |
| 타임라인은 일치를 센다 | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | `timeline`은 페이징 이전에 **전체** 매치 집합을 날짜별로 정렬하고, `total_events`는 모든 매치를 셉니다. 이전에는 순위별로 선택된 100개 행 슬라이스를 정렬하고 그 클램프를 말뭉치 커버리지로 보고했습니다 — 따라서 타임라인이 원하는 가장 이른 이벤트들이 가장 떨어질 가능성이 있었습니다. |
| 뷰 레지스트리 + 멀티뷰 융합 | ✅ | [`tesserae/retrieval/views.py`](../../tesserae/retrieval/views.py), [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 하나의 메모리이지만 네 개의 직교 그래프로 순회 가능 — `semantic` / `temporal` / `causal` / `entity`, 각각 엣지 어휘의 명명된 부분집합입니다. 새로운 순위 매기기 알고리즘이 아닙니다: 뷰는 모든 비뷰 엣지 타입에 대해 0 가중치로 해결되고, 근방 보행은 같은 집합에서 필터링하므로 비뷰 전용 노드는 절대 인정되지 않습니다. 여러 뷰는 가중 RRF로 융합되고, 각 citation은 `via_views`를 보고합니다. |
| 지속되는 벡터 캐시 | ✅ | [`tesserae/retrieval/vector_cache.py`](../../tesserae/retrieval/vector_cache.py) | 모든 embedding 호출 사이트가 매 호출마다 전체 말뭉치를 다시 embedding했습니다. `node_vectors` 테이블이 이제 셋 모두를 뒷받침하며, `(backend, dim, sha256(embedded_text))`로 키됩니다 — **노드 id가 아니라**, 변하지 않은 노드는 전체 재컴파일이나 이동 이후에 히트하고, 다시 설명된 것은 미스하며, 두 모델의 벡터는 절대 만나지 않습니다. `embedding_status`는 `vectors_cached`와 프로세스 전체 히트/미스/에러를 보고합니다. |
| 차선별 검색 프로파일링 | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `explain: true` on `search_nodes` / `compile_context`는 차선별 가중치, 말뭉치, embed 호출, 캐시 히트/미스, 벽 시간, 그리고 어느 차선이 각 우승자에 기여했는지 반환합니다. Neo4j의 `PROFILE`처럼 옵트인입니다. 측정 비용이 들기 때문입니다 — 그리고 모든 수가 융합이 이미 생산한 테이블에서 읽혀 나오므로 순위를 옮길 수 없습니다. |
| 병합 원장 — 죽은 id가 생존자로 해결됨 | ✅ | [`tesserae/merge_ledger.py`](../../tesserae/merge_ledger.py) | 매 컴파일이 중복을 세 방식으로 축소했고 각 답을 버리곤 했으므로, 지난 컴파일의 노드 id를 들고 있는 에이전트는 단순 not-found를 얻었습니다. `merge-ledger.json`은 loser→survivor 묘비이며, 그래프가 미스한 이후에만 참조됩니다(살아있는 id는 리다이렉트될 수 없음); `node_context`는 `status: merged`와 `merged_from` / `merged_into`를 보고합니다. 유도된 상태, 이력 아님: 돌아오는 패자는 떨어집니다. |
| 철회 (`retracts`) | ✅ | [`tesserae/research_graph.py`](../../tesserae/research_graph.py), [`tesserae/graph_filters.py`](../../tesserae/graph_filters.py) | 에이전트는 대체를 발명하지 않고 "이것은 잘못되었습니다"라고 말할 수 있습니다: 노드를 **id로** 가리키는 `retracts` 엣지는 발견에서 떨어져 나갑니다(`search_nodes`, `fresh_insights`), 컨텍스트 선택에서 떨어져 나갑니다(`compile_context`), `node_context`의 이웃 목록과 incident edge에서 떨어져 나갑니다. 정확한 `node_context` 조회(id나 이름으로)는 여전히 노드 자체를 반환하며 `retracted` 플래그가 붙습니다 — 노드의 이름을 지정하는 것은 발견이 아닙니다. `include_superseded`는 그것을 발견 표면에 복원하고, 아무것도 삭제되지 않습니다. |
| 후보 same-as 판정 원장 | ✅ | [`tesserae/candidate_ledger.py`](../../tesserae/candidate_ledger.py) | "이것들은 다릅니다"라고 답한 검토자는 거기로부터 계속 같은 질문을 받곤 했습니다 — `apply_decisions`는 `keep_separate`를 소비했고 지속된 것은 아무것도 없었습니다. `.tesserae/candidate-same-as.json`은 정렬된 노드-id 쌍과 다른 것 없음으로만 판정을 키합니다. 따라서 다시 쓰인 설명, 새 소스, 다른 embedding 백엔드가 모두 그것을 그대로 놔둡니다. 누적, 절대 제거 안 함: 판정은 여기서 기계가 다시 유도할 수 없는 유일한 것입니다. `PENDING_REVIEW`로 노출됨. |
| 두 쌍별 통과를 위한 하나의 차단 계층 | ✅ | [`tesserae/blocking.py`](../../tesserae/blocking.py) | 정규화는 인라인 역 인덱스를 가졌습니다; `supersede`는 결과 그룹의 모든 쌍을 전혀 한계 없이 비교했습니다. 이제 둘 모두 하나의 계층을 공유하며, 테스트가 고정한 두 가지 성질: 캡은 **정렬된 id로** 자르므로 캡된 실행은 도착 순서에 의존하지 않고, 호출자는 자체 토큰화기를 공급합니다. 블로커가 점수 매기는 것보다 거칠기 때문에 진정한 매치를 묵묵히 삭제하기 때문입니다. 각 통과는 조용히 더 짧은 큐를 반환하는 대신 히트한 캡을 보고합니다. |
| 결과물 증거 노드가 사이트에 도달함 | ✅ | [`tesserae/raganything_adapter.py`](../../tesserae/raganything_adapter.py), [`tesserae/site/raw_view.py`](../../tesserae/site/raw_view.py) | 그림, 표, 방정식이 일등석 `Artifact` 노드가 되며, 각 id는 결과물의 종류와 콘텐츠 해시에서 시종하며 문서, 경로, 캡션, 페이지는 없습니다. 그림은 추가로 원본 페이지와 `raw-assets/` 아래 콘텐츠 주소형 바이트를 얻습니다(표와 방정식은 asset을 운반하지 않습니다 — 그들의 콘텐츠 *는* 설명입니다), 그리고 자산이 프로젝트 안에 사는 그림의 경우 `drill_down`은 `asset_path` / `asset_sha256` / `asset_site_path`를 되돌립니다. 소유자별 사실 — kind, page, caption, ordinal — 은 `part_of` 엣지를 탑니다. 노드가 구성상 문서-불가지론이고 한 그림을 인쇄하는 두 문서가 다른 문서의 페이지를 잃을 것이기 때문입니다. 증거는 **그래프 캔버스를 떠남**: 전체 assertion 계층은 영구적으로 제외됩니다. [rag-anything](integrations/rag-anything.ko.md) 참고. |
| 플래너가 그래프를 걷고 쓰기를 제안함 | ✅ | [`tesserae/ask_planner.py`](../../tesserae/ask_planner.py) | 카탈로그는 일곱 개의 투영 원시를 가졌고 그래프를 걸을 길이 없었습니다; `compile_context`가 그것에 조인하면, 뷰 조합이 다시 타이프되기보다 레지스트리에서 보간됩니다. 플래너는 또한 `proposed_write`를 반환할 수 있습니다 — 노드와 엣지는 *질문이* 주장한 것으로만 접지됩니다. **제안, 절대 수행 아님**: provenance는 항상 null이므로 `graph_write`는 agent 키와 외부 anchor를 가진 호출자가 하나를 공급할 때까지 그것을 거부합니다. |
| 읽기 감사 — 누가 그래프를 읽었는가 | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py), [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | 접근 카운트가 사용 불용에 의한 망각을 운전하지만, *누가* 그것을 야기했는지는 아무것도 기록하지 않습니다. `TESSERAE_READ_AUDIT=1`은 접근 카운트가 범프되는 곳마다 `{tool, actor, node_ids, at, tesserae_version}`을 기록합니다 — 한 줄이 그 호출이 카운트한 모든 노드를 명명합니다. 다만 `fresh_insights`는 노드마다 범프하므로 노드당 한 줄씩 쓰고, 표면화한 것이 없는 호출은 한 줄도 쓰지 않습니다 — `read_audit`으로 actor별 집계와 함께 다시 읽힙니다. **기본값 off**, 그리고 게이트는 스토어 열기 전에 앉으므로 — 테이블을 만드는 것 자체가 쓰기입니다. [agent memory](agent-memory.ko.md#망각--절대-삭제-아님) 참고. |
| 일등석 동사로서 `tesserae schema-drift` | ✅ | [`tesserae/schema_drift.py`](../../tesserae/schema_drift.py) | 부분-타입 제안은 `lab`을 통해서만 도달했습니다. 제안은 노드 메타데이터가 아니라 `.tesserae/schema-drift-proposals.json`에 생존합니다 — 대역외 메타데이터 키는 증분 컴파일을 견디고 전체 컴파일에서 사라질 것이니, 바이트-멱등성 맹점입니다. `SUGGESTED_SUBTYPE`으로 노출됨; **승격은 `ResearchNodeType`에 대한 인간적 수정으로 유지**되어, 그 다음 `"approved": true`와 `TESSERAE_SCHEMA_DRIFT_APPLY=1`. |
| 이동 가능한 컴파일 + agent-write lock | ✅ | [`tesserae/locking.py`](../../tesserae/locking.py) | lock은 `if fcntl is None: yield`였습니다 — Windows에서 아무것도 lock하지 않았고, agent-write 오버레이는 두 비동기 append가 JSONL 줄을 찢는 유일한 경로입니다. 이제 그것이 존재하는 곳에서는 `flock(2)`, 그렇지 않으면 `msvcrt.locking`입니다(msvcrt가 파일 위치에서 lock을 걸기 때문에 1 바이트 범위로 고정). 두 원시를 모두 가지지 않은 플랫폼은 프로세스당 한 번 경고합니다. 건넘 replay 줄은 이제 lint 발견(`AGENT_WRITE_SKIPPED`)입니다. 단지 stderr 경고가 아닙니다. |
| 사이드카 레지스트리 | ✅ | [`tesserae/sidecars.py`](../../tesserae/sidecars.py) | 모든 `.tesserae/` 엔트리는 소유자, 종류(`derived` / `accumulated` / `cache` / `scratch`)와 그것을 삭제하는 비용을 선언합니다 — 그리고 `safe_to_delete`는 별도의 필드입니다. `cache`가 모델에서 온 답이라면 drop하는 것이 안전하지 않고 `derived` 파일은 인간의 승인을 운반할 수 있기 때문입니다. Doctor의 `sidecars` 점검은 실제 디렉터리를 읽고 그것에 대해 비교합니다. [sidecars](sidecars.ko.md) 참고. |
| Kuzu는 export이지, 결코 store가 아님 | ✅ | [`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) | 일방형으로 규칙: `tesserae export kuzu`는 `graph.kuzu`를 쓰고, 어떤 컴파일 또는 런타임 경로도 그것을 다시 읽지 않습니다 — `read_graph`는 내보내기를 그것이 나온 그래프에 대해 확인할 수 있게 보존됩니다. [architecture § Kuzu export](architecture.ko.md#kuzu-내보내기) 참고. |

## 인지 메모리와 범위 — v0.29.0 → v0.31.0 (2026년 8월)

그래프가 무엇이 쓰였는지만이 아니라 *무슨 일이 일어났는지*를 알게 만든
사이클입니다: 결과가 수집을 살아남고, 그로부터 인과 엣지 하나가 도출되며,
조용하던 저하가 이제 소리를 냅니다.

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| 코드 계층 선택제 | ✅ | `cli.py`, [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | `compile`은 더 이상 기본적으로 코드 심볼을 수집하지 않습니다. 큰 저장소에서 코드 심볼은 다른 모든 것보다 많아 검색을 밀어냈습니다. `tesserae code ingest`로 CodeGraph를 의도적으로 연결할 수 있습니다. [ingest](ingest.ko.md) 참고. |
| 감춰졌던 검색 표면 공개 | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | 이중 시간(bitemporal)과 뷰 선택 매개변수는 구현과 테스트가 끝났는데도 MCP로 닿을 수 없었습니다. 이제 `search_facts`가 `current_only`와 함께 `as_of`(과거 시점 기준 응답)를 받습니다 — 서로 다른 시계이므로 **동시 사용은 거부**되며, `undated_included`가 받은 결과 중 날짜가 없는 것이 얼마나 되는지 보고합니다. |
| 소리 내는 저하 | ✅ | [`tesserae/lint.py`](../../tesserae/lint.py), [`tesserae/ingest/fetch.py`](../../tesserae/ingest/fetch.py), [`tesserae/ingest/orchestrator.py`](../../tesserae/ingest/orchestrator.py) | 세 가지 조용한 실패를 명시적으로 만들었습니다: 아무것도 만들어내지 못한 바이너리 수집, 날짜 없는 구간 커버리지(`INTERVAL_COVERAGE`), 버려진 비텍스트 콘텐츠. 침묵이 성공으로 읽혔지만, 이제는 아닙니다. |
| 소스에서 파생된 `first_seen_at` | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/session_graph.py`](../../tesserae/session_graph.py) | 노드의 날짜는 컴파일 시점의 벽시계가 아니라 그 소스가 수집된 경로에서 옵니다 — 그래서 재실행해도 날짜가 같고 바이트 멱등성이 살아남습니다. |
| 절차적 검색 풀 | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py), [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `context`가 절차적 메모리 — 무엇을 실행했고 결과가 무엇이었는지 — 를 위한 슬롯을 예약하되, 기본 제공이 아니라 **출처로 획득**하게 합니다. `PROCEDURAL_POOLS` lint가 그 슬롯을 정직하게 채울 수 없을 때 보고합니다. |
| 도구 결과는 하나의 턴 | ✅ | [`tesserae/session_event.py`](../../tesserae/session_event.py), [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | 종료 코드와 오류 플래그가 수집을 살아남아 `Event` 노드에 새겨집니다. 그래프가 실패한 명령과 그저 실행된 명령을 구별할 수 있습니다. 홈 디렉터리는 들어오는 길에 가려집니다. |
| `recovers` 엣지 | ✅ | [`tesserae/session_recovery.py`](../../tesserae/session_recovery.py) | 유일한 인과 엣지: '저것이 실패한 뒤 이것이 성공했다'를, 도구·프로그램 계열·작업 디렉터리·피연산자가 일치하는 한 세션 안의 두 **관측된** 결과에서 도출합니다. `CAUSAL_EDGE_TYPES`는 의도적으로 원소 하나입니다. [세션 이력](session-history.ko.md) 참고. |
| 헌장에 따른 도메인 구조 | ✅ | [`tesserae/charter.py`](../../tesserae/charter.py), [`tesserae/project.py`](../../tesserae/project.py), `cli.py` | 커뮤니티 탐지는 도메인 어휘를 *제안*하고, 헌장이 명시적 개편 사이에서 그것을 *소유*합니다. 탐지는 결정적이지만 안정적이지 않기 때문입니다(15개 노드짜리 문서 하나가 구성원의 약 29%를 옮깁니다). 이제 모든 `compile`이 `.tesserae/charter/charter.json`으로 헌장을 파생하고 `tesserae domains status`가 그것을 읽습니다. 아무것도 개편하지 않은 재컴파일은 파일을 바이트 단위로 그대로 둡니다 — `reorg_seq`는 컴파일이 아니라 개편을 셉니다. 한 번에 읽을 만큼 작은 프로젝트는 임계값 아래라 헌장을 갖지 않습니다. |
| 공유 디스크 다중 호스트 | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | `TESSERAE_HOST_ID`가 *누가 레코드를 썼는지*로 prune/덮어쓰기 범위를 정해, 하나의 공유 디스크를 쓰는 N대의 서버가 서로의 세션 이력을 지우지 않게 합니다. [세션 이력](session-history.ko.md) 참고. |

## 크로스 프로젝트 & UX — v0.11.0 (2026년 6월)

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| 크로스 프로젝트 연합(federation) | ✅ | [`tesserae/federation.py`](../../tesserae/federation.py) | `ask --scope federated`는 등록된 여러 프로젝트에서 하나의 그래프를 조립합니다 — 아이덴티티 병합(같은 arxiv/repo/hash/symbol) + 옵트아웃 가능한 embedding 기반 `shares_concept_with` 링크 — 그리고 합집합 위에서 상호 참조된 citation 답변 하나를 반환합니다(PPR + `compile_context`). 프로젝트별 `graph.json`은 읽기 전용; 아이덴티티 전용이면 결정적. |
| 스마트 `ask` 라우터 (활성 프로젝트 없음) | ✅ | [`tesserae/ask_router.py`](../../tesserae/ask_router.py) | "활성 프로젝트" 개념이 제거되었습니다 — 등록된 모든 프로젝트는 동등합니다. 인자 없는 `ask`는 스스로 라우팅합니다(프로젝트를 지명 → 그 프로젝트; 비교형 → federated; 후속 질문 → 라우트 유지; 그 외 → federated 폴백). 선택적 LLM 타이브레이커와 대화별 연속성 지원. 프로젝트별 작업은 cwd로 프로젝트를 해석. |
| 연합 검사 | ✅ | `tesserae/federation.py`, `cli.py` | `tesserae federation status`(프로젝트별 노드 수, 아이덴티티 병합, 시맨틱 링크)와 `federation explain <node>`(노드가 왜 프로젝트를 잇는지). |
| 멀티 프로젝트 serve | ✅ | [`tesserae/serve.py`](../../tesserae/serve.py), `cli.py` | 인자 없는 `tesserae serve`는 등록된 모든 프로젝트를 하나의 서버 아래에서 서빙합니다(`/`에 랜딩, 각 프로젝트는 `/<alias>/`, 헤더에 Projects 스위처, 경로 격리); `--project X`는 라이브 ask 위젯과 함께 하나만 서빙. |
| `compile`의 LLM 개념 레이어 | ✅ | `cli.py`, [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py), [`tesserae/selective_extractor.py`](../../tesserae/selective_extractor.py) | `tesserae compile`은 설정된 프로바이더(`llm_provider`에 따라 codex/claude/api)를 통해 **기본으로** 개념/클레임 레이어를 빌드합니다(`--extractor llm`); `--extractor deterministic`은 구조적이고 바이트 안정적인 옵트아웃; `selective-llm --llm-include … --llm-limit N`은 비용 인지형. |
| `tesserae setup` (대화형) | ✅ | `cli.py`, [`tesserae/deps.py`](../../tesserae/deps.py) | 최상위 `tesserae setup` — 기본은 대화형(LLM 프로바이더/effort + 어떤 선택적 deps); 플래그가 프롬프트를 건너뜀. pip 없는 uv-tool 환경에서도 설치 동작(uv-pip 폴백). |

## 상호 운용, 검색 & 설정 — v0.10.0 (2026년 6월)

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| Google **OKF v0.1** 가져오기/내보내기 | ✅ | [`tesserae/okf.py`](../../tesserae/okf.py) | `tesserae export okf [--import DIR]`. Markdown + YAML frontmatter 번들; Tesserae 자체 번들은 `x_tesserae` 네임스페이스를 통해 무손실 왕복, 외부 번들은 best-effort. |
| 빠른 트랜스크립트 검색 (memex) | ✅ | [`tesserae/memex_search.py`](../../tesserae/memex_search.py) | Claude/Codex 트랜스크립트에 대한 `nicosuave/memex` BM25 인덱스, `GET /api/transcript-search`를 통해 `tesserae serve` sessions 대시보드에 연결. 선택적이며 없어도 우아하게 동작. |
| 읽기 규율 핸들 | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | `compile_context` `preview=N`은 제한된 미리보기 + 콘텐츠 키 핸들을 반환; `get_handle`이 나머지를 페이징. 거대한 페이로드를 에이전트의 컨텍스트 밖에 유지. |
| 추출 품질 신호 | ✅ | [`tesserae/session_graph_llm.py`](../../tesserae/session_graph_llm.py) | finding별 `confidence` + `confidence_rationale` + `revisit_signals` (바이트 안정적; `fresh_insights`에 노출). |
| 머신 전역 설정 + deps | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py), `cli.py` | `tesserae setup`이 글로벌 LLM 기본값을 기록하고 선택적 deps(memex, raganything)를 설치; `tesserae config deps`가 목록/설치; `tesserae init`이 memex를 제안. 프로젝트별 config가 여전히 재정의. |

## 컨텍스트 엔진 — v0.5.0 (2026년 6월)

세 기둥을 구동하는 엔진 스파인. 엔진 스파인 모듈 맵, 자기 개선 메모리 사이드카, 컨텍스트 컴파일러 데이터플로는 [`docs/architecture.md`](architecture.ko.md)를 참조하세요.

### 엔진 스파인 (기둥 1 & 2)

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| `Pipeline` — `List[StepResult]`를 반환하는 재사용 가능 refresh 체인 | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | CLI, 데몬, MCP가 모두 호출하는 단일 스텝 러너. 스텝별 `Exception` 포착; 첫 실패에서 중단. |
| `Daemon` — 단일 소유자 asyncio 슈퍼바이저 | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | 소스 + vault + harness-session 디렉터리 감시; 디바운스된 cancel-and-reschedule이 버스트를 하나의 `Pipeline.run()`으로 병합. Pidfile; 진행 중 예외에도 생존. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon`은 `engine`의 별칭. |
| `project refresh` — prose 체인 (ingest → compile → project) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only`(옵트인 증분), `--no-sessions`. |
| 라이브 세션 모니터 → findings | ✅ | `harness_sessions.py` + session-graph 모듈 | 가져온 세션이 그래프를 채움; `fresh_insights` / `find_session_findings`가 이를 노출. |

### 자기 개선 메모리 (기둥 2)

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| `node_memory` SQLite 사이드카 (decay / confidence / superseded) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + 저장소 불문 접근자; 가변 상태 전용. 최초 관측(first-seen)은 별도의 `node_provenance` 사이드카에 존재. |
| Ebbinghaus 감쇠 점수 | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | 세션 finding을 최신 + 최다 접근 순으로 순위화(`fresh_insights` 구동). |
| Supersede 패스 (**기본 켜짐**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | 결정적 판정이 더 오래된 근사 중복 인사이트를 더 새로운 것에 의해 대체된 것으로 표시; `supersedes` 엣지 추가. |
| 인사이트 → 코드 심볼 링크 | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | 세션 인사이트에서 참조하는 심볼로의 `discusses` 엣지. |
| Reinforce + contradiction 패스 | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 같은 사이드카 위에서 접근 강화 + 모순 감지. |
| 출력의 수치 재발 신뢰도 | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | temporal fact가 `NodeMemoryRow.confidence`로부터 `confidence`를 각인, `infer_confidence`로 폴백. |

### Retrieval + embedding (기둥 2 & 3)

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| Hybrid retriever (BM25 + 어휘 + embedding, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | 로컬 우선, 완전 결정적. |
| Personalized PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | 멀티홉 시드 확장; 깊이 제한 서브그래프. |
| 실제 기본 embedding (Track B, Phase 6) | ✅ | `retrieval/hybrid.py` | 기본값 = 결정적 hash-bucket 유사 embedding(의존성 없음); 설치되어 있으면 `sentence-transformers`(`all-MiniLM-L6-v2`)를 선호, 지연 로드. `embedding_status` MCP 도구가 활성 백엔드를 보고. |

### 온디맨드 컨텍스트 컴파일러 (기둥 3 — 헤드라인)

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| `compile_context` — citation이 달린 인메모리 `ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 시드 해석 → PPR 확장 → 예산 제한 선택 → citation markdown → 선택적 LLM 합성. `synthesize=true`가 아니면 결정적. 디스크에 아무것도 기록하지 않음. |
| `project context` CLI | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = 무제한), `--llm`, `--output`. |
| `compile_context` MCP 도구 | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP를 통한 같은 파이프라인; `budget=0`은 무제한. |
| 토픽 범위 export 슬라이스 | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | `compile_context`를 통한 토픽 범위 `llms.txt` + `render_harness_context`. |

### 증분 compile (Phase 4 — 실험적)

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| 출처 사이드카 (`node_provenance`, first-seen) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | changed-only 삭제의 토대; 항상 기록됨. |
| `GraphStore` 삭제 표면 | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (출처 집합이 비는 노드를 제거; 여러 파일에 걸친 개념은 생존). |
| `url_resolver` 런타임 저장소 디스패치 | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| `incremental_compile` 플래그 | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **기본 OFF / 실험적.** 여러 편집 형태에서 바이트 패리티가 증명되었지만 다중 소유자/생산자 라이프사이클 공백이 남아 있음; 전체 compile이 여전히 기본. |

## 프론트엔드 리디자인 — 2026년 4월

문서 우선의 계층적 위키가 옛 그래프 덤프를 대체합니다. 라우트별 투어는 [`docs/frontend-redesign.md`](frontend-redesign.ko.md), 3레이어 모델은 [`docs/architecture.md`](architecture.ko.md)를 참조하세요.

### 위키 레이어 (L2 markdown)

| 기능 | 상태 | 소스 | 문서 앵커 |
|---|---|---|---|
| `WikiPageStore` (멱등 body-hash 쓰기, frontmatter 파서) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Module map](architecture.ko.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — 위키 레이어 노드당 md 페이지 하나 | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.ko.md#pipeline) |
| `sources/` 페이지 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.ko.md#sources) |
| `concepts/` 페이지 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.ko.md#concepts) |
| `entities/` 페이지 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.ko.md#entities) |
| `papers/` 페이지 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.ko.md#papers) |
| `repos/` 페이지 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.ko.md#repos) |
| `topics/` 페이지 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.ko.md#topics) |
| `questions/` 페이지 (Open questions) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.ko.md#questions) |
| `syntheses/` 페이지 | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.ko.md#syntheses) |

### Synthesis 종류 (L2 → 파생)

`SynthesisProjector`는 일곱 개의 결정적 템플릿을 생산하고 `Synthesis` 노드 + `synthesizes` / `summarizes` 엣지를 그래프에 다시 추가합니다.

| 종류 | 상태 | 소스 | 비고 |
|---|---|---|---|
| `pulse` (글로벌 하나, `/`를 구동) | ✅ | `synthesis.py` | 매 compile마다 재빌드. |
| `daily_digest` | ✅ | `synthesis.py` | `data/research/daily/<date>/`당 하나. |
| `weekly` | ✅ | `synthesis.py` | `data/research/weekly/<iso-week>/`당 하나. |
| `topic` | ✅ | `synthesis.py` | 논문 3편 이상인 `ResearchTopic` / `ApproachFamily` 클러스터당 하나. |
| `comparison` | ✅ | `synthesis.py` | 같은 태스크에서 경쟁하는 `ApproachFamily` 쌍당 하나. |
| `field_overview` | ✅ | `synthesis.py` | `ResearchField`당 하나. |
| LLM 업그레이드 요약 (env 플래그) | ⚠ | hook만 | 휴리스틱 베이스라인 출시; `TESSERAE_SYNTHESIS_LLM=1` hook은 스텁으로 남음. |

### 정적 사이트 라우트

| 라우트 | 상태 | 소스 | 비고 |
|---|---|---|---|
| `/` (홈, 히어로 pulse) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | 스탯 행 + 큐레이션된 진입점 + 최근 활동. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | 히트맵 + 일자 목록 + synthesis 레일. |
| `/timeline/<YYYY-MM-DD>.html` (일자별 상세) | ⚠ | 아직 없음 | 히트맵 셀은 임시로 그 날의 `digest.md` 소스 페이지로 링크. Subagent P가 `StaticSiteBuilder`를 통해 일자별 상세 페이지를 연결 중. |
| `/graph/` (인터랙티브 2D + 3D) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, 호버 툴팁, 엣지 레이블, 커서 기준 줌. |
| `/about.html` | ✅ | `pages.py::render_about` | 스키마, 빌드 정보. |

### AI 친화적 export

| 아티팩트 | 상태 | 소스 | 목적 |
|---|---|---|---|
| 페이지별 `<page>.txt` 시블링 | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | 한 페이지의 일반 텍스트 뷰(내비게이션·스타일 없음). |
| 페이지별 `<page>.json` 시블링 | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | llmstxt.org 짧은 인덱스. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | 모든 페이지 본문, 5 MB 상한. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`, 위키 레이어 노드만. |
| `graph.json` | ✅ | `__init__.py::write_site` | 전체 그래프 페이로드(도구용 코드 노드 포함). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | 팔레트 + 페이지 검색; 위키 레이어 종류만. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | 방출된 모든 라우트, `lastmod`는 frontmatter에서. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | 최근 30개의 synthesis. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | 허용적 — 크롤 + 인덱스. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | 기계 판독 가능 사이트 맵. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | 방출된 모든 파일의 sha256 + 크기(멱등성 하니스). |

### 비주얼 디자인 + UX

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| 디자인 토큰 (라이트 + 다크 테마, 테라코타 액센트) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | `assets/style.css`의 단일 CSS 번들. |
| 테마 토글 (영속화, 플래시 없음) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `localStorage`의 `data-theme="dark"`, 페인트 전에 적용. |
| 검색 팔레트 (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | `search-index.json`에 대한 퍼지 매치; 최근 페이지 목록. |
| 고정 오른쪽 TOC | ✅ | `pages.py` + `tokens.py` | 데스크톱 전용; 모바일은 `<details>` 드로어. |
| 월 + 요일 레이블이 있는 활동 히트맵 | ✅ | `components.py::heatmap_svg` | 26주 SVG, 셀은 그 날의 `digest.md`로 링크. |
| 스파크라인 (concept/entity별) | ✅ | `components.py::sparkline_svg` | 주간 언급 수, 최근 12주. |
| 모바일 셸 (드로어 레일, 하단 내비, 유동 타이포) | ✅ | `tokens.py` + `pages.py` | 터치 히트 타깃 ≥ 44 px. |
| 페이지 전환 (120 ms opacity, prefers-reduced-motion) | ✅ | `tokens.py` | |
| 3D + 2D 그래프 뷰 (호버, 엣지 레이블, 커서 기준 줌) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, CDN 스냅샷으로 벤더링. |
| 페이지별 AI 시블링 푸터 | ✅ | `components.py::ai_siblings_footer` | 현재 페이지의 `.txt`와 `.json`으로의 인라인 링크. |
| Harness 세션 이력 페이지 | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | 명시적 Claude Code/Codex 가져오기; markdown 턴, 왼쪽 턴 레일, 접힌 tool use, 검색 항목을 갖춘 `/sessions/` 인덱스와 상세 페이지. |

### 파이프라인 + CLI

| 기능 | 상태 | 소스 | 비고 |
|---|---|---|---|
| `project compile`이 synthesis + wiki + site를 순서대로 호출 | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | 리디자인 계획의 Phase 3. |
| `project build-site` 독립 실행 | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | `wiki/` + `graph.json`을 읽고 `site/`를 기록. |
| `project serve` 로컬 HTTP | ✅ | `cli.py` | 순수 stdlib 서버. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | `gh-pages`로 worktree 푸시; 선택적 `--enable-pages`는 `gh` CLI를 통해. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Claude Code/Codex의 인바운드 세션 이력; 발견은 명시적이며 프로젝트 작업 디렉터리로 범위 한정. |
| `project watch` 변경 시 재빌드 | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | 독립 폴링 감시자: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. 멀티 소스 슈퍼바이저는 `project engine`/`daemon` 아래에 존재(컨텍스트 엔진 참조). |
| `project context` — citation 컨텍스트 문서 compile | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 기둥 3 헤드라인; 컨텍스트 엔진 섹션 참조. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | prose refresh 체인 + 슈퍼바이저 루프; 컨텍스트 엔진 섹션 참조. |

## 기존 기능 (변경 없이 유지)

### CLI와 설치

- ✅ `pyproject.toml`을 통한 설치 가능한 Python 패키지.
- ✅ 콘솔 명령: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `curl | bash` 설치를 위한 `scripts/install.sh`.
- ✅ 빠른 로컬 개발을 위한 기본 editable 설치.

### 추출

- ✅ 통제된 노드/엣지 어휘를 갖는 결정적 연구 노트 추출기.
- ✅ API 키 없이 더 높은 품질의 구조화 추출을 위한 Claude CLI/OAuth 추출기.
- ✅ glob과 예산 제한에 의한 선택적 Claude 라우팅.
- ✅ Python 프로젝트용 결정적 개발 코드 추출기.
- ✅ 콘텐츠 해싱과 `--changed-only`를 지원하는 배치 ingest.
- ✅ 잘못된 UTF-8에 관용적인 소스 읽기.

### 그래프 거버넌스

- ✅ 통제된 `ResearchNodeType` 목록 — 이제 `SYNTHESIS` 포함.
- ✅ 통제된 엣지 타입 화이트리스트 — 이제 `synthesizes`, `summarizes` 포함.
- ✅ 스키마 드리프트를 거부하는 검증.
- ✅ Alias 정규화.
- ✅ 모호한 근사 중복 노드를 위한 리뷰 큐.
- ✅ 리뷰 결정 템플릿과 merge/keep-separate 워크플로.
- ✅ 파일별 그래프로부터의 코퍼스 트렌드 요약.

### 영속화와 보고서

- ✅ 그래프 JSON export.
- ✅ SQLite 그래프 저장소.
- ✅ 선택적 Kuzu 그래프 저장소.
- ✅ 개수, 근거 커버리지, 고아 노드, 날짜 버킷, alias 과다 노드를 담은 그래프 보고서.
- ✅ MegaMem, Graphiti/Zep, MCP 그래프 서버, agentic RAG에서 흡수한 아이디어를 기술하는 경쟁 보고서.

### 프로젝트 로컬 워크플로

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy` (GitHub Pages)
- ✅ `tesserae sessions discover/import/list` (명시적 로컬 에이전트 이력 가져오기)
- ✅ `tesserae export site --watch` (독립 폴링 감시자)
- ✅ `tesserae engine` (슈퍼바이저 루프 — v0.5.0)
- ✅ `tesserae refresh` (prose ingest → compile → project 체인 — v0.5.0)
- ✅ `tesserae context` (온디맨드 컨텍스트 컴파일러 — v0.5.0)
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ 바로 열 수 있는 vault export.
- ✅ `.obsidian/app.json`과 그래프 설정.
- ✅ Markdown 프로젝션.
- ✅ `raw/assets/` 구조.
- ✅ Dataview 쿼리가 있는 `_meta/dashboard.md`.

### 에이전트 harness

다음 타깃 파일 생성:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering 및 MCP 설정
- ✅ Cursor: 프로젝트 규칙 및 MCP config
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / temporal facts

- ✅ 출처, 현재성, 신뢰도, 무효화 필드를 갖는 temporal fact 프로젝션.
- ✅ 의존성 없는 Graphiti 에피소드 JSONL export.
- ✅ Graphiti 설치 없이 `sync-graphiti --dry-run` 스모크.
- ✅ `graphiti_core`와 Neo4j를 사용하는 선택적 라이브 동기화.

### MCP 서버

- ✅ stdio JSON-RPC를 통한 `tesserae_mcp` / `python3 -m tesserae.mcp_server`.
- ✅ Retrieval/그래프 도구: `schema`, `graph_summary`, `search_nodes`, `node_context` (`use_ppr` 지원), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`.
- ✅ 컨텍스트 엔진 도구 (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (감쇠 순위), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ 설정 도구: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ 멀티 프로젝트 레지스트리: `list_projects`, `register_project`, `unregister_project`, `list_sessions`. `url_resolver`를 통한 저장소 URL 디스패치.

## 테스트

현재 스위트가 커버하는 것:

- ✅ 온톨로지 가드레일 (새 `Synthesis` 노드 + `synthesizes` / `summarizes` 엣지 포함);
- ✅ 결정적 추출;
- ✅ Claude CLI 래퍼 파싱/검증;
- ✅ 선택적 Claude 라우팅;
- ✅ 정규화/리뷰 워크플로;
- ✅ 배치 ingest;
- ✅ 보고서;
- ✅ SQLite/Kuzu 영속화;
- ✅ Graphiti export/sync dry-run;
- ✅ 프로젝트 CLI 워크플로;
- ✅ 에이전트 harness export;
- ✅ Obsidian export;
- ✅ 프론트엔드 생성 + 링크 무결성 (`nodes/codeclass-*.html` 없음);
- ✅ 위키 저장소 멱등성;
- ✅ synthesis 프로젝터 골든 + 멱등성;
- ✅ 사이트 컴포넌트, 페이지, export, 관련성;
- ✅ AI 시블링 형태 (페이지당 `.txt` + `.json`);
- ✅ 엔드투엔드 compile-twice 멱등성;
- ✅ 엔진 스파인: pipeline, refresh 체인, 데몬 코어 + 소스, `project engine` CLI;
- ✅ 자기 개선 메모리: 사이드카, decay/supersede, supersede 억제 (MCP 포함), reinforce/contradiction;
- ✅ retrieval + embedding: hybrid 검색, PPR, 실제 기본 embedding (Phase 6);
- ✅ 컨텍스트 컴파일러: 형태/citation 무결성/결정성/예산/PPR 폴백, `project context` CLI, MCP `compile_context`;
- ✅ 증분 compile (실험적): differ, 패리티 게이트, 출처 준비 상태, SQLite 출처;
- ✅ 패키지 설치와 설치 프로그램 계약.
