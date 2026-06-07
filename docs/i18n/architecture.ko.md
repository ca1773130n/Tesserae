# 건축학

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae는 **컨텍스트 엔진**입니다. 프로젝트로부터 자기 개선형 지식 기반을 재구성하고 이를 에이전트가 바로 사용할 수 있는 컨텍스트로 제공합니다. 세 가지 기둥 위에서 동작합니다. (1) **세션 모니터링** — 라이브 에이전트/작업 세션을 관찰하고 발견 사항을 발생하는 즉시 포착합니다. (2) **자율적·능동적 지식 수집** — 파이프라인 + 슈퍼바이저 루프가 지식을 지속적으로 끌어와 재추출하여, 지시를 기다리지 않고 기반을 계속 개선합니다. (3) **온디맨드 문서/컨텍스트** — 동일한 기반에서 컴파일된 사용자 요청 산출물입니다. 입력된 그래프, 마크다운 볼트, 정적 사이트는 지식 기반의 *프로젝션*이며, 엔진은 이들을 최신 상태로 유지하고 에이전트에 공급하는 루프입니다.

그 아래에서 Tesserae는 소스 자료의 디렉토리를 제어되고 입력된 지식 그래프로 바꾸고 내구성 있는 마크다운 위키 레이어를 통해 해당 그래프를 정적 AI 친화적인 웹 사이트로 프로젝트합니다. 2026년 4월 재설계에서는 프로젝션 측을 Karpathy 3계층 모델을 중심으로 재구성했습니다. 원시 증거는 원시 상태로 유지되고, 입력된 그래프는 온톨로지를 관리하며, 마크다운 위키 계층은 그래프와 렌더링된 출력 사이에 위치합니다. 정적 사이트는 스키마로 [`tesserae/research_graph.py`](../../tesserae/research_graph.py)의 제어된 온톨로지를 사용하여 그래프를 직접 덤프하는 것이 아니라 위키 레이어의 *렌더러*입니다. **v0.5.0** 마일스톤(2026년 6월)은 세 기둥을 모두 구동하는 엔진 스파인을 추가했습니다 — 아래의 *엔진 스파인* 및 *온디맨드 컨텍스트 컴파일러*를 참조하세요.

## Karpathy 3층 모델

LLM 친화적인 지식 기반에 대한 Andrej Karpathy의 프레임은 각각 고유한 내구성을 보장하는 세 가지 계층으로 구분됩니다.

| 레이어 | 우려사항 | 레포 위치 | 소유자 |
|---|---|---|---|
| L1 — 원시 소스 | 사용자가 작성하거나 수집한 리터럴 바이트입니다. 추가 전용. | `data/`, `docs/`, `.tesserae/config.json`에서 참조되는 프로젝트 트리 | 사용자 |
| L2 — 위키 | YAML 서두가 포함된 입력된 마크다운 페이지(소스, 개념, 엔터티, 논문, 저장소, 주제, 종합, 질문). 멱등성: 각 컴파일을 다시 생성하지만 콘텐츠 해시가 변경될 때만 다시 작성됩니다. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — 렌더링됨 | 정적 HTML 사이트, AI 형제 내보내기, 검색 색인, 사이트맵, JSON-LD. 모든 컴파일을 지우고 다시 작성했지만 재실행 시 바이트가 안정적입니다. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

스키마는 세 레이어 모두에 별도의 축으로 위치합니다. `graph.json`의 `ResearchGraph`는 L2 페이지가 연결되는 제어된 온톨로지이며, [`tesserae/research_graph.py`](../../tesserae/research_graph.py)의 `ResearchNodeType`/에지 화이트리스트는 어떤 유형이 존재하는지에 대한 정보의 소스입니다.

재설계에서는 L2가 명시적으로 추가되었습니다. 2026년 4월 이전에는 정적 사이트가 `graph.json`에서 바로 투영되었습니다. 위키 레이어는 Obsidian 볼트 내보내기 내부에만 존재했습니다. 이를 분할하면 다음과 같은 정보가 제공됩니다.

- 사람이 편집할 수 있는 단일 표면(Obsidian 또는 마크다운 편집기에서 `.tesserae/wiki/` 열기)
- 멱등성 재구축: `project compile`를 다시 실행하면 소스 콘텐츠가 변경되지 않는 한 파일 차이가 전혀 발생하지 않습니다.
- 진화 로그: 합성 페이지는 시간이 지남에 따라 축적되며 프로젝트 자체에 대한 설명을 제공합니다.

