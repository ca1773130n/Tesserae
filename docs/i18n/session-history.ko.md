# Harness 세션 이력

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae는 로컬 AI 에이전트 트랜스크립트를 가져와 정적 사이트의 `sessions/` 섹션 아래에 프로젝트 메모리로 렌더링할 수 있습니다.

이 기능은 의도적으로 `export harness`와 분리되어 있습니다:

- `export harness`는 Claude Code, Codex, Gemini, Cursor, Kiro, OpenCode 같은 도구를 위한 아웃바운드 컨텍스트입니다.
- `sessions ...`는 인바운드 이력입니다: 현재 프로젝트의 이전 Claude Code/Codex 세션을 정규화하고, `.tesserae/harness_sessions/` 아래에 저장하며, `export site`가 세션 인덱스/상세 페이지를 게시할 수 있게 합니다.

## 두 가지 진입 경로: 배치 가져오기와 라이브 모니터링

세션 수집은 더 이상 배치 전용이 아닙니다. 같은 정규화 저장소로 들어가는 두
경로가 있습니다:

- **배치 가져오기(batch import)** — `sessions discover/import`는 요청 시
  트랜스크립트 루트를 스캔하고 일회성으로 기록합니다. 이 페이지의 아래에서
  그 플로우를 설명합니다.
- **라이브 모니터링** — 슈퍼바이저 데몬(`tesserae engine`)은 *이 프로젝트
  자신의* Claude Code 및 Codex 트랜스크립트를 감시하고 새 턴이 도착하는 대로
  수집하는 `SessionTailer`를 실행합니다. 각 틱은 영속화된 파일별 바이트
  오프셋으로 이동해 새 바이트만 읽고, 완결된 턴을 SQLite
  `HarnessSessionsDB`(`.tesserae/sqlite.db`)에 저장한 **후에** 디바운스된
  재compile을 큐에 넣으므로, compile은 항상 일관된 상태를 읽습니다. 테일러는
  프로젝트 자신의 세션으로 범위가 한정되며(Claude
  `projects/<slug>/*.jsonl`; Codex는 cwd로 필터링), 재시작 후에는 턴을
  재생하지 않고 저장된 오프셋에서 재개합니다.

라이브 루프 실행:

```bash
tesserae engine        # watch sources, coalesce bursts, auto-recompile
tesserae engine --once # single drain cycle then exit (deterministic)
```

`tesserae refresh`는 장수 감시자를 시작하지 않고 동일한 ingest → compile →
프로젝션 파이프라인을 프로세스 내에서 한 번 실행합니다(harness-session 발견
스캔을 건너뛰려면 `--no-sessions` 전달).

## 프라이버시 모델

두 수집 경로 모두 명시적입니다: 라이브 테일러는 `tesserae engine`을 살려 두는
동안에만 실행되고, 배치 발견은 `--import`가 있어야만 기록합니다. 일반적인
`tesserae compile`이나 `tesserae export site`는 `.tesserae/harness_sessions/`의
이미 정규화된 세션과 `.tesserae/sqlite.db`의 라이브 레코드를 읽을 뿐, 스스로
비공개 harness 트랜스크립트 디렉터리를 몰래 긁어가지 않습니다.

가져온 세션 레코드는 로컬 프로젝트 아티팩트입니다. 공개 사이트를 게시하기 전에 검토하세요 — 특히 트랜스크립트에 시크릿, 비공개 경로, 고객 데이터, 미공개 코드가 포함될 수 있다면 더욱 그렇습니다.

## 로컬 세션 발견 및 가져오기

프로젝트 루트에서:

```bash
tesserae sessions discover --import
```

발견(discovery)은 현재 프로젝트 작업 디렉터리에 속하는 로컬 Claude Code 및 Codex 트랜스크립트 루트를 스캔합니다. 특정 config 디렉터리를 스캔하려면 `--root`를 사용하고, 발견 범위를 제한하려면 `--harness`를 반복하세요:

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

`--import`가 없으면 발견은 정규화된 세션 레코드를 기록하지 않고 찾은 것을 출력만 합니다.

## 정규화된 JSON 직접 가져오기

다른 도구가 이미 정규화된 `HarnessSession` JSON을 생성했다면, 파일 하나 또는 파일 목록을 가져오세요:

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

각 입력은 세션 객체 하나 또는 세션 객체 목록을 담을 수 있습니다.

## 저장소는 어떻게 기록되는가

두 진입 경로 모두 `.tesserae/harness_sessions/`에 기록하지만, 기록 방식이 다릅니다:

- `sessions import <path>`는 **병합합니다**. 기존 레코드는 유지되고, 파일명이 같은 레코드는 그 자리에서 덮어쓰입니다.
- `sessions discover --import`는 **스캔한 루트 내에서 교체합니다**. 스캔한 harness 루트 아래에 있는 트랜스크립트의 레코드는 더 이상 스캔에서 찾을 수 없으면 제거되므로, 파일명 스킴을 바꾸거나 중복 제거 가져오기가 고아 페이지와 검색 항목을 남길 수 없습니다. 다른 곳에서 온 레코드는 그 범위를 벗어나 있으며 유지됩니다.

