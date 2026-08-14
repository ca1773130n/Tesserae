# RAG-Anything 멀티모달 컴패니언

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything)은 MinerU/Docling/PaddleOCR을 통해 PDF, Office 문서, 이미지, 수식을 파싱하는 멀티모달 RAG 프레임워크(LightRAG 기반)입니다. Tesserae는 이를 멀티모달 수집 파이프라인(UA 스타일 네이티브 그래프 프로젝션)이자 선택적 런타임 메모리 백엔드로 통합합니다.

## 왜 둘 다 쓰는가?

- Tesserae — 장수 에이전트 메모리, 위키 컴파일, 그래프 프로젝션.
- RAG-Anything — 멀티모달 수집 + LightRAG 런타임 retrieval.

둘은 서로 보완합니다: RAG-Anything은 Tesserae의 텍스트 우선 소스 로더가 제공하지 않는 PDF/Office/이미지 이해를 가져오고; Tesserae는 세션을 넘어 살아남는, 쿼리 가능한 장수 메모리를 유지합니다.

## 현재의 저마찰 워크플로

권장 경로는 설정 마법사입니다:

```bash
tesserae init
```

RAG-Anything은 이제 CLI 플래그 집합이 아니라 **대화형 마법사 프롬프트**입니다.
마법사가 실행되면 통합 프롬프트에 답하세요:

- 프롬프트가 나오면 RAG-Anything을 활성화;
- 요청받으면 설치(`raganything` + `docling` 설치);
- 파서로 `mineru` 선택;
- 제안받으면 설치 후 refresh 실행을 활성화.

그런 다음 compile:

```bash
tesserae compile
```

비대화형 자동화(CI)의 경우, 마법사를 기본값으로 실행하고(모든 선택적 통합
OFF), `.tesserae/config.json`에서 RAG-Anything을 활성화한 뒤 — 마법사는 통합
설정을 `external_tools` / `memory_backends` 키 아래에 기록합니다(이 문서가
아래에서 참조하는 키 참조) — 관리형 refresh를 실행하세요:

```bash
tesserae init --yes
# enable raganything in .tesserae/config.json (external_tools key)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

설정 마법사는 `raganything`과 `docling`을 함께 설치합니다. MinerU는 옵트인으로 남습니다: ingest할 PDF나 이미지가 있을 때만 `pip install 'mineru[core]'`로 설치하세요.

Tesserae는 사용자에게 직접 만들라고 요구하는 대신 관리형 refresh 명령을 저장합니다:

```bash
tesserae integrations refresh raganything --parser mineru
```

compile 중에 Tesserae는:

1. `.tesserae/external/raganything/manifest.json`이 존재하는지, (저장된 `meta.json#gitCommitHash`를 통해) 현재 git 커밋과 일치하는지 확인합니다;
2. 없거나 오래되었거나 `--refresh-external-tools`가 전달되면 관리형 refresh 래퍼를 실행합니다;
3. 비코드 소스(PDF, Office 문서, 이미지, markdown)를 발견하고 설정된 파서로 파싱합니다;
4. `manifest.json` + `meta.json`을 기록합니다;
5. 일반 메모리 compile을 계속합니다.

compile 전에 설정된 모든 외부 refresh 명령을 강제할 수 있습니다:

```bash
tesserae compile --refresh-integrations
```

## 수동 등가물

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## 컴파일 타임 vs 런타임

Tesserae는 통합을 깔끔하게 분리합니다:

- **컴파일 타임 파싱** (`refresh-raganything`과 `compile`): 파서를 직접 실행합니다 — `.md/.txt/.rst`는 네이티브 읽기, 나머지는 모두 `docling.DocumentConverter`. 여기서는 RAG-Anything의 전체 파이프라인이 호출되지 *않으므로*, compile 성공에 LLM/embedding/vision 키가 필요 없습니다.
- **런타임 쿼리** (`project ask`): `raganything_query.py`가 프로젝트에 설정된 LLM/embedding/vision 함수로 `RAGAnything`을 인스턴스화하고 LightRAG의 저장소에 대해 `aquery`를 실행합니다. 이 경로는 API 키가 필요합니다.

