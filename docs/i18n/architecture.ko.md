# 아키텍처

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae는 **컨텍스트 엔진**입니다. 프로젝트로부터 자기 개선 지식 베이스를 재구축하고 이를 바로 쓸 수 있는 컨텍스트로 에이전트에게 건네줍니다. 세 개의 기둥 위에서 동작합니다: (1) **세션 모니터링** — 라이브 에이전트/작업 세션을 감시하고 발견 사항을 발생하는 대로 포착; (2) **자율적·능동적 지식 수집** — 지시를 기다리는 대신 지식을 지속적으로 끌어오고 재추출하여 베이스를 개선하는 파이프라인 + 슈퍼바이저 루프; (3) **온디맨드 문서/컨텍스트** — 같은 베이스에서 컴파일되는 사용자 요청 아티팩트. 타입 그래프, markdown vault, 정적 사이트는 지식 베이스의 *프로젝션*이고; 엔진은 그것들을 신선하게 유지하며 에이전트에게 공급하는 루프입니다.

그 아래에서 Tesserae는 소스 자료 디렉터리를 통제된 타입 지식 그래프로 바꾸고, 그 그래프를 내구성 있는 markdown 위키 레이어를 거쳐 정적이고 AI 친화적인 웹사이트로 프로젝션합니다. 2026년 4월 리디자인은 프로젝션 쪽을 Karpathy 3레이어 모델 중심으로 재조직했습니다: 원시 증거는 원시 그대로 유지되고, 타입 그래프가 온톨로지를 통제하며, markdown 위키 레이어가 그래프와 모든 렌더링 출력 사이에 자리합니다. 정적 사이트는 그래프의 직접 덤프가 아니라 그 위키 레이어의 *렌더러*이며, 스키마는 [`tesserae/research_graph.py`](../../tesserae/research_graph.py)의 통제 온톨로지입니다. **v0.5.0** 마일스톤(2026년 6월)은 세 기둥 모두를 구동하는 엔진 스파인을 추가했습니다 — 아래의 *엔진 스파인*과 *온디맨드 컨텍스트 컴파일러*를 참조하세요.

## Karpathy 3레이어 모델

LLM 친화적 지식 베이스에 대한 Andrej Karpathy의 프레이밍은 각각 고유한 내구성 보장을 갖는 세 레이어를 구별합니다:

| 레이어 | 관심사 | 저장소 위치 | 소유자 |
|---|---|---|---|
| L1 — 원시 소스 | 사용자가 저작하거나 수집한 문자 그대로의 바이트. Append-only. | `data/`, `docs/`, `.tesserae/config.json`에 참조된 프로젝트 트리 | 사용자 |
| L2 — 위키 | YAML frontmatter를 갖는 타입 markdown 페이지(sources, concepts, entities, papers, repos, topics, syntheses, questions). 멱등: 매 compile마다 재생성되지만 콘텐츠 해시가 바뀔 때만 다시 기록됨. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — 렌더링 | 정적 HTML 사이트, AI 시블링 export, 검색 인덱스, sitemap, JSON-LD. 매 compile마다 삭제 후 재기록되지만 재실행 간 바이트 안정적. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

스키마는 별도의 축으로 세 레이어 모두를 가로지릅니다: `graph.json`의 `ResearchGraph`는 L2 페이지가 링크하는 통제 온톨로지이고, [`tesserae/research_graph.py`](../../tesserae/research_graph.py)의 `ResearchNodeType` / 엣지 화이트리스트가 어떤 타입이 존재할 수 있는지에 대한 진실의 원천입니다.

리디자인은 L2를 명시적으로 추가했습니다. 2026년 4월 이전에는 정적 사이트가 `graph.json`에서 곧바로 프로젝션되었고; 위키 레이어는 Obsidian vault export 안에만 존재했습니다. 이를 분리해서 얻은 것:

- 단일한 인간 편집 가능 표면 (Obsidian이나 아무 markdown 에디터에서 `.tesserae/wiki/`를 열기).
- 멱등 재빌드: `project compile`을 재실행해도 소스 콘텐츠가 바뀌지 않았다면 파일 diff가 0.
- 진화 로그: synthesis 페이지가 시간에 걸쳐 축적되어 프로젝트가 스스로를 서술하게 함.

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

