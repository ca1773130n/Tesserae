# Understand-Anything: code-graph 전용 모드

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything-code-only.md">English</a> · <a href="understand-anything-code-only.zh.md">中文</a> · <a href="understand-anything-code-only.ja.md">日本語</a> · <a href="understand-anything-code-only.ru.md">Русский</a> · <a href="understand-anything-code-only.es.md">Español</a> · <a href="understand-anything-code-only.fr.md">Français</a> · <a href="understand-anything-code-only.de.md">Deutsch</a></p>
<!-- translations:end -->

이 문서는 [understand-anything.md](../../integrations/understand-anything.md)의 후속편입니다. 기본 문서에서는 [Understand-Anything](https://github.com/Lum1104/Understand-Anything)(UA)을 동반 도구로 설치 및 활성화하여 `.understand-anything/knowledge-graph.json`에 code-graph를 생성하는 방법을 설명합니다. **이 문서는 UA가 오직 code graph만 기여하도록 만들고, 문서에서 추출된 섹션 헤딩으로 Tesserae의 research-graph Concept 레이어를 결코 오염시키지 않도록 하는 방법을 설명합니다.**

UA를 활성화한 뒤 타입드 그래프를 열어 보고 Concept 레이어가 `'Quickstart'`, `'2) Paste it into your MCP client'`, 혹은 같은 헤딩이 일곱 개 언어로 나열된 것으로 가득 차 있는 모습을 본 적이 있다면, 바로 이 문서가 해결하는 문제를 겪은 것입니다.

## 왜 이런 일이 발생하는가

같은 실수의 두 레이어가 겹쳐서 발생합니다:

1. **UA는 기본적으로 문서를 순회합니다.** 별다른 설정 없이는 UA의 소스 로더가 프로젝트 루트 아래의 모든 읽기 가능한 파일을 순회합니다 — `docs/`, `docs/i18n/`, 모든 언어의 README 등을 포함해서요. UA가 보는 모든 markdown 헤딩마다 헤딩 텍스트를 엔티티 이름으로 삼는 노드를 `.understand-anything/knowledge-graph.json`에 기록합니다.
2. **Tesserae는 UA의 그래프 전체를 네이티브로 병합합니다.** `external_tools`가 UA를 `sync_mode: "native_graph"`로 나열하면, `ProjectWiki._merge_configured_understand_anything_graph()`가 그 아티팩트를 읽고 모든 UA 노드를 `Concept`로 research graph에 임포트합니다. UA의 "이것은 코드 심볼이다"라는 의도가 "이것은 연구 개념이다"로 납작해지고, 여러분의 문서 헤딩 노드들도 함께 휩쓸려 들어갑니다.

순효과: 번역된 모든 헤딩이 중복 Concept(`'Setup'`, `'설정'`, `'安装'`, `'インストール'`, `'Установка'`, `'Configuración'`, `'Configuration'`, `'Einrichtung'`)로 나타나서 slug 충돌이 발생하고, projector는 이를 `setup-2.md`, `setup-3.md`, …, `setup-7.md`로 이름을 바꿉니다.

> [!warning] 보면 바로 알 수 있습니다
> 이 문제가 발생한 프로젝트에서 증상 점검:
> ```bash
> .venv/bin/python -c "
> import json
> from collections import Counter
> nodes = json.load(open('.tesserae/graph.json'))['nodes']
> srcs = Counter(n.get('source_path','') for n in nodes if n['type']=='Concept')
> print(srcs.most_common(3))
> "
> ```
> 최상위 소스가 수백 개의 Concept 노드를 가진 `.understand-anything/knowledge-graph.json`이라면, 가지고 있는 모든 번역된 헤딩이 별도의 개념으로 임포트되고 있다는 뜻입니다.

## 세 단계로 고치기

### 1단계 — Tesserae 쪽에서 UA를 Concept로 임포트하는 것을 중단

`.tesserae/config.json`을 편집하고 UA 도구 항목에서 `enabled: false`와 `sync_mode: "disabled"`를 모두 설정하세요. 병합 코드 경로는 이 두 안전장치 플래그를 모두 확인합니다:

```jsonc
{
  "external_tools": [
    {
      "id": "understand-anything",
      "enabled": false,            // ← 이전엔 true
      "sync_mode": "disabled",     // ← 이전엔 "native_graph"
      "auto_refresh": false,       // 선택사항: 매 컴파일마다 UA를 새로 고치는 것을 중단
      // ...나머지 항목은 그대로 유지
    }
  ]
}
```

`enabled: false`는 `_merge_configured_understand_anything_graph()`가 해당 도구를 완전히 건너뛰게 만듭니다. `sync_mode: "disabled"`는 향후 버그가 `enabled` 플래그를 무시할 경우를 대비한 보조 가드입니다.

### 2단계 — 잔존 아티팩트를 삭제해서 흔적을 남기지 않기

이전에 UA가 활성화된 상태로 컴파일을 실행한 적이 있다면, 오염된 아티팩트가 여전히 디스크에 남아 있습니다:

```bash
rm -f .understand-anything/knowledge-graph.json
rm -f .tesserae/external/understand-anything.md
```

Tesserae는 도구가 활성화되어 있을 때만 `.tesserae/external/understand-anything.md`를 재생성하므로, 1단계가 적용된 이후에는 이를 삭제하는 것이 안전합니다.

### 3단계 — 재컴파일 + Obsidian vault 정리

```bash
tesserae compile
tesserae vault sync --prune-orphans
```

컴파일은 UA 병합을 건너뛰며, research graph에는 UA에서 유래한 Concept가 남지 않게 됩니다. prune 단계는 병합이 생성했던 node_id를 가리키던 Obsidian vault의 모든 고아 페이지를 삭제합니다.

## 검증

재컴파일 후 위의 감사 스크립트는 `.understand-anything/knowledge-graph.json`에서 유래한 Concept 노드가 0개(또는 거의 0개)로 보고되어야 합니다. 추가로 유용한 점검은 다음과 같습니다:

```bash
.venv/bin/python -c "
import json, re
from collections import defaultdict
nodes = json.load(open('.tesserae/graph.json'))['nodes']
concepts = [n for n in nodes if n['type']=='Concept']
def slug(s): return re.sub(r'[^a-z0-9가-힣]+','-',s.lower()).strip('-')
buckets = defaultdict(list)
for n in concepts: buckets[slug(n['name'])].append(n)
collisions = {s: ns for s, ns in buckets.items() if len(ns)>1}
print(f'{len(collisions)} Concept slug collision(s), {sum(len(ns)-1 for ns in collisions.values())} duplicate page(s)')
"
```

수정이 적용되었다면 `0 Concept slug collision(s), 0 duplicate page(s)`가 출력되어야 합니다.

## code-graph 탐색이 실제로 다시 필요할 때

UA의 code graph는 문서 헤딩 노이즈에 파묻혀 있지 않을 때라면 — 호출/임포트 엣지, 클래스 계층 등 — 진정으로 유용합니다. 이를 올바르게 다시 활성화하려면:

1. **UA 자체의 범위를 코드로 한정하고, 문서는 제외하세요.** UA는 include/exclude 패턴을 받습니다. `src/`, `lib/`, `tesserae/` 등만 순회하고 `docs/`, `README*.md`, `docs/i18n/`은 명시적으로 제외하도록 설정하세요. 정확한 설정 노브는 UA 자체 문서 [Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything)에 있습니다.
2. **`.tesserae/config.json`에서 다시 활성화하세요**: `enabled`를 다시 `true`로, `sync_mode`를 다시 `"native_graph"`로, `auto_refresh`를 다시 `true`로 되돌립니다.
3. **재컴파일하고** 감사를 다시 실행하세요. 깨끗하게 실행된 UA는 영어 섹션 헤딩이 아니라 실제 코드 심볼(함수명, 클래스명, 모듈)에 매핑되는 Concept를 생성해야 합니다.

비대칭성이 따끔합니다 — 비활성화는 설정 한 번 뒤집기로 끝나지만, 깨끗하게 다시 활성화하려면 UA의 소스 범위 한정을 이해해야 합니다 — 하지만 이것이 옳은 경계선입니다. UA의 일은 code graph이고, Tesserae의 일은 research graph이며, 둘 사이의 이음매에서 문서 헤딩이 한쪽에서 다른 쪽으로 건너가게 두어서는 안 됩니다.

## 이 설정이 위치하는 자리

| 레이어 | 관심사 | 설정 위치 |
|---|---|---|
| UA 자체 walker | UA가 애초에 어떤 파일을 읽을지 | UA의 설정 (Tesserae 범위 밖) |
| UA 도구의 `auto_refresh` | `tesserae compile`이 UA를 재실행할지 여부 | `.tesserae/config.json`의 external_tools 항목 |
| UA 도구의 `enabled` | Tesserae가 UA를 고려할지 여부 자체 | `.tesserae/config.json`의 external_tools 항목 |
| UA 도구의 `sync_mode` | UA의 노드가 research graph로 병합될지 여부 | `.tesserae/config.json`의 external_tools 항목 |

`enabled` + `sync_mode` 노브는 두 프로젝트 사이의 이음매입니다. walker + `auto_refresh` 노브는 UA의 내부 관심사입니다.
