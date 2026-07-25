# 튜닝 참조 — 환경 변수

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae가 환경에서 읽는 모든 설정, 기본값, 그리고 실제로 변경해야 하는 시점을 설명합니다.
여기의 모든 것이 필수는 아닙니다. 기본값은 일반 `tesserae compile` 명령이 올바르게 작동하도록 선택되었습니다.

프로젝트 및 전역 설정(`.tesserae/config.json`, `~/.tesserae/config.json`)은
LLM 백엔드 설정에 우선권을 가집니다. 아래의 환경 변수는 설정된 실행에서 둘 다 override합니다.

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
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Project registry 위치 |
| `TESSERAE_DISCOVERY_CACHE` | — | Session-discovery cache |
| `TESSERAE_ARXIV_CACHE` | — | arXiv metadata cache |
| `TESSERAE_NO_FEDERATION_CACHE` | off | federated-graph LRU 비활성화 |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | off | combined cross-project graph emit |
| `TESSERAE_FLEET_PIDFILE` | — | Engine fleet pidfile |
| `TESSERAE_CLIP_TOKEN` | — | web clipper용 shared secret |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | off | schema-drift proposals 적용 (`tesserae lab`) |

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
