# 세션 그래프

<!-- translations:start -->
<p align="center"><a href="../../integrations/sessions.md">English</a> · <a href="sessions.zh.md">中文</a> · <a href="sessions.ja.md">日本語</a> · <a href="sessions.ru.md">Русский</a> · <a href="sessions.es.md">Español</a> · <a href="sessions.fr.md">Français</a> · <a href="sessions.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae의 세션 그래프는 프로젝트에 대한 Claude Code / Codex 대화를 타입드 지식 그래프의 1급 노드로 만들고, 대화에 등장한 문서들에 다시 연결합니다. 컴파일 후, `tesserae project ask "3D Gaussian Splatting에 대해 어떤 결정을 내렸나요?"`라고 물으면 그것을 만들어낸 세션과 함께 구체적인 Insight / Decision / Question / TODO / Hypothesis / Takeaway 노드를 받을 수 있습니다.

## 작동 방식

세션당 두 단계로 실행됩니다:

1. **구조적** (항상 실행, LLM 미사용). `tesserae sessions discover --import`가 `.tesserae/harness_sessions/`에 쓴 정규화된 `HarnessSession` 레코드를 읽습니다. 각 세션에 대해 `Session` 봉투 노드를 만들고, 에이전트가 연 모든 문서에서 `discussed_in` 엣지를 발행하며, 기존 `decisions` 필드를 `SessionDecision` 노드로 변환합니다.
2. **LLM** (선택적, `ANTHROPIC_API_KEY` 설정 시 실행). 정규화된 대화 턴(원본 트랜스크립트 파일이 아닌 `metadata["turns"]` 필드)을 JSON 전용 발견 스키마와 함께 Claude로 보냅니다. 6가지 종류의 발견을 반환하며, 각각 특정 턴과 현재 그래프의 특정 doc 노드 ID를 인용합니다. content_hash + project_root_hash로 캐시되므로 변경되지 않은 세션은 다음 컴파일에서 호출을 건너뜁니다.

## 세션을 넣는 두 가지 방법: 배치 vs 라이브

위 파이프라인은 세션이 어떻게 도착하든 동일합니다. 다른 것은 *언제* 발견되고 컴파일되는지입니다:

- **배치(수동).** `tesserae project sessions discover --import`를 실행한 다음 직접 `tesserae project compile`을 실행합니다. 일회성 백필이나 CI에 적합합니다. 이 페이지의 나머지는 이 경로를 다룹니다.
- **라이브(연속).** 엔진이 프로젝트를 감시하며 작업이 일어나는 대로 재컴파일하도록 하여, 무언가를 실행하는 것을 기억하지 않아도 그래프가 최신 상태를 유지합니다:
  - **수퍼바이저 데몬** — `tesserae project engine`(별칭 `tesserae project daemon`)은 단일 소유자 asyncio 루프를 실행하여 구성된 소스를 감시하고, 편집 버스트를 하나의 `Pipeline.run()`으로 통합하며 자동 재컴파일합니다. `--once`로 결정론적 단일 드레인을, `--interval` / `--debounce`로 통합을 조정합니다. `tesserae project refresh`는 동일한 import → compile → vault-sync 체인을 인프로세스로 한 번 실행합니다.
  - **Claude Code 플러그인 훅** — [플러그인](claude-code-plugin.ko.md)이 설치되면 `SessionEnd` 훅이 대화 종료 시 import + compile을 백그라운드로 실행하여 *이번* 세션의 인사이트가 *다음* 세션의 그래프 노드가 됩니다. `SessionStart` 훅은 진입 시 현재 그래프 요약을 출력합니다. 이것이 수동 discover/compile 단계 없이 세션을 "일어나는 대로" 포착하는 것에 가장 가깝습니다.

## 설정

```bash
# 이 프로젝트에 대한 세션을 `.tesserae/harness_sessions/`로 가져옵니다. cwd로 필터링하여 이 프로젝트 내에서 실행된 세션만 가져옵니다.
tesserae sessions discover --import

# 컴파일. 구조적 패스는 무료로 실행됩니다. LLM 패스는 `claude` CLI에 로그인되어 있으면 자동 실행됩니다 — API 키 불필요.
tesserae project compile
```

세션 없이 컴파일하려면 (예: 하니스 히스토리가 없는 서버에서):

```bash
tesserae project compile --no-sessions
```

구조적 전용을 강제하려면 (키가 설정되어 있어도 LLM 호출 건너뛰기):

