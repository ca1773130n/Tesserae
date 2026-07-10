# Obsidian — 컴파일된 위키를 진짜 vault로 열기

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.ru.md">Русский</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.fr.md">Français</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae의 Obsidian export는 컴파일된 타입 그래프를 진짜 잘 짜인 [Obsidian](https://obsidian.md) vault로 바꿔줍니다. 단순한 markdown 디렉터리가 아니라 — `.obsidian/` 설정, 타입 인지형 [callout](https://help.obsidian.md/Editing+and+formatting/Callouts), [Dataview](https://blacksmithgu.github.io/obsidian-dataview/)로 쿼리 가능한 frontmatter, vault 대시보드, 그리고 vault 간 `wiki://` 참조 인덱스를 갖춘 vault입니다.

## 사전 요구사항

먼저 프로젝트를 compile하세요:

```bash
cd /path/to/your-project
tesserae init
tesserae compile
```

compile은 `.tesserae/graph.json`(진실의 원천)과 `.tesserae/markdown_projection/`의 일반 markdown 프로젝션을 생산합니다. Obsidian export는 그 프로젝션 위에 구축되지만 모든 페이지에 Obsidian 네이티브 강화 요소를 덧입힙니다.

## 1) vault 내보내기

```bash
tesserae vault export --output ~/Documents/tesserae-vault
```

디렉터리가 없으면 생성됩니다. 재실행은 멱등적으로 덮어씁니다 — markdown 프로젝션은 같은 그래프가 주어지면 결정적입니다.

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

Obsidian이 `.obsidian/`을 감지해 진짜 vault로 인식하고 로드합니다. community-plugins 목록에 Dataview가 포함되어 있으므로 Obsidian이 활성화를 제안합니다(권장 — 없으면 dataview 블록이 코드 펜스로 렌더링됩니다).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) vault 둘러보기

### 진입점

- `README.md` — 이 vault가 무엇이고 어떻게 새로고침하는지
- `index.md` — 섹션별(papers, concepts, claims) 모든 노드와 wikilink
- `_meta/dashboard.md` — dataview 개요: 최근 페이지, papers, concepts/claims

### 페이지별 강화 요소

이제 모든 노드 페이지에 다음이 포함됩니다:

**타입 인지형 callout.** 각 페이지 상단의 시맨틱 callout이 노드 타입을 한눈에 보여줍니다:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

매핑(주요 항목): `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Dataview로 쿼리 가능한 엣지.** frontmatter가 이제 타입 엣지를 중첩 맵으로 담습니다:

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

**vault 간 브리지.** 노드의 description이나 메타데이터에 언급된 모든 `wiki://<alias>/<kind>/<slug>` URI는 frontmatter 필드로:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

그리고 `Cross-vault references` 본문 섹션으로 함께 노출됩니다. vault 수준의 `_bridges.md` 인덱스는 모든 아웃바운드 참조를 목적지 alias별로 집계하므로, vault 간 링크를 한 페이지에서 감사할 수 있습니다.

**Related (dataview) 블록.** 모든 페이지는 역링크하는 페이지를 보여주는 쿼리로 끝나며, 자동으로 채워집니다:

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

`_meta/dashboard.md`는 가장 유용한 집계 뷰를 위한 dataview 블록을 담고 있습니다: 최근 업데이트된 페이지, 메타데이터 컬럼이 있는 모든 papers, 타입별로 정렬된 모든 concepts와 claims. 자유롭게 편집하세요 — 이는 시작점이지 고정 계약이 아닙니다.

### vault 그래프 뷰

Obsidian 내장 그래프 뷰(`Ctrl/Cmd+G`)는 `## Outgoing` / `## Incoming` 섹션에 방출된 wikilink에 대해 이미 동작합니다. 사전 제공되는 `.obsidian/graph.json`은 방향 잡기를 위해 `papers/`, `concepts/`, `claims/` 경로를 색상으로 구분합니다. 더 풍부한 단면을 위해 그 위에 dataview 필터링 뷰를 얹을 수 있습니다.

## vault 간 워크플로

여러 Tesserae vault를 등록하면 `wiki://` URI가 vault를 가로질러 해석됩니다:

```bash
tesserae projects register /path/to/research --name research
tesserae projects register /path/to/notes    --name notes
```

등록 후 각 vault를 다시 내보내세요. 이제 각 export의 `_bridges.md`가 alias별로 그룹화된, vault 간 해석 가능한 참조를 보여줍니다.

Obsidian 자체는 `wiki://` URI를 네이티브로 따라가지 않습니다 — 인라인 텍스트로 렌더링됩니다 — 그러나 전용 Obsidian 플러그인이 나오기 전까지 `_bridges.md`와 페이지별 `Cross-vault references` 섹션이 수동 인덱스가 되어줍니다.

