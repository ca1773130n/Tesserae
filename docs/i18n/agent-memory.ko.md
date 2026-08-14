# 계층화된 에이전트 메모리 — 에이전트별 지식 그래프

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.zh.md">中文</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.fr.md">Français</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

아무도 모든 것을 기억하지 못합니다. 그리고 어떤 에이전트의 컨텍스트 윈도우도 모든 것을 담을 수 없습니다.
Tesserae의 답은 **계층화된 지식 기반**입니다. 모든 에이전트는 자신의 세션에서 자신의 메모리를 성장시키고, 그 메모리는 주기적으로 **증류되며**(조직화, 압축, 연마, 개선 — 그리고 안전하게 잊혀집니다), 관리자는 자신의 보고서의 증류된 계층만 봅니다. 관리자의 관리자는 더 이상의 롤업을 봅니다. 실제 조직처럼, 단일 읽기 담당자는 전체 아카이브가 필요하지 않습니다.

아래의 모든 것은 선택사항이고 추가적입니다. `tesserae distill`을 실행하지 않는 프로젝트는 이전처럼 정확히 동작합니다.

## 계층

- **L0 — 프로젝트 그래프** (`.tesserae/graph.json`). 변경되지 않음. 여전히
  바이트 멱등입니다. 컴파일의 구조 통과는 이제 관찰된 에이전트마다 하나의 `Agent` 노드와 각 세션에서 `performed_by` 엣지를 생성합니다 — 원본 귀속, LLM 비용 없음.
- **L1 — 에이전트당 하나의 아티팩트** (`.tesserae/agents/<key>/distilled.graph.json`).
  `tesserae distill`에 의해 작성됩니다. 일반 그래프 파일이지만 **48k 읽기 하나로 제한됩니다**.
  따라서 모든 에이전트가 단일 호출로 전체 증류된 메모리를 로드할 수 있습니다.
- **L2' — 관리자 롤업.** 보고서를 가진 에이전트를 증류할 때 보고서의 L1을 롤업합니다.
  혈통별로 중복 제거, 공유된 원본 증거별로 그룹화, 최고 메모는 **그대로** 유지합니다 — LLM 재요약 깊이는 1로 제한되므로, 요약은 절대 요약의 바꿔 말하기가 아닙니다. 같은 통과가 모든 조직 깊이로 재귀합니다.

## 에이전트 아이덴티티

에이전트는 `harness:account:role`로 키됩니다 — 역할 등급이므로, `reviewer` 서브에이전트와 `planner` 서브에이전트는 한 머신에서도 *다른* 전문성을 성장시킵니다. 역할은 트랜스크립트의 서브에이전트 설명자에서, 그 다음 선언적 레지스트리 일치 규칙에서, 그 다음 `default`로 폴백합니다.

```bash
tesserae agents init         # 세션 스캔, 조직 추론, .tesserae/agents/registry.json 작성
tesserae agents tree         # 조직도, 세션 수 + 증류 부실
tesserae agents list         # 관찰된 키, 레이블, 부모, 세션 수
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # 아티팩트 디렉토리 + 레지스트리 원자적으로 마이그레이션
```

`init`은 역할 신호에서 계층을 추론합니다. 서브에이전트 역할(`claude-code:me:reviewer`)은 그것을 생성한 주요 에이전트(`claude-code:me:default`)에 부모됩니다. 따라서 한 명령이 작동하는 다중 레벨 조직을 제공합니다 — `set-parent`가 필요하지 않습니다. `--flat`을 전달하면 이전의 모두-루트-아래 차트를 강제합니다. `set-parent`는 더 깊고 손으로 설계한 계층 구조에만 사용됩니다. 제로 구성은 여전히 작동합니다. 레지스트리가 없으면 모든 에이전트가 `org:root`에 보고하고 `agent="org"`는 플랫 팀 개요입니다.

## 증류

```bash
tesserae distill                      # 모든 에이전트, 리프 우선, 관리자 마지막
tesserae distill --agent <key>        # 하나의 에이전트
tesserae distill --dry-run            # LLM 호출 예측, 아무 것도 작성하지 않음
tesserae distill --max-llm-calls 50   # 하드 예산; 제한된 실행은 재실행에서 수렴
tesserae distill --retry-fallbacks    # 폴백된 클러스터 재시도
tesserae distill --full               # 워터마크 무시, 처음부터 재증류
```

통과는 에이전트의 발견을 클러스터링하고, 각 클러스터를 요약하며(인용 화이트리스트 및 진실성 린트), 증류된 메모를 생성합니다. 이들의 아이덴티티는 **혈통 키**입니다 — 아래 원본 L0 증거의 해시이지, LLM의 단어는 절대 아닙니다. 캐싱은 공격적이고 공유됩니다. 변경되지 않은 입력은 워터마크 스킵되고, 성장하는 클러스터는 점진적으로 접혀 들어가며, 제공자 실패는 회로 차단되고 결정적 구조 폴백을 생성합니다(플래그됨, 재시도 가능, 성공으로 캐시되지 않음).

