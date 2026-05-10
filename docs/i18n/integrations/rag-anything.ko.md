# RAG-Anything 멀티모달 동반 도구

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything)은 PDF, Office 문서, 이미지, 수식을 MinerU/Docling/PaddleOCR을 통해 파싱하는 멀티모달 RAG 프레임워크(LightRAG 기반)입니다. LLM-Wiki는 이를 멀티모달 수집 파이프라인(UA 스타일 네이티브 그래프 투영)이자 Cognee와 함께 동작하는 런타임 메모리 백엔드로 통합합니다.

## 왜 둘 다 사용하나요?

- LLM-Wiki — 오래 지속되는 에이전트 메모리, 위키 컴파일, 그래프 투영.
- RAG-Anything — 멀티모달 수집 + LightRAG 런타임 검색.

둘은 서로를 보완합니다: RAG-Anything은 LLM-Wiki의 텍스트 우선 소스 로더가 제공하지 않는 PDF/Office/이미지 이해를 가져오고, LLM-Wiki는 세션을 가로질러 살아남는 오래 지속되며 쿼리 가능한 메모리를 유지합니다.

## 현재의 저마찰 워크플로

권장 경로는 설정 마법사입니다:

```bash
llm_wiki project setup
```

자동화에는 다음을 사용하세요:

```bash
llm_wiki project setup \
  --yes \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything
llm_wiki project compile
```

LLM-Wiki는 사용자가 직접 만들어내는 명령이 아니라 관리형 새로고침 명령을 저장합니다:

```bash
llm_wiki project refresh-raganything --parser mineru
```

컴파일 중 LLM-Wiki는:

1. `.llm-wiki/external/raganything/manifest.json`이 존재하며 현재 git 커밋과 일치하는지 확인합니다(저장된 `meta.json#gitCommitHash`를 통해);
2. 없거나 오래되었거나 `--refresh-external-tools`가 전달된 경우 관리형 새로고침 래퍼를 실행합니다;
3. 코드가 아닌 소스(PDF, Office 문서, 이미지, markdown)를 발견하고 구성된 파서로 파싱합니다;
4. `manifest.json` + `meta.json`을 씁니다;
5. 일반 메모리 컴파일을 계속합니다.

컴파일 전에 구성된 모든 외부 새로고침 명령을 강제로 실행할 수 있습니다:

```bash
llm_wiki project compile --refresh-external-tools
```

## 수동 등가 절차

```bash
pip install 'raganything[all]'
python -m llm_wiki.raganything_refresh --project . --parser mineru
llm_wiki project compile
```

## 네이티브 그래프 동기화

LLM-Wiki는 구성된 도구가 `sync_mode: native_graph`를 사용할 때 컴파일 중 파싱된 manifest를 네이티브로 가져옵니다.

네이티브 어댑터는 `.llm-wiki/external/raganything/manifest.json`을 읽고, 파싱된 각 문서를 멀티모달 블록 메타데이터를 가진 `SourceFile` node로 투영한 뒤 sync manifest를 씁니다:

```text
.llm-wiki/external/raganything-sync.json
```

현재 매핑:

| RAG-Anything | LLM-Wiki 방향 |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | `SourceFile.description`에 접힘; concepts는 기존 추출기를 통해 |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` 와 `metadata.equations[]` (LaTeX 보존) |

각 노드에 provenance가 보존됩니다:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".llm-wiki/external/raganything/manifest.json"}
```

## 런타임 메모리 백엔드

`memory_backends.raganything`(`default_raganything_backend_config`로 생성되는 기본값)은 Cognee와 공존합니다. `project ask`는 우선순위에 따라 백엔드를 시도하며, 프로젝트별 우선순위는 `memory_backends.priority`로 설정할 수 있습니다. RAG-Anything은 옵트인입니다(기본 `enabled: false`); 설정 플래그 `--with-raganything`이 이를 켭니다.

## 시스템 사전 요구사항

- **Python 3.10+** (RAG-Anything 요구사항; LLM-Wiki 자체는 3.9+를 대상으로 함).
- **`.doc/.docx/.ppt/.pptx/.xls/.xlsx` 파싱을 위한 LibreOffice** — 플랫폼의 패키지 관리자를 통해 별도로 설치하세요. LibreOffice가 없으면 RAG-Anything은 경고와 함께 Office 문서를 건너뜁니다.
- **MinerU 모델 가중치**는 첫 파싱 시 다운로드되어 캐시됩니다(~GB). 이후 실행은 캐시를 재사용합니다.
- 런타임 메모리 백엔드를 위한 **OpenAI 호환 LLM/임베딩/비전 키**(`OPENAI_API_KEY`, `OPENAI_BASE_URL`). 파서 전용 모드는 키가 필요하지 않습니다.

## 협업 원칙

LLM-Wiki는 memory compiler로 남습니다. RAG-Anything은 독립적인 동반 도구로 남습니다: 멀티모달 파서 + LightRAG 검색 엔진.