## 파이프라인

```
data/, docs/, src/                                    (L1 raw)
        │
        ▼  project compile  (tesserae/project.py)
┌───────────────────────────┐
│ ResearchGraphExtractor    │   deterministic + selective Claude
│ + canonicalization        │
└───────────┬───────────────┘
            │
            ▼
┌───────────────────────────┐
│ ResearchGraph (graph.json)│   schema: research_graph.py
└───────────┬───────────────┘
            │
            ├──▶ WikiLayerProjector   (one page per L1/L2 node)
            ├──▶ SynthesisProjector   (pulse, daily, weekly, topic, …)
            │
            ▼
┌───────────────────────────┐
│ .tesserae/wiki/  (L2 md)  │   sources/, concepts/, entities/,
│                            │   papers/, repos/, topics/,
│                            │   syntheses/, questions/
└───────────┬───────────────┘
            │
            ▼  StaticSiteBuilder.write_site
┌───────────────────────────┐
│ .tesserae/site/  (L3 html)│   index.html, <kind>/index.html,
│                            │   <kind>/<slug>.html,
│                            │   per-page .txt + .json siblings,
│                            │   llms.txt, llms-full.txt,
│                            │   graph.json, graph.jsonld,
│                            │   search-index.json,
│                            │   sitemap.xml, rss.xml,
│                            │   robots.txt, ai-readme.md,
│                            │   manifest.json
└───────────────────────────┘
```

모든 단계는 점진적입니다. 그래프 추출기는 `manifest.json` 콘텐츠 해시를 사용하여 변경되지 않은 소스 파일을 건너뜁니다. `WikiPageStore.write_page`는 본문 해시가 이미 디스크에 있는 것과 일치하면 `False`를 반환하고 쓰기를 건너뜁니다. `StaticSiteBuilder`는 `.tesserae/site/`를 지우고 다시 작성하지만 출력은 결정적입니다. 아래 "멱등성 이야기"를 참조하세요.

## 컨텍스트 컴파일러 데이터 흐름

온디맨드 컨텍스트 컴파일러([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py))는 기둥 3의 핵심 경로입니다. 쿼리 및/또는 명시적 시드 노드 ID가 주어지면 `compile_context`는 그래프에서 바로 맞춤형의 **인용된** 마크다운 번들을 만들어 메모리에 반환합니다 — `.tesserae/` 아래에 아무것도 쓰지 않습니다.

```
query / seeds
     │
     ▼  1. 시드 해석
        명시적 시드(그래프에 존재할 때만 유지) + hybrid_search() 결과, 중복 제거, 안정적 순서
     │
     ▼  2. PPR 확장
        retrieval.ppr.personalized_pagerank가 깊이 제한 k-홉 이웃을 랭킹;
        결과가 비면(분리된 시드) → 시드 순서로 폴백(번들은 절대 비지 않음)
     │
     ▼  3. 예산 제한 선택
        PPR 순서를 따라가며 각 노드의 인용 본문을 다음 본문이 `budget` 문자를
        초과하기 직전까지 포함(budget <= 0 = 무제한; 단어 경계에 초과 마커)
     │
     ▼  4. 인용 마크다운 조립
        선택된 노드당 한 섹션 + 끝의 `## Citations` 블록.
        본문 텍스트는 (store + 공개 위키 종류가 있을 때) 프로젝트된 위키 페이지를 우선하고,
        없으면 노드 설명, 그것도 없으면 최소 스텁을 사용. LLM 없는 본문은 벽시계
        타임스탬프를 전혀 넣지 않음 → 동일한 (graph, query, seeds, depth, budget)에 대해 바이트 동일.
     │
     ▼  5. 선택적 LLM 합성  (synthesize=true이고 ANTHROPIC_API_KEY가 설정된 경우에만)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

기본값: `depth=2`, `budget=32000`. 결정적 조립(1~4단계)이 계약이며, LLM 합성은 순수하게 부가적입니다. 동일한 파이프라인이 `project context` CLI 명령, `compile_context` MCP 도구, 그리고 주제 범위 내보내기 슬라이스(`slice_export_context_for_topic`, 주제 범위 `llms.txt`)를 뒷받침합니다.

## 모듈 맵

