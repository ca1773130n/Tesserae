# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Tesserae 그래프 뷰" width="100%" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.zh.md">中文</a> ·
  <a href="./README.ja.md">日本語</a> ·
  <a href="./README.ru.md">Русский</a> ·
  <a href="./README.es.md">Español</a> ·
  <a href="./README.fr.md">Français</a> ·
  <a href="./README.de.md">Deutsch</a>
</p>

[라이브 데모](https://ca1773130n.github.io/Tesserae) · [문서](docs/) · [MCP 설정](docs/i18n/integrations/mcp.ko.md) · [Obsidian 내보내기](docs/i18n/integrations/obsidian.ko.md)

Tesserae는 프로젝트 메모리 컴파일러입니다. 마크다운, 소스 파일, 선택적으로 PDF/Office 문서/이미지가 들어 있는 디렉터리를 입력으로 받아 타입이 지정된 지식 그래프를 추출하고, 쿼리 가능한 위키를 작성하며, 이식 가능한 아티팩트를 함께 생성합니다: 마크다운 프로젝션, Cognee용 번들, 에이전트 하니스, 그리고 Claude Code, Codex 또는 모든 MCP 클라이언트에 연결할 수 있는 MCP 서버. 호스팅 서비스가 아니라 프로젝트 컨텍스트를 위한 빌드 단계입니다.

## 언제 사용하고, 언제 사용하지 않을지

다음과 같은 경우에 사용하세요:

- 단일 프로젝트의 텍스트 중심 소스(문서, 코드, 연구 노트)에 대해 지속 가능하고 검사 가능한 지식 그래프가 필요할 때.
- 자신의 파일을 근거로 질문에 답하는 로컬 MCP 서버가 필요할 때.
- 직접 글루 코드를 작성하지 않고도 Cognee에 깨끗한 번들을 공급하거나, Obsidian에 마크다운 프로젝션을 넣고 싶을 때.

다음의 경우라면 건너뛰세요:

- 작은 디렉터리에 대한 벡터 검색만 필요하다면 — `ripgrep`과 임베딩 라이브러리가 더 간단합니다.
- 편집 UI가 있는 호스팅 위키를 원한다면. 여기서 제공하는 정적 사이트는 읽기 전용입니다.
- 즉시 사용 가능한 정확한 의미 임베딩이 필요하다면. 기본 RAG-Anything 임베딩은 결정적입니다([Status](#상태) 참조).
- 턴키 방식의 "무엇이든 질문" 에이전트를 기대한다면. 이 프로젝트는 그 기반을 만들 뿐, 원하는 에이전트에 연결하는 것은 사용자의 몫입니다.

## 상태

진화 중인 연구/에이전트 도구 프로젝트입니다. 알려진 한계:

- 컴파일 시간은 코퍼스 크기에 거의 선형으로 비례합니다. 큰 마크다운 트리(수천 개 파일)에 대한 첫 컴파일은 수 분이 걸릴 수 있습니다.
- 기본 RAG-Anything 임베딩 공급자는 `deterministic`입니다. 재현 가능하고 의존성이 없지만 의미 검색 품질은 제한적입니다. 더 나은 검색을 위해 `ollama`(예: `qwen3-embedding:0.6b`)나 OpenAI 호환 엔드포인트로 전환하세요 — [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md) 참조.
- RAG-Anything의 비전 지원(이미지 내용 추출)은 아직 엔드-투-엔드로 연결되지 않았습니다. 이미지 파일은 구조적으로 파싱되지만 설명되지는 않습니다.
- Cognee 런타임 cognify는 best-effort입니다: 누락된 공급자, 유료 API 키, 또는 네트워크 실패는 빌드를 중단시키지 않고 로그에 남고 건너뛰어집니다.
- MCP 서버는 안정적인 도구 집합을 노출하지만, 내부 그래프 스키마는 여전히 추가될 수 있습니다.

## 빠른 시작

Python 3.9 이상이 필요합니다. RAG-Anything을 사용하면 Python 3.10 이상이 필요합니다.

```bash
pip install tesserae

cd /path/to/my-project
tesserae project setup
tesserae project compile
tesserae project ask "Where is Mermaid rendering implemented?"
tesserae project build-site && tesserae project serve --port 8765
```

설정 마법사는 일반적인 소스(`README.md`, `docs/`, `src/`, `data/`)를 감지하고 `.tesserae/config.json`을 작성합니다. LLM 호출 기능은 기본적으로 OAuth 기반의 `codex` CLI를 사용하므로 일반적인 경로에서는 API 키가 필요 없습니다. 더 자세한 내용은 [docs/quickstart.md](docs/quickstart.md)와 [docs/installation.md](docs/installation.md)를 참고하세요.

> [!tip]
> **설치 후 `tesserae: command not found`가 뜨나요?** `pip`가 바이너리를 셸이 찾지 않는 곳에 두었습니다. **모든 플랫폼**에서 가장 확실한 해결책은 [`pipx`](https://pipx.pypa.io/)입니다 — CLI 도구를 격리된 venv에 설치하고 `PATH`를 자동으로 관리합니다:
>
> ```bash
> # macOS — `brew install pipx`
> # Ubuntu / Debian — `sudo apt install pipx`
> # 기타 — `python3 -m pip install --user pipx`
> pipx ensurepath          # ~/.local/bin을 PATH에 추가합니다. 이후 새 셸을 여세요
> pipx install tesserae
> ```
>
> **Ubuntu 23.04+** 에서 그냥 `pip install tesserae`를 쓸 때 흔히 만나는 문제:
>
> | 에러 | 원인 | 해결책 |
> |---|---|---|
> | `error: externally-managed-environment` | PEP 668 — 시스템 Python이 잠겨 있음 | `pipx` 사용(위), 또는 `pip install --user --break-system-packages tesserae`(지저분), 또는 venv |
> | `pip install --user …` 후 `tesserae: command not found` | `~/.local/bin`이 `PATH`에 없음 | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
> | Ubuntu 20.04에서 `ModuleNotFoundError: pydantic` | 시스템 `python3`가 3.8, tesserae는 3.9 이상 필요 | `sudo apt install python3.11 python3.11-venv` 후 `python3.11 -m pip install --user tesserae` |


## 컴파일 후 얻는 것

```text
.tesserae/
  config.json
  graph.json              # 타입 지정된 노드/엣지
  manifest.json           # 소스 지문 (--changed-only가 사용)
  sqlite.db               # 쿼리 가능한 그래프 저장소
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  markdown_projection/    # 사람이 읽기 쉬운 위키 페이지
  obsidian_vault/         # Obsidian에 바로 넣을 수 있는 보관소
  agent_harness/          # 에이전트별 설정 (Claude/Codex/Gemini/Cursor/...)
  harness_sessions/       # 가져온 Claude/Codex 세션 메모리
  cognee_bundle/          # Cognee 수집용 JSONL
  site/                   # build-site가 만드는 정적 사이트
  external/               # 보조 도구 출력 (UA, RAG-Anything)
```

`project compile` 이후 `ls .tesserae/`로 무엇이 생성되었는지 확인하세요.

## CLI 개요

일상적으로 사용하는 명령입니다. 전체 플래그는 `tesserae <subcommand> --help`로 확인하세요.

| 명령 | 하는 일 |
|---|---|
| `tesserae project setup` | 대화형 마법사. `.tesserae/config.json`을 작성합니다. `--with-understand-anything`, `--with-raganything`, `--run-cognee` 등을 받습니다. |
| `tesserae project compile` | 설정된 소스를 읽고, 보조 도구 새로고침을 실행하고, `.tesserae/` 아래의 모든 아티팩트를 작성합니다. 증분 재빌드에는 `--changed-only`를 사용하세요. |
| `tesserae project build-site` | 정적 프론트엔드를 `.tesserae/site/`에 빌드합니다. |
| `tesserae project serve --port 8765` | 정적 사이트를 로컬에서 제공합니다. |
| `tesserae project refresh-understand-anything` | Tesserae의 관리형 Understand Anything 새로고침 래퍼를 실행합니다. |
| `tesserae project refresh-raganything --parser mineru` | RAG-Anything으로 비코드 소스(PDF, Office, 이미지)를 다시 파싱합니다. |
| `tesserae project ask "<question>"` | 설정된 백엔드(`auto`/`raganything`/`cognee`/`wiki`)에 질문합니다. |
| `tesserae project mcp-config` | Claude Code, Codex 또는 Hermes에 붙여넣을 MCP 서버 설정 스니펫을 출력합니다. |
| `tesserae wiki register <path> --name <alias>` | 공유 레지스트리에 프로젝트를 등록합니다. |
| `tesserae wiki list` / `tesserae wiki activate <name>` | 등록된 프로젝트를 나열하고, 활성 프로젝트를 설정합니다. |
| `tesserae ask "<question>" [--wiki <name>]` | 레지스트리를 통해 해석하는 최상위 ask 명령입니다. |

## 통합

모든 통합은 옵트인입니다. 일반 마크다운/코드 프로젝트에서 Tesserae를 사용하는 데 필수는 아닙니다.

- **세션 그래프** — 프로젝트에 대한 Claude Code / Codex 대화를 그래프의 1급 노드(Insight / Decision / Question / TODO / Hypothesis / Takeaway)로 만들어, 등장한 문서에 연결합니다. `tesserae sessions discover --import`를 한 번 실행한 후, 매 `tesserae project compile`이 새 세션을 가져옵니다. 구조적 패스는 무료, LLM 패스는 `claude` CLI에 로그인되어 있으면 자동 실행됩니다 — **API 키 불필요**. [docs/integrations/sessions.md](docs/integrations/sessions.md) 참조.
- **Understand Anything** — `.understand-anything/knowledge-graph.json`에 코드 지식 그래프를 생성하는 별도 프로젝트([Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything))입니다. `--with-understand-anything`으로 활성화합니다. Tesserae가 관리형 새로고침 래퍼를 저장하므로 `project compile`이 그래프를 최신 상태로 유지합니다. [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md) 참조.
- **RAG-Anything** — MinerU/Docling/PaddleOCR을 통해 PDF, Office 문서, 이미지를 처리하는 멀티모달 수집([HKUDS/RAG-Anything](https://github.com/HKUDS/RAG-Anything))입니다. `--with-raganything`으로 활성화합니다. 런타임 질문 백엔드(LightRAG) 역할도 합니다. Python 3.10 이상이 필요합니다. [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md) 참조.
- **Cognee** — 그래프+벡터 메모리 백엔드입니다. `--run-cognee --install-cognee`로 활성화합니다. 일반 컴파일은 항상 `.tesserae/cognee_bundle/`을 작성하며, 런타임 `cognify` 패스는 best-effort이고 명시적으로 활성화한 경우에만 실행됩니다.

## 멀티 프로젝트 레지스트리

`~/.tesserae/registry.json`에 있는 영구 레지스트리를 통해 최상위 `ask` CLI와 MCP 서버가 호출마다 `--project`를 지정하지 않아도 프로젝트 이름을 루트로 해석할 수 있습니다.

```bash
tesserae wiki register /path/to/my-project --name myproj
tesserae wiki activate myproj
tesserae ask "Where is the parser entry point?"
```

MCP 서버도 같은 레지스트리를 읽으므로, MCP 클라이언트는 등록된 모든 위키에 대해 `list_projects`, `activate_project`, `ask`를 호출할 수 있습니다.

## MCP

`tesserae project mcp-config`는 Claude Code, Codex 또는 다른 MCP 클라이언트에 붙여넣을 수 있는 서버 항목을 출력합니다. 서버는 `schema`, `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`, `wiki_page`, `raw_source`, `lint_report`, `ask` 도구와 레지스트리 도구 `list_projects` / `register_project` / `activate_project` / `unregister_project`를 노출합니다. 특정 프로젝트가 필요한 도구는 CLI와 동일한 레지스트리를 통해 해석합니다.

## 인증과 LLM 공급자

일반적인 경로에서는 API 키가 필요 없습니다:

- **Codex CLI**(기본)를 OAuth로 사용합니다. `--raganything-llm-provider codex`가 기본이며, Cognee `codex_cognify` 모드는 Cognee의 LLM 클라이언트를 Codex CLI로 패치합니다.
- **Claude Code CLI**를 OAuth로 사용합니다. RAG-Anything 런타임 질의에는 `--raganything-llm-provider claude`를 설정하세요. 멀티 계정 설정은 `--raganything-claude-config-dir ~/.claude`를 사용합니다(Tesserae가 호출 전에 `CLAUDE_CONFIG_DIR`을 export 합니다).
- **임베딩**은 기본적으로 인프로세스 결정적 공급자를 사용합니다. `--cognee-embedding-provider ollama --cognee-ollama-embedding-model qwen3-embedding:0.6b`로 Ollama에 연결하거나, OpenAI 호환 엔드포인트를 연결할 수 있습니다 — 두 방법 모두 통합 문서에 설명되어 있습니다.

`ANTHROPIC_API_KEY`나 `OPENAI_API_KEY`를 설정하면 해당 경로에서 인식되지만, 필수는 아닙니다.

## 프로젝트 레이아웃

```text
tesserae/        # 패키지 (CLI, 컴파일러, MCP 서버, 어댑터)
docs/            # 영어 문서 + 6개 언어를 위한 docs/i18n/
ontology/        # 컴파일러가 검증하는 노드/엣지 스키마
prompts/         # 추출 및 종합 프롬프트
scripts/         # 유지보수 스크립트
tests/           # pytest 스위트
evals/           # 그래프 품질 평가 하니스
data/            # 셀프 도그푸딩에 사용하는 연구 노트 예시
```

## 현지화된 문서

[English](./README.md) ·
[中文](./README.zh.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md)

긴 형식의 문서는 `docs/i18n/` 와 `docs/i18n/integrations/` 에 미러링되어 있습니다.

## 라이선스

MIT. [LICENSE](LICENSE)를 참조하세요.
