# Obsidian — 컴파일된 위키를 실제 vault로 열기

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.ru.md">Русский</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.fr.md">Français</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae의 Obsidian export는 컴파일된 타입 그래프를 실질적이고 의견이 반영된 [Obsidian](https://obsidian.md) vault로 변환합니다. 단순한 markdown 디렉터리가 아니라 `.obsidian/` 설정, 타입을 인식하는 [callouts](https://help.obsidian.md/Editing+and+formatting/Callouts), [Dataview](https://blacksmithgu.github.io/obsidian-dataview/)로 쿼리 가능한 frontmatter, vault 대시보드, 그리고 vault 간 `wiki://` 참조 인덱스를 갖춘 진짜 vault입니다.

## 사전 요구사항

먼저 프로젝트를 컴파일하세요:

```bash
cd /path/to/your-project
tesserae project setup
tesserae project compile
```

컴파일은 진실의 원천인 `.tesserae/graph.json`과 `.tesserae/markdown_projection/`에 위치한 단순 markdown 투영을 생성합니다. Obsidian export는 이 투영 위에 구축되지만 모든 페이지에 Obsidian 네이티브 보강을 덧붙입니다.

## 1) vault export

```bash
tesserae project export-obsidian --vault ~/Documents/tesserae-vault
```

디렉터리가 없으면 생성됩니다. 다시 실행해도 멱등적으로 덮어씁니다 — 동일한 그래프가 주어지면 markdown 투영은 결정론적입니다.

디스크에 생성되는 것:

```text
tesserae-vault/
  .obsidian/                  # Obsidian config (app.json, graph.json, plugins)
  README.md                   # Vault entry point
  index.md                    # All nodes grouped by section
  _bridges.md                 # Cross-vault wiki:// references, grouped by alias
  _meta/
    dashboard.md              # Dataview overview tables
  papers/                     # Paper / Repository / SourceDocument pages
  concepts/                   # Concept / Topic / Field / Method / Algorithm pages
  claims/                     # Claim / OpenQuestion / Evidence pages
  raw/                        # Optional raw-source attachments (created lazily)
```

## 2) Obsidian에서 디렉터리 열기

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`.

Obsidian이 `.obsidian/`를 감지하여 실제 vault로 인식하고 로드합니다. 커뮤니티 플러그인 목록에는 Dataview가 포함되어 있으므로, Obsidian이 활성화를 권유합니다(권장 — 활성화하지 않으면 dataview 블록이 코드 펜스로 렌더링됩니다).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) vault 둘러보기

### 진입점

- `README.md` — 이 vault가 무엇이며 어떻게 새로고침하는지
- `index.md` — 섹션별(papers, concepts, claims) 모든 노드를 wikilink와 함께 정리
- `_meta/dashboard.md` — dataview 개요: 최근 페이지, papers, concepts/claims

### 페이지별 보강

이제 모든 노드 페이지는 다음을 함께 제공합니다:

**타입 인식 callouts.** 각 페이지 상단의 의미론적 callout이 노드 타입을 한눈에 보여줍니다:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

매핑(주요 항목): `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Dataview로 쿼리 가능한 엣지.** 이제 frontmatter는 타입이 부여된 엣지를 중첩 맵으로 담습니다:

```yaml
edges_out:
  uses: [gaussian-splatting, volumetric-rendering]
  part_of: [3d-4d-vision-and-reconstruction]
  supports_claim: [performance-claim-..., comparison-...]
edges_in:
  mentioned_in: [project-pulse, topic-visual-slam]
```

다음과 같은 쿼리를 작성할 수 있습니다:

````markdown
```dataview
LIST FROM "papers" WHERE contains(edges_out.uses, "nerf")
```

```dataview
TABLE edges_out.supports_claim AS "Claims"
FROM "papers"
WHERE length(edges_out.supports_claim) > 3
SORT length(edges_out.supports_claim) DESC
LIMIT 10
```
````

**vault 간 브리지.** 노드의 설명이나 메타데이터에 언급된 모든 `wiki://<alias>/<kind>/<slug>` URI는 frontmatter 필드로:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

그리고 `Cross-vault references` 본문 섹션으로 함께 노출됩니다. vault 단위 `_bridges.md` 인덱스는 대상 alias별로 묶인 모든 외부 참조를 집계하므로, 단일 페이지에서 vault 간 링크를 감사할 수 있습니다.

**Related (dataview) 블록.** 모든 페이지는 역참조하는 페이지를 자동으로 채워 보여주는 쿼리로 끝납니다:

````markdown
```dataview
LIST
FROM "papers" OR "concepts" OR "claims"
WHERE contains(file.outlinks, this.file.link) AND file.name != this.file.name
SORT file.name
LIMIT 25
```
````

### vault 대시보드

`_meta/dashboard.md`는 가장 유용한 집계 뷰를 위한 dataview 블록을 함께 제공합니다: 최근 업데이트된 페이지, 메타데이터 컬럼이 포함된 모든 papers, 타입별로 정렬된 모든 concepts와 claims. 자유롭게 편집하세요 — 고정된 계약이 아니라 출발점입니다.

### vault 그래프 뷰

Obsidian 기본 그래프 뷰(`Ctrl/Cmd+G`)는 `## Outgoing` / `## Incoming` 섹션에 방출된 wikilink에 대해 이미 작동합니다. 함께 제공되는 `.obsidian/graph.json`은 방향성을 위해 `papers/`, `concepts/`, `claims/` 경로를 색상으로 구분합니다. 더 풍부한 슬라이스를 위해 dataview로 필터링된 뷰를 그 위에 겹쳐 쌓을 수 있습니다.

## vault 간 워크플로

여러 Tesserae vault를 등록하여 `wiki://` URI가 그들 사이에서 해결되도록 만드세요:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

등록 후 각 vault를 다시 export하세요. 각 export의 `_bridges.md`는 이제 alias별로 묶여 vault 간에 해결 가능한 참조를 보여줍니다.

Obsidian 자체는 `wiki://` URI를 네이티브로 따라가지 않으므로 인라인 텍스트로 렌더링됩니다 — 하지만 `_bridges.md`와 페이지별 `Cross-vault references` 섹션이 전용 Obsidian 플러그인이 등장할 때까지 수동 인덱스를 제공합니다.

## 새로고침 워크플로

소스 파일에서 새 소스나 수정을 반영하려면:

```bash
# 프로젝트의 소스 디렉터리 아래 소스 파일을 편집한 뒤:
tesserae project compile
```

`compile`이 vault를 자동으로 다시 투영합니다 — 더 이상 별도의 export 단계를 실행할 필요가 없습니다. (전체 재컴파일 없이 일회성 재투영만 하려면 `tesserae project export-obsidian --vault <경로>`도 여전히 사용할 수 있습니다.) Obsidian은 디스크에서 변경된 파일을 핫 리로드합니다.

vault 내부에 그래프에서 투영되지 않은 markdown 노트(예: 본인의 개인 주석)를 추가했다면, 그것들은 살아남습니다 — 프로젝터는 자신이 소유한 파일, 즉 `papers/`, `concepts/`, `claims/` 아래의 파일과 `index.md`, `_bridges.md`, `_meta/dashboard.md`, `README.md`만 덮어씁니다. 직접 작성한 페이지(`node_id:` 프런트매터가 없는 파일)와 각 투영 페이지의 전용 사용자 노트 블록(`<!-- user-notes:start -->` … `<!-- user-notes:end -->`)은 재컴파일 사이에 보존됩니다.

### Obsidian에서의 편집은 되돌아 흐릅니다 (양방향 동기화)

v0.5.0부터 vault는 더 이상 **단방향 export가 아닙니다**. 이제 *양방향 투영*입니다: 타입 그래프가 여전히 진실의 원천이지만, `project compile`이 다시 투영하기 **전에** Obsidian에서의 편집을 vault에서 다시 읽어 그래프에 오버레이합니다. Obsidian에서 노드의 `title`, `aliases`, 설명 콜아웃, 또는 시스템 외 프런트매터 스칼라를 편집하고 재컴파일하면 변경이 살아남으며 — 정적 사이트, MCP, 그 외 모든 투영으로 전파됩니다.

```bash
tesserae project compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

오버레이가 수집하는 항목(*vault 우선* 필드):

- `title` → 노드 `name`
- `aliases` → 노드 별칭
- 본문 설명 콜아웃(또는 첫 문단) → 노드 `description`
- 예약되지 않은 모든 프런트매터 스칼라 → `metadata.<key>` (예약/시스템 키 `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault`는 사용자 오버라이드로 취급되지 않습니다)

오버레이가 실행될 때마다 `.tesserae/diverged-fields.md` 리포트(`## Field overrides — N across M node(s)`)를 작성하므로 무엇이 되돌아 반영되었는지 정확히 감사할 수 있습니다. 사용자 노트 블록 안에 추가한 위키링크는 `user_link` 엣지가 됩니다. 한 번의 실행에서 오버레이를 우회하려면 `tesserae project compile --no-vault-pull`을 전달하세요 — 복구하거나 소스 markdown이 이기길 의도할 때 유용합니다.

이 기능을 활성화한 뒤 첫 컴파일은 "프리 패스"를 받습니다: 아직 `vault_snapshot.json` 기준선이 없으므로 아무것도 수집되지 않으며, 마지막에 작성되는 스냅샷이 다음 컴파일의 diff 기준선이 됩니다.

전용 라이브 워크플로로, `tesserae project obsidian-sync`는 전체 재컴파일 없이 오버레이를 다시 적용하고 재투영합니다:

```bash
# 그래프를 변경하지 않고 컴파일이 무엇을 되돌려 가져올지 미리 봅니다.
tesserae project obsidian-sync --dry-run

# vault를 감시하며 편집을 실시간으로 라운드트립합니다(Ctrl-C로 중지).
tesserae project obsidian-sync --watch

# 노드 이름 변경/삭제 후, 고아가 된 투영 페이지를 삭제합니다.
tesserae project obsidian-sync --prune-orphans
```

전체 필드별 소유권 매트릭스와 설계 근거는 [obsidian-sync.md](obsidian-sync.md)를 참고하세요.

## 정적 사이트와 비교해 언제 사용하나

컴파일된 HTML 사이트(`tesserae project build-site` → `.tesserae/site/`)는 공유를 위한 단방향 읽기 전용 export입니다 — GitHub Pages, S3, 모든 정적 호스트에 푸시할 수 있습니다. Obsidian vault는 Dataview와 Obsidian 그래프 뷰로 로컬에서 **읽고, 쿼리하고, 편집하기** 위한 것입니다: 편집이 그래프로 되돌아 흐르는 유일한 투영입니다(위의 양방향 동기화 섹션 참고). 둘 다 동일한 그래프에서 투영되므로 결코 어긋나지 않으며 — Obsidian에서 가한 수정은 다음 컴파일에서 사이트로 전파됩니다.
