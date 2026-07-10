# Understand Anything 컴패니언 워크플로

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a> · <a href="understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything)과 Tesserae는 상호 보완적인 프로젝트입니다.

- Understand Anything은 코드베이스 지식 그래프와 인터랙티브 대시보드를 만드는 데 뛰어납니다.
- Tesserae는 장수 에이전트 메모리에 집중합니다: 문서, markdown/위키 컴파일, 정적 게시, 세션 이력, 에이전트 대상 export.

Tesserae는 Understand Anything을 벤더링하거나 흡수해서는 안 됩니다. 유용한 그래프 아티팩트를 생산할 수 있는 독립적인 컴패니언으로 취급하세요.

## 왜 둘 다 쓰는가?

Understand Anything은 다음을 기록할 수 있습니다:

```text
.understand-anything/knowledge-graph.json
```

그 그래프는 파일, 함수, 클래스, 모듈, 개념, 의존성, 레이어, 투어 같은 코드 구조를 포착합니다.

그러면 Tesserae는 그 아티팩트를 프로젝트 메모리의 나머지와 함께 보존할 수 있습니다:

- 소스 문서와 markdown 페이지;
- 저장소 파일;
- 연구 노트;
- 로컬 Claude Code / Codex 세션 이력;
- 생성된 정적 위키 페이지;
- 2D / 3D 그래프 웹사이트 뷰;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json`, 그리고 페이지별 에이전트 시블링.

## 현재의 저마찰 워크플로

권장 경로는 설정 마법사입니다:

```bash
tesserae init
```

컴패니언 도구 단계에서 Understand Anything을 선택하세요(**기본은 off**입니다 — 그 refresh는 원격 설치 스크립트를 실행합니다). Tesserae는 관리형 refresh 명령을 `.tesserae/config.json`의 `external_tools` 아래에 기록합니다. compile 시 자동 refresh도 기본은 off입니다(`auto_refresh: false`); UA 그래프가 없거나 오래되었을 때 `tesserae compile`이 래퍼를 자동으로 실행하기를 원한다면 `true`로 설정하세요.

비대화형 자동화의 경우, `tesserae init --yes`(통합 OFF)를 실행하고 `.tesserae/config.json`에서 Understand Anything을 활성화한 뒤:

```bash
tesserae integrations refresh understand-anything --platform codex
tesserae compile
```

저장된 명령은 Tesserae가 소유하는 것이지, 사용자가 직접 만들어내야 하는 것이 아닙니다:

```bash
tesserae integrations refresh understand-anything --platform codex
```

compile 중에 Tesserae는:

1. `.understand-anything/knowledge-graph.json`이 존재하는지, 메타데이터가 있을 때 현재 git 커밋과 일치하는지 확인합니다;
2. 해당 `external_tools` 항목이 `auto_refresh: true`이고 그래프가 없거나 오래되었을 때, 또는 refresh가 강제되었을 때만 설정된 에이전트 플랫폼(`codex`, `opencode`, 또는 `claude`)을 실행합니다;
3. 그래프가 기록되었는지 검증합니다;
4. `.tesserae/external/understand-anything.md`를 구체화합니다;
5. 일반 메모리 compile을 계속합니다.

compile 전에 설정된 모든 외부 refresh 명령을 강제할 수 있습니다:

```bash
tesserae compile --refresh-integrations
```

Cognee도 필요한가요? Cognee 역시 옵트인입니다: `pip install tesserae[cognee]`로 설치하고 `.tesserae/config.json`에서 `memory_backends.cognee.enabled: true`를 설정하세요(`tesserae query --backend cognee`로 명시적으로 쿼리).

## 수동 등가물

관리형 설정 경로가 선호됩니다. 의도적으로 UA를 Tesserae 밖에서 쓰고 싶다면, 먼저 에이전트 환경 안에서 Understand Anything을 실행하세요:

```bash
/understand
```

그런 다음 설정 마법사를 실행하고 **프롬프트가 나오면 Understand Anything을
활성화**하여 Tesserae가 markdown 프로젝션 소스를 기록하게 하세요. 직접적인
JSON 파일은 손으로 입력한 소스 경로가 아니라 원시 컴패니언 아티팩트로
유지됩니다.

```bash
tesserae init
# enable Understand Anything when the wizard prompts
tesserae compile
tesserae export site
```

비대화형 자동화의 경우, `tesserae init --yes`(통합 OFF)를 실행하고
`.tesserae/config.json`에서 Understand Anything을 활성화한 뒤(마법사는 이
통합을 `external_tools` 키 아래에 기록합니다), compile 전에 `tesserae
integrations refresh understand-anything`을 실행하세요.

로컬 에이전트 세션 메모리도 원한다면:

```bash
tesserae sessions discover --import
tesserae export site
```

## 네이티브 그래프 동기화

Tesserae는 이제 가독성을 위해 markdown 프로젝션을 유지하면서, 설정된 도구가 `sync_mode: native_graph`를 사용할 때 compile 중에 UA 그래프도 네이티브로 가져옵니다.

네이티브 어댑터는 `.understand-anything/knowledge-graph.json`을 읽고, UA 노드/엣지를 Tesserae의 통제 온톨로지에 매핑하며, 동기화 매니페스트를 기록합니다:

```text
.tesserae/external/understand-anything-sync.json
```

현재 매핑:

| Understand Anything | Tesserae 방향 |
|---|---|
| `project` | 저장소/프로젝트 메타데이터 |
| `nodes[type=file]` | `SourceFile` 노드 |
| `nodes[type=function]` / `method` | `CodeFunction` 노드 |
| `nodes[type=class]` / `component` | `CodeClass` 노드 |
| `nodes[type=module]` / `package` | `CodeModule` 노드 |
| `nodes[type=concept]` / `topic` | 정규(canonical) `Concept` 노드 |
| `nodes[type=feature]` / `capability` | `Capability` 노드 |
| `edges[type=imports]` | `imports` 엣지 |
| `edges[type=contains]` | `contains` 엣지 |
| `edges[type=calls]` | `calls` 엣지 |
| 알 수 없는 엣지 타입 | `ua_edge_type` 메타데이터를 갖는 `shares_concept_with` |

개념 동기화는 맹목적으로 복제되는 대신 정규화됩니다. UA가 `Mermaid Rendering`을 방출했는데 Tesserae에 이미 `Mermaid rendering`이 있다면, compile은 개념 노드 하나를 유지하고 `metadata.external_refs` 아래에 UA 출처를 추가합니다.

Tesserae는 메모리 컴파일러로 남고; UA는 독립적인 컴패니언 그래프 생성기로 남습니다.

## 협업 원칙

Tesserae를 Understand Anything의 대체재로 프레이밍하지 마세요.

더 나은 프레이밍:

- Understand Anything은 개발자가 지금 코드베이스를 이해하도록 돕습니다.
- Tesserae는 에이전트가 시간에 걸쳐 프로젝트 지식을 기억하고, 검색하고, 인용하고, 갱신하고, 게시하도록 돕습니다.
