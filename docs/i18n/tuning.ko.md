# 튜닝 참조 — 환경 변수

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae가 환경에서 읽는 모든 설정, 기본값, 그리고 실제로 변경해야 하는 시점을 설명합니다.
여기의 모든 것이 필수는 아닙니다. 기본값은 일반 `tesserae compile` 명령이 올바르게 작동하도록 선택되었습니다.

프로젝트 및 전역 설정(`.tesserae/config.json`, `~/.tesserae/config.json`)은
LLM 백엔드 설정에 우선권을 가집니다. 아래의 환경 변수는 설정된 실행에서 둘 다 override합니다.

---

## 돈을 쓰는 훅

Claude Code 플러그인은 백그라운드 컴파일을 시작할 수 있는 훅을 제공합니다. 지출하는 모든 것은 **기본값으로 비활성화됩니다**:

```sh
export TESSERAE_HOOK_AUTOCOMPILE=1   # 자동 재컴파일 선택
```

게이트됨: `posttooluse-edit.sh` (모든 Edit/Write에서 실행) 및 `session-end.sh`. 게이트되지 않음, 비용이 들지 않기 때문: `session-start.sh`는 `tesserae code sync`를 실행하며, 이는 결정론적이고, `pretooluse-compile.sh`은 당신이 직접 입력한 `tesserae compile`만 가로챕니다.

이 기본값이 존재하는 이유는 측정되었기 때문입니다. `~/.tesserae`의 지식 기반은 `$HOME`을 프로젝트 루트처럼 보이게 만들고, 훅 해석기는 작업 디렉터리에서 **위로** 첫 번째 `.tesserae/`을 찾아서 걸어갔습니다 — 따라서 등록된 프로젝트 외부의 모든 세션은 `$HOME`으로 해석되고 전체 홈 디렉터리를 컴파일했습니다: 15k 파일, 795 MB 그래프, **~10시간의 LLM 지출**, 세션을 시작한 분리된 프로세스에서.

`resolve_project_root()`는 이제 경로로든 `$HOME`을 거부하고, 작업 디렉터리로 폴백하지 않고 빈 값을 반환하므로, 호출자들은 추측 대신 no-op을 수행합니다. 백그라운드 모델 작업을 수행하는 훅은 청구서가 도착한 후 끄는 것이 아니라 의도적으로 켜져야 합니다.

---

## 추출

### `TESSERAE_EXTRACT_TIMEOUT`

**기본값 `1800`(초), 시도당.** 각 codex/claude 추출 호출을 제한하여
wedged CLI 자식이 compile을 hang할 수 없도록 합니다.

이것은 실제로 일어났습니다: compile이 **5 h 43 m** 동안 0% CPU에서
관찰되었고, **4 h 6 m** 동안 idle 상태인 `codex exec` 자식이 뒤에 있었으며,
`.tesserae/compile.lock`을 계속 유지하고 있었습니다. 이미 32개의 커뮤니티 요약을
메모리에 작성했지만 persist되지 않았습니다.

시도당이며, 문서당이 아닙니다. timeout에서 클라이언트는 다음
`CODEX_HOME` / claude 설정 디렉터리로 rotate하므로, 한 문서의 최악의 경우는
`timeout × 설정된 프로필 수`입니다.

```sh
export TESSERAE_EXTRACT_TIMEOUT=3600   # 매우 큰 문서에 대한 더 많은 여유
export TESSERAE_EXTRACT_TIMEOUT=0      # cutoff 없음 — 완료될 때까지 실행
```

설정되었지만 사용할 수 없는 값(`10m`, `600s`, 음수, `inf`)은 stderr에서 경고하고
기본값을 유지합니다. typo가 safety valve를 조용히 해제해서는 안 됩니다.

### `TESSERAE_EXTRACT_CONCURRENCY`

**기본값 `4`.** 병렬로 추출된 문서. 각각은 대략 1분이 걸리는 blocking CLI
subprocess이므로, sequential loop는 wall-clock을 모든 model round-trip의 합계로 만듭니다 —
161개 문서에서 ~2 h 40 m로 측정됨.

