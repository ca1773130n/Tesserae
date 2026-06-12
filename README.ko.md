# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="개념, 논문, 저장소, 합성, 엔티티가 포커스 노드 주변에 클러스터된 Tesserae 그래프 뷰" width="100%" />
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

> 프로젝트에 대한 자기 개선 지식 베이스를 유지하고, 에이전트가 바로 사용할 수 있는 컨텍스트를 온디맨드로 컴파일하는 컨텍스트 엔진입니다.

<p align="center">
  <img src="docs/screencasts/showcase.gif" alt="세 단계 스크린캐스트: tesserae init → compile → ask, 135개 문서 데모 코퍼스로 녹화" width="100%" />
</p>

<p align="center">
  <a href="https://ca1773130n.github.io/Tesserae">라이브 데모</a> ·
  <a href="docs/">문서</a> ·
  <a href="docs/release-notes/">릴리스 노트</a> ·
  <a href="docs/integrations/mcp.md">MCP 설정</a> ·
  <a href="docs/integrations/obsidian.md">Obsidian 내보내기</a>
</p>

## 무엇인가요

마크다운, 소스 코드, 그리고 선택적으로 PDF/Office 문서/이미지가 담긴 디렉터리를 Tesserae에 지정하세요. 그러면 프로젝트의 **타입이 지정된 지식 그래프**를 재구성하고 최신 상태로 유지하므로, 에이전트는 항상 출처가 명시된 근거 있는 컨텍스트를 사용할 수 있습니다. 세 가지 기둥 위에서 작동합니다:

1. **세션 모니터링** — Claude Code / Codex 대화가 발생하는 즉시 1급 그래프 노드(결정, 인사이트, 질문, TODO)로 포착됩니다.
2. **자율적 수집** — 감독형 엔진이 소스와 세션을 감시하고, 버스트를 합치며, 재컴파일합니다. 자기 개선 사이드카가 반복되는 발견을 강화하고 오래된 것을 대체합니다.
3. **온디맨드 컨텍스트** — 컨텍스트 컴파일러가 임의의 쿼리 또는 시드 노드에 대해 인용이 달린 맞춤 컨텍스트 문서를 조립합니다(문자 예산 내에서 Personalized PageRank 확장). 모든 에이전트에 붙여넣을 수 있습니다.

타입이 지정된 그래프, Obsidian 보관소, 정적 사이트는 하나의 지식 베이스의 *프로젝션*입니다. 모든 것이 로컬에서 실행되며, 호스팅 서비스가 아닌 빌드 단계이자 라이브 엔진입니다.

## 빠른 시작

**Python 3.10+**가 필요합니다.

```bash
pip install tesserae          # 실제 임베딩을 원하면 [semantic] 추가
# 또는: pipx install tesserae   # PATH 안전 설치에 가장 권장
# 또는: npx @jokerized/tesserae # 동일한 CLI를 감싼 Node 래퍼

cd /path/to/my-project
tesserae init --yes           # 마법사; --yes는 감지된 기본값을 수락
tesserae compile              # 지식 그래프 빌드
tesserae ask "Mermaid 렌더링은 어디에 구현되어 있나요?"

# 쿼리에 대한 맞춤 인용 컨텍스트 문서 컴파일:
tesserae context "파서가 arXiv ID를 어떻게 처리하나요?" --budget 32000 -o context.md

tesserae serve --port 8765    # 그래프 + 위키를 로컬에서 탐색
```

LLM 지원 기능은 기본적으로 OAuth 방식의 `codex` / `claude` CLI를 사용합니다 — 일반적인 경로에서 **API 키가 필요하지 않습니다**. [docs/quickstart.md](docs/quickstart.md)와 [docs/installation.md](docs/installation.md)를 참고하세요.

<details>
<summary><strong>설치 후 <code>tesserae: command not found</code>? Linux 관련 문제?</strong></summary>

