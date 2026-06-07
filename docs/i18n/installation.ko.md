# 설치

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae는 PyPI에 게시되어 있으며, 사용자가 `python3 -m tesserae.cli`를 직접 실행하지 않아도 되도록 셸 명령을 제공합니다.

## PyPI에서 설치(권장)

```bash
pip install tesserae
```

끝입니다. `pip`가 `PATH`에 두 개의 콘솔 스크립트를 등록합니다.

```bash
tesserae --help
tesserae_mcp --help
```

문서에서 사용하는 표준 명령은 `tesserae`입니다. `tesserae_mcp`는 MCP 서버를 시작합니다(이제 온디맨드 `compile_context` 도구를 제공합니다 — 퀵스타트 참고).

> **pipx도 괜찮습니다.** CLI 도구를 각각 격리된 venv에 두고 싶다면:
> ```bash
> pipx install tesserae
> ```

## 업그레이드

```bash
pip install --upgrade tesserae
```

## 선택적 통합

기본 wheel은 의도적으로 가볍게 유지됩니다. 설정 마법사는 사용자가 요청할 때만 더 무거운 동반/런타임 구성 요소를 설치할 수 있습니다.

```bash
# Understand Anything companion graph + Cognee runtime memory
tesserae project setup \
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
- Understand Anything — `--install-understand-anything`이 선택되면 upstream 설치 프로그램으로 설치됩니다. Tesserae는 사용자에게 셸 명령을 만들라고 요구하는 대신 관리형 refresh wrapper를 저장합니다.
- `graphiti-core` — 실시간 Graphiti/Neo4j 동기화. `export-graphiti`와 `sync-graphiti --dry-run`은 이것 없이도 동작합니다.

Anthropic 기반 합성 경로는 extras 마커를 사용합니다.

```bash
pip install "tesserae[synthesis-llm]"
```

실제 시맨틱 임베딩(v0.5.0부터 기본 검색 경로)은 `semantic` extra로 제공됩니다.

```bash
pip install "tesserae[semantic]"
```

이는 `model2vec`를 설치하고, 가볍고 오프라인에서 동작하며 torch가 필요 없는 정적 모델(약 8 MB `potion-base-8M`, 최초 사용 시 한 번 다운로드)을 내려받습니다. 이 extra가 없으면 하이브리드/임베딩 검색이 비시맨틱 해시 버킷 스텁으로 대체되며 큰 경고를 출력합니다. 따라서 `project ask`, `project context` 또는 MCP `compile_context` 도구를 사용하는 경우 이 extra 설치를 권장합니다.

## 소스에서 설치(기여자용)

코드베이스를 수정하려면 editable checkout으로 설치하세요.

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

편의 설치 프로그램도 포함되어 있습니다. clone하고, 프로젝트 로컬 `.venv`를 만들고, `pip install -e .`를 실행한 뒤 wrapper를 `~/.local/bin`에 둡니다.

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

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
tesserae project init --help
tesserae project compile --help
tesserae project build-site --help
```
