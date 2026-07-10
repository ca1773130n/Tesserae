# Self-dogfood 데모

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
이 프로젝트는 자기 자신을 인덱싱할 수 있습니다. self-dogfood 플로우는 Tesserae를 설치하고, 자체 저장소 안에서 설정하고, 자체 docs/소스/테스트/스크립트를 ingest하고, 선택적으로 Understand Anything과 Cognee를 refresh하고, 그래프 아티팩트를 compile하고, 정적 웹 프론트엔드를 빌드할 수 있음을 증명합니다.

같은 플로우는 멀티모달 스모크 테스트를 겸합니다. RAG-Anything이 설치되어 있고(`tesserae setup --install raganything`) `.tesserae/config.json`에서 활성화되어 있으면(`memory_backends.raganything.enabled: true`), dogfood compile은 RAG-Anything을 Tesserae 자체의 `docs/` markdown과 `docs/assets/` 및 프로젝트 수준 `assets/` 이미지로 향하게 합니다. 이는 별도의 픽스처 세트를 만들지 않고도 — 텍스트 우선 소스 로더가 건너뛰는 스크린샷과 다이어그램을 포함해 — 실제 프로젝트 소유의 비코드 코퍼스에 대해 멀티모달 파이프라인을 검증합니다.

또한 자기 개선(self-improvement) 루프도 실행합니다. 각 compile은 가변 메모리
상태 — `decay_score`, `access_count`, `confidence`, `superseded` 플래그 — 를
`.tesserae/sqlite.db` 내부의 **`node_memory` 사이드카** 테이블로 다시
도출합니다. 이 스칼라들은 사이드카에*만* 존재하며 `graph.json`에는 절대
없으므로, 새 dogfood compile은 그래프에서는 바이트 단위로 동일하고 사이드카는
감쇠(decay)와 재발(recurrence)을 추적합니다. `>= 3`개의 서로 다른 세션에
걸쳐 재발하는 인사이트는 `(0, 1]` 범위의 수치 신뢰도로 강화되어(3 세션 →
`0.5`, 4 → `0.75`, 5+ → `1.0`, 상한 적용) 사이드카에 기록되고 MCP
`fresh_insights` 도구로 노출됩니다. 이 도구는 기본적으로 더 새로운
근사-중복(near-duplicate)에 의해 대체된(superseded) 발견 사항을 숨깁니다.

## 명령

저장소 루트에서:

```bash
# Ensure the shell command is installed.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (optional) install the default semantic embedding backend.
pip install 'tesserae[semantic]'

# Set up this repository as an Tesserae project.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts

# (optional) install + enable the heavier companions afterwards:
#   tesserae setup --install raganything --install understand-anything --install cognee
#   then flip memory_backends.*.enabled / external_tools in .tesserae/config.json

# Compile the configured sources.
tesserae compile

# Rebuild the static frontend explicitly.
tesserae export site

# Serve locally (auto-builds the site first if needed).
tesserae serve --port 8765
```

열기:

```text
http://127.0.0.1:8765/
```

## 생성되는 워크스페이스

self-demo는 생성된 아티팩트를 다음 아래에 기록합니다:

```text
.tesserae/
```

주요 아티팩트:

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # typed graph + node_memory sidecar + live HarnessSessionsDB
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

생성된 워크스페이스는 의도적으로 기본 커밋 대상이 아닙니다. 위의 명령으로 저장소 소스에서 재현할 수 있습니다.

## 최근 검증된 실행

Tesserae 저장소 자체에서 `2026-04-27 11:11:23 KST`에 검증됨.

통합 옵트인(Understand Anything, cognee)은 이제 CLI 플래그가 아니라 **대화형
마법사 프롬프트**입니다. 아래의 비대화형 등가물은 `tesserae init --yes`(통합
OFF)를 실행하고, `.tesserae/config.json`에서 통합을 활성화한 뒤(마법사는
`memory_backends`와 `external_tools` 키 아래에 기록합니다 — 정확한 키는 통합
문서 참조), compile 전에 각각을 refresh합니다.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # then enable Understand Anything + cognee in .tesserae/config.json and run:
                 #   tesserae integrations refresh understand-anything
                 #   tesserae integrations refresh cognee
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

최종 아티팩트 개수:

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

상위 노드 타입:

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

## 이것이 증명하는 것

- 공개 설치 경로가 동작합니다.
- `tesserae` 셸 명령이 동작합니다.
- 저장소가 프로젝트 로컬 `.tesserae` 워크스페이스를 부착할 수 있습니다.
- 연구/문서 markdown과 개발 코드 그래프 노드가 공존할 수 있습니다.
- Markdown, Obsidian, 프론트엔드, Graphiti, Cognee, SQLite, 보고서, agent-harness 프로젝션이 하나의 그래프 파이프라인에서 생산됩니다.
- 정적 HTML 프론트엔드가 JavaScript 빌드 단계 없이 프로젝트 그래프를 탐색할 수 있습니다.
- 자기 개선 루프가 실행되고 영속화됩니다: 감쇠, 접근 횟수, 재발 신뢰도, supersede 플래그가 `graph.json`을 건드리지 않고 `node_memory` 사이드카에 기록됩니다.
- `tesserae[semantic]`이 설치되어 있으면 hybrid retrieval이 실제 시맨틱 백엔드를 해석합니다(기본 `auto` 순서: model2vec → sentence-transformers → hash-bucket 스텁); 없으면 embedding retrieval은 비시맨틱 hash-bucket 스텁으로 강등되고 큰 경고를 출력합니다.
