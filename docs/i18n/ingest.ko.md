# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
단일 문서 파일 또는 URL을 지식 베이스에 병합합니다.

## 사용법

    tesserae ingest <input>...  [--title T] [--source-kind K] [--full] [--dry-run]

`<input>`은 하나 이상의 로컬 파일 경로 또는 `http(s)` URL입니다. URL은 가져와서 markdown으로
변환되고, 출처(provenance) front-matter(`source_url`, `fetched_at`, `content_sha256`,
감지되면 `arxiv_id`)와 함께 `data/ingested/<slug>.md` 아래에 영속화된 뒤 병합됩니다.
프로젝트 외부의 로컬 파일은 `data/ingested/`로 복사되어 추적되는 소스가 됩니다
(이후의 전체 compile이 이를 동일하게 재현합니다).

URL ingest에는 선택적 extra가 필요합니다:

    pip install tesserae[ingest-url]

## 동작 방식

기본적으로 `ingest`는 새 소스를 증분 compile로 병합합니다 — 전체 코퍼스를 다시 추출하지
않습니다 — 그리고 결과는 전체 compile과 바이트 단위로 동일합니다(증분 경로가 처리할 수
없는 모든 경우에 대해 자동 전체-재compile 폴백이 정확성을 보장합니다).
전체 코퍼스의 전체 재compile을 강제하려면 `--full`을 전달하세요.

## 플래그

- `--full` — 전체 코퍼스의 전체 재compile을 강제.
- `--dry-run` — 가져와서 무엇이 ingest될지 보고; 그래프는 기록하지 않음.
- `--title` — 타이틀 재정의, 순수 URL에 유용.
- `--source-kind` — 소스 분류를 재정의.

## 개념 레이어 (`--extractor`)

Tesserae는 LLM 위키이므로 `compile`은 **기본적으로 개념/클레임 레이어**를
빌드합니다(`--extractor llm`): 설정된 LLM 프로바이더 — `llm_provider`에 따라
**codex / claude / Anthropic API** — 를 통해 각 문서를 읽고 개념, 클레임,
capability, 기술 용어, 근거 스팬(evidence span), 그리고 이들 사이의 타입
엣지를 생성합니다. 이 레이어가 있어야 그래프가 단지 *"어느 파일이 그것을
말했는가"*가 아니라 *"이것이 어떤 아이디어이고 어떻게 연관되는가"*에 답할 수
있습니다.

    tesserae compile                        # LLM concept layer, configured provider
    tesserae compile --llm-provider codex   # force a provider for this run

LLM 백엔드가 설정/인증되어 있지 않으면 compile은 **결정적(deterministic)**
추출기로 강등되고(구조만 — 소스, 섹션, 명시적 링크) 경고를 냅니다. 명시적으로
요청할 수도 있습니다 — 빠르고, 키가 필요 없고, 바이트 단위로 안정적인 CI /
재현 가능 모드입니다:

    tesserae compile --extractor deterministic

### 어떤 계정을 사용할지 지정하기 (`llm_claude_config_dirs`)

`claude` 프로바이더에서 Tesserae는 로그인된 Claude CLI 계정들을 순회한다.
한 계정이 사용량 한도에 걸리면 다음 계정으로 넘어가므로, 남은 작업 전체가
결정론적 추출로 떨어지지 않는다. 기본값은 `~/.claude*` 디렉터리 자동 탐색이다.

**codex** 프로바이더도 동일하게 동작한다. 인증된 `~/.codex*` 홈들(디렉터리에
`auth.json`이 있어야 홈으로 인정된다)을 순회하며, `llm_codex_homes`로 설정한다.
프로바이더마다 별도의 키를 쓰는 이유는 디스크상의 계정 구조가 서로 다르기 때문이다.
Claude CLI 설정 디렉터리와 Codex 홈은 서로 호환되지 않는다:

| 프로바이더 | 설정 키 | 나열 대상 |
|---|---|---|
| `claude` | `llm_claude_config_dirs` | Claude CLI 설정 디렉터리 (`~/.claude*`) |
| `codex`  | `llm_codex_homes`        | Codex 홈 (`~/.codex*`) |

어떤 계정을 어떤 순서로 사용할지 직접 지정하려면 `.tesserae/config.json`(프로젝트)
또는 `~/.tesserae/config.json`(전역)에 `llm_claude_config_dirs`를 설정한다:

```json
{
  "llm_claude_config_dirs": [
    "/Users/you/.claude-work",
    "/Users/you/.claude-personal"
  ]
}
```