## 새로고침 워크플로

소스 파일의 새 소스나 수정 사항을 반영하려면:

```bash
# Edit source files under your project's source dirs, then:
tesserae compile
```

`compile`은 vault를 자동으로 재프로젝션합니다 — 더 이상 별도의 export 단계를 실행할 필요가 없습니다. (`tesserae vault export --output <path>`는 전체 재compile 없이 일회성 재프로젝션을 위해 여전히 존재합니다.) Obsidian은 디스크에서 변경된 파일을 핫 리로드합니다.

그래프에서 프로젝션되지 않은 markdown 노트를 vault 안에 추가했다면(예: 개인 주석), 그것들은 살아남습니다 — 프로젝터는 `papers/`, `concepts/`, `claims/` 아래의 자신이 소유한 파일과 `index.md`, `_bridges.md`, `_meta/dashboard.md`, `README.md`만 덮어씁니다. 손으로 쓴 페이지(`node_id:` frontmatter 없음)와 각 프로젝션된 페이지의 전용 사용자 노트 블록(`<!-- user-notes:start -->` … `<!-- user-notes:end -->`)은 재compile을 거쳐도 보존됩니다.

### Obsidian에서의 편집이 되돌아 흐릅니다 (양방향 동기화)

v0.5.0부터 vault는 **더 이상 단방향 export가 아닙니다**. 이것은 *양방향 프로젝션*입니다: 타입 그래프가 여전히 진실의 원천이지만, `project compile`은 이제 Obsidian 편집을 vault에서 다시 읽어 재프로젝션 **전에** 그래프에 오버레이합니다. Obsidian에서 노드의 `title`, `aliases`, description callout, 또는 비시스템 frontmatter 스칼라를 편집하고 재compile하면 변경이 살아남습니다 — 그리고 정적 사이트, MCP, 그 밖의 모든 프로젝션으로 전파됩니다.

```bash
tesserae compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

오버레이가 수확하는 것(*vault-wins* 필드):

- `title` → 노드 `name`
- `aliases` → 노드 aliases
- 본문 description callout(또는 첫 단락) → 노드 `description`
- 예약되지 않은 모든 frontmatter 스칼라 → `metadata.<key>` (예약/시스템 키 `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault`는 절대 사용자 재정의로 취급되지 않음)

매 오버레이 실행은 `.tesserae/diverged-fields.md` 보고서(`## Field overrides — N across M node(s)`)를 기록하므로 정확히 무엇이 되돌아왔는지 감사할 수 있습니다. 사용자 노트 블록 안에 추가한 wikilink는 `user_link` 엣지가 됩니다. 한 번의 실행에서 오버레이를 우회하려면 `tesserae compile`을 (`.tesserae/config.json`에 `compile_options.no_vault_pull = true`를 설정하고) 실행하세요 — 복구할 때나 의도적으로 소스 markdown이 이기게 하고 싶을 때 유용합니다.

이 기능을 활성화한 뒤 첫 compile은 "무료 통과"를 받습니다: 아직 `vault_snapshot.json` 베이스라인이 없으므로 아무것도 수확되지 않습니다; 끝에 기록되는 스냅샷이 다음 compile의 diff를 위한 베이스라인이 됩니다.

전용 라이브 워크플로를 위해서는 `tesserae vault sync`가 전체 재compile 없이 오버레이를 재적용하고 재프로젝션합니다:

```bash
# Preview what a compile would pull back, without mutating the graph.
tesserae vault sync --dry-run

# Watch the vault and round-trip edits live (Ctrl-C to stop).
tesserae vault sync --watch

# After renaming/removing nodes, delete projected pages left orphaned.
tesserae vault sync --prune-orphans
```

필드별 소유권 매트릭스 전체와 설계 근거는 [obsidian-sync.ko.md](obsidian-sync.ko.md)를 참조하세요.

## 정적 사이트 대비 언제 이것을 쓰는가

컴파일된 HTML 사이트(`tesserae export site` → `.tesserae/site/`)는 공유를 위한 단방향 읽기 전용 export입니다 — GitHub Pages, S3, 어떤 정적 호스트에든 푸시하세요. Obsidian vault는 Dataview와 Obsidian 그래프 뷰로 로컬에서 **읽고, 쿼리하고, 편집하기** 위한 것입니다: 편집이 그래프로 되돌아 흐르는 유일한 프로젝션입니다(위 양방향 동기화 섹션 참조). 둘 다 같은 그래프에서 프로젝션되므로 절대 어긋나지 않습니다 — 그리고 Obsidian에서 한 수정이 다음 compile에서 사이트로 전파됩니다.
