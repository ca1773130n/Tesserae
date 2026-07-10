# Obsidian 양방향 동기화 — 제안된 설계

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian-sync.md">English</a> · <a href="obsidian-sync.zh.md">中文</a> · <a href="obsidian-sync.ja.md">日本語</a> · <a href="obsidian-sync.ru.md">Русский</a> · <a href="obsidian-sync.es.md">Español</a> · <a href="obsidian-sync.fr.md">Français</a> · <a href="obsidian-sync.de.md">Deutsch</a></p>
<!-- translations:end -->

> **상태: 출시됨 (Tier 1, v0.5.0).** 아래에 기술된 오버레이 리더, 사용자 노트 append 존, watch 모드, 고아 정리(orphan pruning)는 `tesserae vault sync` 뒤에서 라이브로 동작합니다. 이 페이지는 설계 근거이자 사용자 가이드를 겸합니다. 다중 vault 연합(Tier 3)은 여전히 범위 밖입니다.

[Obsidian export](obsidian.ko.md)는 과거에 엄격히 단방향이었습니다: `.tesserae/graph.json`의 타입 그래프가 vault로 프로젝션되고, `project compile`이 프로젝션된 파일을 덮어씁니다. `obsidian-sync`는 반대 방향을 추가합니다 — Obsidian에서 description을 편집하면 재compile 후에도 살아남습니다.

이 문서는 데이터 모델을 일관성 없게 만들지 않으면서 그것이 어떻게 동작하는지 자세히 설명합니다.

## 전략적 전환, 있는 그대로

현재 README는 라이브 편집을 부인합니다:

> Tesserae picks compile-from-source over live editing. If you want to edit notes in a UI, use Logseq or Obsidian.

양방향 동기화는 필드의 일부 집합에 대해 **그 계약을 변경합니다**. 신중할 가치가 있습니다. 목표는 "Obsidian이 편집기가 된다"가 아니라 — "사용자의 Obsidian 편집이 재compile에서 조용히 파괴되지 않는다"입니다.

## 핵심 아이디어: 병합이 아니라 오버레이

같은 노드의 갈라진 두 복사본을 병합하려 하기보다, vault를 프로젝션 위의 **diff 레이어**로 취급합니다:

```text
source markdown  ──extract──▶  base_graph
                                    +
                              vault_overrides     ◀── computed from vault
                                    ↓
                              final_graph  ──project──▶  vault (.md files)
```

`vault_overrides.json`은 `.tesserae/`에 살고 **계산되는** 것이지 저작되는 것이 아닙니다. 매 compile마다 Tesserae는 vault를 순회하며, 프로젝션된 각 페이지를 이전 프로젝션이 기록한 것과 비교하고, 사용자가 도입한 모든 변경을 오버레이 항목으로 기록합니다. 최종 그래프는 오버레이가 적용된 `base_graph`입니다. 다음 프로젝션이 그 결과를 디스크에 다시 기록합니다.

라운드트립 안정적입니다. 소스 측 변경 없이 같은 vault를 재compile하면 diff가 생기지 않습니다.

## 필드별 소유권

노드의 각 필드에는 소유자가 있습니다. 소스와 vault가 불일치할 때 어떤 일이 일어날지는 소유권이 결정합니다.

| 필드 | 소스 소유 | vault 재정의 가능 | 비고 |
|---|---|---|---|
| `id`, `type` | 예 | 아니오 | 스키마 통제; 추출기 소유 |
| `name` | 초기값 | 예 | 사용자가 추출기보다 정식 이름을 더 잘 아는 경우가 많음 |
| `aliases` | 초기값 | 예 | vault에서 append-only; vault 항목은 항상 보존됨 |
| `description` | 초기값 | **예** | 가장 흔한 Obsidian 편집 |
| `source_path` | 예 | 아니오 | 출처; 편집으로 지울 수 없음 |
| `metadata` (선언된 키) | 초기값 | 예 | 예: `arxiv_id`, `github_repo` — 사용자가 수정 가능 |
| `metadata.user.*` | 해당 없음 | 예 | 사용자 전용 키를 위한 예약 네임스페이스; 추출기는 절대 기록하지 않음 |
| 아웃고잉 엣지 (타입) | 예 | 아니오 | 엣지는 vault가 아니라 온톨로지에 존재 |
| 사용자가 입력한 새 wikilink | 해당 없음 | 예 | `edge_type=user_link`로 노출되어 그래프에 기록 |
| `<!-- user-notes -->` 본문 블록 | 절대 기록 안 함 | 항상 보존 | 프로젝터가 절대 건드리지 않는 append-only 존 |

