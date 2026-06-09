# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
단일 문서 파일 또는 URL을 지식 베이스에 병합합니다.

## 사용법

    tesserae ingest <input>...  [--title T] [--source-kind K] [--exact] [--dry-run]

`<input>`은 하나 이상의 로컬 파일 경로 또는 `http(s)` URL입니다. URL은 가져와서 마크다운으로
변환된 다음 출처 정보가 담긴 front-matter(`source_url`, `fetched_at`, `content_sha256`, 그리고
감지된 경우 `arxiv_id`)와 함께 `data/ingested/<slug>.md`에 저장된 후 병합됩니다.
프로젝트 외부의 로컬 파일은 `data/ingested/`로 복사되어 추적되는 소스가 됩니다(이후 전체 컴파일이
동일하게 재현합니다).

URL 인제스트에는 선택적 추가 패키지가 필요합니다.

    pip install tesserae[ingest-url]

## 작동 방식

기본적으로 `ingest`는 증분 컴파일을 통해 새 소스를 병합합니다. 전체 코퍼스를 다시 추출하지 않으며,
그 결과는 전체 컴파일과 바이트 단위로 동일합니다(증분 경로가 처리할 수 없는 경우에도 자동 전체
재컴파일 폴백이 정확성을 보장합니다). 전체 코퍼스의 전체 재컴파일을 강제하려면 `--exact`를 전달하세요.

## 플래그

- `--exact` — 전체 코퍼스의 전체 재컴파일을 강제합니다.
- `--dry-run` — 인제스트될 항목을 가져와 보고하지만 그래프는 쓰지 않습니다.
- `--title` — 제목 재정의로, 단순 URL에 유용합니다.
- `--source-kind` — 소스 분류를 재정의합니다.

## 관련 명령어

- `tesserae compile`(인수 없음)은 추적되는 전체 코퍼스를 다시 추출합니다.
- `tesserae ingest <x>`는 하나의 소스를 증분 방식으로 추가합니다.
- `tesserae code ingest`는 Python 소스에서 코드 그래프를 생성합니다(다른 명령어입니다).
