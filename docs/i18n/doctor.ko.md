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

카테고리별로 묶인 점검 항목:

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
| `compile_lock` | processes | 라이브 compile lock이 잡혀 있는지, 어느 pid **와 어느 호스트**가 잡고 있는지 | 보고만 — doctor는 **살아 있는 lock을 절대 죽이거나 제거하지 않음** |
| `filesystem_locking` | processes | `.tesserae/`가 `flock(2)`이 조용한 no-op일 수 있는 네트워크 파일시스템 위에 있는지 | 보고만 (호스트 간 강제를 증명할 수는 없음 — 아래 참조) |
| `daemon_pid` | processes | `daemon.<host>.pid`가 살아 있는 엔진 프로세스를 가리키는지 | **SAFE**: 소유자가 죽었을 때 **이 호스트의** pidfile을 제거; 다른 머신의 것은 보고할 뿐 절대 건드리지 않음 |
| `llm_login` | environment | 프로젝트가 실제로 사용할 config 디렉터리가 존재하는지 | 보고만 — **자격 증명을 검증하지 않음** (아래 참조) |
| `optional_deps` | environment | 선택적 의존성 상태 (memex, raganything) | 보고만 (설치는 네트워크가 필요) |
| `embedding_backend` | environment | 실제 시맨틱 embedding 백엔드가 사용 가능한지 | 보고만 (`pip install tesserae[semantic]` 제안) |
| `environment` | environment | 환경 전반 감지 요약 | 보고 전용 섹션 |
| `build_history` | hygiene | `.build-history` 크기와 형태 | **SAFE**: 트리밍하되 항상 최신 `git_head` 항목을 보존 (staleness 점검이 이것에 의존) |
| `idempotence` | hygiene | 출력-스냅샷 `idempotence_suspect` 트립와이어 | 보고만 (이는 버그 신호이지 자동 복구 대상이 아님) |
| `orphan_worktrees` | hygiene | 오래된 `git worktree` 등록 | **SAFE**: `git worktree prune`; 디렉터리 삭제는 보고만 |
| `hook_log_bloat` | hygiene | `.tesserae/.session-*-hook.log` 증가 | **SAFE**: 10 MB 초과 로그를 로테이션/절단 |

점검이 크래시하면 error finding으로 보고됩니다 — doctor 자체는 절대 예외를
던지지 않습니다.

## `llm_login`이 말해 주는 것과 말해 주지 않는 것

config 디렉터리가 존재한다고 보고할 뿐입니다. 그 안의 CLI가 유효한 토큰을 쥐고
있다는 것은 보고하지 **않으며**, 자신의 finding 텍스트에 그렇게 적어 둡니다.

이 구분은 트집이 아닙니다. 이 점검은 예전에 `~/.claude/history.jsonl` 같은
파일을 근거로 `credentialed LLM CLI: claude, codex`를 보고했습니다 — 그런
파일이 증명하는 것은 CLI가 *사용된 적 있다*는 사실이지, *지금* 인증할 수
있다는 사실이 아닙니다. 같은 초에 연달아 실행했을 때 `tesserae compile`은
`Claude CLI not logged in (tried 1 config dir)`를 출력하는데 doctor는 초록색
체크를 출력했습니다. 지금 눈앞에 놓인 실패와 모순되는 진단은 진단이 없는 것보다
나쁩니다.

자격 증명을 검증한다는 것은 `tesserae doctor`를 실행할 때마다 실제 LLM 호출을
쓴다는 뜻이며, 이 점검이 스스로 판단해 떠안을 비용은 아닙니다. 그래서 자신이
실제로 확인한 것만 말합니다. 확정적인 답은 `tesserae compile`로 얻으세요.

이 점검의 범위는 프로젝트가 실제로 시도할 디렉터리로 한정되며,
`ProjectWiki._build_json_client`가 쓰는 것과 동일한 경로로 해석됩니다 — 그리고
프로젝트의 provider가 `codex`일 때는 claude config 디렉터리에 대해 아무 말도
하지 않습니다.

## 공유 디스크와 `flock(2)`

Tesserae의 모든 동시성 보장은 — 무엇보다 compile lock은 — `.tesserae/`를 담고
있는 파일시스템이 `flock(2)`을 강제한다는 전제 위에 서 있습니다. NFS와 SMB에서
그것은 설정에 따라 달라집니다: 동작하는 lock 데몬이 없으면 `flock`은 조용히
no-op으로 퇴화할 수 있고, 그러면 두 호스트가 각자 배타적 lock을 쥐고 있다고
믿으면서 같은 프로젝트를 동시에 compile합니다.

`filesystem_locking`은 단일 호스트가 판단할 수 있는 것만 보고합니다:
프로젝트를 받치는 파일시스템 종류, 그것이 네트워크 파일시스템인지, 그리고
`flock` 획득이 애초에 성공하는지. 네트워크 파일시스템이면 경고합니다.

이 점검은 호스트 간 강제를 **증명할 수 없으며**, 증명한다고 주장하지도
않습니다. 한 호스트가 lock을 잡았다는 사실은 두 번째 호스트가 그 lock을 잡지
못하도록 막히는지에 대해 아무것도 말해 주지 않습니다. 여러 대의 머신에서 공유
스토리지를 상대로 Tesserae를 돌린다면, compile lock에 기대기 전에 실제
하드웨어에서 직접 시험해 보세요.

## `--fix` 정책

- `--fix`는 위에서 SAFE로 표시된 점검**만** 실행한 뒤 재감지하여 보고서가
  수정 이후 상태를 반영하도록 합니다.
- 모든 수정은 멱등적입니다: `doctor --fix`를 두 번 실행하면 두 번째 실행은
  깨끗하게 통과합니다.
- Doctor는 **프로세스를 절대 죽이지 않으며 살아 있는 compile lock을 절대
  제거하지 않습니다** — 잡혀 있는 lock은 소유 pid와 호스트와 함께 보고되고
  그대로 남겨집니다.
- Doctor는 **다른 머신의 pidfile을 절대 건드리지 않습니다.** 공유
  스토리지에서 로컬 프로세스 테이블은 다른 호스트가 기록한 pid에 대해 아무것도
  말해 주지 않으므로, `daemon.<other-host>.pid`는 무조건 보고하고
  건너뜁니다 — liveness 확인을 위해 읽지조차 않습니다. 제거 대상이 될 수 있는
  것은 이 호스트 자신의 pidfile뿐입니다.
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