### 위키 + 합성(L2)

| 모듈 | 책임 |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | `WikiPage` 데이터 클래스, 파일 시스템 I/O용 `WikiPageStore`. Stdlib 전용 YAML 하위 집합 앞부분 파서. 신체-해시 멱등성. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: Wiki 레이어 유형의 각 `ResearchGraph` 노드를 오른쪽 `kind/` 폴더의 마크다운 페이지에 매핑합니다. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: 펄스, daily_digest, 주간, 주제, 비교, field_overview에 대한 결정적 템플릿입니다. `Synthesis` 노드와 `synthesizes` / `summarizes` 에지를 그래프에 다시 추가합니다. |

### 그래프 + 온톨로지

| 모듈 | 책임 |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `ResearchNodeType` 열거형(`SYNTHESIS` 포함), 에지 유형 화이트리스트(`synthesizes`, `summarizes` 포함), 검증. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | 별칭 정규화 + 거의 중복된 검토 대기열. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | 개발 슬라이스를 위한 결정적 Python AST 추출기입니다. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Claude CLI/OAuth 선택적 추출기. |

### 사이트 렌더러(L3)

| 모듈 | 책임 |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: 사이트 지우기 + 재구축, 모든 경로 탐색, 내보내기 + AI 형제 + 매니페스트 내보내기. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | 경로당 하나의 렌더러(홈, 인덱스, 세부 정보 페이지, 타임라인, 그래프, 정보). `SiteContext`는 미리 계산된 인덱스를 전달하므로 렌더러가 순수하게 유지됩니다. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML 프리미티브: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | 디자인 토큰 — CSS 변수, 밝은 + 어두운 테마, 레이아웃, 타이포그래피, 여기에 스타일이 지정된 모든 구성 요소. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | 클라이언트 JS 번들: 검색 팔레트, 테마 토글, 시그마 + 3D-force 그래프 보기. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Stdlib 전용 마크다운 렌더러(링크, 자동 링크, 코드, 강조, 제목). 외부 종속성이 없습니다. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | 모든 `Related` 섹션에서 사용되는 4개 신호 관련성 점수(직접 링크, 소스 중복, Adamic-Adar, 유형 선호도). |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | `search-index.json` 빌더. Wiki 레이어 종류에만 해당됩니다. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | 가져온 하네스 기록을 위한 세션 색인/세부 렌더러: 프로젝트 메모리 요약 섹션, 대화 전환 레일, 마크다운 기록 렌더링 및 축소된 도구 사용 블록. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, 페이지당 `.txt`/`.json` 형제. |

### 파이프라인 오케스트레이션

| 모듈 | 책임 |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: 추출 → 그래프 → 메모리 패스 → 위키 레이어 → 사이트를 구동합니다. `ProjectPaths`(`config`, `graph`, `manifest`, `wiki`, `site` 등)를 소유합니다. 프로비넌스 기반 증분 컴파일이 가능한지(`incremental_compile`로 게이트, 기본 OFF) 사전에 결정합니다. |
| [`tesserae/cli.py`](../../tesserae/cli.py) | `compile`, `refresh`, `context`, `build-site`, `serve`, `watch`, `engine`/`daemon`, `deploy`를 포함한 모든 `tesserae project …` 하위 명령. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `project deploy`: 작업 트리를 통해 `.tesserae/site/`를 `gh-pages` 분기에 푸시하고 선택적으로 `gh`를 통해 페이지를 활성화합니다. |

### 엔진 스파인 (v0.5.0 — 기둥 1 & 2)

엔진 스파인은 세션 모니터링과 자율 재수집을 구동하는 인프로세스 루프입니다. 동일한 `Pipeline.run()`이 CLI, 슈퍼바이저 데몬, (추후) MCP 서버가 모두 호출하는 단일 새로고침 경로입니다.