스코핑이 중요한 이유는 로컬 harness 규약 밖에서 Tesserae에 데이터를 공급할 때입니다 — 오케스트레이터가 자신의 에이전트 세션을 내보내거나, CI 작업이 다른 머신에서 트랜스크립트를 가져오거나, 마이그레이션 스크립트가 있을 때 말입니다. 이러한 레코드는 로컬 스캔이 유추할 수 없는 속성을 가지고 있으며, 로컬 스캔은 이들에 대한 권한이 없습니다. 0.28.5까지는 비어 있지 않은 discovery가 *전체* 저장소를 제거했으므로, 이들은 조용히 지워졌으며, 플러그인의 `SessionEnd` 훅은 모든 세션 종료에서 discovery를 실행합니다([#104](https://github.com/ca1773130n/Tesserae/issues/104)).

알면 좋은 두 가지 동작:

- 빈 discovery는 절대 제거하지 않습니다. 아무것도 찾지 못한 스캔 — 잘못된 `HOME`, 분리된 harness 루트 — 지우기 대신 병합합니다.
- 레코드를 제거하는 discovery는 가져오기 개수 옆에 제거 개수를 출력하므로, 저장소가 성장만 보고하는 라인 내에서 축소될 수 없습니다.

## 가져온 세션 나열

```bash
tesserae sessions list
```

세션은 다음 아래에 저장됩니다:

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

라이브로 모니터링된 세션은 SQLite `HarnessSessionsDB`(`.tesserae/sqlite.db`)에
추가로 추적되며, 여기에는 테일러가 재개에 사용하는 파일별 읽기 오프셋도
영속화됩니다. `tesserae sessions list`는 통합된 뷰를 보고합니다.

## 정적 세션 페이지 빌드

세션을 가져온 후 사이트를 다시 빌드하세요:

```bash
tesserae export site
```

사이트는 다음을 출력합니다:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

생성된 사이트는 글로벌 레일, 홈 Browse 카드, 검색 항목, 그리고 각 세션 상세 페이지의 브레드크럼 트레일에서 Sessions를 링크합니다.

## 빠른 트랜스크립트 검색 (memex)

사이트를 `tesserae serve`로 서빙하면, **sessions 대시보드**에 인덱싱된 모든
Claude/Codex 트랜스크립트에 대한 전문(full-text) 검색 상자가 생깁니다.
[`nicosuave/memex`](https://github.com/nicosuave/memex)(BM25)가 백엔드입니다.
결과는 `project · role · date · score`와 매칭 스니펫을 보여줍니다.

```bash
cargo install --git https://github.com/nicosuave/memex --locked   # or: tesserae config deps --install memex
memex index                                                        # build the index once
tesserae serve                                                     # search box appears on /sessions
```

이것은 **선택적이며 우아하게 동작합니다**: `memex` 바이너리(또는 인덱스)가
없으면 상자는 명확하고 실행 가능한 메시지를 표시하고 대시보드의 나머지는
영향받지 않습니다. 검색 엔드포인트(`GET /api/transcript-search`)는
same-origin/loopback 호출자로 제한되어, 방문한 웹 페이지가 로컬 이력을 탐침할
수 없습니다.

## 세션 상세 페이지 레이아웃

세션 상세 페이지는 독립된 트랜스크립트 덤프가 아니라 공유 정적 사이트 셸을 사용합니다. 다음을 포함합니다:

- 히어로와 스탯 스트립;
- 상위 수준 요약;
- 타임라인과 크기 메타데이터;
- 있을 경우 decisions, files, commands, tools, errors;
- 접힌 서브에이전트 트리;
- 턴 단위 user/assistant 대화;
- 직전 assistant 턴 아래에 부착된 접힌 tool-use 블록;
- `#turn-N` 앵커로 링크하는 왼쪽 대화 레일.

대화 markdown은 사이트 markdown 렌더러를 통해 렌더링됩니다. 인라인 코드, 명시적 명령/태그 마크업, 경로, 파일명, 해시태그 같은 시맨틱 표면은 컴팩트한 칩으로 장식됩니다; 대문자로 시작하는 임의의 명사는 자동으로 칩 처리되지 않습니다.

현재 트랜스크립트 타이포그래피:

| 표면 | 셀렉터 | 크기 |
|---|---|---|
| 대화 markdown prose | `.session-turn-text`, prose children | `8px` |
| 일반 대화 코드 펜스 | `.session-turn-text pre` | `10px` |
| Bash/shell 펜스 코드 내용 | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool details/summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-use 헤더 | `.session-tool-use-header` | `8px` |
| Tool 페이로드 텍스트 | `.session-tool-use-text` | `6px` |

## 세션 게시 체크리스트

세션을 포함하는 공개 사이트를 배포하기 전에:

1. `tesserae sessions list`를 실행하고 개수가 예상과 일치하는지 확인.
2. `.tesserae/harness_sessions/`에 민감한 내용이 없는지 검사.
3. `tesserae export site`로 재빌드.
4. `sessions/index.html`과 세션 상세 페이지를 최소 하나 로컬에서 열어보기.
5. tool 블록이 기본적으로 접혀 있고 원시 tool 페이로드가 게시 가능한 수준인지 확인.
6. 소스 트리가 커밋된 뒤 `tesserae export site --deploy`로 배포.
