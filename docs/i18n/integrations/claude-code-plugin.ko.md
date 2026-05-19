# Claude Code 플러그인

<!-- translations:start -->
<p align="center"><a href="../../integrations/claude-code-plugin.md">English</a> · <a href="claude-code-plugin.zh.md">中文</a> · <a href="claude-code-plugin.ja.md">日本語</a> · <a href="claude-code-plugin.ru.md">Русский</a> · <a href="claude-code-plugin.es.md">Español</a> · <a href="claude-code-plugin.fr.md">Français</a> · <a href="claude-code-plugin.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae는 [Claude Code](https://docs.claude.com/en/docs/claude-code) 플러그인을 함께 제공합니다. TUI 세션 내부에서 전체 Tesserae 워크플로우를 사용할 수 있게 해줍니다 — 슬래시 명령, 자동 등록 MCP 서버, 에이전트를 안내하는 스킬, 에이전트↔프로젝트 메모리 루프를 닫는 네 개의 훅. 플러그인은 저장소 내 `plugin/`에 있습니다.

## 설치

```bash
# 사전 조건: `tesserae`가 이미 설치되어 있어야 합니다(`pip install tesserae` 또는 `pipx install tesserae`).
/plugin install /path/to/Tesserae/
```

사전 조건: `tesserae`가 이미 설치되어 있어야 합니다(`pip install tesserae` 또는 `pipx install tesserae`). pipx로 설치하는 경우, `~/.local/bin`이 Claude Code가 시작 시 상속받는 PATH에 있는지 확인하세요.

## 포함된 것

* **슬래시 명령 9개** — CLI에 1:1로 매핑되는 7개 래퍼(`/tesserae:compile`, `/tesserae:ask`, `/tesserae:sessions-import`, `/tesserae:build-site`, `/tesserae:serve`, `/tesserae:obsidian-sync`, `/tesserae:setup`) + 두 개의 워크플로우 매크로(`/tesserae:refresh`는 import + compile + obsidian-sync를 체인, `/tesserae:status`는 그래프 카운트와 마지막 컴파일 표시).
* **`tesserae_mcp` 서버 자동 등록** — 에이전트가 `ask`, `search_nodes`, `list_sessions`, `find_session_findings` 등을 수동 설정 편집 없이 `mcp__plugin_tesserae_tesserae__<tool>`로 호출할 수 있습니다.
* **`using-tesserae` 스킬** — 사용자가 타입드 그래프, 과거 세션 회수, 위키/볼트 콘텐츠, 또는 tesserae 워크플로우에 대해 질문할 때 자동 로드됩니다. 에이전트에게 어떤 MCP 도구를 사용할지 vs 어떤 슬래시 명령을 제안할지 가르쳐줍니다.
* **훅 4개** — `SessionStart`는 그래프 요약을 출력; `SessionEnd`는 이번 대화의 인사이트가 다음 세션의 그래프 노드가 되도록 백그라운드 import+compile; `PostToolUse`(선택)는 docs/ 편집 시 증분 재컴파일; `PreToolUse`는 큰 그래프 컴파일을 확인 대화상자로 게이팅.

전체 세부 정보, 명령/훅 표, 프로젝트별 opt-out 설정은 플러그인 자체의 [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md)에 있습니다.

## 왜 플러그인과 MCP 서버 모두?

역할이 다릅니다:

- **MCP 도구** = 대화 중 에이전트가 호출하는 읽기 전용 그래프 쿼리. 항상 켜져 있고 마찰이 적습니다.
- **슬래시 명령** = 명시적으로 호출하는 워크플로우 작업(compile, refresh, obsidian-sync). 영향력이 크지만 사용자의 결정이어야 합니다.

MCP 서버만 단독으로 사용할 수도 있습니다(`tesserae project mcp-config`를 통한 수동 `claude_desktop_config.json` 편집). 플러그인은 단지 슬래시 명령, 스킬, 훅과 함께 패키징하여 한 단계로 설치할 수 있게 해줍니다.

## 설치 확인

```
/plugin list
/mcp
/tesserae:status
```

## 제거

```
/plugin uninstall tesserae
```

되돌릴 수 있습니다. 어떤 프로젝트의 `.tesserae/` 디렉토리도 건드리지 않습니다.

## 참고

- [구현 계획](../../superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [설계 사양](../../superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [세션 통합](sessions.ko.md) — 플러그인의 훅이 루프를 닫는 세션 그래프 기능
