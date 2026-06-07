# Self-dogfood 데모

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
이 프로젝트는 자기 자신을 인덱싱할 수 있습니다. self-dogfood 흐름은 Tesserae를 설치하고, 자체 저장소 안에서 설정하고, 자체 docs/source/tests/scripts를 수집하며, 선택적으로 Understand Anything과 Cognee를 새로고침하고, 그래프 아티팩트를 컴파일하고, 정적 웹 프론트엔드를 빌드할 수 있음을 증명합니다.

또한 자기 개선 루프도 실행합니다. 매 컴파일은 변경 가능한 메모리 상태 — `decay_score`, `access_count`, `confidence`, `superseded` 플래그 — 를 `.tesserae/sqlite.db` 내부의 **`node_memory` 사이드카** 테이블로 다시 도출합니다. 이 스칼라들은 사이드카에**만** 존재하며 `graph.json`에는 절대 들어가지 않으므로, 새로 컴파일해도 그래프는 byte-identical하면서 사이드카는 decay와 recurrence를 추적합니다. `>= 3`개의 서로 다른 세션에 걸쳐 재발하는 insight는 `(0, 1]` 범위의 숫자 confidence로 강화되며(3 세션 → `0.5`, 4 → `0.75`, 5+ → `1.0`, 상한), 사이드카에 기록되고 MCP `fresh_insights` 도구가 노출합니다. 이 도구는 기본적으로 더 새로운 near-duplicate로 대체된(superseded) finding을 숨깁니다.

## 명령

저장소 루트에서:

```bash
# shell 명령이 설치되어 있는지 확인합니다.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (선택) 기본 시맨틱 임베딩 백엔드를 설치합니다.
pip install 'tesserae[semantic]'

# 이 저장소를 Tesserae 프로젝트로 설정합니다.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee

# 구성된 소스를 컴파일합니다.
tesserae compile

# 정적 프론트엔드를 명시적으로 다시 빌드합니다.
tesserae export site

# 로컬에서 제공합니다(필요하면 사이트를 먼저 자동 빌드합니다).
tesserae serve --port 8765
```

열기:

```text
http://127.0.0.1:8765/
```

## 생성된 워크스페이스

self-demo는 생성된 아티팩트를 다음 위치에 씁니다:

```text
.tesserae/
```

주요 아티팩트:

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # 타입 그래프 + node_memory 사이드카 + 라이브 HarnessSessionsDB
.tesserae/report.md
.tesserae/competitive_report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
.tesserae/cognee_bundle/
```

생성된 워크스페이스는 기본적으로 커밋하지 않도록 의도되어 있습니다. 위 명령으로 저장소 소스에서 재현할 수 있습니다.

## 최근 검증된 실행

Tesserae 저장소 자체에서 `2026-04-27 11:11:23 KST`에 검증되었습니다.

통합 옵트인(Understand Anything, cognee)은 이제 CLI 플래그가 아니라 **대화형
마법사 프롬프트**입니다. 아래 비대화형 동등 절차는 `tesserae init --yes`(통합
OFF)를 실행하고, `.tesserae/config.json`에서 통합을 활성화한 뒤(마법사는 이를
`memory_backends`와 `external_tools` 키 아래에 씁니다 — 정확한 키는 통합 문서
참조) 컴파일 전에 각각을 새로고침합니다.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # 그런 다음 .tesserae/config.json에서 Understand Anything + cognee를 활성화하고 실행:
                 #   tesserae integrations refresh understand-anything
                 #   tesserae integrations refresh cognee
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

최종 아티팩트 수:

```text
nodes:               667
edges:               1020
markdown notes:      684
obsidian notes:      686
agent harness files: 14
cognee nodes:        667
cognee edges:        1020
graphiti episodes:  1020
temporal facts:      1020
site files:          index.html, nodes/index.html, sources/index.html, graph/index.html, graph.json, search-index.json, llms.txt, llms-full.txt, manifest.json, assets/style.css, assets/app.js
node pages:          687
source pages:        56
```

상위 노드 유형:

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

브라우저 검증:

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## 이것이 보여주는 것

- 공개 설치 경로가 동작합니다.
- `tesserae` shell 명령이 동작합니다.
- 저장소가 프로젝트 로컬 `.tesserae` 워크스페이스를 연결할 수 있습니다.
- 연구/문서 markdown과 개발 코드 그래프 노드가 공존할 수 있습니다.
- Markdown, Obsidian, frontend, Graphiti, Cognee, SQLite, report, agent-harness 투영이 하나의 그래프 파이프라인에서 생성됩니다.
- 정적 HTML 프론트엔드는 JavaScript 빌드 단계 없이 프로젝트 그래프를 탐색할 수 있습니다.
- 자기 개선 루프가 실행되고 영속화됩니다. decay, access count, recurrence confidence, supersede 플래그가 `graph.json`을 건드리지 않고 `node_memory` 사이드카에 기록됩니다.
- `tesserae[semantic]`이 설치되면 하이브리드 검색이 실제 시맨틱 백엔드를 사용합니다(기본 `auto` 순서: model2vec → sentence-transformers → hash-bucket 스텁). 설치하지 않으면 임베딩 검색은 비-시맨틱 hash-bucket 스텁으로 격하되고 요란한 경고를 냅니다.
