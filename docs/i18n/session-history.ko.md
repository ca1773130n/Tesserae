# Harness 세션 기록

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae는 로컬 AI-agent transcript를 가져와 정적 사이트의 `sessions/` 섹션 아래 프로젝트 메모리로 렌더링할 수 있습니다.

이 기능은 의도적으로 `export-agent-harness`와 분리되어 있습니다.

- `export-agent-harness`는 Claude Code, Codex, Gemini, Cursor, Kiro, OpenCode 같은 도구에 전달하는 outbound context입니다.
- `project sessions ...`는 inbound history입니다. 현재 프로젝트의 이전 Claude Code/Codex 세션을 정규화하고 `.tesserae/harness_sessions/` 아래 저장하며, `project build-site`가 세션 index/detail 페이지를 게시하게 합니다.

## 두 가지 경로: 배치 가져오기와 라이브 모니터링

세션 수집은 더 이상 배치 전용이 아닙니다. 동일한 정규화 저장소로 들어가는 두 가지 경로가 있습니다.

- **배치 가져오기** — `project sessions discover/import`는 요청 시 transcript root를 스캔하고 한 번에 기록합니다. 이 경로는 아래에서 설명합니다.
- **라이브 모니터링** — supervisor daemon(`project engine`, alias `project daemon`)이 `SessionTailer`를 실행하여 *이 프로젝트 자체의* Claude Code 및 Codex transcript를 감시하고 새 turn이 들어오는 대로 수집합니다. 매 tick마다 파일별로 영속화된 byte offset으로 seek하여 새로 들어온 byte만 읽고, debounce된 재컴파일을 enqueue하기 **전에** 완전한 turn을 SQLite `HarnessSessionsDB`(`.tesserae/sqlite.db`)에 저장하므로 컴파일은 항상 일관된 상태를 읽습니다. tailer는 프로젝트 자체 세션으로 범위가 제한되며(Claude `projects/<slug>/*.jsonl`; Codex는 cwd로 필터링) 재시작 후에는 저장된 offset에서 재개하여 turn을 다시 재생하지 않습니다.

라이브 루프 실행:

```bash
tesserae project engine        # 소스 감시, 버스트 병합, 자동 재컴파일
tesserae project engine --once # 단일 drain 사이클 후 종료(결정적)
```

`tesserae project refresh`는 동일한 ingest → compile → project 파이프라인을 장기 watcher 없이 in-process로 한 번 실행합니다(`--skip-sessions`로 harness 세션 discovery 스캔을 건너뜀).

## 개인정보 모델

두 수집 경로 모두 명시적입니다. 라이브 tailer는 `project engine`/`daemon`을 살려두는 동안에만 실행되고, 배치 discovery는 `--import`로만 기록합니다. 일반 `project compile` 또는 `project build-site`는 `.tesserae/harness_sessions/`의 이미 정규화된 세션과 `.tesserae/sqlite.db`의 라이브 레코드를 읽지만, 스스로 비공개 harness transcript 디렉터리를 몰래 스크랩하지 않습니다.

가져온 세션 레코드는 로컬 프로젝트 산출물입니다. 공개 사이트에 게시하기 전에 검토하세요. transcript에 비밀, 비공개 경로, 고객 데이터, 미공개 코드가 포함될 수 있다면 특히 중요합니다.

## 로컬 세션 검색 및 가져오기

프로젝트 루트에서:

```bash
tesserae project sessions discover --import
```

Discovery는 현재 프로젝트 working directory에 속한 로컬 Claude Code 및 Codex transcript root를 스캔합니다. 특정 config 디렉터리를 스캔하려면 `--root`를 사용하고, discovery를 제한하려면 `--harness`를 반복하세요.

```bash
tesserae project sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

`--import`가 없으면 discovery는 정규화된 세션 레코드를 쓰지 않고 찾은 항목만 출력합니다.

## 정규화된 JSON 직접 가져오기

다른 도구가 이미 정규화된 `HarnessSession` JSON을 만들었다면 파일 하나 또는 파일 목록을 가져올 수 있습니다.

```bash
tesserae project sessions import path/to/session.json path/to/more-sessions.json
```

각 입력은 하나의 세션 객체 또는 세션 객체 목록을 포함할 수 있습니다.

## 가져온 세션 목록 보기

```bash
tesserae project sessions list
```

세션은 아래에 저장됩니다.

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

라이브로 모니터링되는 세션은 SQLite `HarnessSessionsDB`(`.tesserae/sqlite.db`)에도 추적되며, 여기에는 tailer가 재개에 사용하는 파일별 read offset도 영속화됩니다. `project sessions list`는 통합된 뷰를 보고합니다.

## 정적 세션 페이지 빌드

세션을 가져온 뒤 사이트를 다시 빌드하세요.

```bash
tesserae project build-site
```

사이트는 다음을 생성합니다.

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

생성된 사이트는 전역 rail, 홈 Browse 카드, 검색 항목, 각 세션 detail 페이지의 breadcrumb trail에서 Sessions로 연결합니다.

## 세션 detail 페이지 레이아웃

세션 detail 페이지는 독립 transcript dump가 아니라 공유 정적 사이트 shell을 사용합니다. 포함되는 항목:

- hero 및 stat strip;
- 상위 수준 요약;
- timeline 및 size metadata;
- 존재하는 경우 decisions, files, commands, tools, errors;
- 접힌 subagent tree;
- turn별 user/assistant 대화;
- 앞선 assistant turn 아래 붙는 접힌 tool-use block;
- `#turn-N` anchor로 연결되는 왼쪽 conversation rail.

대화 markdown은 사이트 markdown renderer를 통해 렌더링됩니다. inline code, 명시적 command/tag markup, paths, filenames, hashtags 같은 의미 있는 표면은 compact chip으로 꾸며지며, 임의의 대문자 명사는 자동 chip 처리되지 않습니다.

현재 transcript typography:

| Surface | Selector | Size |
|---|---|---|
| 대화 markdown prose | `.session-turn-text`, prose children | `8px` |
| 일반 대화 code fence | `.session-turn-text pre` | `10px` |
| Bash/shell fenced code content | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool details/summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-use header | `.session-tool-use-header` | `8px` |
| Tool payload text | `.session-tool-use-text` | `6px` |

## 세션 게시 체크리스트

세션을 포함한 공개 사이트를 배포하기 전에:

1. `tesserae project sessions list`를 실행하고 개수가 예상과 맞는지 확인합니다.
2. 민감한 내용이 있는지 `.tesserae/harness_sessions/`를 검사합니다.
3. `tesserae project build-site`로 다시 빌드합니다.
4. 로컬에서 `sessions/index.html`과 최소 하나의 세션 detail 페이지를 엽니다.
5. tool block이 기본적으로 접혀 있고 raw tool payload를 공개해도 되는지 확인합니다.
6. source tree가 commit된 뒤 `tesserae project deploy --build`로 배포합니다.
