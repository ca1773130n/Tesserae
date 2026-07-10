# 일일 세션 청크 — `.tesserae/session_chunks.db`

<!-- translations:start -->
<p align="center"><a href="../session-chunks.md">English</a> · <a href="session-chunks.zh.md">中文</a> · <a href="session-chunks.ja.md">日本語</a> · <a href="session-chunks.ru.md">Русский</a> · <a href="session-chunks.es.md">Español</a> · <a href="session-chunks.fr.md">Français</a> · <a href="session-chunks.de.md">Deutsch</a></p>
<!-- translations:end -->
윈도우 기반 세션 쿼리 — `tesserae summary`, `tesserae decisions`, 그리고
`ask` 플래너의 activity 액션 — 는 과거에는 호출할 때마다 윈도우 내 모든
Claude Code / Codex 트랜스크립트를 다시 파싱했습니다. 일일 청크 저장소는
정규화된 각 턴을 KST 일자 레이블로 버킷팅하여 **한 번만** 영속화하므로,
완전히 커버된 과거 일자는 원시 재스캔 대신 SQLite에서 서빙됩니다. 실제 수천
세션 규모의 코퍼스에서 측정한 결과, 윈도우 요약이 **약 20배 빨라집니다**.

저장소는 SQLite 파일 하나, `.tesserae/session_chunks.db`입니다(WAL, 작업당
단명 연결): 일자로 인덱싱된 `turns` 테이블, 어떤 `(day, harness)` 쌍이
완결되었는지 기록하는 `day_coverage` 테이블, 스키마 버전을 담는 `meta`
테이블로 구성됩니다.

## 무엇이 기록하는가

1. **라이브 — 엔진 테일러(tailer).** `tesserae engine`이 실행되는 동안 세션
   테일러는 턴을 테일링하는 대로 폴링마다 저장소에 덧붙이고, 영향받은 일자의
   커버리지를 upsert합니다(`source: "tailer"`). 쓰기 경로는 append-only이고,
   재전달된 턴에 대해 멱등적이며, 데몬 루프로 예외를 절대 전파하지 않습니다.
   **SessionEnd hook 기록자는 의도적으로 없습니다** — 백그라운드로 돌린
   SessionEnd 기록자는 계속 쌓입니다(기록된 실패 모드).
2. **백필(backfill).** 두 진입점이 기존 트랜스크립트를 순회하며 이력을
   채웁니다(`source: "backfill"`):
   - `tesserae refresh`는 sessions-import 단계의 일부로 백필을 자동
     실행하므로, 업그레이드 후 첫 refresh가 별도 조치 없이 저장소를
     채웁니다.
   - `tesserae sessions chunk-backfill [--since YYYY-MM-DD]`는 이를
     명시적으로 실행합니다; `--since`는 얼마나 과거까지 순회할지 제한합니다
     (기본값: 전체 이력).

   백필은 `.tesserae/session_chunks.lock`에 대해 skip-if-held 시맨틱을 갖는
   **논블로킹** flock을 잡습니다 — 동시 백필(또는 이미 lock을 잡고 있는
   엔진)이 있으면 두 번째 호출자는 대기열에 서는 대신 깔끔하게 건너뜁니다.
   백필 upsert는 `(session_path, ts, role, hash(text))`를 키로 하므로, 테일러
   행과 백필 행은 서로 절대 중복되지 않습니다. 증분 백필의 1일 오버랩은
   일자 커버리지가 처음 확정된 뒤에 도착한 턴을 치유합니다.

## 무엇이 읽는가

빠른 경로는 단일 스캔 관문
(`activity_summary.iter_project_transcripts` / `scan_messages`)에 자리하므로,
다운스트림 전부가 이를 투명하게 물려받습니다:

- `tesserae summary` (내장된 decisions 수집 포함)
- `tesserae decisions`
- `tesserae ask` — 플래너의 `activity_summary` / `decisions` 액션
- MCP `activity_summary`와 `query_decisions`
- 라이브 세션 뷰

## 커버리지 규칙: 오늘은 항상 원시 스캔

윈도우가 청크에서 서빙되는 것은 다음이 **모두** 성립할 때뿐입니다:

1. KST에 정확히 정렬된 단일 일자일 것;
2. 그 일자가 **엄격하게 오늘 이전**일 것 — 오늘은 아직 기록 중이므로 항상
   원시 트랜스크립트 스캔을 사용합니다;
3. 그 일자에 요청된 **모든** harness에 대해 `day_coverage` 행이 존재할 것.

그 외에는 해당 윈도우에 대해 원시 스캔으로 폴백합니다.

## 원시 스캔 폴백 보장

청크 저장소는 가속기일 뿐, 결코 진실의 원천(source of truth)이 아닙니다:

- 어떤 DB 오류든, 파일 누락/손상이든, `schema_version` 불일치든 청크 경로는
  **아무것도** 내놓지 않습니다 — 호출자의 원시 트랜스크립트 스캔이 이전과
  똑같이 진행됩니다. 스키마 불일치는 저장소를 삭제하고 빈 상태로 재구축하며,
  커버리지도 함께 사라지므로 폴백은 계속 올바르게 동작합니다.
- 커버리지 없는 일자(예: 엔진이 실행되지 않았고 백필도 없었던 경우)는
  조용히 느린 경로를 탑니다. 올바르지만 속도 향상은 사라집니다 —
  `tesserae doctor`가 최근 윈도우의 커버리지 공백을 보고하고
  `tesserae sessions chunk-backfill`을 안내합니다
  ([doctor.ko.md](doctor.ko.md) 참조).
- **패리티 불변성:** 완전히 커버된 일자에 대해, 청크로 서빙된 턴은 원시
  스캔이 산출했을 결과와 동일합니다(같은 timestamp, role, name, text, 세션
  키, harness).

## 운영 참고 사항

- `tesserae engine`을 계속 실행해 두면 과거 일자가 라이브로 커버 상태를
  유지합니다; 그렇지 않으면 가끔의 `tesserae refresh`(또는 명시적
  `chunk-backfill`)가 공백을 메웁니다.
- 저장소는 프로젝트별이며 `.tesserae/` 아래에 있고 언제든 안전하게 삭제할 수
  있습니다 — 다음 백필이 재구축하며, 그동안 리더는 원시 스캔으로
  폴백합니다.
