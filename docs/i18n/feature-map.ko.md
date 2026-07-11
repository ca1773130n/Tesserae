# 기능 맵

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
이 문서는 현재 Tesserae에 구현된 기능을 상태, 소스 파일, 문서 위치와 함께 요약합니다.

Tesserae는 세 개의 기둥 위에서 동작하는 **컨텍스트 엔진**입니다: (1) 세션 모니터링, (2) 자율적·능동적 지식 수집, (3) 온디맨드 문서/컨텍스트. 타입 그래프, vault, 정적 사이트는 지식 베이스의 프로젝션입니다. 아래 기능은 어느 기둥에 봉사하는지에 따라 그룹화되어 있습니다; **v0.5.0** 마일스톤(2026년 6월)이 엔진 스파인과 기둥 3의 헤드라인 기능인 온디맨드 컨텍스트 컴파일러를 출시했습니다.

상태 범례: ✅ 출시됨 · ⚠ 진행 중 / 부분적.

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