증류는 **선택사항**입니다. `TESSERAE_AGENT_DISTILL=1`을 설정하거나(`config.json`의 `{"agent_distill": {"enabled": true}}`), `tesserae refresh`도 자동으로 증류합니다 — 하지만 **메모리 압박**에 있는 에이전트에만 해당합니다(증류되지 않은 발견이 컨텍스트 읽기의 절반에 더 이상 맞지 않습니다). MemGPT 스타일 통합 트리거입니다.

## 자동 통합(수면 사이클)

증류를 기억할 필요가 없습니다. 뇌가 휴식 중에 메모리를 통합하는 것처럼, 항상 켜진 `tesserae engine` 데몬은 프로젝트가 **유휴**일 때마다 자체적으로 통합합니다(몇 분 동안 편집 또는 세션 없음). 그리고 정기적인 상한이 있으므로 지속적으로 바쁜 프로젝트도 여전히 통합합니다. 각 실행은 세 가지 작업을 수행합니다. **압축 및 망각**(아래 증류 통과), 미검색 지식을 **사용 감소로 페이드**(위의 LRU 감소), 그리고 **살아남은 것 사이의 새로운 연결 발견**입니다. 증류 단계는 위에 설명된 `maybe_distill_on_refresh` 트리거와 정확히 같은 옵트인 게이트, 에이전트별 워터마크, 메모리 압박 체크를 래핑하므로, 사이클은 `TESSERAE_AGENT_DISTILL`이 설정되고 컴파일 게이트 아래에서 실행되며 결정적 아티팩트를 방해하지 않는 한 노-옵입니다.

전체 동작, CLI 플래그(`--consolidate-idle` / `--consolidate-every` / `--consolidate-check`), 그리고 플릿 노트:
[docs/engine-consolidation.md](engine-consolidation.ko.md).

## 망각 — 절대 삭제 아님

- **흡수**: 감소, 낮은 신뢰도 발견이 llm 품질 증류액에 의해 커버되면 그것에 접혀 들어가고(`absorbed_refs`) 기본 읽기에서 억제됩니다 — 하지만 `include_superseded` 및 `drill_down`을 통해 도달 가능합니다.
- **강등**: 다른 모든 것은 최악의 경우 전체 본문에서 제목+참조 행으로 떨어집니다. 나이만으로는 지식이 보이지 않게 됩니다.
- **사용 감소(LRU)**: 감소는 *검색 최근성*으로 구동되며, 생성 나이만으로는 아닙니다. 읽기 표면 레코드 접근 — `last_accessed_at` / `access_count` — `node_memory` 사이드카로(절대 `graph.json`으로는 아님). 증류는 그 라이브 접근 상태를 작업 보기에 병합합니다 **감소를 계산하기 전에**, 따라서 아무도 검색하지 않은 발견은 감소하고 흡수 또는 강등 대상이 되는 반면, 최근에 읽은 것은 나이와 관계없이 유지됩니다. 빈 사이드카는 정확히 이전 나이 전용 동작을 재생성합니다.
- **원장**: 모든 승격/강등/흡수는 망각 원장에 추가되고 `tesserae lint`로 표면화됩니다(`AGENT_FORGET_LEDGER`), 에이전트당 증류되지 않은 백로그 메트릭(`AGENT_UNDISTILLED_BACKLOG`)과 함께.
- **누가 읽었는가**(옵트인): 위의 접근 횟수는 노드가 읽혔다는 사실만 말할 뿐 누가 읽었는지는
  말하지 못합니다 — 그래서 노드를 계속 두드리는 수다스러운 에이전트와 한 번 읽은 사람이 망각에는
  같은 입력이 됩니다. MCP 서버에 `TESSERAE_READ_AUDIT=1`을 설정하면 각 읽기가
  `{tool, actor, node_ids, at, tesserae_version}` 형태로 같은 `.tesserae/sqlite.db`
  사이드카에도 기록되고, `read_audit` 도구에서 액터별 집계와 함께 읽을 수 있습니다. 액터는 호출이
  에이전트 뷰를 해석할 때는 `agent` 인자, 그렇지 않으면 `TESSERAE_ACTOR`입니다. 둘 다 없으면 지어낸
  이름에 귀속시키는 대신 익명 읽기로 기록합니다. **기본값이 꺼짐인 것은 의도입니다** — 모든 읽기 표면에
  항상 켜진 원장은 모든 읽기를 쓰기로 만듭니다. 끄면 기록이 멈출 뿐 이미 기록된 것이 지워지지는 않으며,
  이 가운데 어느 것도 `graph.json`에 들어가지 않습니다.

## 발견된 연결