```bash
tesserae project compile --sessions-llm=false
```

## 구성

`.tesserae/config.json`은 `sessions` 블록을 받습니다:

```jsonc
{
  "sessions": {
    "enabled": true,
    "llm_enabled": "auto",
    "max_turns_per_chunk": 30,
    "model": "claude-sonnet-4-7-20251201",
    "include_doc_id_context": 200
  }
}
```

CLI 플래그가 구성을 재정의합니다. `llm_enabled = "auto"` (기본값)는 `claude` CLI 로그인 또는 `ANTHROPIC_API_KEY` 둘 중 하나가 있을 때 LLM 패스를 실행합니다. 둘 다 없으면 구조적 패스만 실행됩니다 (오류 없음, 외부 호출 없음).

## 쿼리

기존 검색/위키 도구 위에 두 개의 MCP 도구가 추가됩니다:

* `list_sessions(since?, limit?)` — 활성 프로젝트의 Session 봉투 (id, started_at, title, 발견 개수).
* `find_session_findings(node_id, kinds?)` — `discussed_in` 또는 `references`를 통해 `node_id`에 연결된 모든 Session 파생 발견. 선택적으로 insight / decision / question / todo / hypothesis / takeaway로 필터링 가능.

CLI에서:

```bash
tesserae sessions list
tesserae project ask "what did we decide about extractor dedup?"
```

## 개인 정보 보호

* `claude` CLI 로그인이 없고 `ANTHROPIC_API_KEY`도 없으면 (또는 `--sessions-llm=false`로) 외부 네트워크 호출이 0건입니다. 구조적 패스만 실행됩니다.
* LLM 패스가 실행될 때, 아직 캐시되지 않은 세션의 **전체 정규화 대화 턴**이 전송됩니다. 트랜스크립트 파일 자체는 디스크에 남고, LLM의 JSON 출력만 그래프와 세션별 캐시에 보존됩니다.
* 캐시 파일은 `.tesserae/session_findings/<session_id>.findings.json`에 `content_hash`와 `project_root_hash` 모두와 함께 저장됩니다. 프로젝트 간에 복사된 캐시 파일은 읽기 시 거부됩니다 — 프로젝트 간 재생이 없습니다.
* 세션은 로드 후 `session_matches_project`를 통해 필터링되므로, `cwd`가 형제 프로젝트인 트랜스크립트는 이 프로젝트의 그래프에 노드를 생성하지 않습니다.

## Vault 레이아웃

Obsidian vault 아래에 발견당 한 페이지씩 세션별로 그룹화되어 렌더링됩니다:

```
<vault>/
  sessions/
    <session-id-slug>/
      cache-findings-by-content-hash.md
      path-index-needs-basename-suppression.md
      ...
```

발견 페이지의 `<!-- user-notes:start -->` … `<!-- user-notes:end -->` 블록 안의 사용자 노트는 다른 모든 vault 페이지와 동일한 계약으로 재컴파일 후에도 유지됩니다.

## 문제 해결

* **컴파일 후 Session 노드가 나타나지 않습니다.** 먼저 `tesserae sessions discover --import`를 실행했나요? 컴파일 경로는 `.tesserae/harness_sessions/`만 소비하며, `~/.claude/projects/`를 자동으로 스캔하지 않습니다 (수천 개의 과거 세션이 있는 머신에서 그 스캔은 몇 분이 걸릴 수 있습니다).
* **LLM 비용 우려.** 캐시는 각 세션이 content-hash당 최대 한 번 LLM으로 전송되도록 합니다. 긴 세션은 `max_turns_per_chunk` (기본값 30)에서 5턴 오버랩으로 청크됩니다. 총 비용을 제한하려면 `max_turns_per_chunk`를 낮추거나, `include_doc_id_context`를 낮추거나, `--sessions-llm=false`로 설정하세요.
* **발견이 존재하지 않는 노드 ID를 인용합니다.** 오케스트레이터는 인용된 모든 참조를 라이브 doc 그래프에 대해 검증하고 알 수 없는 것을 조용히 삭제합니다. 로그에서 경고를 본다면 LLM이 인용을 환각한 것입니다 — 살아남은 참조는 여전히 신뢰할 수 있습니다.

## 사양

전체 디자인은 [docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md](../../superpowers/specs/2026-05-19-session-graph-extractor-design.md)에 있습니다. 구현 계획은 [docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md](../../superpowers/plans/2026-05-19-session-graph-extractor-plan.md)입니다.