모든 단계는 증분적입니다. 그래프 추출기는 `manifest.json` 콘텐츠 해시를 사용해 변경되지 않은 소스 파일을 건너뜁니다. `WikiPageStore.write_page`는 본문 해시가 디스크에 이미 있는 것과 일치하면 `False`를 반환하고 쓰기를 건너뜁니다. `StaticSiteBuilder`는 `.tesserae/site/`를 삭제하고 재기록하지만 출력은 결정적입니다 — 아래 "멱등성 이야기"를 참조하세요.

## 컨텍스트 컴파일러 데이터플로

온디맨드 컨텍스트 컴파일러([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py))는 기둥 3의 헤드라인 경로입니다. 질의 그리고/또는 명시적 시드 노드 id가 주어지면, `compile_context`는 그래프에서 곧바로 맞춤형 citation markdown 번들을 빌드해 메모리로 반환합니다 — `.tesserae/` 아래에 아무것도 기록하지 않습니다.

```
query / seeds
     │
     ▼  1. Seed resolution
        explicit seeds (kept iff they exist) + hybrid_search() hits, deduped, stable order
     │
     ▼  2. PPR expansion
        retrieval.ppr.personalized_pagerank ranks the depth-bounded k-hop neighbourhood;
        empty result (disconnected seeds) → fall back to seed order (bundle is never empty)
     │
     ▼  2b. 절차적 예약 (부여가 아니라 획득)
        PROCEDURAL_POOL_ORDER 순서로 풀당 슬롯 하나: Runbook, Gotcha, Event,
        DistilledNote, ExpertiseProfile. 슬롯은 그 타입에서 가장 높은 순위이면서
        생산자 provenance를 지닌 노드에게 갑니다 — 타입 이름만으로는 안 됩니다
     │
     ▼  3. Budget-bound selection
        walk PPR order, include each node's cited body until the next would overflow
        `budget` chars (budget <= 0 = uncapped; over-budget marker on a word boundary)
     │
     ▼  4. Cited markdown assembly
        one section per selected node + a trailing `## Citations` block.
        Body text prefers the projected wiki page (when a store + public wiki kind exist),
        else the node description, else a minimal stub. The no-LLM body embeds NO
        wall-clock timestamp → byte-identical for the same (graph, query, seeds, depth, budget).
     │
     ▼  5. Optional LLM synthesis  (only when synthesize=true AND ANTHROPIC_API_KEY is set)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

기본값: `depth=2`, `budget=32000`. 결정적 조립(1–4단계)이 계약이고; LLM 합성은 순수하게 부가적입니다. 같은 파이프라인이 `project context` CLI 명령, `compile_context` MCP 도구, 토픽 범위 export 슬라이스(`slice_export_context_for_topic`, 토픽 범위 `llms.txt`)를 뒷받침합니다.

**절차적 슬롯이 provenance로 획득되는 이유.** 다섯 개의 절차적 타입은 에이전트가
무엇을 했고, 무엇을 하는 법을 배웠고, 무엇을 잘하는지를 가리킵니다 — 그런데 문서
추출도 그 타입들을 만들 수 있어서, 논문 모집 공고를 읽은 LLM이 "CVPR 2026"이라는
`Event`를 정당하게 만들어 냅니다. 예약은 *가산적*입니다: 이웃 어디에 있든 노드를
예산 워크의 맨 앞으로 승격시킵니다. 따라서 타입만으로 예약하면 학회 마감일이
실제로 그 슬롯을 획득한 세션 발견을 밀어낼 수 있습니다. 둘을 가르는 것이
`has_producer_provenance`이며, 예약은 슬롯에 대한 주장일 뿐 그 증거가 아닙니다 —
`delivered`는 예산 워크 이후에 결정되므로 호출자는 "절차적 메모리가 예약되었다"와
"절차적 메모리가 도착했다"를 구별할 수 있습니다. `PROCEDURAL_POOLS` lint 코드가 그
격차를 보고합니다.


## 모듈 맵

### 위키 + synthesis (L2)

| 모듈 | 책임 |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | `WikiPage` dataclass, 파일시스템 I/O를 위한 `WikiPageStore`. stdlib 전용 YAML 부분집합 frontmatter 파서. 본문 해시 멱등성. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: 위키 레이어 타입의 각 `ResearchGraph` 노드를 알맞은 `kind/` 폴더의 markdown 페이지에 매핑. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: pulse, daily_digest, weekly, topic, comparison, field_overview를 위한 결정적 템플릿. `Synthesis` 노드와 `synthesizes` / `summarizes` 엣지를 그래프에 다시 추가. |