이 분리 덕분에 `compile`은 빠르고 결정적이며 키가 필요 없습니다; retrieval 시점의 작업만 LLM 토큰을 소모합니다.

## 네이티브 그래프 동기화

설정된 도구가 `sync_mode: native_graph`를 사용하면 Tesserae는 compile 중에 파싱된 매니페스트를 네이티브로 가져옵니다.

네이티브 어댑터는 `.tesserae/external/raganything/manifest.json`을 읽고, 파싱된 각 문서를 멀티모달 블록 메타데이터를 갖는 `SourceDocument` 노드로 프로젝션하며 — 해석 가능한 콘텐츠를 갖는 각 figure/table/equation마다 일급 `Artifact` evidence 노드(content-hash id, `part_of` 문서, `evidenced_by`로 타게팅 가능) — 동기화 매니페스트를 기록합니다:

```text
.tesserae/external/raganything-sync.json
```

현재 매핑:

| RAG-Anything | Tesserae 방향 |
|---|---|
| `documents[*]` | `SourceDocument` 노드, `metadata.parser="raganything"`, `metadata.content_hash` = source sha256 |
| `content_list[type=text]` | `SourceDocument.description`으로 병합; 개념은 기존 추출기를 통해 |
| `content_list[type=image]` | `Artifact` 노드 (asset **bytes** sha256에서 생성된 id, caption을 description으로) + `SourceDocument.metadata.multimodal_blocks[]` (`img_path`, `caption`, `content_hash` join key); 해석할 수 없는 asset은 노드를 건너뜁니다 (sync manifest의 `skipped_blocks` 기록) |
| `content_list[type=table]` | `Artifact` 노드 (`table_body` sha256에서 생성된 id, body를 description으로) + `multimodal_blocks[]` (`table_body`, `caption`, `content_hash`) |
| `content_list[type=equation]` | `Artifact` 노드 (`latex` sha256에서 생성된 id, LaTeX을 description으로) + `multimodal_blocks[]`와 `metadata.equations[]` (LaTeX 보존) |

### 소유자별 사실이 `part_of` 엣지를 탑니다

`Artifact`의 id는 콘텐츠 해시만에서 시종되므로, 노드는 의도적으로 **문서 불가지론**입니다: 두 논문에 인쇄된 같은 그림은 소유자별 `part_of` 엣지를 가진 하나의 노드입니다. 하지만 `kind`, `page`, `caption`과 1 기반 per-kind `ordinal`은 *(artifact, document)* 쌍에 대한 사실입니다 — 노드에만 보존되며, 공유 아티팩트가 먼저 병합된 문서를 유지하고 이후 소유자의 페이지를 묵묵히 잃을 것입니다. 그들은 엣지를 탑니다. 엣지는 소유자별이므로 구성상 그렇습니다. 노드는 뒤로 호환성을 위해 자체 복사본을 유지합니다; 이것은 추가되고 이동되지 않습니다. 같은 바이트가 한 문서에 두 번 나타나면, 이전 위치가 결정론적으로 이깁니다.

그 엣지의 `evidence`는 의도적으로 null로 유지됩니다: 이 코드베이스의 모든 `edge.evidence`는 주장을 허가한 축자 구간이고, caption은 아무것도 주장하지 않습니다.

### 바이트에 도달하기

**그림** `Artifact`는 이미지의 바이트가 존재한다고 주장합니다 — 노드는 수입 시점에 해시되었기 때문에만 존재합니다 — 따라서 사이트가 그것들을 서빙합니다. `tesserae export site`는 `metadata['asset_path']`를 그 자신의 소스로 읽으며, 그 그림에 원시 페이지, sitemap 항목, 그리고 **콘텐츠 주소** 파일명 아래의 바이트를 `raw-assets/`에 제공하는데, 그것은 그래프가 이미 선언한 다이제스트에서 도출되며, 재해시는 절대 아닙니다. 바이트의 순수 함수인 이름이 `asset_site_path`를 아래에서 예측이 아니라 사실로 만듭니다.

