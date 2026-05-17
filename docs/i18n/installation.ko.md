# 설치

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki는 PyPI에 게시되어 있으며, 사용자가 `python3 -m llm_wiki.cli`를 직접 실행하지 않아도 되도록 셸 명령을 제공합니다.

## PyPI에서 설치(권장)

```bash
pip install llm-research-wiki
```

끝입니다. `pip`가 `PATH`에 세 개의 콘솔 스크립트를 등록합니다.

```bash
llm_wiki --help
llm-wiki --help
llm_wiki_mcp --help
```

문서에서 사용하는 표준 명령은 `llm_wiki`입니다. `llm-wiki`(대시 포함)는 별칭입니다. `llm_wiki_mcp`는 MCP 서버를 시작합니다.

> **pipx도 괜찮습니다.** CLI 도구를 각각 격리된 venv에 두고 싶다면:
> ```bash
> pipx install llm-research-wiki
> ```

## 업그레이드

```bash
pip install --upgrade llm-research-wiki
```

## 선택적 통합

기본 wheel은 의도적으로 가볍게 유지됩니다. 설정 마법사는 사용자가 요청할 때만 더 무거운 동반/런타임 구성 요소를 설치할 수 있습니다.

```bash
# Understand Anything companion graph + Cognee runtime memory
llm_wiki project setup \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

고급 워크플로에서는 수동 패키지 설치도 계속 사용할 수 있습니다.

```bash
pip install kuzu cognee graphiti-core
```

- `kuzu` — Kuzu 그래프 영속성.
- `cognee` — 런타임 Cognee add/cognify 워크플로. 설정은 `{python} -m pip install cognee`를 저장하고 Cognee가 없으면 한 번 재시도합니다.
- Understand Anything — `--install-understand-anything`이 선택되면 upstream 설치 프로그램으로 설치됩니다. LLM-Wiki는 사용자에게 셸 명령을 만들라고 요구하는 대신 관리형 refresh wrapper를 저장합니다.
- `graphiti-core` — 실시간 Graphiti/Neo4j 동기화. `export-graphiti`와 `sync-graphiti --dry-run`은 이것 없이도 동작합니다.

Anthropic 기반 합성 경로는 extras 마커를 사용합니다.

```bash
pip install "llm-research-wiki[synthesis-llm]"
```

## 소스에서 설치(기여자용)

코드베이스를 수정하려면 editable checkout으로 설치하세요.

```bash
git clone https://github.com/ca1773130n/LLM-Wiki.git
cd LLM-Wiki
pip install -e .
```

편의 설치 프로그램도 포함되어 있습니다. clone하고, 프로젝트 로컬 `.venv`를 만들고, `pip install -e .`를 실행한 뒤 wrapper를 `~/.local/bin`에 둡니다.

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

유용한 플래그(`./scripts/install.sh --help`):

| 옵션 | 목적 |
| --- | --- |
| `--dir PATH` | `PATH`의 checkout을 설치하거나 업데이트합니다. |
| `--branch NAME` | 특정 브랜치를 설치합니다. |
| `--repo URL` | Git 저장소 URL을 재정의합니다. fork나 로컬 smoke test에 유용합니다. |
| `--bin-dir PATH` | 명령 wrapper를 `~/.local/bin`이 아닌 위치에 씁니다. |
| `--no-venv` | `.venv`를 만들지 않고 현재 Python 환경에 설치합니다. |
| `--skip-shell-config` | `.zshrc` / `.bashrc` 편집을 피합니다. |

`--skip-shell-config`를 사용했다면 셸을 다시 시작하거나 다음을 실행하세요.

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 설치 확인

```bash
llm_wiki project init --help
llm_wiki project compile --help
llm_wiki project build-site --help
```
