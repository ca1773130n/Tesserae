# 웹 클리퍼 (Chrome 확장 프로그램)

<!-- translations:start -->
<p align="center"><a href="../../integrations/chrome-extension.md">English</a> · <a href="chrome-extension.zh.md">中文</a> · <a href="chrome-extension.ja.md">日本語</a> · <a href="chrome-extension.ru.md">Русский</a> · <a href="chrome-extension.es.md">Español</a> · <a href="chrome-extension.fr.md">Français</a> · <a href="chrome-extension.de.md">Deutsch</a></p>
<!-- translations:end -->

웹 페이지 전체 또는 선택한 텍스트만 Tesserae 지식 베이스로 직접 클립할 수 있습니다. 클리퍼는 로컬 `tesserae serve` 인스턴스에 페이지를 POST하고, 서버가 출처 기록이 있는 마크다운 파일을 프로젝트 코퍼스에 기록한 후 증분 컴파일을 실행하므로 클립이 그래프, 볼트 및 사이트의 타입된 노드로 표시됩니다.

이것이 "자율적이고 능동적인 지식 수집" 기둥을 한 번의 클릭으로 구현한 것입니다. 보존할 가치가 있는 것을 보면 클립하고, 에이전트가 사용할 수 있는 컨텍스트가 됩니다.

---

## 기능

1. 페이지를 탐색하고 클리퍼를 누릅니다(도구 모음 버튼 또는 키보드 단축키).
2. 확장 프로그램이 페이지의 `url`, `title`, 페이지 메타데이터 및 **전체 읽기 가능한 콘텐츠** 또는 텍스트가 강조표시된 경우 **선택 영역만** 가져옵니다. 선택적 **메모** 및 **태그**를 추가할 수 있으며, **요약** 생성을 전환할 수 있습니다.
3. 페이로드를 실행 중인 `tesserae serve`의 `http://localhost:<port>/api/clip`으로 POST합니다.
4. 서버가 제공되는 프로젝트를 해석하고, `data/ingested/<slug>.md`를 기록하며, 선택적으로 한 번의 LLM 요약을 앞에 붙이고, CLI가 사용하는 동일한 수집 경로(`ingest_sources`)를 호출하여 새 소스를 그래프에 증분 컴파일합니다.
5. JSON 보고서(`status`, `path`, `tldr`, `node_count`, `edge_count`)가 반환됩니다.

클립된 마크다운은 다음과 같습니다:

```markdown
---
clipped_at: 2026-06-13T00:00:00Z
note: read later
source: web-clip
tags: python, web
title: An Article
url: https://example.com/article
---

## TL;DR

두 문장 요약 (요약이 활성화되어 성공한 경우에만 표시됨).

## Note

read later

## Content

클립된 페이지 텍스트 (또는 선택 영역).
```

요약은 **최선의 노력**입니다: CLI 기반 Claude 레이어(API 키 필요 없음)를 사용합니다. `claude` CLI를 사용할 수 없거나 호출이 실패하면, 클립이 여전히 수집됩니다. `## TL;DR` 섹션이 없을 뿐입니다.

---

## 설치 (압축 해제 로드)

> 확장 프로그램은 저장소의 `extension/` 디렉토리에 포함되어 있습니다 (개발 중에 압축 해제 로드; Chrome 웹 스토어 등록이 검토 중).

1. `chrome://extensions`를 엽니다.
2. **개발자 모드**(우측 상단)를 켭니다.
3. **압축 해제 로드**를 클릭하고 `extension/` 디렉토리를 선택합니다.
4. Tesserae 클리퍼를 도구 모음에 고정합니다.

확장 프로그램은 기본적으로 `http://localhost:8765`와 통신합니다. 확장 프로그램 옵션에서 포트를 `tesserae serve`에 전달하는 포트와 일치하도록 설정합니다.

---

## 서버 실행

프로젝트를 컴파일한 다음 제공합니다:

```bash
python3 -m tesserae serve --project /path/to/project --port 8765
```

`tesserae serve`는 정적 사이트 **및** 동일한 출처에 두 개의 JSON 경로를 노출합니다:

- `POST /api/ask`  — 질문 답변 ([mcp.md](mcp.md) 참조)
- `POST /api/clip` — 웹 클립 수집 (이 기능)

탐색하는 동안 실행 상태로 유지합니다. 각 클립은 `/api/clip`을 누릅니다.

---

## `/api/clip` 계약

JSON 본문으로 `POST /api/clip`:

| 필드        | 유형      | 필수 | 설명 |
|-------------|-----------|------|-------|
| `url`       | string    | 예   | 소스 페이지 URL (출처 + 파일명 슬러그). |
| `title`     | string    | 아니요 | 페이지 제목; 파생된 제목으로 폴백됨. |
| `content`   | string    | 예\* | 전체 페이지 텍스트. |
| `selection` | string    | 아니요 | 존재하면 `content` **재정의** — 강조 표시된 텍스트만 클립합니다. |
| `meta`      | object    | 아니요 | 추가 페이지 메타데이터가 통과됨. |
| `note`      | string    | 아니요 | 자유 텍스트 주석 → `## Note`. |
| `tags`      | string[]  | 아니요 | 전면 물질 태그. |
| `tldr`      | boolean   | 아니요 | 기본값 `true`. 요약 생성을 건너뛰려면 `false`로 설정합니다. |

\* `content` 또는 `selection` 중 하나는 비어있지 않아야 합니다.

**응답** `200 OK`:

```json
{
  "status": "ok",
  "path": "/path/to/project/data/ingested/example-com-article.md",
  "tldr": "A two-sentence summary…",
  "node_count": 142,
  "edge_count": 287
}
```

오류는 `400` (잘못된 요청 / 빈 본문) 또는 `500` (수집 실패)을 `{"error": "..."}` 값과 함께 반환합니다.

### CORS

클리퍼가 브라우저 확장 프로그램이고 `localhost`에 접근하기 때문에, 엔드포인트는 CORS를 지원하지만 신뢰할 수 있는 호출자에 대해서만이므로, 방문하는 임의의 웹사이트가 그래프에 POST할 수 없습니다:

- `OPTIONS /api/clip`은 사전 확인 헤더를 반환합니다.
- 서버는 요청 `Origin`을 검증하고 **신뢰할 수 있는** 브라우저 확장 프로그램(`chrome-extension://…`) 및 루프백(`http://localhost`, `http://127.0.0.1`) 출처만 반영합니다. 외부 웹사이트 출처는 `403`으로 거부되고 수집 경로에 도달하지 않습니다.
- 허용된 응답은 `Access-Control-Allow-Origin: <that origin>`, `Access-Control-Allow-Methods: POST, OPTIONS` 및 `Access-Control-Allow-Headers: Content-Type`을 전송합니다.
- Chrome의 **Private Network Access** 사전 확인이 수행됩니다: 요청이 `Access-Control-Request-Private-Network: true`를 포함할 때, 서버는 `Access-Control-Allow-Private-Network: true`로 응답하므로 웹 스토어 확장 프로그램이 `localhost`에 도달할 수 있습니다.
- 요청 본문이 읽혀지기 전에 제한됩니다 (5 MB).

---

## MCP `ingest` 도구

동일한 수집 경로는 `ingest` 도구를 통해 Tesserae MCP 서버의 에이전트에 노출되므로, 에이전트는 브라우저 없이 발견한 콘텐츠를 클립할 수 있습니다:

| 입력      | 필수 | 설명 |
|-----------|------|-------|
| `content` | 예   | 수집할 텍스트. |
| `url`     | 아니요 | 소스 URL (출처 + 슬러그). |
| `title`   | 아니요 | 문서 제목. |
| `note`    | 아니요 | 주석 → `## Note`. |
| `tags`    | 아니요 | 전면 물질 태그. |
| `tldr`    | 아니요 | 기본값 `true`. |

**활성** 프로젝트(`activate_project`로 해석하거나 `project` 전달)로 수집하고 동일한 `{status, path, tldr, node_count, edge_count}` 보고서를 반환합니다. MCP 설정은 [mcp.md](mcp.md)를 참조하세요.

---

## 요약 전환

요약은 기본적으로 켜져 있습니다. 빠르고 결정론적인 클립이 필요할 때 확장 프로그램 팝업에서 클립당 끕니다 (또는 `"tldr": false`를 전송합니다). LLM 호출이 없습니다. 예를 들어, 에어 갭 프로젝트로 클립하거나 `claude`가 PATH에 없을 때 말입니다. 활성화되면, 실패하거나 누락된 요약기는 클립을 차단하지 않습니다. `## TL;DR` 섹션이 없을 뿐입니다.

---

## 키보드 단축키

클리퍼는 `chrome://extensions/shortcuts`에서 바인딩할 수 있는 명령을 등록합니다. 기본값은:

- **현재 페이지 / 선택 영역 클립:** `Ctrl+Shift+S` (macOS: `Cmd+Shift+S`)

다른 확장 프로그램과 충돌하면 다시 바인딩합니다.