표와 방정식은 `asset_path`를 운반하지 않습니다 — 그들의 콘텐츠 *는* 노드의 설명입니다 — 그리고 tree 외부 asset은 수입 시점에 키를 떨어뜨립니다. 둘 다 올바르게 서빙할 수 없으며 오류가 아닙니다.

MCP를 통해, `raw_source`는 절대 바이트를 반환하지 않습니다; `drill_down`이 대신 주소를 보고합니다 — `asset_path`(디스크), `asset_sha256`, `asset_site_path`(실행 중인 `tesserae serve`에서 가져올 수 있음). 잘못된 선언 해시는 주소를 지어내지 않고 `asset_site_path`를 떨어뜨립니다.

### 결과물은 그래프 캔버스를 떠납니다

`Artifact`는 `EvidenceSpan`과 assertion 계층의 모든 Claim 변형과 함께 묶이며, 전체 assertion 계층은 인터랙티브 그래프 뷰에서 제외됩니다 — 의도적으로 그리고 영구적으로, 미결정 아닙니다. 그것은 캔버스 위의 노드의 동료가 아니라 *그들의* 증거이며, 두 기계적 이유가 같은 것을 말합니다: 증거가 지지하는 것보다 더 많습니다(이미 `SourceDocument`를 `show_sources` 뒤에 놓은 폭주), 그리고 `Artifact`의 유일한 엣지는 `part_of`에서 기본적으로 숨겨지는 `SourceDocument`입니다 — 따라서 그것만 인정하면 도달할 수 없는 고아 점들을 그릴 것입니다. `drill_down`과 원시 asset 페이지를 통해 증거를 읽으세요. 그것이 바로 주소를 잡을 수 있는 곳입니다.

각 노드에 출처가 보존됩니다:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

참고: 인터랙티브 그래프 뷰는 개념과 엔티티에 집중하기 위해 기본적으로 `sources` 그룹 노드를 숨깁니다 — 프로젝션된 raganything SourceDocument는 `graph.json`에 남아 있고(MCP, 검색, 페이지별 위키 뷰에서는 여전히 보임), 단지 캔버스를 넘치게 하지 않을 뿐입니다. 밀집 뷰를 복원하려면 `.tesserae/config.json`에서 `graph_view.show_sources = true`를 설정하세요.

## 런타임 메모리 백엔드

`memory_backends.raganything`(`default_raganything_backend_config`가 생성하는 기본값)은 유일한 선택적 메모리 백엔드입니다. RAG-Anything은 옵트인입니다(기본 `enabled: false`); 설정 플래그 `--with-raganything`이 켭니다.

### LLM 프로바이더 (API 키 불필요)

RAG-Anything의 런타임 백엔드는 쿼리에 답하기 위한 LLM이 필요합니다. Tesserae는 기존의 OAuth 기반 CLI 통합을 기본값으로 합니다 — API 키가 필요 없습니다:

| 프로바이더 | 인증 방식 | 설정 플래그 |
|---|---|---|
| `codex` (기본) | `codex` CLI OAuth (`codex login`으로 한 번 로그인) | `--raganything-llm-provider codex` |
| `claude` | `claude -p` CLI; 멀티 계정 설정을 위해 `CLAUDE_CONFIG_DIR` 준수 | `--raganything-llm-provider claude --raganything-claude-config-dir ~/.claude-personal2` |

멀티 계정 Claude 설정(예: `~/.claude-personal1`, `~/.claude-personal2`)의 경우, 설정 시 `--raganything-claude-config-dir <path>`를 전달하세요. 런타임 백엔드는 각 호출 전에 `CLAUDE_CONFIG_DIR=<path>`를 export하여, 기본 `~/.claude`를 건드리지 않고 선택된 계정의 인증을 사용합니다.

### Embedding

| 프로바이더 | 언제 사용하는가 |
|---|---|
| `deterministic` (기본) | 외부 의존성 없음. 해시 기반; 시맨틱 품질은 낮지만 LightRAG가 인덱스를 구축하기에는 충분. 통합이 동작함을 증명하는 좋은 베이스라인. |
| `ollama` | embedding 모델(예: `nomic-embed-text`)로 실행 중인 로컬 Ollama. `--raganything-embedding ollama` 전달; 백엔드 기본값은 `http://localhost:11434`. |

