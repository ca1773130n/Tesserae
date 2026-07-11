# output-snapshot 게이트를 사용한 예약 CI 리프레시

<!-- translations:start -->
<p align="center"><a href="../../integrations/ci-refresh.md">English</a> · <a href="ci-refresh.zh.md">中文</a> · <a href="ci-refresh.ja.md">日本語</a> · <a href="ci-refresh.ru.md">Русский</a> · <a href="ci-refresh.es.md">Español</a> · <a href="ci-refresh.fr.md">Français</a> · <a href="ci-refresh.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae의 컴파일은 기계가 읽을 수 있는 no-op 신호를
`.tesserae/output-snapshot.json`에 기록합니다(`tesserae/output_snapshot.py`
참조). 매 컴파일 후, 바이트 멱등적인 아티팩트 집합(그래프 레이어 +
`wiki/`/`site/`/`markdown_projection/`)이 이전 상태와 실제로 달라졌을 때만
`changed`가 `true`가 됩니다. 예약된 워크플로는 이 플래그로 PR 단계를 게이트할
수 있습니다 — OpenWiki의 `examples/openwiki-update.yml` 스냅샷 게이트를 그대로
반영한 방식입니다.

`changed` 게이트는 끝없는 예약-PR 루프를 막아주는 장치입니다(OpenWiki의 교훈):
게이트가 없으면 아무 것도 바뀌지 않아도 매 cron 실행마다 PR이 열립니다.
따름정리: **저장소에서 *아무 것도* 바뀌지 않았는데 리프레시 PR이 나타난다면
그것은 바이트 멱등성 회귀의 실시간 증상입니다** — 컴파일이 동일한 입력의
프로젝션을 다르게 다시 썼다는 뜻입니다. 머지하지 말고 버그로 등록하세요.

```yaml
name: Tesserae refresh

on:
  schedule:
    - cron: "0 8 * * *"
  workflow_dispatch: {}

permissions:
  contents: write
  pull-requests: write

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: true

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Tesserae
        run: pip install tesserae

      - name: Compile the knowledge base
        run: tesserae compile --project .

      - name: Read the output-snapshot gate
        id: gate
        run: |
          if [ "$(jq -r .changed .tesserae/output-snapshot.json)" != "true" ]; then
            echo "no-op compile — skipping PR"
            echo "changed=false" >> "$GITHUB_OUTPUT"
          else
            echo "changed=true" >> "$GITHUB_OUTPUT"
          fi

      - name: Open refresh PR
        if: steps.gate.outputs.changed == 'true'
        uses: peter-evans/create-pull-request@v7
        with:
          add-paths: .tesserae/wiki
          branch: tesserae/refresh
          commit-message: "docs: refresh tesserae knowledge base"
          title: "docs: refresh tesserae knowledge base"
```
