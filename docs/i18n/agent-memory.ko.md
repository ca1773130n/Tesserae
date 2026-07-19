# 레이어드 에이전트 메모리 — 에이전트별 지식 그래프

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.zh.md">中文</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.fr.md">Français</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

누구도 모든 것을 기억할 수 없고, 어떤 에이전트의 컨텍스트 윈도우도 모든 것을 담을 수 없습니다.
Tesserae의 답은 **레이어드 지식 베이스**입니다. 모든 에이전트는 자신의 세션에서
자신의 메모리를 키우고, 그 메모리는 주기적으로 **압축**됩니다(조직화, 압축, 연마, 정제 —
그리고 안전하게 잊혀집니다). 관리자는 자신의 리포트의 압축된 레이어만 봅니다.
관리자의 관리자는 추가 롤업을 봅니다. 실제 조직처럼, 어떤 단일 리더도 전체 아카이브가
필요하지 않습니다.

아래의 모든 것은 옵트인이며 추가적입니다. `tesserae distill`을 실행하지 않는 프로젝트는
이전처럼 정확하게 동작합니다.

## 레이어들

- **L0 — 프로젝트 그래프** (`.tesserae/graph.json`). 변경되지 않음, 여전히
  바이트 동일성을 유지합니다. 컴파일의 구조적 패스는 이제 관찰된 에이전트마다 하나의
  `Agent` 노드를 생성하고 각 세션으로부터 `performed_by` 엣지를 생성합니다 —
  원시 속성, LLM 비용 없음.
- **L1 — 에이전트당 하나의 아티팩트** (`.tesserae/agents/<key>/distilled.graph.json`).
  `tesserae distill`이 작성합니다. 일반 그래프 파일은 **하나의 48k 읽기**로
  제한되므로, 어떤 에이전트든 단일 호출에서 전체 압축된 메모리를 로드할 수 있습니다.
- **L2 — 관리자 롤업.** 리포트가 있는 에이전트를 압축할 때 리포트의 L1을 롤업합니다.
  혈통으로 중복 제거, 공유 원시 증거로 그룹화, 최고의 노트는 **문자 그대로** 가져갑니다 —
  LLM 재요약 깊이는 1로 제한되므로, 요약은 요약의 의역이 절대 아닙니다. 같은 패스는
  모든 조직 깊이로 재귀합니다.

## 에이전트 아이덴티티

에이전트는 `harness:account:role`로 키됩니다 — 역할 등급이므로 `reviewer` 서브에이전트와
`planner` 서브에이전트는 한 기계에서도 *다른* 전문성을 키웁니다. 역할은 트랜스크립트의
서브에이전트 디스크립터에서 비롯되고, 선언적 레지스트리 매칭 규칙에서 나오며,
`default`로 폴백됩니다.

```bash
tesserae agents init         # 세션 스캔, .tesserae/agents/registry.json 제안
tesserae agents list         # 관찰된 키, 레이블, 부모, 세션 수
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # 아티팩트 디렉토리 + 레지스트리 원자적으로 마이그레이션
```

영구 설정 필요 없음: 관찰된 모든 에이전트는 암시적으로 `org:root`에 리포트하고,
`agent="org"`는 레지스트리 없이 완전한 팀 개요를 제공합니다.

## 압축

```bash
tesserae distill                      # 모든 에이전트, 리프 우선, 관리자 마지막
tesserae distill --agent <key>        # 하나의 에이전트
tesserae distill --dry-run            # LLM 호출 예측, 아무 것도 작성하지 않음
tesserae distill --max-llm-calls 50   # 하드 예산; 제한된 실행은 재실행에서 수렴
tesserae distill --retry-fallbacks    # 폴백된 클러스터 재시도
tesserae distill --full               # 워터마크 무시, 처음부터 재압축
```

패스는 에이전트의 발견을 클러스터링하고, 각 클러스터를 요약합니다(인용
화이트리스트됨 및 신뢰성 린트됨), 압축된 노트를 생성합니다. 이 노트의 아이덴티티는
**혈통 키**입니다 — 아래의 원시 L0 증거의 해시, LLM의 표현이 절대 아닙니다. 캐싱은
공격적이고 공유됩니다: 변경되지 않은 입력은 워터마크 스킵되고, 성장하는 클러스터는
증분 폴드인, 제공자 실패는 회로 차단되고 결정적 구조 폴백을 생성합니다(플래그됨,
재시도 가능, 성공으로 캐시되지 않음).