직접적인 OpenAI embedding 지원은 v1에서 이 플래그들로 연결되어 있지 않습니다 — OpenAI 키가 있는 사용자는 `OPENAI_API_KEY`를 설정하고 `.tesserae/config.json`에서 `memory_backends.raganything.embedding.provider`를 직접 재정의할 수 있습니다(RAGAnything은 LightRAG의 기본값을 통해 env var를 인식합니다).

### CLI에서 호출

```bash
# `ask` never enters memory backends: it plans retrieval over the compiled
# graph and synthesizes a cited LLM answer (--no-llm = ranked hits only).
tesserae ask "What does the integration spec say about parser routing?"

# Explicit backends live on `query` — raw retrieval, no LLM synthesis.
tesserae query "..." --backend raganything
tesserae query "..." --backend wiki
```

`tesserae query --backend raganything`은 `tesserae.raganything_query.query`를 직접 호출합니다. `memory_backends.raganything`의 상대 `working_dir`은 호출 전에 프로젝트 루트를 기준으로 해석됩니다.

### 최상위 `ask` (멀티 프로젝트 레지스트리 사용)

각 프로젝트로 `cd`하지 않고 등록된 여러 Tesserae 프로젝트를 가로질러 질문하고 싶은 워크플로를 위해, 최상위 `tesserae ask` 명령은 MCP 서버와 공유하는 영속 레지스트리를 통해 프로젝트를 해석합니다:

```bash
# One-time: register your projects (saved to ~/.tesserae/registry.json).
tesserae projects register ~/Developer/Projects/Tesserae --name tesserae
tesserae projects register ~/Developer/Projects/Other --name other

# List registered projects.
tesserae projects list

# Ask from inside a project (the smart router picks the target otherwise).
tesserae ask "How does the parser routing work?"

# Ask a specific registered project by name.
tesserae ask "What is the architecture?" --name other

# Pass a direct path, or hit a memory backend explicitly via `query`.
tesserae ask "..." --project /tmp/somewhere
tesserae query "..." --backend raganything --json
```

디스패치 로직 — `--project > --name > router` — 은 최상위 ask 핸들러에 구현되어 있고, 답변 포매팅은 `tesserae.query.ask_project`를 통해 MCP `ask` 도구와 공유됩니다(메모리 백엔드는 `tesserae query --backend …`를 통해서만 도달 가능). 레지스트리는 파일 기반이므로(기본 `~/.tesserae/registry.json`) 세션을 넘어 지속되고 MCP 서버의 프로젝트 목록과 동기화 상태를 유지합니다.

#### 여러 vault를 가로질러 쿼리 (`--scope all-registered`)

Bet B2 — 등록된 프로젝트가 여러 개일 때(연구 vault, 업무 vault, 사이드 프로젝트 vault) 같은 질문을 전부에 던지고 싶다면 `--scope all-registered`를 사용하세요:

```bash
# Fan out across every registered project. The aggregated envelope is
# {"scope": "all-registered", "question": "...", "by_project": {"<alias>": <envelope>}}.
tesserae ask "What did I write about RLHF?" --scope all-registered --json

# Restrict to a hand-picked subset of aliases.
tesserae ask "..." --scope all-registered --scope-aliases research side-projects
```

핸들러는 등록된 프로젝트를 알파벳 순으로 순회하며 각각에 대해 `ask_project`를 호출하고 프로젝트별 envelope를 집계합니다. 단일 프로젝트의 실패 — 설정 누락, RAG-Anything 비활성화 — 는 해당 alias 슬롯에 `{"error": "..."}`로 포착되며 나머지 fan-out을 절대 중단시키지 않습니다. 같은 `scope` 인자를 MCP `ask` 도구도 받아들이므로, MCP 기반 코딩 에이전트도 추가 배관 없이 같은 fan-out을 얻습니다.

