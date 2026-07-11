# 설치

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae는 PyPI에 게시되어 있으며, 사용자가 `python3 -m tesserae.cli`를 직접 실행하지 않아도 되도록 셸 명령을 제공합니다.

## PyPI에서 설치 (권장)

```bash
pip install tesserae
```

이것으로 끝입니다. `pip`가 `PATH`에 두 개의 콘솔 스크립트를 등록합니다:

```bash
tesserae --help
tesserae_mcp --help
```

문서에서의 표준 명령은 `tesserae`입니다. `tesserae_mcp`는 MCP 서버를 시작합니다(이제 온디맨드 `compile_context` 도구를 노출합니다 — Quickstart 참조).

> **pipx도 좋습니다.** CLI 도구를 각자의 격리된 venv에 두는 것을 선호한다면:
> ```bash
> pipx install tesserae
> ```

## 업그레이드

```bash
pip install --upgrade tesserae
```

## 머신 전역 설정 (한 번 설정, 모든 프로젝트)

프로젝트별 설정 대신 Tesserae를 한 번만 설정하고, 선택적 의존성을 하나의
명령으로 설치하세요:

```bash
# Interactive machine-wide setup — pick LLM provider/effort + which deps to install:
tesserae setup
# …or non-interactively (written to ~/.tesserae/config.json) + install everything:
tesserae setup --llm-provider codex --reasoning-effort medium --install all

# Just see / manage optional dependencies
tesserae config deps                     # show what's installed
tesserae config deps --install memex     # fast transcript search (needs cargo)
```

알려진 선택적 의존성: **memex** (빠른 트랜스크립트 검색), **cognee**,
**raganything**. 프로젝트별 `.tesserae/config.json`은
여전히 이 글로벌 기본값을 재정의합니다(해석 순서: env → project → global →
built-in). `tesserae init`도 대화형 설정 중에 memex 설치를 제안합니다.

## 선택적 통합 (프로젝트별)

기본 wheel은 의도적으로 가볍고, 선택적 메모리 백엔드는 **기본적으로
꺼져** 있습니다. `tesserae init`이 프로젝트별 온보딩의 유일한 단계입니다 —
그 마법사가 LLM 프로바이더와 감지된 소스를 선택합니다; 더 무거운
컴패니언/런타임 구성 요소는 `tesserae setup --install …`(또는
`tesserae config deps --install …`)로 머신 전역에 설치하고 프로젝트별로
`.tesserae/config.json`에서 활성화합니다:

```bash
# Machine-wide installs of the optional pieces:
tesserae setup --install raganything --install cognee

# Then per project: enable what you want in .tesserae/config.json
#   memory_backends.raganything.enabled: true
#   memory_backends.cognee.enabled: true        (query via `tesserae query --backend …`)
```

고급 워크플로를 위한 수동 패키지 설치도 여전히 가능합니다:

```bash
pip install kuzu graphiti-core
pip install "tesserae[cognee]"
```

- `kuzu` — Kuzu 그래프 영속화.
- `tesserae[cognee]` — 옵트인 Cognee 런타임 add/cognify 워크플로(기본 비활성화; Codex 패치된 cognify 모드는 제거됨).
- RAG-Anything — `pip install 'raganything[all]'`로 설치(`tesserae setup --install raganything`); Tesserae는 멀티모달 파서 실행을 위한 관리형 refresh 래퍼를 저장합니다.
- `graphiti-core` — 라이브 Graphiti/Neo4j 동기화. `export graphiti`와 `export graphiti --sync --dry-run`은 이것 없이도 동작합니다.

Anthropic 기반 synthesis 경로는 extras 마커를 사용합니다:

```bash
pip install "tesserae[synthesis-llm]"
```

실제 시맨틱 embedding(v0.5.0부터 기본 retrieval 레인)은 `semantic` extra 뒤에 제공됩니다:

```bash
pip install "tesserae[semantic]"
```

이는 `model2vec`을 가져오고 가볍고 오프라인이며 torch가 필요 없는 정적 모델(~8 MB `potion-base-8M`, 첫 사용 시 한 번 다운로드)을 내려받습니다. 이것이 없으면 hybrid/embedding retrieval은 비시맨틱 hash-bucket 스텁으로 폴백하고 큰 경고를 출력하므로, `tesserae ask`, `tesserae context`, 또는 MCP `compile_context` 도구를 사용하는 모든 사용자에게 이 extra 설치를 권장합니다.

모든 파서가 사전 설치된 멀티모달 RAG-Anything 스택은:

```bash
pip install 'tesserae[raganything-all]'
```

> **시스템 사전 요구사항:** `.doc/.docx/.ppt/.pptx/.xls/.xlsx` 파싱에는 호스트에 LibreOffice가 필요합니다. 플랫폼의 패키지 매니저로 설치하세요(예: `brew install --cask libreoffice`, `apt-get install libreoffice`); LibreOffice가 없으면 RAG-Anything은 경고와 함께 Office 문서를 건너뜁니다.

## 소스에서 설치 (기여자용)

코드베이스를 직접 수정하고 싶다면 editable 체크아웃을 설치하세요:

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

편의 설치 스크립트도 번들되어 있습니다 — 클론하고, 프로젝트 로컬 `.venv`를 만들고, `pip install -e .`를 실행하고, 래퍼를 `~/.local/bin`에 배치합니다:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

유용한 플래그 (`./scripts/install.sh --help`):

| 옵션 | 용도 |
| --- | --- |
| `--dir PATH` | `PATH`의 체크아웃을 설치 또는 업데이트. |
| `--branch NAME` | 특정 브랜치를 설치. |
| `--repo URL` | Git 저장소 URL을 재정의. 포크나 로컬 스모크 테스트에 유용. |
| `--bin-dir PATH` | 명령 래퍼를 `~/.local/bin`이 아닌 다른 곳에 기록. |
| `--no-venv` | `.venv`를 만드는 대신 현재 Python 환경에 설치. |
| `--skip-shell-config` | `.zshrc` / `.bashrc` 편집을 피함. |

`--skip-shell-config`를 사용했다면, 셸을 재시작하거나 다음을 실행하세요:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 설치 확인

```bash
tesserae init --help
tesserae compile --help
tesserae export site --help
```
