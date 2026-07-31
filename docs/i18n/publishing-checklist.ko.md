# 게시 체크리스트

<!-- translations:start -->
<p align="center"><a href="../publishing-checklist.md">English</a> · <a href="publishing-checklist.ko.md">한국어</a> · <a href="publishing-checklist.zh.md">中文</a> · <a href="publishing-checklist.ja.md">日本語</a> · <a href="publishing-checklist.ru.md">Русский</a> · <a href="publishing-checklist.es.md">Español</a> · <a href="publishing-checklist.fr.md">Français</a> · <a href="publishing-checklist.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae를 공개적으로 소개하기 전에 이 체크리스트를 사용하세요.

## 저장소 위생

- [ ] README가 프로젝트가 무엇이며 어떤 문제를 해결하는지 설명한다.
- [ ] 새 shell에서 설치 명령이 동작한다.
- [ ] Quickstart가 `python3 -m`이 아니라 `tesserae`를 사용한다.
- [ ] 아키텍처 문서가 원시 증거 → 그래프 → 투영을 설명한다.
- [ ] 기능 맵이 미래 작업을 과장하지 않고 구현된 기능을 나열한다.
- [ ] 세션 기록 문서가 명시적 가져오기, 프라이버시 검토, 생성된 routes, transcript typography를 설명한다.
- [ ] Self-dogfood 데모가 실행되고 문서화되었다.
- [ ] 생성된 아티팩트가 재현 가능하며 무시되거나 의도적으로 게시된다.

## 검증

```bash
.venv/bin/pytest tests/ -x          # 실패가 하나라도 있으면 중단 — 빨간 빌드는 절대 출시하지 않는다
./scripts/install.sh --help
tesserae init --help
tesserae compile --help
tesserae context --help     # 온디맨드 컨텍스트 컴파일러
```

### 데모 빌드 스모크 (수동 — CI에서 다루지 않음)

릴리스마다 손으로 실행한다. 예전에는 `main` 푸시마다 도는 `build-demo` CI 잡과
동일했지만 그 워크플로는 제거되었다. 따라서 이 컴파일 경로를 확인하는 곳은 이제
여기뿐이다. `tests.yml`은 유닛 스위트를 실행할 뿐 `init` → `compile` → `export site`를
끝까지 실행하지는 않는다.

결정론적 추출기(LLM 호출 없음, API 키 불필요)로 Tesserae를 자체 소스 트리에 대해
컴파일하고 사이트를 빌드한다:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## 릴리스 흐름

`release` 스킬(`.claude/skills/release/SKILL.md`)이 이 흐름을 주도하며, 그것이
정본이다 — 둘이 어긋나면 스킬이 이기고, 고쳐야 할 것은 이 목록이다.

- [ ] `main`에서, 작업 트리 클린, `git pull --ff-only origin main`.
- [ ] 테스트 그린(`uv run pytest tests/ -x`, 약 9분). 데모 빌드 스모크는 수동이며
      더 이상 CI가 다루지 않는다 — 위 참조.
- [ ] **세 개 모두**의 버전 파일을 올린다: `pyproject.toml`,
      `.claude-plugin/plugin.json`, `npm/package.json`. 서로 그리고 태그와
      일치해야 한다. npm 래퍼는 `tesserae==<npm 버전>`을 고정한다.
- [ ] 릴리스 노트와 7개 번역을 작성한다. `uv run pytest
      tests/test_docs_i18n.py -q`가 그린이어야 한다.
- [ ] `uv lock`을 실행하고 `uv.lock`을 스테이징한다 — 이 파일은 `tesserae`를 자기
      버전으로 고정하며, CI가 `uv sync --locked`를 돌리므로 락이 낡으면 실패한다.
- [ ] `git log v<prev>..HEAD`에서 뽑은 한 단락 변경 로그와 함께
      `release: vX.Y.Z`를 커밋한다.
- [ ] **PR을 연다 — `main`은 보호되어 있어 직접 푸시가 거부된다**(`GH006`;
      `enforce_admins` 켜짐, 체크 3개 필수). 세 레인이 모두 그린일 때만 머지한다.
      빨간 빌드에는 절대 태그를 달지 않는다.
- [ ] 머지된 커밋에 태그를 달고(`git tag -a vX.Y.Z -m "vX.Y.Z"`) 태그를 푸시한다.
      여기가 되돌릴 수 없는 지점이다: 태그 푸시가 npm OIDC 워크플로를 발동시키고,
      한 번 게시된 npm 버전은 영원히 재사용할 수 없다.
- [ ] GitHub 릴리스를 게시한다.
- [ ] **PyPI 게시 — 선택이 아니라 필수.** 태그의 깨끗한 worktree에서 빌드해
      업로드한 뒤, `--no-cache-dir`로 새 venv 설치를 검증한다(pip이 인덱스를
      캐시해서, 이미 올라간 버전을 없다고 보고한다).
- [ ] **npm 게시 — 필수.** 태그 푸시 시 OIDC로 자동 실행된다. run을 지켜보고
      `npx -y @jokerized/tesserae@X.Y.Z status`로 스모크한다. 손으로 publish하지
      말 것 — 토큰이 없고, 수동 게시는 provenance 증명을 건너뛴다.

### GitHub Pages

**이제 어떤 워크플로도 사이트를 배포하지 않는다.** `build-demo` 워크플로가 `main`
푸시마다 배포했지만 제거되었다. 그것이 마지막으로 배포한 사이트는 여전히 서빙되고
있고 README도 여전히 라이브 데모로 링크한다 — 즉 그 페이지는 마지막 `build-demo`
실행 시점에 얼어붙은 스냅샷이지 현재 `main`의 모습이 아니다.

다시 게시하려면 수동 `tesserae export site`와 업로드, 또는 새 워크플로가 필요하다.
어느 쪽이든 의도적으로 결정할 것: 코드와 조용히 어긋나는 데모 링크는 데모 링크가
없는 것보다 나쁘다.

## Self-dogfood

통합 옵트인(RAG-Anything)은 이제 CLI 플래그가 아니라
**대화형 마법사 프롬프트**입니다. 마법사를 실행하고 답하세요:

```bash
tesserae init \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts
# 마법사가 물어보면:
#   - RAG-Anything 활성화, 설치: 예, 파서: mineru, 이후 실행: 예
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

완전한 비대화형 실행에는 `tesserae init --yes`(모든 통합 OFF)를 사용한 뒤,
`.tesserae/config.json`에서 각 통합을 활성화하고(마법사는 이를 `memory_backends`와
`external_tools`(RAG-Anything) 키 아래에 씁니다)
컴파일하기 전에 각 통합에 대해 `tesserae integrations refresh <name>`을 실행하세요.
정확한 설정 키는 통합 문서를 참조하세요.

## 데모 설명 포인트

- Tesserae는 일반적인 명사구 그래프가 아닙니다. 제어된 ontology를 사용합니다.
- 연구 및 개발 코드는 인프라를 공유하지만 서로 다른 schema를 유지합니다.
- Markdown과 HTML은 투영이며, 권위 있는 진실 저장소가 아닙니다.
- 기본 경로는 로컬이며 API 키 없이 사용하기 쉽습니다.
- 에이전트 harness와 MCP는 코딩 에이전트가 그래프를 사용할 수 있게 합니다.
- 가져온 harness 세션 페이지는 transcript 발견을 명시적으로 유지하면서 이전 Claude Code/Codex 작업을 검색 가능한 프로젝트 메모리로 바꿉니다.