| 모듈 | 책임 |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: 순차 스텝 러너. 산문형 새로고침 체인(수집 → 컴파일 → 프로젝트/게시)을 import 가능한 객체로 정형화하며, 출력-후-종료 대신 구조화된 `List[StepResult]`를 반환하여 각 호출자가 결과를 어떻게 표면화할지 직접 결정합니다. `run()`은 스텝마다 `Exception`을 잡고(`KeyboardInterrupt`/`SystemExit`는 통과시킴) 첫 실패에서 멈춥니다. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: 단일 소유자 asyncio 슈퍼바이저. 소스 디렉토리, Obsidian 볼트, 하네스 세션 디렉토리를 감시하고, 취소-후-재스케줄 디바운스를 통해 `TriggerEvent` 버스트를 정확히 하나의 `Pipeline.run()`으로 통합합니다. 기존 `watch.py` / `vault_watch.py` 감시기를 재사용(재작성하지 않음)하고, pidfile을 쓰며, 진행 중 예외에서도 살아남습니다. `project engine` / `project daemon`(`--interval`, `--debounce`, `--once`)으로 노출됩니다. |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | 독립형 `project watch` 명령과 데몬의 소스/볼트 레인이 함께 재사용하는 폴링 감시기. |

### 자기 개선 메모리 (v0.5.0 — 기둥 2)

Phase 5는 영속적 자기 개선을 활성화했습니다. 노드별 가변 상태는 `node_memory` SQLite 사이드카(`.tesserae/sqlite.db` 내부)에 위치하며, 불변의 `node_provenance.first_seen_at` 최초 관측 스탬프(Phase 4 사이드카)와 분리됩니다. 컴파일은 그래프에 대해 일련의 결정적 패스를 구동합니다.

| 모듈 | 책임 |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + `node_memory` 테이블에 대한 스토어 비종속 접근자(`read_memory`, `write_memory`, `bump_access`) — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. 어떤 호출 지점도 원시 SQL을 포함하지 않습니다. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: 세션 발견을 랭킹하는 데 쓰이는 에빙하우스식 신선도 점수(최신 + 가장 많이 접근됨 우선). |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass`(**기본 ON**): 더 오래된 근사 중복 인사이트를 더 새로운 것으로 대체됨 표시하고 `supersedes` 에지를 추가하는 결정적 판정. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: 세션 인사이트를 그것이 논하는 코드 심볼에 `discusses` 에지로 연결합니다. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 동일한 사이드카에 대한 접근 강화 및 모순 감지 헬퍼. |

재발 신뢰도는 출력에서 수치형입니다. 시간 프로젝션은 각 사실의 `confidence`를 `NodeMemoryRow.confidence`(SQLite의 텍스트, `temporal.py`를 통해 표면화)에서 스탬핑하며, 저장 값이 없을 때만 `infer_confidence`로 폴백합니다.

### 검색 (v0.5.0 — 기둥 2 & 3)

| 모듈 | 책임 |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: 세 레인 — Okapi BM25(k1=1.5, b=0.75), 대소문자 무시 어휘/FTS식 부분 문자열, 플러그형 임베딩 레인 — 을 상호 순위 융합(RRF, k=60)으로 융합하는 로컬 우선 하이브리드 검색기. 완전히 결정적입니다. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: 그래프에 대한 HippoRAG-2식(arXiv:2502.14802) 개인화 PageRank로 다중 홉 시드 확장 — 1홉 이웃만이 아니라 시드에서 여러 홉 떨어져 있어도 잘 연결된 노드를 표면화합니다. |
| 임베딩 백엔드 (Phase 6, Track B) | 하이브리드 임베딩 레인의 기본 백엔드는 추가 의존성이 필요 없는 결정적 해시 버킷 의사 임베딩입니다. 선택적 의존성이 설치된 경우 `sentence-transformers`(`all-MiniLM-L6-v2`)가 선호되어 지연 로드됩니다. `embedding_status` MCP 도구가 활성 백엔드를 보고합니다. |

### 온디맨드 컨텍스트 컴파일러 (v0.5.0 — 기둥 3 핵심)

| 모듈 | 책임 |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: 기둥 3의 핵심 기능. 쿼리/시드 세트에 대한 맞춤형 **인용된** 컨텍스트 번들을 그래프에서 바로 컴파일합니다 — 아래 *컨텍스트 컴파일러 데이터 흐름* 참조. 인메모리 `ContextBundle`(`ContextCitation` 포함)을 반환하며 디스크에 아무것도 쓰지 않습니다. `project context` CLI 명령과 `compile_context` MCP 도구로 노출됩니다. |

### 영속성 포트 + 그래프 스토어

| 모듈 | 책임 |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `GraphStore` 프로토콜: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical`, 그리고 Phase 4 삭제 표면 — `delete_node` 및 `delete_nodes_by_source`(주어진 소스 경로를 제거한 뒤 프로비넌스 집합이 비는 노드를 삭제하므로 교차 파일 개념은 살아남음). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: 독립형 백킹 스토어; `node_provenance` 및 `node_memory` 사이드카 테이블을 소유합니다. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | 스토어 URL(`sqlite:///…`, `hypepaper-postgres://…`)을 올바른 `GraphStore`로 해석하여 MCP 서버가 런타임에 임의의 백킹 스토어를 가리킬 수 있게 합니다. |

