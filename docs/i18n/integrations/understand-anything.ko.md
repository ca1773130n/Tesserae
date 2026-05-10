# Understand Anything 동반 워크플로

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything)와 LLM-Wiki는 서로를 보완하는 프로젝트입니다.

- Understand Anything은 코드베이스 지식 그래프와 인터랙티브 대시보드를 생성하는 데 뛰어납니다.
- LLM-Wiki는 오래 지속되는 에이전트 메모리에 초점을 둡니다: 문서, markdown/wiki 컴파일, 정적 게시, 세션 기록, 에이전트용 내보내기.

LLM-Wiki는 Understand Anything을 벤더링하거나 흡수해서는 안 됩니다. 유용한 그래프 아티팩트를 생성할 수 있는 독립적인 동반 도구로 다루세요.

## 왜 둘 다 사용하나요?

Understand Anything은 다음을 쓸 수 있습니다:

```text
.understand-anything/knowledge-graph.json
```

이 그래프는 파일, 함수, 클래스, 모듈, 개념, 의존성, 레이어, 투어 같은 코드 구조를 포착합니다.

LLM-Wiki는 그런 다음 해당 아티팩트를 나머지 프로젝트 메모리와 함께 보존할 수 있습니다:

- 소스 문서와 markdown 페이지;
- 저장소 파일;
- 연구 노트;
- 로컬 Claude Code / Codex 세션 기록;
- 생성된 정적 wiki 페이지;
- 2D / 3D 그래프 웹사이트 보기;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json`, 페이지별 에이전트 sibling.

## 현재의 저마찰 워크플로

권장 경로는 설정 마법사입니다:

```bash
llm_wiki project setup
```

동반 도구 단계에서 Understand Anything을 선택하세요. LLM-Wiki는 요청 시 동반 skills를 설치/업데이트하고 관리형 새로고침 명령을 `.llm-wiki/config.json`에 기록합니다. 이후 `llm_wiki project compile` 호출은 UA 그래프가 없거나 오래된 경우 이 래퍼를 자동으로 실행합니다.

비대화형 자동화에는 다음을 사용하세요:

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
llm_wiki project compile
```

저장된 명령은 사용자가 직접 만들어야 하는 것이 아니라 LLM-Wiki가 소유합니다:

```bash
llm_wiki project refresh-understand-anything --platform codex
```

컴파일 중 LLM-Wiki는:

1. `.understand-anything/knowledge-graph.json`이 존재하며, 메타데이터가 있을 때 현재 git 커밋과 일치하는지 확인합니다;
2. 그래프가 없거나 오래되었거나 새로고침이 강제된 경우에만 구성된 에이전트 플랫폼(`codex`, `opencode`, 또는 `claude`)을 실행합니다;
3. 그래프가 작성되었는지 검증합니다;
4. `.llm-wiki/external/understand-anything.md`를 구체화합니다;
5. 일반 메모리 컴파일을 계속합니다.

컴파일 전에 구성된 모든 외부 새로고침 명령을 강제로 실행할 수 있습니다:

```bash
llm_wiki project compile --refresh-external-tools
```

Cognee도 필요하신가요? 같은 setup 명령에 런타임 메모리 플래그를 추가하세요:

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## 수동 등가 절차

관리형 설정 경로가 권장됩니다. 의도적으로 LLM-Wiki 밖에서 UA를 사용하려면 먼저 에이전트 환경 안에서 Understand Anything을 실행하세요:

```bash
/understand
```

그런 다음 `llm_wiki project setup --with-understand-anything`을 실행하여 LLM-Wiki가 markdown 투영 소스를 기록하게 하세요. 직접 JSON 파일은 수동 입력 소스 경로가 아니라 원시 동반 아티팩트로 유지됩니다.

```bash
llm_wiki project setup --with-understand-anything
llm_wiki project compile
llm_wiki project build-site
```

로컬 에이전트 세션 메모리도 원한다면:

```bash
llm_wiki project sessions discover --import
llm_wiki project build-site
```

## 네이티브 그래프 동기화

LLM-Wiki는 읽기 쉬운 markdown projection을 계속 유지하면서, 설정된 도구가 `sync_mode: native_graph`를 사용할 때 compile 중 UA 그래프도 네이티브로 가져옵니다.

네이티브 어댑터는 `.understand-anything/knowledge-graph.json`을 읽고, UA 노드/에지를 LLM-Wiki의 제어된 온톨로지로 매핑한 뒤 sync manifest를 씁니다:

```text
.llm-wiki/external/understand-anything-sync.json
```

현재 매핑:

| Understand Anything | LLM-Wiki 방향 |
|---|---|
| `project` | repository/project metadata |
| `nodes[type=file]` | `SourceFile` nodes |
| `nodes[type=function]` / `method` | `CodeFunction` nodes |
| `nodes[type=class]` / `component` | `CodeClass` nodes |
| `nodes[type=module]` / `package` | `CodeModule` nodes |
| `nodes[type=concept]` / `topic` | canonical `Concept` nodes |
| `nodes[type=feature]` / `capability` | `Capability` nodes |
| `edges[type=imports]` | `imports` edges |
| `edges[type=contains]` | `contains` edges |
| `edges[type=calls]` | `calls` edges |
| unknown edge types | `ua_edge_type` metadata가 있는 `shares_concept_with` |

Concept synchronization은 무작정 중복 생성하지 않고 canonicalize합니다. UA가 `Mermaid Rendering`을 내보내고 LLM-Wiki에 이미 `Mermaid rendering`이 있으면, compile은 하나의 concept node만 유지하고 `metadata.external_refs`에 UA provenance를 추가합니다.

LLM-Wiki는 memory compiler로 남고, UA는 독립적인 companion graph generator로 남습니다.

## 협업 원칙

LLM-Wiki를 Understand Anything의 대체물로 표현하지 마세요.

더 나은 표현:

- Understand Anything은 개발자가 지금 코드베이스를 이해하도록 돕습니다.
- LLM-Wiki는 에이전트가 시간이 지나도 프로젝트 지식을 기억하고, 검색하고, 인용하고, 업데이트하고, 게시하도록 돕습니다.