상한선은 컴퓨터가 아니라 provider 계정의 rate limit입니다. 이것이 기본값이
modest인 이유입니다. strictly sequential 동작을 위해 `1`로 설정합니다.

Concurrency는 출력을 절대 변경하지 않습니다: work-list는 경로 순서로 고정되고
결과는 인덱스별로 수집되므로, parallel 실행은 sequential 실행과 byte-identical합니다.

### `TESSERAE_LLM_CACHE`

**기본값 on.** CLI provider 응답의 content-addressed cache는
`~/.tesserae/llm_cache` 아래에 있으며, (document, kind, guidance) 및 model과
reasoning effort로 keyed합니다 — 따라서 model을 switching하면 이전 model의 답변을
serving하는 대신 re-ask합니다. Parseable 응답만 stored되므로, 하나의 bad generation도
permanent가 될 수 없습니다.

```sh
export TESSERAE_LLM_CACHE=0   # 항상 re-ask
```

### `TESSERAE_LLM_CHUNK_CHARS`

문서가 한 호출에 너무 클 때 chunk당 문자 수. context limit에 hitting하지 않는 한
설정하지 않은 상태로 둡니다.

---

## LLM 백엔드

| 변수 | 기본값 | 노트 |
|---|---|---|
| `TESSERAE_LLM_PROVIDER` | `claude` | `codex`, `claude`, `anthropic`, `custom` |
| `TESSERAE_LLM_MODEL` | provider-specific | provider로 scoped하여 claude-shaped model이 절대 codex path에 landing하지 않음 |
| `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | Structured extraction은 interactive work에 설정할 수 있는 `xhigh`를 필요로 하지 않습니다 — `xhigh`는 multi-document compile을 여러 배 느리게 만듭니다 |

`tesserae config status`는 resolved backend를 print하고 liveness를 위해 ping합니다.

---

## Compile 패스

| 변수 | 기본값 | 제어 대상 |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **on** | GraphRAG-style summary pass. ≥ 5 members인 cluster당 LLM 호출 1회, membership digest로 cached. `false`/`0`/`no`/`off` 비활성화 |
| `TESSERAE_ENABLE_LLM_PASSES` | off | 추출 이상의 optional LLM enrichment passes |
| `TESSERAE_AGENT_DISTILL` | off | Per-agent L1 expertise artifacts (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | off | Runbook/Gotcha distilled-memory nodes |
| `TESSERAE_SESSION_EVENT_PASS` | **on** | 세션 트랜스크립트의 턴별 `Event` 노드. LLM을 쓰지 않고 바이트 단위로 결정적이지만, 유의미한 턴마다 노드 하나씩 — 긴 코퍼스에서는 규모가 커집니다. `false`/`0`/`no`/`off`로 비활성화 |
| `TESSERAE_INSIGHT_SYMBOL_LINK` | on | session insights를 code symbols에 link |
| `TESSERAE_SUPERSEDE_PASS` | on | 수정된 claims 사이의 `superseded_by` edges |
| `TESSERAE_PROMPT_SIGNATURES` | off | drift detection을 위한 prompt signatures 기록 |
| `TESSERAE_COMPILE_LOCK_WAIT` | — | `.tesserae/compile.lock`을 기다릴 초 단위의 초 |

**커뮤니티 요약 정보:** compile pass는 가장 coarse level을 eagerly cover합니다;
`graph_map`은 추가로 cold scope에 처음 descend할 때 summary를 lazily materializes하고,
level당 cached합니다. pass를 끄는 것은 legitimate cost strategy입니다 —
실제로 방문하는 branches에만 pay합니다 — 하지만 한 가지 주의가 있습니다:
**federated descent는 절대 lazily materializes하지 않습니다.** sibling project의 cards는
in-graph summaries나 이미-warm caches에서만 named될 수 있으므로,
cross-project navigate하는 project는 eager pass를 원합니다.

---

## 쿼리 및 합성

| 변수 | 기본값 | 노트 |
|---|---|---|
| `TESSERAE_QUERY_LLM` | off | `tesserae query`에 대한 LLM planner |
| `TESSERAE_QUERY_DRY_RUN` | off | model을 호출하지 않고 plan |
| `TESSERAE_SYNTHESIS_LLM` | off | `tesserae ask`의 prose synthesis |
| `TESSERAE_SYNTHESIS_MODEL` | — | synthesis model override |
| `TESSERAE_SYNTHESIS_WORKERS` | — | Parallel synthesis workers |
| `TESSERAE_SYNTHESIS_DRY_RUN` | off | model skip, pipeline exercise |

---

## 경로 및 인프라

| 변수 | 기본값 | 노트 |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Project registry 위치. **모든** 명령이 이를 존중합니다 — 0.28.7 이전에는 엔진의 fleet 모드만 이것을 읽었기 때문에, 다른 곳에서 설정해도 조용히 아무 효과가 없었고 명령들은 계속 진짜 registry를 사용했습니다 |
| `TESSERAE_HOST_ID` | `~/.tesserae/host_id`에 한 번 생성됨 | 이 머신의 정체성. [여러 머신을 하나의 프로젝트에 붙여 돌리기](#여러-머신을-하나의-프로젝트에-붙여-돌리기) 참조 |
| `TESSERAE_DISCOVERY_CACHE` | — | Session-discovery cache |
| `TESSERAE_ARXIV_CACHE` | — | arXiv metadata cache |
| `TESSERAE_NO_FEDERATION_CACHE` | off | federated-graph LRU 비활성화 |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | off | combined cross-project graph emit |
| `TESSERAE_FLEET_PIDFILE` | — | Engine fleet pidfile |
| `TESSERAE_CLIP_TOKEN` | — | web clipper용 shared secret |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | off | schema-drift proposals 적용 (`tesserae lab`) |

---

## 여러 머신을 하나의 프로젝트에 붙여 돌리기

이 절이 상정하는 형태: 여러 대의 서버가 각각 코딩 에이전트를 돌리고, 각자
자신의 로컬 세션 트랜스크립트를 가지며, 디스크를 공유합니다 — 그래서 같은
프로젝트 디렉터리와 같은 `.tesserae/`를 봅니다.

**한 호스트에게 compile을 맡기고, 나머지는 수집만 하게 하세요.**

```bash
# on the compiling host
tesserae engine