### 그래프 + 온톨로지

| 모듈 | 책임 |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `ResearchNodeType` enum (`SYNTHESIS` 포함), 엣지 타입 화이트리스트 (`synthesizes`, `summarizes` 포함), 검증. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | Alias 정규화 + 근사 중복 리뷰 큐. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | 개발 슬라이스를 위한 결정적 Python AST 추출기. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Claude CLI/OAuth 선택적 추출기. |

### 사이트 렌더러 (L3)

| 모듈 | 책임 |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: 사이트를 삭제 + 재빌드하고, 모든 라우트를 순회하며, export + AI 시블링 + manifest를 방출. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | 라우트당 렌더러 하나(홈, 인덱스, 상세 페이지, 타임라인, 그래프, about). `SiteContext`가 사전 계산된 인덱스를 운반하여 렌더러가 순수하게 유지됨. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML 프리미티브: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | 디자인 토큰 — CSS 변수, 라이트 + 다크 테마, 레이아웃, 타이포그래피, 모든 컴포넌트가 여기서 스타일링됨. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | 클라이언트 JS 번들: 검색 팔레트, 테마 토글, sigma + 3D-force 그래프 뷰. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | stdlib 전용 markdown 렌더러(링크, 오토링크, 코드, 강조, 헤딩). 외부 의존성 없음. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | 모든 `Related` 섹션이 사용하는 4신호 관련성 점수(직접 링크, 소스 겹침, Adamic-Adar, 타입 친화도). |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | `search-index.json` 빌더. 위키 레이어 종류만. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | 가져온 harness 이력을 위한 세션 인덱스/상세 렌더러: 프로젝트 메모리 요약 섹션, 대화 턴 레일, markdown 트랜스크립트 렌더링, 접힌 tool-use 블록. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, 페이지별 `.txt`/`.json` 시블링. |

### 파이프라인 오케스트레이션

| 모듈 | 책임 |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: 추출 → 그래프 → 메모리 패스 → 위키 레이어 → 사이트를 구동. `ProjectPaths`(`config`, `graph`, `manifest`, `wiki`, `site` 등)를 소유. 출처 기반 증분 compile이 가능한지 선제 판단(`incremental_compile`로 게이팅, 기본 OFF). |
| [`tesserae/cli.py`](../../tesserae/cli.py) | 평평한 동사 CLI 디스패치(레거시 `project`/`wiki` 하위 명령 그룹 삭제 후 ~2,732줄). 동사들 — `init`, `compile`, `ingest`, `context`, `ask`, `query`, `doctor`, `summary`, `decisions`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `setup`, `config`, `projects`, `sources`, `federation`, `integrations` — 은 [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py)에 메타데이터로 선언되고 손으로 등록되는 대신 그 트리로부터 연결됨. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`: worktree를 통해 `.tesserae/site/`를 `gh-pages` 브랜치로 푸시하고, 선택적으로 `gh`를 통해 Pages를 활성화. |

### 엔진 스파인 (v0.5.0 — 기둥 1 & 2)

엔진 스파인은 세션 모니터링과 자율 재수집을 구동하는 프로세스 내 루프입니다. 같은 `Pipeline.run()`이 CLI, 슈퍼바이저 데몬, 그리고 (이후) MCP 서버가 모두 호출하는 단일 refresh 경로입니다.

| 모듈 | 책임 |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: 순차 스텝 러너. prose refresh 체인(ingest → compile → project/publish)을 출력하고-종료하는 대신 구조화된 `List[StepResult]`를 반환하는 import 가능한 객체로 성문화하여, 모든 호출자가 결과 노출 방식을 스스로 결정. `run()`은 스텝별로 `Exception`을 포착하고(`KeyboardInterrupt`/`SystemExit`은 통과) 첫 실패에서 중단. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: 단일 소유자 asyncio 슈퍼바이저. 소스 디렉터리, Obsidian vault, harness-session 디렉터리를 감시; cancel-and-reschedule 디바운스로 `TriggerEvent` 버스트를 정확히 하나의 `Pipeline.run()`으로 병합. 기존 `watch.py` / `vault_watch.py` 감시자를 재사용하고(재작성하지 않음), pidfile을 기록하며, 진행 중 예외에도 생존. `engine`으로 노출(`--interval`, `--debounce`, `--once`). |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | 독립 실행 `export site --watch` 명령과 데몬의 소스/vault 레인 모두가 재사용하는 폴링 감시자. |

### 자기 개선 메모리 (v0.5.0 — 기둥 2)

Phase 5가 영속적 자기 개선을 활성화했습니다. 가변적인 노드별 상태는 `node_memory` SQLite 사이드카(`.tesserae/sqlite.db` 내부)에 존재하며, 불변의 `node_provenance.first_seen_at` 최초 관측 스탬프(Phase 4 사이드카)와 분리되어 있습니다. compile이 그래프 위에서 결정적 패스 집합을 구동합니다.

| 모듈 | 책임 |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + `node_memory` 테이블에 대한 저장소 불문 접근자(`read_memory`, `write_memory`, `bump_access`) — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. 어떤 호출 지점도 원시 SQL을 포함하지 않음. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: 세션 finding 순위화에 사용되는 Ebbinghaus 스타일 신선도 점수(최신 + 최다 접근 우선). |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass` (**기본 켜짐**): 더 오래된 근사 중복 인사이트를 더 새로운 것에 의해 대체된 것으로 표시하는 결정적 판정, `supersedes` 엣지 추가. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: 세션 인사이트를 그것이 논하는 코드 심볼에 `discusses` 엣지로 링크. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 같은 사이드카 위의 접근 강화와 모순 감지 헬퍼. |