### 외부 어댑터(이번 라운드에서는 변경되지 않음)

| 모듈 | 책임 |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Obsidian 볼트 투영(그래프 색상 지정, Dataview 대시보드, 원시 자산). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Claude 코드 / Codex / Gemini / Kiro / Cursor / OpenCode 하네스 수출. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | 인바운드 Claude 코드/Codex 세션 검색, 정규화, `.tesserae/harness_sessions/` 하의 저장 및 수정된 마크다운 요약. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | 임시 사실 JSONL + 선택적 라이브 Graphiti 동기화. |
| [`tesserae/cognee_adapter.py`](../../tesserae/cognee_adapter.py) | Cognee 노드/에지 JSONL 번들 및 직접 추가/인식 경로. |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP stdio 서버. 검색/그래프: `schema`, `graph_summary`, `search_nodes`, `node_context`(`use_ppr` 포함), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`. 컨텍스트 엔진(v0.5.0): `compile_context`(온디맨드 컨텍스트 컴파일러), `embedding_status`, `fresh_insights`(감쇠 랭킹 세션 발견), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. 그 외 `ask`, 다중 프로젝트 레지스트리 도구(`list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`), `tesserae_setup_plan` / `tesserae_setup_apply`. |

## 프로젝트 작업공간 레이아웃

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; node_provenance(최초 관측, Phase 4)와
                              node_memory(감쇠 / 신뢰도 / 대체됨, Phase 5) 사이드카 테이블도 소유
  temporal_facts.jsonl        Graphiti-style temporal projection (수치형 재발 신뢰도)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  cognee_bundle/              Cognee nodes/edges/manifest JSONL
  wiki/                       L2 markdown wiki — see below
  site/                       L3 static site — see below
```

### `.tesserae/wiki/` (L2)

```text
wiki/
  sources/<slug>.md           raw documents from data/ + docs/, with frontmatter
  concepts/<slug>.md          Concept / TechnicalTerm / Algorithm / etc.
  entities/<slug>.md          Model / Dataset / Benchmark / Metric / Org / Person
  papers/<slug>.md            Paper hub
  repos/<slug>.md             Repository / Project / CodeProject
  topics/<slug>.md            ResearchField / ResearchTopic / ApproachFamily / Trend
  syntheses/<slug>.md         pulse, daily_digest, weekly, topic, comparison, field_overview
  questions/<slug>.md         OpenQuestion
```

각 파일은 직접 편집할 수 있습니다. 다음 컴파일에서는 본문 해시가 프로젝터가 작성하는 것과 다른 한 사용자 편집을 존중합니다. (본문만 편집하면 승리하며, 머리말 편집은 머리말이 재생성되기 때문에 다음 컴파일에서 패합니다.) Obsidian 사용자는 `.tesserae/wiki/`를 직접 열 수 있습니다. 기존 `obsidian_vault/` 어댑터는 대체품이 아닌 별도의 프로젝션입니다.

### `.tesserae/site/` (L3)

```text
site/
  index.html                  home + project pulse
  about.html                  schema, build info
  assets/{style.css,app.js}   single CSS bundle + single JS bundle
  sources/index.html
  sources/<slug>.html
  sources/<slug>.txt          AI sibling — plain text
  sources/<slug>.json         AI sibling — structured record
  concepts/…  entities/…  papers/…  repos/…  topics/…  syntheses/…  questions/…
  sessions/index.html          imported harness-session index
  sessions/<project>/<id>.html session detail: summary, metadata, turn rail, markdown turns, collapsed tools
  timeline/index.html
  graph/index.html            interactive 2D + 3D force layout
  graph.json                  full graph payload (incl. code nodes, for tooling)
  graph.jsonld                schema.org Dataset, wiki-layer nodes only
  search-index.json           palette + page search; wiki-layer kinds only
  llms.txt                    llmstxt.org — short index
  llms-full.txt               llmstxt.org — every page body, capped 5MB
  sitemap.xml                 every emitted route
  rss.xml                     last 30 syntheses
  robots.txt                  permissive (crawl + index)
  ai-readme.md                machine-readable site map
  manifest.json               sha256 + size for every emitted file
```

