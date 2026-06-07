# 프런트엔드 재설계 — 주석이 있는 라우트 둘러보기

<!-- translations:start -->
<p align="center"><a href="../frontend-redesign.md">English</a> · <a href="frontend-redesign.ko.md">한국어</a> · <a href="frontend-redesign.zh.md">中文</a> · <a href="frontend-redesign.ja.md">日本語</a> · <a href="frontend-redesign.ru.md">Русский</a> · <a href="frontend-redesign.es.md">Español</a> · <a href="frontend-redesign.fr.md">Français</a> · <a href="frontend-redesign.de.md">Deutsch</a></p>
<!-- translations:end -->
이 문서는 재설계된 Tesserae 정적 사이트에서 보이는 모든 라우트를 안내하는 투어입니다. [`architecture.md`](architecture.ko.md)의 상위 수준 모델과 [`feature-map.md`](feature-map.ko.md)의 상태 표를 보완합니다.

사이트는 `tesserae compile` 실행 후 `.tesserae/site/`에 생성됩니다. 로컬에서 확인하려면:

```bash
tesserae serve --port 8765
# open http://127.0.0.1:8765/
```

모든 라우트는 정적 HTML 파일이며, AI 소비자를 위한 두 형제 파일(`<page>.txt`, `<page>.json`)이 함께 생성됩니다. 사이트 전체 AI 내보내기(`llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, `manifest.json`)는 이 문서의 끝부분에서 설명합니다.

상태 범례: ✅ 출시됨 · ⚠ 진행 중.

## 모든 페이지의 공통 규칙

모든 리프 페이지는 동일한 구조를 따릅니다(디자인 사양 §3.3):

```
breadcrumbs
eyebrow (type · last updated · ≈ reading time)
TITLE
right-rail TOC (sticky on desktop, drawer on mobile)
lead paragraph
rendered markdown body
Mentions in the corpus  — bullets with badges + counts
Related (4-signal ranked) — card grid
Source provenance       — file path, line excerpt
Activity                — sparkline, weekly mentions
AI siblings footer      — links to the .txt and .json
```

사이트 전체 크롬:

- **상단 바.** 왼쪽에는 로고와 프로젝트 이름, 오른쪽에는 검색 트리거와 테마 토글이 있습니다.
- **왼쪽 레일**(데스크톱 ≥ 1024 px): 계층형 트리 — Home, Recent activity, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Open questions, Sessions, Timeline, Graph view, About. 개수는 `manifest.json`에서 가져옵니다.
- **하단 내비게이션**(모바일): 드로어 레일은 접히고, 하단 내비게이션에는 가장 자주 쓰는 종류가 노출됩니다.
- **검색 팔레트.** `cmd+k` / `ctrl+k` / `/` — `search-index.json`을 대상으로 위키 종류 범위에서 퍼지 매칭합니다. 최근 페이지는 `localStorage`에 저장됩니다.
- **테마.** 기본은 라이트 모드이며, 토글하면 `localStorage`에 `data-theme="dark"`가 유지됩니다. 깜박임을 피하기 위해 페인트 전에 적용됩니다.

## 홈

### `/` ✅

> _스크린샷: home.png_

홈 페이지는 프로젝트의 현재 흐름을 보여줍니다. 전역 `pulse` 종합(`wiki/syntheses/pulse.md`)이 구동하며, 이는 컴파일 때마다 다시 생성됩니다. 히어로 영역은 소스, 개념, 논문, 열린 질문으로 구성된 통계 행이며, 그 뒤에는 최신 `pulse` 본문에서 가져온 1~3개의 “이번 주 새로운 내용” 카드가 이어집니다. 히어로 아래에는 처음 방문한 사용자가 내비게이션을 읽지 않고도 들어갈 수 있도록 각 종류의 인덱스 페이지로 연결되는 선별된 진입점이 있습니다.

LLM 에이전트를 가장 먼저 착지시키기 좋은 페이지입니다. 말뭉치에서 신호 대 잡음비가 가장 높은 요약을 제공합니다. 카드는 인덱스로 되돌아가지 않고 리프 페이지로 연결됩니다.

**주목할 상호작용.** 통계 행을 클릭하면 해당 종류의 인덱스로 스크롤하거나 이동합니다. 히어로 문구는 편집 가능합니다. `wiki/overview.md`를 직접 작성하면 다음 컴파일 때 홈 페이지가 이를 반영합니다.

**관련 라우트.** 활동 로그는 [Timeline](#timeline), 긴 형식의 글은 [Syntheses](#syntheses), 공간적 보기는 [Graph](#graph-view)를 참고하세요.

## 소스

이는 L1 원시 문서입니다. `.tesserae/config.json`이 참조하는 `data/`, `docs/`, 프로젝트 트리의 파일들이 해당됩니다. 모든 소스는 `SourceDocument`(또는 `Paper` / `Repository`) 노드가 되며 `WikiLayerProjector`가 위키 페이지로 투영합니다.

### `/sources/` ✅

> _스크린샷: sources-index.png_

말뭉치가 알고 있는 모든 원시 문서의 표입니다. 열은 유형 배지(Document / Paper / Repository / Project), 제목, 언급 수, 마지막 업데이트로 구성됩니다. 표는 지브라 스트라이프가 적용되고, 호버하면 한 줄 미리보기가 표시되며, 유형 배지는 검색 팔레트(`/ kind:papers`)로 필터링할 수 있습니다.

위키가 어떤 문자 그대로의 근거 위에 구축되었는지 에이전트가 열거해야 할 때 사용하는 인덱스입니다.

**관련 라우트.** 논문만 보려면 [Papers](#papers), 저장소만 보려면 [Repos](#repos), 추출된 관점은 [Concepts](#concepts)를 참고하세요.

### `/sources/<slug>.html` ✅

> _스크린샷: source-detail.png_

stdlib 마크다운 파이프라인(`tesserae/site/markdown.py`)을 통해 렌더링된 단일 소스 문서입니다. 본문은 안전한 링크/이미지 렌더링이 적용된 원본 마크다운입니다. 본문 아래에는 다음이 있습니다.

- **언급** — 이 소스에서 추출된 모든 개념 / 엔티티 / 논문과 엣지 유형 배지.
- **백링크** — 여기로 연결되는 다른 모든 위키 페이지.
- **관련 항목** — 네 가지 신호로 순위화(직접 링크 3.0 + 소스 중복 4.0 + Adamic-Adar 1.5 + 유형 친화도 1.0).
- **소스 출처** — 원본 파일 경로 + 근거로 쓰이는 원시 파일의 처음 12줄.
- **활동** — 최근 12주간 주간 언급의 스파크라인.
- **AI 형제 푸터** — `<page>.txt` 일반 텍스트 보기, `<page>.json` 구조화 레코드.

**주목할 상호작용.** 본문 안의 URL과 arXiv ID는 자동 링크됩니다. 코드 블록은 클릭하여 복사할 수 있고, 오른쪽 레일 TOC는 스크롤을 추적합니다.

## 개념

개념은 말뭉치 전반에서 반복되는 아이디어, 용어, 알고리즘, 아키텍처, 방법론입니다. 포함 범위는 `Concept`, `TechnicalTerm`, `Algorithm`, `MathematicalConcept`, `MethodologicalConcept`, `ArchitecturePattern`, `TrainingParadigm`, `InferenceStrategy`, `EvaluationProtocol`, `Task`, `Capability`, `ObjectiveFunction`입니다.

### `/concepts/` ✅

> _스크린샷: concepts-index.png_

패싯 필터가 가능한 카드 그리드입니다. 각 카드는 유형 배지, 제목, 한 줄 정의, 언급 수, 마지막 업데이트 날짜를 담습니다. 페이지는 태그 칩(Algorithm, Architecture, Training paradigm, …)을 통한 유형 필터를 지원하며 기본적으로 언급 수 순으로 정렬됩니다.

“이 말뭉치는 무엇에 대해 이야기하는가?”라고 물을 때 가는 곳입니다.

**관련 라우트.** [Papers](#papers) — 개념은 보통 논문에서 소개됩니다. [Topics](#topics) — 개념은 주제로 군집화됩니다.

### `/concepts/<slug>.html` ✅

> _스크린샷: concept-detail.png_

종합된 정의(종합이 없으면 가장 권위 있는 소스의 첫 문단), 말뭉치 전체의 모든 언급 목록, 순위화된 관련 이웃, 활동 스파크라인이 있는 풍부한 개념 페이지입니다.

“말뭉치 안의 언급” 섹션은 에이전트에게 핵심입니다. 해당 개념을 참조하는 모든 논문 / 소스 / 저장소를 문맥을 위한 한 줄 발췌와 함께 나열합니다.

**주목할 상호작용.** 오른쪽 레일 TOC는 본문의 `<h2>` / `<h3>`를 추적합니다. 관련 카드 그리드는 네 가지 신호 점수를 따르므로 이웃 항목이 단순히 인접한 것이 아니라 실제로 관련 있게 느껴집니다.

## 엔티티

엔티티는 말뭉치 안의 이름 붙은 식별 가능한 대상입니다: `Model`, `Dataset`, `Benchmark`, `Metric`, `Organization`, `Person`. 그래프 노드에서 투영되며, 페이지는 일반 설명보다 주장과 결과를 강조합니다.

### `/entities/` ✅

> _스크린샷: entities-index.png_

유형 패싯이 있는 표입니다. 열은 유형 배지, 이름, 요약(frontmatter `description`이 있으면 첫 문장, 없으면 본문의 첫 문단), 언급 수입니다. 유형 칩으로 필터링할 수 있습니다.

### `/entities/<slug>.html` ✅

> _스크린샷: entity-detail.png_

상세 페이지는 세 섹션을 전면에 둡니다.

- **주장** — 이 엔티티와 닿아 있는 `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim` 엣지와 인라인 근거 발췌. (주장 노드 자체에는 별도 URL이 없으며 여기에서 bullet로 노출됩니다.)
- **보고된 결과** — 이 엔티티에 연결된 모든 `Result` / `evaluated_on` / `reports_result`를 지표 + 점수 + 논문 출처와 함께 나열합니다.
- **말뭉치 안의 언급** — 개념 페이지와 같은 형태입니다.

에이전트가 “모델 X에 대해 무엇을 알고 있는가?” 또는 “벤치마크 Y는 어떤 데이터셋에서 보고되었는가?”에 답해야 할 때 착지하기 좋은 페이지입니다.

## 논문

논문은 1급 근거로 취급되는 연구 문헌입니다. 재설계에서는 논문을 일반 소스 풀에서 분리하고, 논문 전용 기능을 렌더링할 수 있도록 별도 종류를 부여했습니다.

### `/papers/` ✅

> _스크린샷: papers-index.png_

연도, 주제, 계열 칩이 있는 패싯 필터 가능 카드 그리드입니다. 각 카드는 제목, 저자(처음 세 명 + “et al.”), 한 줄 초록 발췌, 연도 배지, 언급 수를 담습니다. 기본 정렬은 연도 내림차순입니다.

### `/papers/<slug>.html` ✅

> _스크린샷: paper-detail.png_

논문 카드 레이아웃입니다. 제목, 저자, 연도, 초록 발췌 다음에 다음 섹션이 이어집니다.

- **기여** — 논문에서 나온 `ContributionClaim` 엣지.
- **결과** — 지표 + 점수가 있는 `reports_result` 엣지.
- **비교** — `compares_against` 엣지.
- **관련 개념** — `introduces` / `extends` / `uses` 엣지.
- **열린 질문** — 논문을 통해 연결된 `OpenQuestion`.

arXiv 링크는 `tesserae/site/markdown.py`를 통해 자동 링크됩니다. 오른쪽 레일 TOC는 위 섹션 목록을 반영합니다.

## 저장소

저장소는 소프트웨어 프로젝트입니다 — `Repository`, `Project`, `CodeProject`. 재설계는 클래스별 / 함수별 HTML 페이지를 의도적으로 렌더링하지 않습니다. 저장소 페이지는 프로젝트 표면을 요약하고 소스 트리로 링크합니다.

### `/repos/` ✅

> _스크린샷: repos-index.png_

언어 배지가 있는 카드 그리드입니다. 각 카드는 이름, 한 줄 README 발췌, 주요 언어, 알려진 경우 스타 수, 마지막 업데이트를 담습니다.

### `/repos/<slug>.html` ✅

> _스크린샷: repo-detail.png_

저장소 페이지는 다음을 보여줍니다.

- **README 발췌** — 말뭉치 안에 `README.md`가 있으면 저장소 `README.md`의 처음 약 600자.
- **의존성** — 다른 저장소 / 모델 / 개념으로 향하는 `depends_on` / `imports` / `uses` 유형의 out-edge.
- **구현** — 논문에서 온 `implemented_in` 엣지(즉 “이 저장소는 논문 X를 구현한다”).
- **모듈 개요** — 모듈 / 클래스 / 함수 수. 단, 설계상 함수별 링크는 없습니다.
- **관련 항목** — 네 가지 신호로 순위화.

접근법 계열 안에서 어떤 저장소를 선택할지 에이전트가 판단해야 할 때 적합한 페이지입니다.

## 주제

주제는 개념을 더 넓은 분야로 묶습니다: `ResearchField`, `ResearchTopic`, `ProblemArea`, `ApproachFamily`, `Trend`. 주제 페이지는 일부는 그래프 노드에서 투영되고 일부는 종합됩니다. 주제 도입부를 구동하는 분야 개요 페이지는 [Syntheses](#syntheses)를 참고하세요.

### `/topics/` ✅

> _스크린샷: topics-index.png_

유형 칩을 키로 하는 카드 그리드입니다. 각 카드는 주제 이름, 상위 분야, “X papers · Y concepts · Z repos” 통계를 노출합니다.

### `/topics/<slug>.html` ✅

> _스크린샷: topic-detail.png_

주제 페이지는 `wiki/syntheses/topic-<slug>.md`에 존재할 때 종합 문단으로 시작하며 다음을 나열합니다.

- **이 주제의 논문** — 표.
- **접근법 계열** — `ApproachFamily` 유형의 하위 주제.
- **범위 안의 개념** — 칩 클라우드.
- **열린 질문** — 연결된 `OpenQuestion` 노드.
- **관련 분야** — `belongs_to` / `rising_in` 이웃.

**관련 라우트.** 긴 서사는 [Syntheses → topic-…](#syntheses), 구성 원자는 [Concepts](#concepts)를 참고하세요.

## 종합

종합은 `SynthesisProjector`가 생성하는 고차 페이지입니다. 일곱 종류(pulse, daily_digest, weekly, topic, comparison, field_overview)가 말뭉치의 시간적·구조적 절단면을 다룹니다. 현재 종합 본문은 결정적 템플릿이며, `TESSERAE_SYNTHESIS_LLM=1`은 LLM 업그레이드 훅(스텁)입니다.

### `/syntheses/` ✅

> _스크린샷: syntheses-index.png_

인덱스는 모든 종합을 종류별로 묶어 나열하며, 각 그룹 안에서는 `generated_at` 내림차순으로 정렬합니다. 각 행은 종류 배지, 제목, 한 줄 리드, 생성 시각 타임스탬프를 담습니다.

### `/syntheses/<slug>.html` ✅

> _스크린샷: synthesis-detail.png_

종합 페이지는 마크다운 본문을 그대로 렌더링합니다. frontmatter에는 `synthesis_kind`, `slug`, `sources`, `inputs`, `generated_at`, `generator`, `content_hash`가 들어 있으며, 페이지는 eyebrow에 `synthesis_kind`와 `generated_at`을 노출합니다. 본문 아래에는 다음이 있습니다.

- **사용된 소스** — `summarizes` 엣지의 대상. 종합이 참고한 소스마다 하나씩.
- **입력(그래프 노드)** — `synthesizes` 엣지의 대상. 입력으로 들어간 모든 노드.
- **관련 종합** — 같은 종류 / 겹치는 입력.

`pulse` 종합은 홈 페이지입니다. 일간 / 주간 종합은 [Timeline](#timeline) 레일의 기준점이 됩니다.

## 질문

열린 질문은 말뭉치에서 `OpenQuestion` 노드로 추출됩니다. 논문, 소스, 종합이 해결되지 않은 문제를 명시적으로 표시한 곳입니다.

### `/questions/` ✅

> _스크린샷: questions-index.png_

열린 질문 하나당 한 행인 목록 보기입니다. 열은 질문 텍스트, 이를 제기한 소스, 관련 개념, 나이(처음 관찰된 이후)입니다. 기본적으로 최신순 정렬입니다.

### `/questions/<slug>.html` ✅

> _스크린샷: question-detail.png_

단일 열린 질문에 집중한 페이지이며 다음을 포함합니다.

- 질문 원문.
- **제기된 곳** — 질문이 나타나는 소스 / 논문 / 종합.
- **관련 개념** — 질문의 주제.
- **인접 질문** — 같은 소스 또는 공유 개념.

에이전트가 “이 영역에서 아직 답이 없는 것은 무엇인가?”라는 질문을 받았을 때 착지하기 좋은 페이지입니다.

## 세션

세션은 로컬 AI 하네스 transcript를 가져와 `.tesserae/harness_sessions/`로 정규화한 뒤 검색 가능한 프로젝트 메모리로 렌더링한 것입니다. 가져오기는 `tesserae sessions discover --import` 또는 `tesserae sessions import ...`로 명시적으로 수행합니다. 일반 사이트 빌드는 이미 정규화된 레코드만 소비합니다.

### `/sessions/` ✅

> _스크린샷: sessions-index.png_

세션 인덱스는 프로젝트의 최상위 Claude Code 및 Codex 세션을 그룹화합니다. 카드/표에는 제목, 하네스, 모델, 프로젝트 경로, 시작/종료 타임스탬프, 메시지 수, 도구 수, 알려진 경우 토큰 수, 변경된 파일, 명령, 미리보기 텍스트가 표시됩니다. 이 페이지는 전역 레일, 홈 Browse 카드, `session` 종류의 검색 팔레트 항목에서 연결됩니다.

### `/sessions/<project>/<session>.html` ✅

> _스크린샷: session-detail.png_

세션 상세 페이지는 원시 transcript 덤프 대신 공유 셸을 사용합니다. 레이아웃에는 히어로, 통계 스트립, High-Level Summary, Timeline & size, decisions/files/commands/tools/errors, 접힌 subagent 트리, 턴별 대화 블록이 포함됩니다.

세션 전용 왼쪽 레일은 일반 파일 트리 레일을 사용자/어시스턴트 턴 앵커(`#turn-N`)로 대체합니다. 사용자와 어시스턴트 턴은 사이트 마크다운 렌더러를 통해 렌더링됩니다. 인라인 코드, 명령/태그 마크업, 경로, 파일명, 해시태그 같은 의미 표면은 조밀한 칩이 됩니다. 도구 호출은 앞선 어시스턴트 턴 아래에 접히며, 라이트/다크 테마 모두에서 어두운 코드/도구 배경을 사용합니다.

현재 상세 타이포그래피는 일반 대화 문장을 8 px, 일반 대화 코드 펜스를 10 px, bash/shell 펜스 코드 내용을 11 px, 도구 details/summary를 10 px, 도구 헤더를 8 px, 도구 payload 텍스트를 6 px로 유지합니다. 선택자 맵과 게시 개인정보 체크리스트는 [`session-history.md`](session-history.ko.md)를 참고하세요.

## 타임라인

타임라인 페이지는 활동 로그입니다. 말뭉치가 언제 커졌는지, 어떤 종류의 노드가 추가되었는지, 일/주 단위로 어떻게 보이는지를 보여줍니다.

### `/timeline/` ✅

> _스크린샷: timeline.png_

페이지에는 세 레일이 있습니다.

- **활동 히트맵** — 월 + 요일 라벨이 있는 26주 SVG이며, 셀은 노드 추가 수에 따라 색칠됩니다. 해당 날짜의 `digest.md` 소스 페이지가 있으면 각 셀이 그곳으로 연결됩니다.
- **일별 목록** — 최근 60개의 날짜별 행이며 `<date> · X activity · Y papers`를 표시합니다. 해당 날짜에 `digest.md`가 있으면 날짜가 링크입니다.
- **종합 레일** — 모든 종합을 최신순으로 정렬하고 종류 배지 + 제목 + 타임스탬프를 표시합니다.

“최근 무슨 일이 있었나” 질문을 위해 북마크할 페이지입니다.

### `/timeline/<YYYY-MM-DD>.html` ✅

> _스크린샷: timeline-day.png_

일별 상세 페이지 — 해당 달력 날짜에 연결된 모든 논문 / 저장소 / 개념 / 종합을 나열 — 가 이제 출시되었습니다. `render_timeline_day`(`tesserae/site/pages.py`)가 `StaticSiteBuilder`(`tesserae/site/__init__.py`)를 통해 날짜별로 emit되며, 각 페이지는 `manifest.json`에 `timeline_day` 라우트로 기록됩니다. 히트맵 셀은 해당 일별 페이지로 직접 연결됩니다(일별 라우트가 없는 경우에만 해당 날짜의 `digest.md` 소스 페이지로 폴백합니다).

## 그래프 보기

### `/graph/` ✅

> _스크린샷: graph.png_

대화형 그래프 보기는 3D force layout(3d-force-graph + Three.js, `assets/`에 CDN 스냅샷으로 벤더링됨)이며 2D fallback이 있습니다. 노드는 `ResearchNodeType`에 따라 색이 지정됩니다. 엣지는 호버 시 유형을 라벨로 표시합니다.

**주목할 상호작용.**

- 노드 호버 → 이름, 유형, 언급 수가 있는 tooltip.
- 노드 클릭 → 해당 위키 페이지로 이동.
- 엣지 호버 → 관계(`uses` / `extends` / `compares_against` / …)를 보여주는 라벨.
- 마우스 휠 → 커서 기준 확대/축소(중앙이 아니라 커서 방향으로 확대).
- 드래그 → orbit(3D) 또는 pan(2D).
- 오른쪽 위에서 2D/3D 토글.

페이지에 임베드된 payload는 `MAX_GRAPH_NODES = 1500`으로 제한됩니다([`pages.py`](../../tesserae/site/pages.py) 참고). 전체 그래프는 도구 사용을 위해 항상 `/graph.json`에서 사용할 수 있습니다. 코드 그래프 노드(`CodeClass`, `CodeFunction`, `Dependency`, …)는 설계상 시각화에서 제외됩니다.

**관련 라우트.** 모든 위키 페이지는 초점이 맞춰진 하위 그래프 보기로 연결됩니다.

## 소개

### `/about.html` ✅

> _스크린샷: about.png_

스키마(모든 `ResearchNodeType`과 엣지 허용 목록, 한 줄 설명 포함), 빌드 정보(commit SHA, generator version, generated-at timestamp), AI 내보내기 목록을 설명하는 정적 페이지입니다.

새 기여자가 어떤 유형이 존재하고 각각이 무엇을 위한 것인지 이해하기에 적합한 페이지입니다.

## AI 형제 — 모든 페이지가 데이터이기도 한 방식

Tesserae는 모든 페이지를 세 가지 형태로 제공합니다. 사람이 읽는 HTML, 일반 텍스트 형제, 구조화 JSON 형제입니다. 여기에 crawler와 agent를 위한 사이트 전체 내보내기도 더해집니다.

### 페이지별 `<page>.txt` ✅

한 페이지의 일반 텍스트 보기입니다. 내비게이션, 스타일, 본문이 명시적으로 사용하는 것 외의 마크다운 장식이 없습니다. 에이전트가 HTML scraper를 작성하지 않고 단일 페이지를 문맥으로 수집하려 할 때 적합합니다.

```bash
curl http://127.0.0.1:8765/concepts/diffusion-model.txt
```

### 페이지별 `<page>.json` ✅

구조화 레코드입니다.

```json
{
  "title": "...",
  "kind": "concepts",
  "body": "raw markdown body",
  "body_text": "plain-text body",
  "links": ["..."],
  "source_path": "data/...",
  "frontmatter": { ... }
}
```

도구가 마크다운 파서 없이 링크 목록, frontmatter, kind 태그처럼 타입이 있는 접근이 필요할 때 적합합니다.

### `/llms.txt` ✅

llmstxt.org 짧은 인덱스입니다. 각 종류별 가장 관련성 높은 항목과 함께 모든 종류를 나열하는 단일 페이지입니다. LLM 에이전트가 사이트를 발견할 때 보내는 첫 요청에 적합합니다.

### `/llms-full.txt` ✅

llmstxt.org 긴 형식입니다. 모든 위키 페이지를 이어 붙입니다. 5 MB로 제한되며, 한도에 닿으면 파일 끝에 `[TRUNCATED — see graph.jsonld for the full set]` 마커가 붙습니다. 에이전트가 전체 말뭉치를 한 요청과 5 MB 문맥으로 수집할 예산이 있을 때 적합합니다.

### `/graph.json` ✅

전체 `ResearchGraph` payload입니다. HTML 페이지가 없는 코드 그래프 노드도 포함합니다. 도구가 자체 분석을 위해 완전한 그래프를 원할 때 적합합니다(MCP, Cognee, Graphiti 소비자가 모두 이를 읽습니다).

### `/graph.jsonld` ✅

schema.org `Dataset` JSON-LD입니다. 위키 계층 노드만 포함합니다(코드 노드 제외). 구조화 데이터를 이해하는 crawler에 적합합니다 — Google Knowledge Graph, 검색 인덱서, schema.org 인식 aggregator 등.

### `/search-index.json` ✅

팔레트 + 페이지 검색 인덱스입니다. 위키 계층 종류만 포함합니다. 제3자 검색 UI를 통합할 때 적합합니다. 스키마는 `{kind, title, slug, body, score_hints}` 항목의 목록입니다.

### `/sitemap.xml` ✅

방출된 모든 라우트와 frontmatter(`generated_at`, `updated_at`, `published_at`, `date`)에서 파생된 `lastmod`를 포함합니다. 검색 엔진에 적합합니다.

### `/rss.xml` ✅

최신순으로 정렬된 최근 30개 종합입니다. “이 위키 구독”에 적합합니다 — RSS readers, IFTTT, mailing lists.

### `/robots.txt` ✅

허용적입니다 — 모든 것을 crawl + index합니다. 이 위키는 에이전트가 읽도록 설계되었습니다.

### `/ai-readme.md` ✅

HTML을 잘 다루지 못하는 AI 에이전트를 위한 기계 판독 가능한 사이트 맵입니다. 위의 모든 artifact와 그 목적, 각 형식이 언제 적합한지에 대한 한 줄 설명을 나열합니다.

### `/manifest.json` ✅

사이트에서 방출된 모든 파일의 sha256 + 크기 레코드입니다. 다음에 사용됩니다.

- compile-twice idempotence test(`tests/test_project_e2e_redesign.py`).
- 전체 diff 없이 “지난 방문 이후 사이트가 바뀌었는가?”를 감지하려는 downstream tooling.
- 아무것도 바뀌지 않았을 때 push를 단락시키는 deploy command.

## 올바른 형식 선택하기

| 당신이… | 읽을 것 |
|---|---|
| 처음 방문한 사람 | `/` 후 [Concepts](#concepts) 또는 [Papers](#papers)로 들어가기 |
| “새로운 것”을 보고 싶은 사람 | [Timeline](#timeline) 및 최근 [Syntheses](#syntheses) |
| 구조를 보고 싶은 사람 | [Graph view](#graph-view) |
| 한 번의 질의를 수행하는 LLM 에이전트 | 타입 접근을 위한 `<page>.json` |
| 한 번의 질의를 수행하지만 예산이 제한된 LLM 에이전트 | `<page>.txt` |
| 모든 것을 수집하는 LLM 에이전트 | `/llms-full.txt` (≤ 5 MB) 또는 `/graph.jsonld` (무제한) |
| 커스텀 UI를 만드는 도구 | `/search-index.json` + `/graph.json` |
| 검색 엔진 | `/sitemap.xml` + `/graph.jsonld` |
| 구독자 | `/rss.xml` |
| 변경 감지기 | `/manifest.json` |

## 관련 문서

- [Architecture](architecture.ko.md) — 세 계층 모델, 모듈 맵, 멱등성 이야기.
- [Feature map](feature-map.ko.md) — 상태, 소스 파일, 여기로의 링크가 포함된 모든 기능.
- [Quickstart](quickstart.ko.md) — `project init`에서 탐색 가능한 사이트까지의 최소 경로.
- [Self-dogfood demo](self-dogfood.ko.md) — 이 사이트를 포함해 Tesserae를 자체 저장소에 대해 실행하기.