재발 신뢰도는 출력에서 수치입니다: temporal 프로젝션은 각 fact의 `confidence`를 `NodeMemoryRow.confidence`(SQLite에서는 텍스트, `temporal.py`를 통해 노출)로부터 각인하고, 저장된 값이 없을 때만 `infer_confidence`로 폴백합니다.

### Retrieval (v0.5.0 — 기둥 2 & 3)

| 모듈 | 책임 |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: 세 레인을 융합하는 로컬 우선 hybrid retriever — Okapi BM25(k1=1.5, b=0.75), 케이스 폴딩 어휘/FTS 스타일 부분 문자열, 플러그형 embedding 레인 — reciprocal-rank fusion(RRF, k=60)을 통해. 완전 결정적. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: 멀티홉 시드 확장을 위한 그래프 위의 HippoRAG-2 스타일(arXiv:2502.14802) Personalized PageRank — 1홉 이웃뿐 아니라 시드에서 여러 홉 떨어진 잘 연결된 노드를 노출. |
| Embedding 백엔드 (Phase 6, Track B) | hybrid embedding 레인의 기본 백엔드는 추가 의존성이 필요 없는 결정적 hash-bucket 유사 embedding; 선택적 의존성이 설치되어 있으면 `sentence-transformers`(`all-MiniLM-L6-v2`)를 선호하며 지연 로드. `embedding_status` MCP 도구가 어느 백엔드가 활성인지 보고. |

### 온디맨드 컨텍스트 컴파일러 (v0.5.0 — 기둥 3 헤드라인)

| 모듈 | 책임 |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: 기둥 3의 헤드라인 기능. 질의/시드 집합에 대한 맞춤형 **citation** 컨텍스트 번들을 그래프에서 곧바로 컴파일 — 위의 *컨텍스트 컴파일러 데이터플로* 참조. 인메모리 `ContextBundle`(`ContextCitation` 포함)을 반환; 디스크에 아무것도 기록하지 않음. `project context` CLI 명령과 `compile_context` MCP 도구로 노출. |

### 영속화 포트 + 그래프 저장소

| 모듈 | 책임 |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `GraphStore` 프로토콜: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical`, 그리고 Phase 4 삭제 표면 — `delete_node`와 `delete_nodes_by_source`(주어진 소스 경로 제거 후 출처 집합이 비게 되는 노드를 삭제하므로, 여러 파일에 걸친 개념은 생존). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: 독립 백킹 스토어; `node_provenance`와 `node_memory` 사이드카 테이블을 소유. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | 저장소 URL(`sqlite:///…`, `hypepaper-postgres://…`)을 알맞은 `GraphStore`로 해석하여, MCP 서버가 런타임에 어떤 백킹 스토어든 가리킬 수 있게 함. |