압축은 **옵트인**입니다. `TESSERAE_AGENT_DISTILL=1` 설정 또는
`{"agent_distill": {"enabled": true}}`를 `config.json`에 설정합니다. 활성화되면,
`tesserae refresh`도 자동으로 압축합니다 — 하지만 *메모리 압력* 하에 있는 에이전트만
(압축되지 않은 발견이 더 이상 컨텍스트 읽기의 절반에 맞지 않음), MemGPT 스타일의
통합 트리거.

## 잊기 — 절대 삭제 아님

- **Absorb**: 감소, 낮은 신뢰도 발견이 llm 품질 압축된 노트에 의해 커버되면
  그것에 폴드되고(`absorbed_refs`), 기본 읽기에서 억제됩니다 — 하지만
  `include_superseded` 및 `drill_down`을 통해 도달 가능합니다.
- **Demote**: 다른 모든 것은 최악의 경우 전체 본문에서 제목+참조 행으로 떨어집니다.
  나이만으로는 지식이 보이지 않게 됩니다.
- **Ledger**: 모든 승격/강등/흡수는 망각 원장에 추가되고 `tesserae lint`로 표면화됩니다
  (`AGENT_FORGET_LEDGER`). 에이전트당 압축되지 않은 백로그 메트릭도 함께(`AGENT_UNDISTILLED_BACKLOG`).

## 에이전트로 읽기 — `agent=` 인수

모든 그래프 읽기 MCP 도구는 `agent=`를 수용합니다:

- **워커 키** → 자신의 원시 경험 ∪ 자신의 압축된 노트, 압축된 선호(흡수된 원시는
  로드 시 파생된 오버레이로 자동 억제됨 — 아무 것도 `graph.json`으로 다시 작성되지 않음).
- **관리자 키** → 리포트의 L1 아티팩트만의 연합. 원시 발견은 절대 위로 누출되지 않습니다.
- **`org`** → 모든 압축된 아티팩트, 영구 설정 없음.

지원 도구: `agent_view_explain`(멤버 + `distilled_through` 부실 워터마크 —
각 리포트의 전문성이 얼마나 오래되었는지), 그리고 `drill_down`(압축된 노트의
`member_refs`를 원시 L0 증거로 해결. 살아있음/변경됨/흡수됨/사라짐 상태 —
모든 호출 감사 로그됨). `compile_context --multi-pool`는 압축된 노트와 전문성 프로필을
위해 예산 슬롯을 예약하고 출력에서 부실 또는 폴백 품질 지식에 레이블을 붙입니다.

## 성장 루프

- **에이전트별 하네스**: `write_harness` 에이전트 모드는 해당 에이전트의
  해결된 뷰에 도달하는 MCP 구성을 포함한 에이전트별 하네스 디렉토리를 내보냅니다.
  플러스 한 번만 시드되는 `purpose.md` 미션 페이지가 그 전문성 프로필에서 생성됩니다.
- **에이전트별 가이던스**: `.tesserae/extraction-guidance-<key>.md`를 통해
  프로젝트 레벨 `.tesserae/distill-guidance.md` 위에 계층화된 하나의 에이전트의
  압축을 조종합니다. 하나의 에이전트의 스트림을 편집하면 그 에이전트만 재압축합니다.
- **의미 다리**(옵트인): 매니저/조직 뷰에서 `shares_concept_with` 엣지로
  에이전트 간의 *관련* 압축된 노트를 링크하세요 — 엣지, 절대 병합 아님.
- **주제 맵**: `agent_topics`는 에이전트의 압축 세트를 결정적 `topics.md`로 롤하세요 —
  에이전트의 목차.
- **서브에이전트 승격**: 타입된 서브에이전트 실행은 서브에이전트 자신의 키 아래에서
  발견을 생성하므로, 위임된 작업은 위임자의 전문성에 축적됩니다.

## 결정성 보장

프로젝트 그래프는 바이트 동일성을 유지합니다. 압축된 아티팩트는
(그래프 바이트, 레지스트리, 캐시 디렉토리, 이전 아티팩트, 옵션)이 주어지면 결정적입니다.
시간은 항상 **코퍼스 시계**입니다 — 세션 자체의 최신 순간, 재귀적으로 관리자를 위한
최신 자식 워터마크 — 절대 벽 시계 아님. 노드 아이덴티티는 LLM 산문에 의존하지 않습니다.
린트 프로브는 에이전트 계층 노드에 타임스탬프/카운터 모양의 메타데이터를 거부합니다.
정확히 그 클래스의 상태가 바이트 동일성을 이전에 깼기 때문입니다.

전체 설계 근거: `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