이 목록이 최종 권위를 가지며, 목록 밖의 계정은 시도되지 않는다. 또한 이 설정은
**주변 환경의 `CLAUDE_CONFIG_DIR`보다 우선한다**. 이 변수는 Claude Code 세션이
띄우는 모든 프로세스에 상속되므로, 그대로 두면 컴파일 전체가 그 세션 하나의
할당량에 묶인다. 설정이 없으면 `CLAUDE_CONFIG_DIR`이 첫 번째 시도 계정으로 쓰인다.

설정된 모든 계정이 사용량 한도에 도달하면, 컴파일은 문서마다 다시 묻는 대신 남은
실행 동안 LLM 호출을 멈추고 해당 문서들을 `fallback: true`로 표시한 뒤 그 사실을
알린다. 한도가 초기화된 뒤 전체 재컴파일 없이 복구하려면:

    tesserae compile --changed-only --retry-fallbacks


**비용 인지형 (`selective-llm`)** — 매칭되는 문서만 LLM을 통해 라우팅하고
나머지는 결정적으로:

    tesserae compile --extractor selective-llm \
      --llm-include "docs/**/*.md" --llm-limit 20

같은 플래그가 `tesserae extract <paths>`(독립 실행)와
`tesserae compile <paths>`(임시 경로 ingest)에서도 동작합니다.

**튜닝:**

- `--llm-provider codex|claude|anthropic` — 프로바이더 재정의(기본값: 설정의
  `llm_provider`).
- `--llm-model` — 추출기용 모델(기본값: 프로바이더의 기본 모델).
- `--llm-include <glob>` — `selective-llm`에서 어떤 파일이 LLM을 거칠지
  (여러 개 지정하려면 반복; 패턴은 절대 경로의 어디에서든 매칭됨, 예:
  `"*docs/superpowers*"`).
- `--llm-limit N` — LLM에 도달하는 파일 수 상한(나머지는 결정적 처리 유지).

**기본 타임아웃 없음.** 큰 설계 문서는 많은 JSON을 생성하며 수 분이 걸릴 수
있습니다; 추출은 조용히 잘리는 대신 완료까지 실행됩니다(타임아웃은 옵트인
전용).

**실제 코퍼스에서 견고함.** 노이즈가 많거나 느린 문서 하나가 전체 compile을
중단시키는 일은 없습니다: 한 문서에 대한 LLM 실패(인증, 오류, 파싱 불가한
생성물)는 *그* 문서에 한해 결정적 베이스라인으로 폴백하고, 통제 어휘를 벗어난
엣지나 노드 타입은 버려지며, 콘텐츠 키 기반 캐싱 덕분에 변경되지 않은 문서의
재compile은 이전 추출을 재사용합니다.

> `claude-cli` / `selective-claude` 추출기 이름(그리고 `--claude-*` 플래그)은
> `llm` / `selective-llm`(그리고 `--llm-*`)의 폐기 예정(deprecated) 별칭입니다;
> 여전히 동작하지만 폐기 예정 안내를 출력합니다.

## compile 범위 관리 (`sources`)

`tesserae compile`(인자 없음)은 프로젝트의 `sources` 목록에 있는 디렉터리를
compile합니다. 그 목록은 — **로컬 또는 글로벌** — `sources` 하위 명령으로
관리합니다:

    tesserae sources add docs                 # local: inside the project (stored project-relative)
    tesserae sources add /data/shared-notes   # global: an absolute path outside the project
    tesserae sources add ../sibling-project   # global: a relative path that escapes the root
    tesserae sources list                     # shows each source tagged local/global, flags missing
    tesserae sources remove docs

프로젝트 내부의 경로는 프로젝트 상대 경로로 저장되고(이식 가능), 외부의 것은
절대 경로로 저장됩니다. 둘 다 compile 시점에 해석되므로 글로벌 소스도 로컬
소스와 똑같이 compile됩니다. (추가는 해석된 위치 기준으로 중복 제거되므로,
같은 디렉터리의 절대 경로 형태와 `../` 상대 경로 형태가 이중 계산되는 일은
없습니다.)

## 관련 명령

- `tesserae compile`(인자 없음)은 추적되는 전체 코퍼스를 다시 추출합니다.
- `tesserae ingest <x>`는 소스 하나를 증분으로 추가합니다.
- `tesserae code ingest`는 Python 소스에서 코드 그래프를 생성합니다(별개의 명령). `codegraph`에 대한 `external_tools` 항목으로 코드 레이어를 활성화한 프로젝트에서 사용합니다.