## 의도적으로 제외된 내용

재설계에서는 코드 클래스 및 코드 기능 노드가 `graph.json`에 유지되지만(MCP, Cognee 및 Graphiti 소비자는 여전히 이를 볼 수 있음) HTML 페이지를 얻지 못하고 `search-index.json`에 나타나지 않으며 탐색에 나타나지 않습니다. 이것이 바로 사용자 대면 계약입니다. 위키는 기능 브라우저가 아닌 문서 우선 지식 기반입니다.

구체적으로, `StaticSiteBuilder`는 유형이 L2 위키 종류 맵(`tesserae/wiki_projector.py::_KIND_FOR_TYPE`)에 없는 모든 노드를 건너뜁니다.

- L2 + L3에서 제외: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, 모든 `Claim` 변형(`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- 여전히 나타나는 표면: 글머리 기호, 배지, 이웃 수 또는 관련 위키 페이지의 인라인 및 다운스트림 도구용 `graph.json`에서 발췌한 증거.

코드 수준 검색이 필요한 경우 소스 트리에서 LSP/호출 그래프 도구를 직접 가리키십시오. 이는 "이 프로젝트가 알고 있는 내용에 대한 위키"와는 다른 문제입니다.

## 멱등성 이야기

재설계는 **변경되지 않은 입력에 대해 두 개의 연속 `project compile` 실행에서 바이트 동일한 출력**을 목표로 합니다. 조각들:

1. **소스 추출**은 `manifest.json` 콘텐츠 해시를 사용합니다. 변경되지 않은 파일은 건너뛰므로 그래프가 안정적으로 유지됩니다.
2. **Wiki 레이어 쓰기**는 본문 수준에서 멱등성을 갖습니다. `WikiPageStore.write_page`는 기존 파일을 읽고, 앞부분을 제거하고, 본문을 sha256s로 처리하고, 새 본문이 동일하게 해시되면 단락합니다. 새 앞부분의 `generated_at` 타임스탬프가 다른 경우에도 마찬가지입니다. 이것은 재구축 시 git diff를 단단히 유지하는 핵심 트릭입니다.
3. **합성 출력**은 머리말에 `content_hash: sha256-…`를 포함합니다. 본문 해시는 `generated_at` 없이 계산되므로 동일한 그래프에서 반복 컴파일하면 동일한 해시가 생성되고 `Synthesis` 노드는 그래프 메타데이터에 동일한 `content_hash`를 전달합니다.
4. **사이트 렌더링**은 `write_site` 시작 부분에서 `site/`를 지운 다음 결정론적으로 씁니다. 경로가 정렬되고 사전이 `sort_keys=True`로 덤프되고 `manifest.json`가 `sorted(rglob("*"))`를 통해 이동됩니다. 두 번 실행하면 매니페스트를 포함하여 바이트와 동일한 파일이 생성됩니다.

이는 `tests/test_site_pages.py` 및 `tests/test_project_e2e_redesign.py`의 종단 간 연기로 확인됩니다(두 번 컴파일, 사이트 비교, 파일 델타 0 예상).

## 스케일링 노트

- **그래프 보기 노드 캡.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py)는 대화형 강제 레이아웃에 대한 페이지 내장 페이로드를 제한합니다. ~1500개 노드를 초과하면 중급 하드웨어에서 브라우저 측 시뮬레이션이 느려지므로 개수가 한도를 초과하면 페이지에서 가장 낮은 등급의 Wiki 레이어 노드를 먼저 삭제합니다. 내보낸 `graph.json`는 영향을 받지 않습니다. 항상 전체 그래프를 포함합니다. 코드 노드는 한도가 적용되기 전에 필터링됩니다.
- **`llms-full.txt` 캡.** [`tesserae/site/exports.py`](../../tesserae/site/exports.py)에는 5MB 안전 캡이 적용됩니다. 캡에 도달하면 파일은 `[TRUNCATED — see graph.jsonld for the full set]` 마커로 끝납니다. JSON-LD 소비자가 전체 세트를 기대하기 때문에 `graph.jsonld`에는 제한이 없습니다.
- **색인 검색.** Wiki 레이어 종류에만 해당됩니다. 코드 그래프 노드는 `search-index.json`를 입력하지 않습니다. dogfood 코퍼스의 재설계 목표는 500KB 미만이며 현재는 그 수준에 훨씬 못 미치고 있습니다.
- **페이지당 바이트 예산(경험 법칙).** 각 세부 정보 페이지 < 60KB gz HTML, 공유 CSS < 30KB, 공유 JS < 25KB, 그래프 페이지에만 시그마 공급업체(~60KB). 그래프 보기는 한 번 로드된 3D-force-graph + Three.js를 사용합니다. 다른 모든 페이지는 바닐라 상태를 유지합니다.
- **dogfood의 컴파일 시간.** 최근 개발 컴퓨터에서는 최대 300개의 마크다운 파일이 5초 이내에 추출됩니다. 사이트 렌더링은 ~2초를 더 추가합니다. 위키 레이어의 멱등성은 후속 컴파일이 변경된 경로만 건드린다는 것을 의미합니다.

## 프런트엔드 상호 작용 표면

- **검색 팔레트** — `cmd+k` / `ctrl+k` / `/`. 위키 종류로 범위가 지정된 `search-index.json`에 대한 퍼지 일치입니다. 최근 페이지는 `localStorage`에 유지되었습니다.
- **테마 토글** — 오른쪽 상단 버튼; `data-theme="dark"`는 `localStorage`에 저장되며 플래시를 방지하기 위해 페인트 전에 적용됩니다.
- **고정된 오른쪽 목차** — 데스크탑에만 해당; 모바일에서는 `<details>` 서랍으로 축소됩니다. 페이지 본문의 `<h2>` / `<h3>`에서 생성됩니다.
- **활동 히트맵** — 월 + 평일 라벨이 포함된 26주 SVG입니다. 셀은 해당 날짜의 `digest.md` 소스 페이지가 있는 경우 해당 페이지로 연결됩니다. (일별 타임라인 세부정보 페이지 — `/timeline/<YYYY-MM-DD>.html` —는 명시적인 후속 작업이며 `render_timeline`의 인라인 공지에 플래그가 지정되어 있습니다. ⚠ 진행 중입니다.)
- **그래프 보기** — `/graph/`. 호버 도구 설명, 가장자리 레이블, 커서 고정 확대/축소 및 2D 대체 보기가 포함된 3D 힘 레이아웃(3d-force-graph + Three.js). 노드 색상은 `ResearchNodeType`에서 나옵니다.
- **모바일 셸** — 서랍 레일, 하단 탐색, 유체 유형, 터치 안전 히트 대상(≥ 44px).

## 테스트 전략

- **유닛** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **엔진 스파인** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **자기 개선 메모리** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **검색 + 임베딩** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **컨텍스트 컴파일러** — `tests/test_context_compiler.py`(형태, 인용 무결성, 결정성, 예산, PPR 폴백), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **증분 컴파일(실험적)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **멱등성** — `tests/test_project_e2e_redesign.py`는 두 번 컴파일하고 `wiki/` 및 `site/`에서 제로 차이를 주장합니다.
- **링크 무결성** — `tests/test_frontend.py`는 href에 대해 방출된 모든 HTML을 구문 분석하고 모든 내부 링크가 생성된 파일로 확인된다고 주장합니다. `nodes/codeclass-*.html`는 생산되지 않습니다.
- **AI 형제** — 모든 `path/foo.html`에 대해 테스트 스위트는 `path/foo.txt` 및 `path/foo.json`가 존재한다고 주장합니다. JSON은 `{title, kind, body, links}`를 구문 분석하고 포함합니다.
- **극작가 없음** — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`의 바닐라 pytest.

## 관련 문서

- [빠른 시작](quickstart.ko.md) — `project init`에서 탐색 가능한 사이트까지의 최소 경로입니다.
- [프런트엔드 재설계 연습](frontend-redesign.ko.md) — 주석이 달린 모든 경로 둘러보기.
- [기능 맵](feature-map.ko.md) — 무엇이 배송되고, 무엇이 진행 중인지, 파일 포인터가 포함되어 있습니다.
- [Self-dogfood 데모](self-dogfood.ko.md) — 자체 저장소에 대해 Tesserae를 실행합니다.