### 외부 어댑터 (이번 라운드에서 변경 없음)

| 모듈 | 책임 |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Obsidian vault 프로젝션(그래프 색상, Dataview 대시보드, 원시 에셋). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode harness export. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | 인바운드 Claude Code/Codex 세션 발견, 정규화, `.tesserae/harness_sessions/` 아래 저장, 그리고 편집(redact)된 markdown 요약. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | Temporal-fact JSONL + 선택적 라이브 Graphiti 동기화. |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP stdio 서버. Retrieval/그래프: `schema`, `graph_summary`, `search_nodes`, `node_context` (`use_ppr` 지원), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`. 컨텍스트 엔진 (v0.5.0): `compile_context`(온디맨드 컨텍스트 컴파일러), `embedding_status`, `fresh_insights`(감쇠 순위 세션 finding), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. 추가로 `ask`, 멀티 프로젝트 레지스트리 도구(`list_projects`, `register_project`, `unregister_project`, `list_sessions`), `tesserae_setup_plan` / `tesserae_setup_apply`. |

## 프로젝트 워크스페이스 레이아웃

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; also owns the node_provenance
                              (first-seen, Phase 4) and node_memory (decay /
                              confidence / superseded, Phase 5) sidecar tables
  temporal_facts.jsonl        Graphiti-style temporal projection (numeric recurrence confidence)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
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

각 파일은 손으로 편집할 수 있습니다; 본문 해시가 프로젝터가 기록할 내용과 다르기만 하면 다음 compile이 사용자 편집을 존중합니다. (본문만 편집하면 이깁니다; frontmatter 편집은 frontmatter가 재생성되기 때문에 다음 compile에서 집니다.) Obsidian 사용자는 `.tesserae/wiki/`를 직접 열 수 있습니다; 기존 `obsidian_vault/` 어댑터는 별도의 프로젝션이지 대체물이 아닙니다.

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

## 헌장

커뮤니티 탐지는 도메인 어휘를 **제안**하고, 헌장
([`tesserae/charter.py`](../../tesserae/charter.py))은 명시적 개편 사이에서 그것을
**소유**합니다. 이 분리가 존재하는 이유는 탐지가 결정적이지만 안정적이지는 않기
때문입니다: 동일한 입력은 1,649개 커뮤니티를 정확히 재현하지만, 15개 노드짜리
문서 하나가 구성원의 약 29%를 커뮤니티 사이로 옮기고, 큰 커뮤니티의 Jaccard를
0.39–0.60으로 떨어뜨립니다. 그래서 커뮤니티 소속을 키로 삼는 모든 것은 수집마다
사실상 전면 캐시 미스를 겪습니다 — 그리고 이 코퍼스는 매일 수집합니다.

그래서 헌장이 제도를 고정합니다: 섹션을 탐지하고, 몫 그래프(섹션당 노드 하나,
섹션 간 L0 엣지당 `part_of` 엣지 하나)로 접은 뒤, **크기가 아니라 하위
커뮤니티로** 부문 → 부서 → 팀으로 나눕니다. 각 도메인의 앵커는 차수가 가장 높은
구성원이며, 어떤 두 도메인도 같은 앵커를 갖지 않도록 탐욕적으로 고릅니다. 사람이
보는 슬러그는 그 앵커에서 한 번 만들어져 고정됩니다. 개편이 일어나면 `succeed`가
앵커를 기준으로 슬러그를 이어받으므로, 아래의 구성원이 뒤섞여도 이름은 살아
남습니다. 모든 노드는 정확히 하나의 도메인에 들어갑니다: `intake_members`가
탐지라면 조용히 잃었을 버려진 싱글턴과 엣지가 고립된 섹션을 붙잡습니다.

`tesserae domains status [--json]`이 트리를 출력합니다. **상태:** 모듈과 CLI
동사는 제공되며 테스트로 덮여 있지만, `compile`은 아직 헌장을 쓰지 않습니다 —
그때까지 이 명령은 "no charter yet"을 보고하고 0으로 종료하는데, 한 번 읽기
경계 아래의 프로젝트에 대해서도 그것이 정직한 답이기 때문입니다.

## 의도적으로 제외된 것

리디자인은 명시적인 선을 그었습니다: 코드 클래스와 코드 함수 노드는 `graph.json`에 남지만(따라서 MCP와 Graphiti 소비자는 여전히 볼 수 있음) HTML 페이지를 절대 얻지 않고, `search-index.json`에 절대 나타나지 않으며, 내비게이션에도 절대 나타나지 않습니다. 그것이 사용자 대상 계약입니다 — 위키는 문서 우선 지식 베이스이지 함수 브라우저가 아닙니다.

구체적으로, `StaticSiteBuilder`는 타입이 L2 위키 종류 맵(`tesserae/wiki_projector.py::_KIND_FOR_TYPE`)에 없는 모든 노드를 건너뜁니다:

- L2 + L3에서 제외: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, 모든 `Claim` 변형(`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- 여전히 나타나는 표면: 관련 위키 페이지의 불릿, 배지, 이웃 수, 인라인 근거 발췌, 그리고 다운스트림 도구를 위한 `graph.json`.

