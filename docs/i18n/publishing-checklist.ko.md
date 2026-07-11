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

### 데모 빌드 스모크 (`build-demo` CI 잡과 동일)

릴리스 흐름과 CI 모두 결정론적 추출기(LLM 호출 없음, API 키 불필요)로 Tesserae를
자체 소스 트리에 대해 컴파일하고 사이트를 빌드한다:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## 릴리스 흐름

`release` 스킬(`.claude/skills/release/SKILL.md`)이 주도한다. 최신 태그는 `v0.5.0`이다.

- [ ] `main`에서 작업 트리가 깨끗하고 `git pull --ff-only origin main`을 실행한다.
- [ ] 테스트 + 데모 빌드 스모크(위)가 통과한다.
- [ ] `pyproject.toml`의 `version = "X.Y.Z"`를 올리고(있다면 `package.json`도 동기화), `git log v<prev>..HEAD`로 만든 한 단락 변경 로그와 함께 `release: vX.Y.Z`를 커밋한다.
- [ ] `git tag -a vX.Y.Z -m "vX.Y.Z"`로 태그하고 커밋 다음에 태그를 푸시한다.
- [ ] CI 그린을 기다린다(`gh run watch <run-id>`) — 빨간 빌드에서는 GitHub 릴리스를 만들지 않는다.
- [ ] GitHub 릴리스를 게시한다. PyPI 게시는 선택 사항(준비되면)이다.

### GitHub Pages

`build-demo` 워크플로(`main` 푸시)는 컴파일된 dogfood 사이트를 검사 가능한 워크플로
아티팩트로 항상 업로드하고, Pages가 활성화되어 있으면 **추가로** GitHub Pages에 배포한다.
Pages 단계는 `continue-on-error`다: 기본 `GITHUB_TOKEN`은 Pages 사이트를 *생성*할 수
없으므로, 최초 배포에는 **Settings → Pages → Source: GitHub Actions**에서 한 번의 수동
전환이 필요하다. 그 전환을 켜기 전까지도 빌드는 그린으로 유지되고 아티팩트는 계속 생성된다.

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