### 멀티 프로젝트 레지스트리 (`tesserae projects`)

| 명령 | 용도 |
| --- | --- |
| `tesserae projects list [--json]` | 등록된 프로젝트 표시(모두 동등 — "활성" 프로젝트는 없음). |
| `tesserae projects register <path> [--name <alias>]` | 레지스트리에 프로젝트 추가; alias 기본값은 정제된 디렉터리 이름. |
| `tesserae projects unregister <name>` | 레지스트리에서 항목 제거. |

이 명령들은 `tesserae.mcp_server.ProjectRegistry`에 직접 작동합니다 — MCP 왕복 없음 — 따라서 MCP 서버를 실행하지 않고도 스크립팅할 수 있습니다.

### MCP에서 호출

stdio MCP 서버는 같은 백엔드 셀렉터를 갖는 `ask` 도구를 노출합니다:

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "tesserae"
  }
}
```

디스패치 순서(`raganything` → 컴파일된 위키 검색)와 `working_dir` 해석은 CLI 핸들러를 정확히 반영하므로, 코딩 에이전트와 인간 운영자가 같은 답으로 수렴합니다.

## 시스템 사전 요구사항

- RAG-Anything에는 **Python 3.10+**가 필요합니다(업스트림 `raganything` 패키지 ≥1.3.0은 Python 3.10+인 `mineru[core]`에 전이 의존). 더 오래된 Python에서 Tesserae는 망가진 플레이스홀더를 조용히 설치하는 대신 명확한 경고와 함께 통합을 비활성화합니다.
- `.doc/.docx/.ppt/.pptx/.xls/.xlsx` 파싱을 위한 **LibreOffice** — 플랫폼의 패키지 매니저로 별도 설치. LibreOffice가 없으면 RAG-Anything은 경고와 함께 Office 문서를 건너뜁니다.
- **MinerU 모델 가중치**는 첫 파싱 시 다운로드되고 캐싱됩니다(~수 GB). 이후 실행은 캐시를 재사용합니다.
- 런타임 메모리 백엔드를 위한 **OpenAI 호환 LLM/embedding/vision 키**(`OPENAI_API_KEY`, `OPENAI_BASE_URL`). 파서 전용 모드는 키가 필요 없습니다.

## 파서 라우팅

Tesserae는 파일 확장자별로 소스를 알맞은 파서에 자동 라우팅합니다:

| 확장자 | 파서 | 이유 |
|---|---|---|
| `.md`, `.markdown`, `.txt`, `.rst` | `docling` | 가벼움; MinerU 모델 다운로드 없음. |
| `.doc`, `.docx`, `.ppt`, `.pptx`, `.xls`, `.xlsx` | `docling` | 업스트림 기준 더 나은 Office 구조 보존. |
| `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp` | 설정된 기본값 (`--raganything-parser`, 기본 `mineru`) | OCR + 표 추출. |

관리형 `tesserae integrations refresh raganything` 래퍼는 `--parser`(PDF/이미지에 대한 설정 기본값), `--parse-method {auto,ocr,txt}`, `--root`(반복 가능, 하위 트리로 제한), `--force`, `--full`을 노출합니다. 버킷별 text/office 라우팅은 고정입니다(둘 다 기본 `docling`). text 또는 office 파서를 명시적으로 재정의하려면 하위 모듈을 직접 호출하세요 — `python -m tesserae.raganything_refresh --text-parser <p> --office-parser <p>` — 이 모듈이 그 두 추가 플래그를 노출합니다. 설정된 기본값은 여전히 PDF와 이미지에 적용됩니다.

파싱 루프가 실행되기 전에, Tesserae는 필요한 각 파서의 Python 패키지가 import 가능한지 탐침하고(`importlib.import_module(...)`), 누락된 모든 파서와 그 설치 명령을 나열하는 단일 집계 오류와 함께 빠르게 중단합니다. 우리는 의도적으로 업스트림 `RAGAnything.check_parser_installation()`을 사용하지 않습니다 — 이것은 인스턴스에 설정된 파서만 검사하고 사전 점검 단계에 맞지 않는 모델 가중치 준비 상태 확인까지 포함하기 때문입니다.

Tesserae는 또한 `RAGAnything`의 생성 시점 파서를 `--raganything-parser`에서 직접 가져오는 대신 실제 라우팅 분포에서 선택합니다(가장 많이 선택된 파서가 승리). 이는 `RAGAnything.__init__`이 모델 가중치가 아직 디스크에 없는 무거운 파서(예: `mineru`)를 초기화하려다 호출별 `parser=` 재정의가 효력을 갖기 전에 전체 실행을 망가뜨리는 실패 모드를 피합니다. `--raganything-parser` 플래그는 여전히 비텍스트, 비Office 소스(PDF, 이미지)의 기본값을 제어합니다.

### 파서 패키지

컴파일 타임 파싱 경로는 모든 비텍스트 소스에 대해 `docling.DocumentConverter`를 직접 사용합니다; 한 번 설치하면 충분합니다:

| 파서 | 설치 명령 |
|---|---|
| `docling` (네이티브 텍스트를 제외한 모든 것의 컴파일 타임 기본값) | `--with-raganything --install-raganything` 실행 시 번들 (또는 독립적으로 `pip install docling`) |
| `paddleocr` (선택적 OCR 대안) | `pip install 'raganything[paddleocr]>=1.3.0'` 및 `pip install paddlepaddle` (플랫폼별 wheel) |

> 참고: `mineru`는 현재 **컴파일 타임에 호출되지 않습니다**. compile 경로는 RAG-Anything의 전체 파이프라인(LLM/embedding/vision callable이 필요)을 우회하고 모든 비텍스트 소스를 docling으로 직접 라우팅합니다. MinerU 지원은 외부에서 생성된 `content_list.json`을 수집하는 미래의 직접 가져오기 경로를 위해 예약되어 있습니다.

설정된 파서가 없으면 `refresh-raganything`은 파일별 실패를 연쇄시키는 대신 — 누락된 모든 파서를 올바른 설치 명령과 함께 단일 오류로 나열하며 — 빠르게 중단합니다.

### 페이지별 ask 위젯

모든 상세 페이지(concept, paper, repo, synthesis, entity, topic, question, source)에는 인라인 "ask about this page" 위젯이 포함됩니다. 이것은 로컬 `tesserae serve` 인스턴스의 `/api/ask`에 POST하고, 그것이 `tesserae.query.ask_project`를 호출해 답변을 인라인으로 렌더링합니다. CLI(`tesserae ask`는 기본 LLM)와 달리, `/api/ask`는 위젯 지연 시간을 위해 기본적으로 **비LLM retrieval**을 사용합니다; 계획/합성된 답변을 옵트인하려면 페이로드에 `{"llm": true}`를 보내세요. 위젯은 현재 페이지의 노드 이름을 자연어 컨텍스트 힌트로 사용자 질문 앞에 붙입니다(예: `` About `<NodeName>`: <question> ``); 미래의 PR이 실제 서브그래프 스코핑을 `ask_project` 자체에 연결할 수 있습니다.

위젯은 로드 시 `/api/ask/health`를 통해 백엔드 가용성을 감지합니다. 위키가 정적으로 서빙될 때(GitHub Pages, `file://`, S3, 어떤 일반 정적 호스트든) 위젯은 로컬 인터랙티브 사용을 위해 `tesserae serve`를 안내하는 한 줄 노트로 접힙니다. 어떤 요청도 실패하지 않고 페이지 렌더링을 막는 것도 없습니다 — 위젯은 더 무거운 그래프 번들과 분리된 지연(deferred) JS 아일랜드입니다.

이를 멀티 프로젝트 레지스트리(`tesserae projects register`)와 결합하면, 등록된 어느 프로젝트의 위키든 그 상세 페이지에서 질문할 수 있습니다.

## 협업 원칙

Tesserae는 메모리 컴파일러로 남습니다. RAG-Anything은 독립적인 컴패니언으로 남습니다: 멀티모달 파서 + LightRAG retrieval 엔진.