어떤 플랫폼에서든 가장 신뢰할 수 있는 방법은 [`pipx`](https://pipx.pypa.io/)입니다:

```bash
# macOS: brew install pipx · Ubuntu/Debian: sudo apt install pipx
pipx ensurepath          # ~/.local/bin을 PATH에 추가; 새 셸을 열어야 함
pipx install tesserae
```

일반 `pip install tesserae`를 사용하는 경우 Ubuntu에서 흔히 발생하는 문제:

| 오류 | 원인 | 해결 방법 |
|---|---|---|
| `error: externally-managed-environment` | PEP 668 — 시스템 Python이 잠겨 있음 | `pipx` (위) 또는 venv 사용 |
| `pip install --user …` 후 `command not found` | `~/.local/bin`이 `PATH`에 없음 | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
| 구형 배포판에서 `ModuleNotFoundError` | 시스템 `python3`이 3.10 미만 | `sudo apt install python3.11 python3.11-venv`, 이후 `python3.11 -m pip`로 설치 |

</details>

<details>
<summary><strong>워크스루 GIF</strong> — 번들된 135개 문서 데모 코퍼스로 각 빠른 시작 단계 시연</summary>

<details>
<summary>1. 설정 — 리서치 디렉터리를 지정하고 프로젝트 위키 스캐폴드 생성</summary>
<br/>
<img src="docs/screencasts/setup.gif" alt="tesserae init --source ./research가 비대화형으로 실행되어 .tesserae/를 작성하는 모습" width="100%" />
</details>

<details>
<summary>2. 컴파일 + 사이트 빌드 — 결정론적, LLM 호출 없음</summary>
<br/>
<img src="docs/screencasts/compile.gif" alt="tesserae compile 후 tesserae export site 실행, graph.json과 정적 사이트 트리 생성" width="100%" />
</details>

<details>
<summary>3. Ask — CLI에서 컴파일된 위키 쿼리</summary>
<br/>
<img src="docs/screencasts/ask.gif" alt="tesserae ask --backend wiki가 점수, 종류, 아웃바운드 관계와 함께 상위 3개 결과를 반환하는 모습" width="100%" />
</details>

`vhs docs/screencasts/<name>.tape`으로 GIF를 재생성할 수 있습니다.

</details>

## 일상적인 명령어

전체 그룹 목록은 `tesserae --help`, 플래그는 `tesserae <cmd> --help`를 실행하세요.

| 명령어 | 설명 |
|---|---|
| `tesserae init` | 설정 마법사 → `.tesserae/config.json`. `--yes` 비대화형, `--bare` 최소 구성. |
| `tesserae compile` | 지식 그래프와 모든 아티팩트를 재빌드합니다. `compile <paths>`로 추가 파일을 임시 수집. |
| `tesserae ingest <file\|url>` | 전체 재컴파일 없이 단일 문서 또는 웹 페이지를 지식 베이스에 병합(패리티 게이트 증분 빠른 경로). |
| `tesserae context "<query>"` | **온디맨드 컨텍스트 컴파일러**: `--budget` 내 PPR 확장으로 인용 컨텍스트 문서 생성; `--synthesize`는 LLM 요약 추가. |
| `tesserae ask "<question>"` | 컴파일된 지식 베이스에 질문(`--scope all-registered`는 프로젝트 전체에 팬아웃). |
| `tesserae engine` | 현재 프로젝트에 대한 감독형 새로고침 데몬: 감시, 디바운스, 재컴파일. |
| `tesserae engine --all` | **플릿 모드**: 하나의 프로세스가 *모든* 등록된 프로젝트를 최신 상태로 유지 — 레지스트리 핫 리로드, `--compile-slots` 스로틀링. |
| `tesserae refresh` | 원샷 파이프라인: 새 세션 가져오기 → 컴파일 → 보관소 동기화. |
| `tesserae sessions discover --import` | 이 프로젝트에 대한 로컬 Claude Code / Codex 세션 기록을 찾아 가져옵니다. |
| `tesserae export site` | 정적 사이트 빌드(`--deploy`, `--watch`). |
| `tesserae serve` | 인라인 ask 위젯과 함께 사이트를 로컬에서 서빙(`/api/ask`). |
| `tesserae projects …` | 다중 프로젝트 레지스트리: `register`, `list`, `activate`, `mcp-config`. |
| `tesserae integrations refresh …` | 동반 도구(Understand-Anything, RAG-Anything) 재실행. |

## 자동으로 최신 상태 유지

엔진은 지식 베이스를 일회성 빌드가 아닌 *자기 개선*으로 만드는 핵심입니다:

```bash
# 단일 프로젝트: 소스 + 라이브 세션 감시, 변경 시 재컴파일.
tesserae engine

# 모든 등록된 프로젝트, 하나의 프로세스 (v0.8.0):
tesserae engine --all --compile-slots 1
```

플릿 모드는 10초마다 `~/.tesserae/registry.json`을 대조하여 재시작 없이 프로젝트 등록/제거가 즉시 반영되고, 프로젝트 간 컴파일을 직렬화하여 동시 LLM 추출이 공유 계정 속도 제한을 침범하지 않도록 합니다. 첫 실행 시 세션 기록을 한 번 스윕하고(로그에 표시됨), 재시작 시에는 영속된 플로어에서 재개합니다.

## 컴파일 후 생성되는 것들

```text
.tesserae/
  graph.json              # 타입이 지정된 노드/엣지 (지식 베이스)
  sqlite.db               # 쿼리 가능한 그래프 저장소
  markdown_projection/    # 사람이 읽을 수 있는 위키 페이지
  obsidian_vault/         # Obsidian에 바로 드롭 가능
  site/                   # 정적 사이트 (그래프 뷰 + 위키 + 검색)
  harness_sessions/       # 가져온 Claude/Codex 세션 메모리
  agent_harness/          # 에이전트별 컨텍스트 구성 (Claude/Codex/Gemini/...)
  cognee_bundle/          # Cognee 수집 준비된 JSONL
  config.json · manifest.json · report.md · …
```

## MCP 서버

`tesserae projects mcp-config`는 Claude Code, Codex, 또는 모든 MCP 클라이언트의 서버 항목을 출력합니다. 주요 도구:

- **`compile_context`** — 쿼리 또는 시드 노드에 대한 맞춤 인용 컨텍스트 문서
  (`synthesize=true`가 아니면 결정론적), `graph_ppr` 기반.
- **그래프 + 위키**: `search_nodes`, `node_context`, `graph_summary`,
  `wiki_page`, `raw_source`, `timeline`, `search_facts`, `lint_report`, `ask`.
- **세션 메모리**: `list_sessions`, `find_session_findings`,
  `find_code_symbol_mentions`, `fresh_insights` (감쇠 랭킹, 중복 제거).
- **레지스트리**: `list_projects`, `register_project`, `activate_project`.

## 다중 프로젝트

`~/.tesserae/registry.json`의 레지스트리가 CLI, MCP, 플릿 엔진 전체에서 프로젝트 이름을 해석합니다:

```bash
tesserae projects register /path/to/my-project --name myproj
tesserae projects activate myproj
tesserae ask "..." --scope all-registered        # 모든 프로젝트에 팬아웃
```

한 프로젝트의 마크다운이 `wiki://<alias>/<kind>/<slug>`로 다른 프로젝트의 노드를 딥링크할 수 있으며, 컴파일 시에 이것이 그래프 뷰의 브리지 노드가 됩니다. 자세한 내용은 [docs](docs/)를 참고하세요.

## 통합 (모두 선택 사항)

- **Claude Code 플러그인** — 슬래시 명령어, 세션 훅, 스킬, MCP 자동 등록을 한 번의 `/plugin install`로 제공.
  [docs/integrations/claude-code-plugin.md](docs/integrations/claude-code-plugin.md)
- **세션 그래프** — Claude Code / Codex 대화 → Insight / Decision /
  Question / TODO 노드, 접촉한 문서와 연결. API 키 불필요.
  [docs/integrations/sessions.md](docs/integrations/sessions.md)
- **Understand-Anything** — 코드 지식 그래프 수집.
  [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md)
- **RAG-Anything** — 멀티모달 수집(MinerU/Docling을 통한 PDF/Office/이미지)과 LightRAG 질문 백엔드.
  [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)
- **Cognee** — 그래프+벡터 메모리 백엔드; 컴파일은 항상 Cognee 준비 번들을 작성하며, 런타임 cognify는 최선 노력으로 제공.
- **Obsidian** — 사용자 편집 오버레이와 함께 양방향 보관소 동기화.
  [docs/integrations/obsidian.md](docs/integrations/obsidian.md)

## 비교

<details>
<summary>Quartz, Logseq, Cognee, Foam과의 기능 비교표</summary>

| 기능 | Tesserae | Quartz | Logseq | Cognee | Foam |
|---|---|---|---|---|---|
| 정적 HTML 출력 | 예 | 예 | 부분(내보내기) | 아니요 | 부분(게시) |
| 내장 그래프 뷰 | 예 | 예 | 예 | 예(별도 UI) | 예(VSCode) |
| 타입이 지정된 노드 스키마 | 예(41가지 타입) | 아니요 | 부분(태그) | 예 | 아니요 |
| 소스에서 개념 추출 | 예(LLM) | 아니요 | 아니요 | 예 | 아니요 |
| 멀티모달 수집(PDF/이미지) | 예(RAG-Anything 경유) | 아니요 | 부분(임베드) | 예 | 아니요 |
| 코드 그래프 수집 | 예 | 아니요 | 아니요 | 부분 | 아니요 |
| MCP 서버 | 예 | 아니요 | 아니요 | 예 | 아니요 |
| 온디맨드 인용 컨텍스트 컴파일러 | 예(PPR + 예산) | 아니요 | 아니요 | 아니요 | 아니요 |
| 라이브 세션 모니터링 → 그래프 | 예 | 아니요 | 아니요 | 아니요 | 아니요 |
| 다중 프로젝트 레지스트리 | 예 | 아니요 | 예(그래프) | 부분 | 아니요 |
| 다중 프로젝트 데몬(플릿) | 예 | 아니요 | 아니요 | 아니요 | 아니요 |
| API 키 없이 작동(OAuth) | 예 | 해당없음 | 해당없음 | 아니요 | 해당없음 |
| 결정론적 바이트 동일 컴파일 | 예 | 예 | 해당없음 | 아니요 | 해당없음 |
| 라이브 편집 | 아니요 | 부분 | 예 | 해당없음 | 예 |
| 실시간 협업 | 아니요 | 아니요 | 예(DB 베타) | 아니요 | 아니요 |

</details>

Tesserae는 라이브 편집보다 소스 기반 컴파일을 선택합니다. UI에서 노트를 편집하고 싶다면 Logseq 또는 Obsidian을 사용하세요. 지식 그래프를 위한 빌드 도구 *겸 라이브 엔진*을 원한다면 이 프로젝트가 적합합니다.

**적합한 경우:** 프로젝트의 텍스트 중심 소스에 대해 견고하고 검사 가능한 지식 그래프를 원하거나, 자신의 파일에 기반한 로컬 MCP 서버가 필요하거나, 글루 코드 없이 Cognee/Obsidian 번들이 필요한 경우.

**적합하지 않은 경우:** 소규모 디렉터리에 대한 벡터 검색만 필요하거나, 편집 UI가 있는 호스팅 위키를 원하거나, 턴키 "무엇이든 물어보세요" 에이전트를 기대하는 경우 — Tesserae는 기반을 만들고, 원하는 에이전트에 연결하는 것은 사용자의 몫입니다.

## 인증 및 LLM 제공자

일반적인 경로는 **API 키가 필요 없습니다**:

- **Codex CLI**(기본값)와 **Claude Code CLI**는 멀티 계정 로테이션을 지원하는 OAuth 방식.
- **임베딩**: 네이티브 하이브리드 검색은 `pip install "tesserae[semantic]"` (`model2vec`)으로 오프라인, torch 없는 시맨틱 레인을 사용합니다. Cognee/RAG-Anything 백엔드는 기본적으로 결정론적 제공자를 사용하며, 더 나은 리콜을 위해 Ollama 또는 OpenAI 호환 엔드포인트로 전환할 수 있습니다.

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`는 설정되어 있으면 자동으로 감지되지만, 필수는 아닙니다.

## 현황 및 제한 사항

현재 릴리스는 [릴리스 노트](docs/release-notes/)를 참고하세요. 알려진 제한 사항:

- 대규모 코퍼스(수천 개 파일)의 첫 번째 컴파일은 몇 분이 걸리며, 컴파일 시간은 대략 선형적으로 증가합니다. 증분 컴파일(`--changed-only`)은 제공되지만 실험적이며 기본적으로 비활성화되어 있습니다.
- `semantic` 추가 없이는 하이브리드 검색이 비시맨틱 스텁으로 성능이 저하됩니다(눈에 띄는 경고 발생).
- RAG-Anything 비전(이미지 설명)은 아직 엔드-투-엔드로 연결되지 않았습니다.
- Cognee 런타임 cognify는 최선 노력으로 제공됩니다: 누락된 제공자는 기록되고 건너뛰어지며, 치명적 오류가 발생하지 않습니다.
- MCP 도구 세트는 안정적이지만, 그래프 스키마는 노드 타입이 추가될 수 있습니다.

## 프로젝트 구조

```text
tesserae/        # 패키지 (CLI, 컴파일러, 엔진, MCP 서버, 어댑터)
docs/            # 영어 문서 + 다른 7개 언어용 docs/i18n/
ontology/        # 컴파일러가 검증하는 노드/엣지 스키마
prompts/         # 추출 및 합성 프롬프트
tests/           # pytest 스위트
evals/           # 그래프 품질 평가 하니스
examples/        # 스크린캐스트에 사용되는 데모 코퍼스
```

## 현지화 문서

[English](./README.md) ·
[中文](./README.zh.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md) ·
[Deutsch](./README.de.md)

장문의 문서는 `docs/i18n/` 아래에 미러링되어 있습니다.

## 라이선스

MIT. [LICENSE](LICENSE)를 참고하세요.