## 충돌 사례와 기본값

| 사례 | 기본값 | 이유 |
|---|---|---|
| vault의 `description`이 재추출된 소스 `description`과 다름 | **vault 승리**, `.tesserae/lint-report.md`의 "diverged fields" 아래에 로그 | 사용자 편집 존중: 사용자가 명백히 그 편집을 의도함. 감사 추적으로 나중에 검토 가능. |
| 소스 파일 삭제됨, 프로젝션된 페이지는 여전히 vault에 있음 | 그래프에서 노드 제거, `.tesserae/orphans.md`에 나열 | 존재 여부는 소스가 권위적; 고아 로그로 복원할지 수용할지 결정 가능 |
| 사용자가 존재하지 않는 slug로 wikilink를 작성 | 툼스톤 노드(타입 `Stub`) 생성, lint 보고서에 노출 | 사용자 의도를 버리지 않음; 정리 대상으로 플래그 |
| 사용자가 스키마가 모르는 frontmatter 키를 추가 | `metadata.user.<key>`로 보존, 절대 덮어쓰지 않음 | 타입 그래프를 오염시키지 않으면서 전방 호환 |
| 서로 다른 머신의 두 vault가 같은 노드를 편집, 둘 다 Obsidian Sync로 동기화 | **v1 범위 밖.** 파일시스템 수준에서 last-writer wins. | 진정한 다중 vault 연합은 Tier 3; 실제 사용 사례가 생길 때까지 연기 |

## 사용자 노트 append 존

프로젝션되는 모든 페이지에는 프로젝터가 절대 건드리지 않는 펜스 존이 생깁니다:

```markdown
> [!quote] Paper
> Headline contribution and method sketch projected from the graph...

<!-- user-notes:start -->

Your notes here. Anything between the markers survives recompile forever.
Wikilinks here become `user_link` edges in the graph on the next pull.

<!-- user-notes:end -->

## Outgoing
- ...
```

두 가지 실용적 효과:
1. 사용자는 어떤 페이지든 주석을 달 수 있고(예: "내 노트의 4장 참조") 재빌드에서 잃지 않습니다.
2. pull 패스는 사용자 노트 블록에서 wikilink를 스캔해 온톨로지 타입의 `user_link` 엣지로 노출하여, 공식 엣지 타입을 오염시키지 않으면서 그래프 도달 가능성을 부여합니다.

## 원격 전송 — 명시적 비목표

Tesserae는 동기화 서버, 인증 레이어, 충돌 해결 데몬, 호스팅 vault를 만들지 **않습니다**. 여기서 "양방향"은 "compile이 vault에서 읽는다"는 뜻입니다 — compile을 수행하는 머신에 vault를 가져다 놓는 것은 사용자의 몫이며, 이미 존재하는 도구들이 해결합니다:

| 스택 | 비용 | 비고 |
|---|---|---|
| Obsidian Sync | 유료, $4-8/mo | E2E 암호화, 공식, 아주 간단 |
| iCloud / Dropbox / OneDrive | OS에 번들 | 동작하지만 충돌 UX가 적대적 |
| Syncthing | 무료, 자체 호스팅 | 혼자 여러 기기를 쓸 때 최선 |
| Git (vault 커밋) | 무료 | 충돌 UX는 기술 사용자에게 최선 |
| LiveSync (CouchDB 플러그인) | 무료, 서버 필요 | 실시간 다중 기기 |

Tesserae는 vault를 변경 스트림이 아니라 디스크 위의 파일로 보기 때문에, 다섯 가지 모두 오버레이 모델과 호환됩니다.

## CLI 표면

`tesserae vault sync`는 vault 편집을 타입 그래프에 적용하고 재프로젝션합니다:

