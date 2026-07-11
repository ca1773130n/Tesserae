# `tesserae doctor` — 프로젝트 상태 점검

<!-- translations:start -->
<p align="center"><a href="../doctor.md">English</a> · <a href="doctor.zh.md">中文</a> · <a href="doctor.ja.md">日本語</a> · <a href="doctor.ru.md">Русский</a> · <a href="doctor.es.md">Español</a> · <a href="doctor.fr.md">Français</a> · <a href="doctor.de.md">Deutsch</a></p>
<!-- translations:end -->
`tesserae doctor`는 Tesserae 워크스페이스를 처음부터 끝까지 — 초기화, 그래프
무결성, 레지스트리 일관성, 최신성(freshness), 잠금(lock), LLM 로그인, 디스크
위생까지 — 점검하고 체크리스트를 출력합니다. **기본적으로 읽기 전용**이며,
`--fix`는 재실행해도 안전한 복구만 적용하고 라이브 상태를 절대 파괴하지
않습니다.

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## 무엇을 점검하는가

카테고리별로 묶인 20개의 점검 항목:

| 점검 | 카테고리 | 검증 내용 | `--fix` 동작 |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/`가 존재하고 Tesserae 워크스페이스로 보이는지 | 보고만 (`tesserae init` 제안) |
| `graph_parse` | core | `graph.json`이 파싱되고 기대한 형태를 갖는지 | 보고만 (`tesserae compile` 제안) |
| `config_valid` | core | `.tesserae/config.json`이 파싱되고 init 템플릿에 대해 유효한지 | 보고만 |
| `vault_configured` | core | 설정된 vault 경로가 해석(resolve)되는지 | **SAFE**: 해석된 vault 디렉터리가 프로젝트 내부에 있을 때 생성 |
| `registry_consistent` | registry | `~/.tesserae/registry.json` 항목이 실제 프로젝트 루트를 가리키는지 | **SAFE**: 루트가 사라진 항목을 정리하고, 레거시 `active` 키를 제거; 그래프 누락은 보고만 |
| `graph_staleness` | freshness | 마지막 compile에 기록된 `git_head` 이후의 git delta | 보고만 (`tesserae refresh` 제안 — compile은 무거움) |
| `site_search_index` | freshness | 정적 사이트 / `search-index.json`이 `graph.json`보다 최신인지 | **SAFE**: 사이트를 재빌드 |
| `backend_artifacts` | freshness | RAG-Anything 아티팩트가 최신인지 | 보고만 (해당 refresh는 LLM/네트워크 비용이 큼) |
| `session_chunks` | freshness | [일일 session-chunk](session-chunks.ko.md) 커버리지에 최근 윈도우 내 공백이 없는지 | 보고만 (`tesserae sessions chunk-backfill` 제안) |
| `wiki_lint` | graph | graph ⇄ wiki 드리프트 + 자명하게 고칠 수 있는 lint 발견 사항 | **SAFE**: lint의 자명한 수정(`fix_trivial`)을 적용 |
| `compile_lock` | processes | 라이브 compile lock이 잡혀 있는지, 어느 pid가 잡고 있는지 | 보고만 — doctor는 **살아 있는 lock을 절대 죽이거나 제거하지 않음** |
| `daemon_pid` | processes | `daemon.pid`가 살아 있는 엔진 프로세스를 가리키는지 | **SAFE**: 소유자가 죽었을 때 pidfile 제거 |
| `llm_login` | environment | 설정된 LLM 백엔드가 실제로 사용 가능한지 (claude/codex CLI 로그인 상태이거나 API 키 존재) | 보고만 (`claude /login` / `codex login` 제안) |
| `optional_deps` | environment | 선택적 의존성 상태 (memex, cognee, raganything) | 보고만 (설치는 네트워크가 필요) |
| `embedding_backend` | environment | 실제 시맨틱 embedding 백엔드가 사용 가능한지 | 보고만 (`pip install tesserae[semantic]` 제안) |
| `environment` | environment | 환경 전반 감지 요약 | 보고 전용 섹션 |
| `build_history` | hygiene | `.build-history` 크기와 형태 | **SAFE**: 트리밍하되 항상 최신 `git_head` 항목을 보존 (staleness 점검이 이것에 의존) |
| `idempotence` | hygiene | 출력-스냅샷 `idempotence_suspect` 트립와이어 | 보고만 (이는 버그 신호이지 자동 복구 대상이 아님) |
| `orphan_worktrees` | hygiene | 오래된 `git worktree` 등록 | **SAFE**: `git worktree prune`; 디렉터리 삭제는 보고만 |
| `hook_log_bloat` | hygiene | `.tesserae/.session-*-hook.log` 증가 | **SAFE**: 10 MB 초과 로그를 로테이션/절단 |

점검이 크래시하면 error finding으로 보고됩니다 — doctor 자체는 절대 예외를
던지지 않습니다.

## `--fix` 정책

- `--fix`는 위에서 SAFE로 표시된 점검**만** 실행한 뒤 재감지하여 보고서가
  수정 이후 상태를 반영하도록 합니다.
- 모든 수정은 멱등적입니다: `doctor --fix`를 두 번 실행하면 두 번째 실행은
  깨끗하게 통과합니다.
- Doctor는 **프로세스를 절대 죽이지 않으며 살아 있는 compile lock을 절대
  제거하지 않습니다** — 잡혀 있는 lock은 소유 pid와 함께 보고되고 그대로
  남겨집니다.
- 무겁거나 네트워크가 필요한 작업(재compile, 의존성 설치, 백엔드 refresh)은
  절대 `--fix`에 포함되지 않습니다; doctor는 대신 사용자가 실행할 명령을
  출력합니다.

## 종료 코드

`tesserae lint`와 같은 관례:

| 종료 코드 | 의미 |
|---|---|
| `0` | 정상 — OK를 넘는 발견 사항 없음 |
| `1` | 경고 존재 |
| `2` | 오류 존재 |

## 보고서 아티팩트

매 실행마다 두 가지 형태의 보고서를 워크스페이스에 기록합니다:

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json`은 markdown 체크리스트 대신 JSON 보고서를 stdout에 추가로
출력합니다. `--all`은 레지스트리의 모든 프로젝트를 순회하며(`--project`
무시) 프로젝트별로 보고합니다.

## MCP: `doctor_report`

MCP 서버는 동일한 보고서를 `doctor_report` 도구로 노출하므로(반환 내용의
바이트 상한을 포함해 `lint_report`를 그대로 따름), 에이전트가 대화 중에 셸을
호출하지 않고도 워크스페이스 상태를 점검할 수 있습니다. 프로젝트 루트가
필요합니다 — `graph_path`/`project`를 전달하거나 기본 그래프를 설정하세요.