코드 수준 탐색이 필요하면 소스 트리에 LSP / call-graph 도구를 직접 대세요 — 그것은 "이 프로젝트가 아는 것의 위키"와는 다른 문제입니다.

## 멱등성 이야기

리디자인은 **변경되지 않은 입력에 대해 연속된 두 번의 `project compile` 실행이 바이트 단위로 동일한 출력**을 목표로 합니다. 구성 요소:

1. **소스 추출**은 `manifest.json` 콘텐츠 해시를 사용합니다; 변경되지 않은 파일은 건너뛰므로 그래프가 안정적으로 유지됩니다.
2. **위키 레이어 쓰기**는 본문 수준에서 멱등적입니다. `WikiPageStore.write_page`는 기존 파일을 읽고, frontmatter를 벗겨내고, 본문을 sha256 해싱하고, 새 본문이 같은 해시라면 — 새 frontmatter의 `generated_at` timestamp가 다르더라도 — 바로 반환합니다. 이것이 재빌드에서 git diff를 좁게 유지하는 핵심 트릭입니다.
3. **Synthesis 출력**은 frontmatter에 `content_hash: sha256-…`를 담습니다. 본문 해시는 `generated_at` 없이 계산되므로 같은 그래프에서 반복 compile해도 같은 해시가 나오고, `Synthesis` 노드는 그래프 메타데이터에 같은 `content_hash`를 담습니다.
4. **사이트 렌더링**은 `write_site` 시작 시 `site/`를 삭제한 뒤 결정적으로 기록합니다: 라우트는 정렬되고, 딕셔너리는 `sort_keys=True`로 덤프되며, `manifest.json`은 `sorted(rglob("*"))`로 순회됩니다. 두 실행이 manifest를 포함해 바이트 단위로 동일한 파일을 생산합니다.
5. **노드의 날짜는 소스에서 파생됩니다.** 노드의 `first_seen_at`은 컴파일 시점의 벽시계가 아니라, 그 소스가 수집된 경로에서 나옵니다. 시계를 읽으면 매 재실행이 diff가 되므로, 이 항목의 순진한 버전은 1번을 무너뜨립니다. 같은 규칙이 `Event` 패스를 바이트 멱등으로 유지합니다: 생성되는 모든 id, 본문, 날짜가 내용에서 파생되며, 481개 세션 코퍼스에서 검증되었습니다.

이는 `tests/test_site_pages.py`와 `tests/test_project_e2e_redesign.py`의 엔드투엔드 스모크(두 번 compile, 사이트 diff, 파일 델타 0 기대)로 검증됩니다.

## 스케일링 참고 사항