압축 및 망각 외에도, 통합은 증류된 노트 간의 **새로운 연결을 발견**합니다 — 프로젝트 내 에이전트 간, 한 에이전트 내뿐만 아니라. 노트를 임베딩하고 가까운 쌍을 `shares_concept_with` 엣지로 링크합니다(`federation_semantic` 마커 포함). 발견은 **임베딩 게이팅**됩니다 — 실제 임베딩 백엔드가 구성되었을 때만 실행되고 해시 스텁을 스킵합니다 — 따라서 거짓 링크를 제조하지 않습니다. 엣지는 누적되는 **사이드카 오버레이**에 `.tesserae` 아래로 작성되지만, `graph.json`으로는 절대 아닙니다. 그리고 쿼리/PPR/연합 읽기 시간에 메모리에서 병합됩니다(범위 지정 보기 오버레이와 정확히 동일). 각 통합 사이클은 이전 사이클이 발견한 것에 대해 중복 제거하고 확장합니다. [docs/engine-consolidation.md](engine-consolidation.ko.md)의 수면 사이클 작업을 참조하세요.

## 범위가 지정된 보기 읽기

**CLI**에서, `--agent KEY`는 `query`, `ask`, 그리고 `context`의 범위를 지정합니다.

```bash
tesserae query "release checklist" --agent claude-code:me:reviewer   # 워커 뷰
tesserae ask "what does my team know about deploys?" --agent org      # 전체 팀
tesserae agents show claude-code:me:manager    # 모드, 멤버, 부실
tesserae agents drill SessionInsight:abc123 --agent claude-code:me:reviewer
```

**MCP**를 통해, 모든 그래프 읽기 도구는 같은 `agent=`를 수용합니다. 두 경우 모두 키는 다음 중 하나로 해석됩니다.

- **워커 키** → 자신의 원본 경험 ∪ 자신의 증류된 노트, 증류액 우선(흡수된 원본은 로드 시간에서 파생된 오버레이에 의해 자동으로 억제됩니다 — `graph.json`에 절대 다시 작성되지 않습니다).
- **관리자 키** → 보고서 L1 아티팩트의 연합만. 원본 발견은 절대 위로 누출되지 않습니다.
- **`org`** → 모든 증류된 아티팩트, 제로 구성.

지원 도구: `agents show` / `agent_view_explain`(멤버 + `distilled_through` 낡음 워터마크 — 각 보고서의 전문성이 얼마나 오래되었는지), 그리고 `agents drill` / `drill_down`(증류된 노트의 `member_refs`를 원본 L0 증거로 다시 해석하세요. 살아있음/변경됨/흡수됨/없음 상태 — 모든 호출 감사 로깅). `compile_context --multi-pool`은 증류된 노트와 전문성 프로필을 위해 예산 슬롯을 예약하고 출력에서 낡거나 폴백 품질 지식에 레이블을 지정합니다. 슬롯을 차지할 수 있는 것은 생산자가 실제로 만든 노드뿐입니다(증류 패스, 세션 이벤트 패스, 또는 에이전트 자신의 `graph_write`). 따라서 문서 추출로만 채워진 타입의 풀은 비어 있는 채로 남으며, CLI와 `knobs.pool_reservations` 모두 아무것도 반환하지 않은 풀을 알려줍니다.

## 성장 루프

- **에이전트별 하네스**: `write_harness` 에이전트 모드는 해당 에이전트의
  해결된 뷰에 도달하는 MCP 구성을 포함한 에이전트별 하네스 디렉토리를 내보냅니다.
  플러스 한 번만 시드되는 `purpose.md` 미션 페이지가 그 전문성 프로필에서 생성됩니다.
- **에이전트별 가이던스**: `.tesserae/extraction-guidance-<key>.md`를 통해
  프로젝트 레벨 `.tesserae/distill-guidance.md` 위에 계층화된 하나의 에이전트의
  증류를 조종합니다. 하나의 에이전트의 스트림을 편집하면 그 에이전트만 재증류합니다.
- **의미 다리**(선택사항): 매니저/조직 뷰에서 `shares_concept_with` 엣지로
  에이전트 간의 *관련* 증류된 노트를 링크합니다 — 엣지, 절대 병합 아님.
- **주제 맵**: `agent_topics`는 에이전트의 증류 세트를 결정적 `topics.md`로 롤합니다 —
  에이전트의 목차.
- **서브에이전트 승격**: 타입된 서브에이전트 실행은 서브에이전트 자신의 키 아래에서
  발견을 생성하므로, 위임된 작업은 위임자의 전문성에 축적됩니다.

## 결정성 보장

프로젝트 그래프는 바이트 멱등을 유지합니다. 증류된 아티팩트는
(그래프 바이트, 레지스트리, 캐시 디렉토리, 이전 아티팩트, 옵션)이 주어지면 결정적입니다.
시간은 항상 **코퍼스 시계**입니다 — 세션 자체의 최신 순간, 재귀적으로 관리자를 위한
최신 자식 워터마크 — 절대 벽 시계 아님. 노드 아이덴티티는 LLM 산문에 의존하지 않습니다.
린트 프로브는 에이전트 계층 노드에 타임스탬프/카운터 모양의 메타데이터를 거부합니다.
정확히 그 클래스의 상태가 바이트 멱등을 이전에 깼기 때문입니다.

전체 설계 근거: `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