# on every other host
tesserae engine --harvest-only
```

`--harvest-only`는 그 머신의 로컬 트랜스크립트를 공유 세션 저장소로 tail할 뿐,
프로젝트의 compile lock은 절대 잡지 않습니다. 경합을 중재하는 대신 아예
없애는 쪽이고, 그래서 timeout을 조절하는 것보다 낫습니다.

**실패시키는 대신 줄을 서게 하고 싶을 때**는 `--wait`을 넘기세요:

```bash
tesserae compile --wait          # up to 30 min, reporting every 5s
tesserae compile --wait 120      # or name your own bound
```

이것이 없으면 lock이 잡혀 있는 것을 발견한 compile은 2로 종료합니다 — 훅에는
맞는 동작이고, 사람에게는 속 터지는 동작입니다. `--wait`이 stdout이
터미널인지에서 유추되지 않고 플래그인 이유는, 같은 명령이 `tee` 아래에서, tmux
캡처에서, CI에서 동작을 바꿔서는 안 되기 때문입니다.
`TESSERAE_COMPILE_LOCK_WAIT=<seconds>`는 프로세스 트리 전체에 대해 같은 일을
합니다.

**모든 프로젝트를 최신으로 유지**하기, 한 번의 호출로:

```bash
tesserae refresh --all               # every registered project, sequentially
tesserae refresh --all --jobs 3      # three at a time
tesserae compile --all --name alpha --name beta
```

한 프로젝트가 실패해도 나머지가 멈추지는 않습니다. 하나라도 실패했으면 종료
코드 `2`, 하나라도 다른 실행에 lock되어 있었으면 `1`, 전부 실행되었으면
`0`입니다. `--jobs`의 기본값이 1인 이유는 compile이 LLM을 많이 쓰기 때문이며,
값을 올리면 할당량을 병렬로 소모합니다.

### 무엇이 이것을 안전하게 만드는가

머신별 상태는 예전에 하나의 공유된 이름 아래 저장되어 모든 호스트가
읽었습니다. 아래 각각은 이제 호스트 id로 분할됩니다:

| 상태 | 위치 | 왜 호스트별이어야 하는가 |
|---|---|---|
| 세션 레코드 | `.tesserae/harness_sessions/` | 호스트는 자신이 수집한 것만 삭제합니다. 그러지 않으면 호스트 B가 호스트 A의 세션을 삭제하고 성공했다고 보고합니다 — 모든 호스트의 스캔이 같은 producer를 스탬프하고 각자의 `~/.claude` 경로도 동일하게 해석되므로, 그 외에 둘을 구별할 것이 없습니다 |
| 엔진 pidfile | `.tesserae/daemon.<host>.pid` | liveness는 **로컬** 프로세스 테이블을 상대로 한 `os.kill(pid, 0)`입니다; 다른 머신이 기록한 pid는 아무 관련 없는 로컬 프로세스를 상대로 판정됩니다 |
| Codex 스캔 하한선 | `.tesserae/harness_sessions.db` | 공유 watermark 하나는, 마지막으로 실행한 호스트가 다른 호스트는 아직 읽지 않은 트랜스크립트 너머로 그것을 밀어 버린다는 뜻이었습니다 — 그 트랜스크립트들은 아예 가져와지지 않았습니다 |

호스트 id는 `~/.tesserae/host_id`에 한 번 생성되며(공유되는 프로젝트 디렉터리가
**아니라** 머신마다 따로), `TESSERAE_HOST_ID`로 고정할 수 있습니다. 호스트명이
아니라 영속화된 id인 이유는 하나의 이미지로 찍어낸 fleet이 호스트명을
재사용하고, 충돌이 나면 한 머신의 레코드를 다른 머신에게 넘겨 버리기
때문입니다.

### 당신이 직접 시험해 봐야 할 전제

위의 모든 것은 `.tesserae/`를 담고 있는 파일시스템이 `flock(2)`을
**강제한다**고 전제합니다. NFS와 SMB에서 그것은 설정에 따라 달라지고, 동작하는
lock 데몬이 없으면 `flock`은 조용히 no-op으로 퇴화할 수 있습니다 — 그 순간 두
호스트가 각자 배타적 lock을 쥐고 있다고 믿으면서 같은 프로젝트를 동시에
compile합니다.

`tesserae doctor`는 프로젝트가 네트워크 파일시스템 위에 있으면 경고하지만,
단일 호스트는 호스트 간 강제를 **증명할 수 없습니다**. 실제 하드웨어에서 직접
시험하세요: 호스트 A에서 lock을 잡고 호스트 B가 거부되는지 확인하는 겁니다.

---

## 저하된 corpus 복구

Document에 대한 추출이 실패하면, deterministic baseline으로 served되며
`.tesserae/manifest.json`에서 **표시**됩니다. 표시 없이는 clean extraction과
구별할 수 없으므로, `--changed-only`는 forever skip하고 degradation은
file의 자체 content가 변경될 때까지 permanent입니다.

```sh
tesserae compile --changed-only --retry-fallbacks
```

표시된 문서만 re-attempt합니다; clean ones는 skipped 상태로 유지됩니다.

## 계층 구조 검사

```sh
tesserae graph-map                          # root map
tesserae graph-map --scope <scope_id>       # descend
tesserae graph-map --scope '<alias>::'      # a sibling registered project
```

각 card는 hierarchy sidecar에서 `size`와 `leaf_member_count`를 report하고,
plus `live_member_count` — *current* graph가 실제로 carry하는 members 수.
`0`이 있는 곳은 scope이 dead입니다 (sidecar/graph skew): skip하지 말고
descend합니다.

## 에이전트가 그래프에 쓰기

\`graph_write\` (MCP)는 스키마 검증 타입 노드와 엣지를 필수 출처와 함께 받으므로, 에이전트는 발견을 추출기가 추측해야 할 산문이 아닌 *구조*로 기록합니다.

강제하지 않고 거부합니다: 타입 없는 엣지, 제어된 어휘 외 노드 또는 엣지 타입, 끝점 없음, 그리고 출처가 없는 쓰기는 모두 거부됩니다. 중복 쓰기는 멱등입니다. 에이전트가 쓴 노드는 전체 재컴파일, 삭제된 \`graph.json\`, \`--limit\`, 그리고 전체 말뭉치 삭제를 견딥니다.

## 그래프에 대해 주장 검증

\`verify_claim\` (MCP)는 그래프가 트리플을 허가하는지 답합니다. \`(subject, predicate, object)\`를 받습니다 — **자연 언어 매개변수가 없습니다**, 설계상 이유로, 파서가 이전 버전으로 하여금 자신이 지원했던 주장의 부정에 SUPPORTED로 답하게 했기 때문입니다.

판정은 그래프 바이트의 순수 함수입니다: LLM, 임베딩, 결정 경로의 어디든 퍼지 매칭이 없습니다.

| 판정 | 의미 |
|---|---|
| \`SUPPORTED\` | 엣지가 존재하고, 자체 증거를 전달하고, 그 텍스트는 소스 파일에 대해 재접지되었습니다 |
| \`PRESENT_UNEVIDENCED\` | 엣지가 존재하지만 문서 기반이 뒤를 서지 않습니다 |
| \`CONTRADICTED\` | 같은 두 끝점 사이에 문서 기반 \`contradicts_claim\` |
| \`DISPUTED_UNEVIDENCED\` | 주장된 불일치, 증거가 없습니다 |
| \`CONFLICTING\` | 둘 다 문서 기반 — 도구가 판정을 거절합니다 |
| \`ABSENT\` | 이 그래프는 트리플을 주장하지 않습니다. 반박이 아닙니다 |
| \`NOT_RESOLVABLE\` | 끝점 또는 술어를 정확하게 해결할 수 없습니다 |

의도적으로 하지 않는 두 가지가 있습니다. \`supersedes\`를 반박으로 간주하지 않습니다 — 그 관계는 트리플이 거짓이라는 뜻이 아니라 *노드*가 교체되었다는 뜻입니다. 그리고 에이전트 쓰기는 출처 클래스를 *약화*만 할 수 있고, 하나를 업그레이드할 수 없으므로, 에이전트가 주장하는 것은 문서 기반으로 제시될 수 없습니다.

결과를 읽을 때 알 만한 가치가 있습니다: 15,284개 엣지가 있는 실제 그래프에서 약 40%의 \`SUPPORTED\` 판정은 동어반복입니다 — 인용된 범위가 엣지의 자신의 목표인 \`evidenced_by\` 엣지. 참이지만, 정보가 아닙니다.

## 질문을 라우팅하기

\`tesserae ask\` 질문 형태로 검색 경로를 선택합니다: 단일 엔티티 조회는 저렴한 백엔드로 가고, 멀티홉 / "뭐가 바뀌었는가" / "왜" / 말뭉치 전체 질문은 그래프로 갑니다. 독립적인 벤치마크는 그래프가 멀티홉, 시간적 및 합성 질문에서 앞서고, 간단한 사실 조회와 비용에서 *뒤떨어진다*는 것을 보여줍니다 — 따라서 모든 질문에 그래프 가격을 내는 것은 손해입니다.

결정은 반환된 봉투에 나타나므로, 저렴한 답변은 감사할 수 있습니다. \`--route\`로 CLI에서 또는 MCP 도구의 \`route\` 매개변수로 재정의하세요.