```bash
# Apply the overlay once: pull user edits, re-project to the vault.
tesserae vault sync

# Inspect what would change first. Writes .tesserae/diverged-fields.md and
# does NOT apply or re-project.
tesserae vault sync --dry-run

# Point at a specific vault for this call (resolution order:
# --vault > config.obsidian.vault_path > .tesserae/obsidian_vault/).
tesserae vault sync --vault ~/Documents/tesserae-vault

# Make that vault path the default for future commands.
tesserae vault sync --vault ~/Documents/tesserae-vault --persist-vault

# Long-running watch: re-apply the overlay every time the vault changes.
# Ctrl-C to stop; tune the poll cadence with --interval (default 1.5s).
tesserae vault sync --watch --interval 1.5

# Delete projected pages whose source node no longer exists (the projector
# only overwrites, never deletes). Pages with user-notes are kept unless you
# also pass --force-prune-with-notes.
tesserae vault sync --prune-orphans
tesserae vault sync --prune-orphans --force-prune-with-notes
```

`/tesserae:obsidian-sync` 슬래시 명령이 이를 래핑하고, `tesserae refresh`
(그리고 `/tesserae:refresh` 매크로)는 import → compile → sync 체인의 마지막
단계로 오버레이를 실행합니다.

## 제공 상태

| Tier | 범위 | 상태 |
|---|---|---|
| **1a** | 오버레이 리더: vault를 순회하고 `vault_overrides.json`을 구축, 동기화 시 적용. 분기(divergence)는 `.tesserae/diverged-fields.md`에 기록. | 출시됨 |
| **1b** | 사용자 노트 append 존: 프로젝터는 `<!-- user-notes:start --> ... <!-- user-notes:end -->` 블록을 절대 건드리지 않음. | 출시됨 |
| **2** | Watch 모드: 장수 `obsidian-sync --watch`가 vault 변경 시 폴 루프에서 오버레이를 재실행. | 출시됨 |
| **3** | 다중 vault 연합: 그래프가 vault별 출처를 저장하고, 동기화된 vault 간 동시 편집 지원. | 실제 사용 사례가 생길 때까지 연기 |

## 비목표 (명시적으로)

- 동기화 서버 / 인증 / 호스팅 백엔드.
- Obsidian 내부의 실시간 협업 편집 (필요하면 LiveSync 사용).
- 모든 필드를 라운드트립하도록 추출기를 다시 쓰는 것 — 재정의 테이블 밖의 모든 것에 대해 소스 markdown이 정본으로 유지됩니다.
- 정적 HTML 사이트의 동기화 (`build-site`는 프로젝션 전용으로 유지).

## 확정된 결정

설계 당시 열린 질문이었던 것들이며, 출시된 Tier 1–2 구현은 다음과 같이 확정했습니다:

1. **Lint 보고서 형태.** 분기된 필드는 `lint-report.md`의 한 섹션이 아니라 전용 `.tesserae/diverged-fields.md` 파일로 노출됩니다(`--dry-run` 및 매 적용 시 기록) — git에서 diff할 수 있게 하기 위해서입니다.
2. **툼스톤 노드 타입.** `Stub`을 진짜 스키마 타입으로 추가할 것인가, 아니면 `_kind: stub` 판별자로 `OpenQuestion`에 얹을 것인가? 제안: 진짜 타입, 이름은 `Stub`, 공개 인덱스에서는 숨김.
3. **compile 시 pull 기본값.** 기본 ON인가 OFF인가? 제안: 설정된 경로에 vault가 존재하면 ON, 다만 처음 활성화될 때 일회성 확인 프롬프트를 두어 사용자가 의도적으로 옵트인하게 함.
4. **diff의 기준이 되는 "이전 프로젝션"은 무엇인가?** `.tesserae/vault_snapshot.json`에 저장된 스냅샷인가, 아니면 매 compile마다 즉석 재프로젝션인가? 제안: 스냅샷, 매 compile 종료 시 기록. 더 저렴하고 추출기 비결정성이 오버레이로 새는 것을 방지.
5. **다국어 vault 프로젝션.** 오늘의 프로젝션은 단일 언어(소스)입니다. 오버레이가 로케일 인지형이어야 하는가(예: 한국어 vault 오버레이의 `description` 편집은 한국어 프로젝션에만 적용)? 제안: v1 범위 밖; vault는 프로젝트의 주 언어와 일치하는 단일 언어.

## `obsidian.md`에는 어떻게 나타나는가

사용자 대상 가이드는 "vault를 읽고 쿼리할 수 있다"에 집중을 유지하고, 라운드트립 스토리는 한 줄 요약과 함께 이곳을 링크합니다: "Obsidian에서 필드를 편집하면 재compile 후에도 살아남습니다. 전체 모델은 [obsidian-sync.md](obsidian-sync.ko.md)를 참조하세요."