- **그래프 뷰 노드 상한.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py)이 인터랙티브 포스 레이아웃의 페이지 내장 페이로드를 제한합니다. 약 1500 노드를 넘으면 중급 하드웨어에서 브라우저 측 시뮬레이션이 느려지므로, 개수가 상한을 넘으면 페이지는 차수가 가장 낮은 위키 레이어 노드부터 버립니다. 내보낸 `graph.json`은 영향받지 않습니다 — 항상 전체 그래프를 담습니다. 코드 노드는 상한 적용 전에 필터링됩니다.
- **`llms-full.txt` 상한.** [`tesserae/site/exports.py`](../../tesserae/site/exports.py)에 5 MB 안전 상한이 적용됩니다; 상한에 도달하면 파일은 `[TRUNCATED — see graph.jsonld for the full set]` 마커로 끝납니다. JSON-LD 소비자는 전체 집합을 기대하므로 `graph.jsonld`는 무제한입니다.
- **검색 인덱스.** 위키 레이어 종류만. 코드 그래프 노드는 `search-index.json`에 절대 들어가지 않습니다; 리디자인 목표는 dogfood 코퍼스 기준 500 KB 미만이며 오늘 우리는 그보다 훨씬 아래에 있습니다.
- **페이지별 바이트 예산 (경험칙).** 각 상세 페이지 < 60 KB gz HTML, 공유 CSS < 30 KB, 공유 JS < 25 KB, sigma 벤더는 그래프 페이지에서만(~60 KB). 그래프 뷰는 한 번 로드되는 3D-force-graph + Three.js를 사용하고; 나머지 모든 페이지는 vanilla로 유지됩니다.
- **dogfood에서의 compile 시간.** 최신 개발 머신에서 ~300개의 markdown 파일이 5초 미만에 추출됩니다; 사이트 렌더가 ~2초를 추가합니다. 위키 레이어의 멱등성 덕분에 이후 compile은 변경된 경로만 건드립니다.

## 프론트엔드 인터랙션 표면

- **검색 팔레트** — `cmd+k` / `ctrl+k` / `/`. 위키 종류로 범위 한정된 `search-index.json`에 대한 퍼지 매치. 최근 페이지는 `localStorage`에 영속화.
- **테마 토글** — 우상단 버튼; `data-theme="dark"`가 `localStorage`에 저장되고 플래시를 피하기 위해 페인트 전에 적용됨.
- **고정 오른쪽 TOC** — 데스크톱 전용; 모바일에서는 `<details>` 드로어로 접힘. 페이지 본문의 `<h2>` / `<h3>`에서 생성.
- **활동 히트맵** — 월 + 요일 레이블이 있는 26주 SVG. 셀은 그 날의 `digest.md` 소스 페이지가 존재하면 그리로 링크. (일자별 타임라인 상세 페이지 — `/timeline/<YYYY-MM-DD>.html` — 는 명시적 후속 작업; `render_timeline`의 인라인 알림이 이를 표시. ⚠ 진행 중.)
- **그래프 뷰** — `/graph/`. 호버 툴팁, 엣지 레이블, 커서 기준 줌을 갖춘 3D 포스 레이아웃(3d-force-graph + Three.js)과 2D 폴백 뷰. 노드 색상은 `ResearchNodeType`에서.
- **모바일 셸** — 드로어 레일, 하단 내비, 유동 타이포, 터치 안전 히트 타깃(≥ 44 px).

## 테스트 전략

- **단위** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **엔진 스파인** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **자기 개선 메모리** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Retrieval + embedding** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **컨텍스트 컴파일러** — `tests/test_context_compiler.py`(형태, citation 무결성, 결정성, 예산, PPR 폴백), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **증분 compile (실험적)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **멱등성** — `tests/test_project_e2e_redesign.py`가 두 번 compile하고 `wiki/`와 `site/`의 diff가 0임을 단언.
- **링크 무결성** — `tests/test_frontend.py`가 방출된 모든 HTML의 href를 파싱하고 모든 내부 링크가 생성된 파일로 해석됨을 단언. `nodes/codeclass-*.html`은 생산되지 않음.
- **AI 시블링** — 모든 `path/foo.html`에 대해 테스트 스위트는 `path/foo.txt`와 `path/foo.json`이 존재함을 단언; JSON은 파싱되고 `{title, kind, body, links}`를 포함.
- **Playwright 없음** — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 아래의 vanilla pytest.

## 관련 문서

- [퀵스타트](quickstart.ko.md) — `project init`에서 탐색 가능한 사이트까지의 최소 경로.
- [프론트엔드 리디자인 워크스루](frontend-redesign.ko.md) — 모든 라우트의 주석 달린 투어.
- [기능 맵](feature-map.ko.md) — 출시된 것, 진행 중인 것, 파일 포인터 포함.
- [Self-dogfood 데모](self-dogfood.ko.md) — Tesserae를 자체 저장소에 대해 실행하기.
