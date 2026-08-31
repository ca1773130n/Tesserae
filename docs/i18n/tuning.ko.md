# 튜닝 참조 — 환경 변수

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae가 환경에서 읽는 모든 설정, 기본값, 그리고 실제로 변경해야 하는 시점을 설명합니다.
여기의 모든 것이 필수는 아닙니다. 기본값은 일반 `tesserae compile` 명령이 올바르게 작동하도록 선택되었습니다.

LLM 백엔드 설정은 `.tesserae/config.json` 및 `~/.tesserae/config.json`에도 있으며,
아래의 환경 변수는 둘 다 설정된 실행에서 override하고, [LLM backend](#llm-backend)에서
전체 순서를 명시합니다.

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
`~/.tesserae/llm_cache` 아래에 있으며, 실제로 보낸 prompt의 digest 및 model과
reasoning effort로 keyed됩니다 — 따라서 다른 질문은 re-ask되고, model을 switching하면 이전 model의 답변을
serving하는 대신 re-ask됩니다. Parseable 응답만 stored되므로, 하나의 bad generation도
permanent가 될 수 없습니다.

예전 항목들은 설계상 도달할 수 없습니다: key는 이전에
prompt의 digest가 아니라 calling stage에 의해 제공된 label이었으므로,
관련 없는 질문들이 한 항목을 공유할 수 있었습니다. 이들은 마이그레이션되지 않습니다 — directory는
안전하게 delete할 수 있으며, compile이 그것을 다시 채울 것입니다.

```sh
export TESSERAE_LLM_CACHE=0   # 항상 re-ask
```

### `TESSERAE_LLM_CHUNK_CHARS`

문서가 한 호출에 너무 클 때 chunk당 문자 수. context limit에 hitting하지 않는 한
설정하지 않은 상태로 둡니다.

---

## LLM 백엔드

어떤 백엔드가 답변하고, 어떤 와이어를 통해, 어떤 자격증을 사용할지. 아래의 모든 키는
같은 방식으로만, 그리고 오직 이 방식으로만 resolve합니다:

**`TESSERAE_*` env var → project `.tesserae/config.json` → `~/.tesserae/config.json` → 내장 기본값.**

| Config 키 | 환경 변수 | 기본값 | 노트 |
|---|---|---|---|
| `llm_provider` | `TESSERAE_LLM_PROVIDER` | `claude` | `claude`, `codex`, `anthropic`, `openai`, `custom` 중 하나. 다른 것은 이름으로 거부됩니다 — typo는 전에는 조용히 `claude`로 처리되어 선택한 모델 없는 anthropic에 대해 실행되고 선택하지 않은 모델에 대한 오류를 보고했습니다 |
| `llm_api_style` | `TESSERAE_LLM_API_STYLE` | `llm_provider`가 `openai`일 때 `openai`, 아니면 `anthropic` | 와이어 프로토콜로, 백엔드와는 다른 질문입니다. `anthropic`은 Anthropic SDK를 통해 `{base_url}/v1/messages`에 post합니다; `openai`는 `{base_url}/chat/completions`에 post합니다 |
| `llm_model` | `TESSERAE_LLM_MODEL` | `sonnet` (claude CLI), `gpt-5.6-luna` (codex CLI), `claude-sonnet-4-6` (anthropic 와이어), `gpt-4o-mini` (openai 와이어) | 두 CLI 백엔드에서 provider로 scoped하여 claude-shaped model이 절대 codex path에 landing하지 않음. 설정된 엔드포인트 provider는 provider와 model이 다른 config layer에서 설정되었을 때도 model을 유지합니다 |
| `llm_base_url` | `TESSERAE_LLM_BASE_URL`, 그리고 `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` (anthropic 와이어), `https://api.openai.com/v1` (openai 와이어) | 엔드포인트로, 각 와이어가 append하는 것으로 잘라집니다 — [custom endpoints](#custom-endpoints) 참조 |
| `llm_api_key` | `TESSERAE_LLM_API_KEY`, 그리고 `ANTHROPIC_API_KEY` | — | `api-key` 자격증: anthropic 와이어에서 `X-Api-Key`, openai 와이어에서 `Authorization: Bearer` |
| `llm_auth_token` | `TESSERAE_LLM_AUTH_TOKEN`, 그리고 `ANTHROPIC_AUTH_TOKEN` | — | bearer 자격증으로 둘 다 와이어에서 `Authorization: Bearer`. **이것 또는** `llm_api_key` 설정하세요: anthropic 와이어에서 token은 SDK에 `auth_token=`로 전달되고 api key는 설정되지 않으므로 둘은 절대 충돌하지 않습니다 |
| `llm_allow_fallback` | `TESSERAE_LLM_ALLOW_FALLBACK` | off | 설정된 엔드포인트 provider가 실패할 때 다른 백엔드로 넘어갈 수 있게 합니다 — [an endpoint provider is a contract](#an-endpoint-provider-is-a-contract) 참조. env var의 공백이 아닌 값이 켭니다 |
| `llm_claude_config_dirs` | `TESSERAE_CLAUDE_CONFIG_DIRS` | CLI의 자체 기본값 | 로테이션 순서로 Claude 설정 디렉터리로, env var에서는 `os.pathsep`으로 구분 — 반복된 `--claude-config-dir`의 환경 변수 통로. *설정된* 목록만 권위를 가집니다. 주변 환경의 `CLAUDE_CONFIG_DIR`는 의도적으로 권위가 없는데, 거기에 고정하면 다중 계정 로테이션이 한 계정으로 붕괴하기 때문입니다 |
| `llm_codex_homes` | `TESSERAE_CODEX_HOMES` | CLI의 자체 기본값 | Codex homes로 같은 모양, 같은 논리입니다. 예전의 단수 `llm_codex_home`은 여전히 작동하고 `one-home` 목록을 의미합니다 |
| `llm_codex_reasoning_effort` | `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | Structured extraction은 interactive work에 설정할 수 있는 `xhigh`를 필요로 하지 않습니다 — `xhigh`는 multi-document compile을 여러 배 느리게 만듭니다 |

`ANTHROPIC_*` 이름은 여전히 작동하며 Tesserae 소유의 이름 하나 아래에 있습니다: 이들은
주변 환경입니다 — 모든 Claude Code 세션이 이들을 내보냅니다 — 그래서 Tesserae에서
구체적으로 설정한 값을 초과하지 않아야 하지만 config 파일 둘 다를 초과합니다.

`tesserae config llm`은 머신 전역 파일을 작성합니다; 한 프로젝트에 대해서는 같은 `llm_*`
키를 `.tesserae/config.json`에 놓으세요. 파일에 작성된 자격증은 **평문**으로 저장되므로
그 둘에 대해 `TESSERAE_LLM_API_KEY` / `TESSERAE_LLM_AUTH_TOKEN`를 선호하세요.

### 커스텀 엔드포인트

`llm_provider`는 어떤 백엔드인지 말합니다; `llm_api_style`은 어떤 HTTP 방언을 말합니다.
그들을 분리하는 것이 non-Anthropic 엔드포인트를 전혀 도달 가능하게 만듭니다: `custom`은
Anthropic 와이어를 암시하곤 해서, OpenAI 호환 서버는 설정할 곳이 없었습니다. 미설정
상태로 두면, `llm_api_style`은 여전히 `custom`에 대해 `anthropic`으로 resolve합니다 —
이것이 존재하기 전에 설정된 엔드포인트는 정확히 그렇게 동작을 유지합니다.

**OpenAI 호환 엔드포인트** — vLLM, LiteLLM, OpenRouter, Together, Ollama, LM Studio:

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=openai
export TESSERAE_LLM_BASE_URL=http://localhost:8000/v1
export TESSERAE_LLM_MODEL=qwen2.5-coder-32b-instruct
export TESSERAE_LLM_AUTH_TOKEN=sk-...   # 또는 TESSERAE_LLM_API_KEY — 여기서 same header
tesserae config status
```

요청은 `POST {base_url}/chat/completions`입니다. 이 와이어는 stdlib `urllib`이므로
extra install이 필요 없고, keyless local server는 자격증이 전혀 필요 없습니다 —
둘 다 미설정 상태로 두어도 여전히 build합니다.

**Anthropic 호환 엔드포인트** — Messages API를 말하는 gateway:

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=anthropic
export TESSERAE_LLM_BASE_URL=https://gateway.internal.example   # no /v1
export TESSERAE_LLM_MODEL=claude-sonnet-4-6
export TESSERAE_LLM_API_KEY=...         # X-Api-Key; bearer gateway의 경우 TESSERAE_LLM_AUTH_TOKEN
tesserae config status
```

요청은 Anthropic SDK를 통해 `POST {base_url}/v1/messages`입니다. 이 와이어와
`llm_provider: custom`을 둘 다 이것을 필요로 합니다:

```bash
pip install "tesserae[synthesis-llm]"
```

**`/v1`은 장식이 아닙니다.** SDK는 `/v1/messages` 자체를 append하므로,
모든 gateway README가 보여주는 `https://host/v1`은 `/v1/v1/messages`를 만들었습니다 — 404로
선택하지 않은 모델에 대한 오류를 읽었습니다. 하나의 trailing `/v1`은 이제 anthropic 와이어에서
제거되고 openai 와이어에서는 보장됩니다. 오직 하나의 trailing segment만이 절대
건드려지고 그것이 잘라지는 것은 그것이 선행하는 것입니다 — proxy가 정말
`/anthropic/v1`을 제공하면 `/v1`도 사라집니다 — 그래서 rewrite는 INFO로 log됩니다.
침묵하지 않고, 그리고 log line은 실제로 사용되는 URL이 있는 곳입니다.

### 엔드포인트 provider는 약정입니다

`anthropic`, `openai` 그리고 `custom`은 당신이 선택한 엔드포인트를 가져갑니다 — URL, model
이름, 자격증. 하나가 설정되면 그것만 build되고, 실패는 provider, 와이어, base URL,
model을 명명하고 어떤 종류의 자격증이 resolved되었는지를 명명하는 `LLMProviderConfigError`를 raise합니다.

전에는 선호도였습니다: build할 수 없는 custom 엔드포인트는 Claude CLI로 넘어갔으며,
그것은 `--model sonnet`으로 당신의 자신의 base URL에 대해 spawn되었고 configure하지
않은 unsupported model에 대한 오류를 보고했으며 실제 원인을 명명하는 것이 없었습니다. 그
chaining을 되찾으려면 `llm_allow_fallback: true`를 설정하세요.

두 OAuth CLI provider는 여전히 chain합니다 — 서로에게, 그리고 그들 뒤의 API client에게.
`claude`와 `codex`는 base URL을 취하지 않고 그들의 model은 per provider로 scoped합니다,
그래서 어느 것도 선택하지 않은 backend에 당신이 선택한 엔드포인트를 가져갈 수 없습니다.
그것이 약정이 존재하는 유일한 것을 방지하는 것입니다.

### Seeing what is actually in effect

```bash
tesserae config status                 # resolved backend + a live probe
tesserae config status --project .     # as this project's config.json sees it
tesserae config status --no-ping       # skip the probe, spend nothing
```

Provider, 와이어, model, base URL, 그리고 resolved credential의 *종류*를 print합니다 —
`api_key`, `auth_token` 또는 none, 절대 secret은 not — 각각 그것을 won layer로 tagged,
그 다음 그것에 대답한 client의 class와 identity. 그 client는 real run이 사용하는 같은 settings
dict에서 build되고, probe는 절대 cached되지 않으므로 passing line은 backend가 지금 방금
대답했다는 뜻입니다. 과거의 어떤 시점이 아니라.

호출이 fail할 때, 실패는 flattened가 아니라 classified됩니다: `401`과 `403`은 auth로,
`404` — 그리고 model을 naming하는 `400` — endpoint로 reported되며, 각각 그것을 produce한
endpoint를 명명합니다. 그 전에, misconfigured URL은 LLM을 설치하지 않은 것과 구별할 수
없었습니다.

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
| `TESSERAE_VERIFY_BAND` | on | `ask`의 불확실한 검토 플래그를 측정된 0.30–0.70 구간에서 모델로 다시 판정한다. `lo-hi`는 그 구간을 덮어쓴다. `off`는 토큰도 네트워크도 쓰지 않는 무료 플래그만 남긴다 |
| `TESSERAE_EMBEDDING_PREFER` | auto | 밀집 레인 인코더: `model2vec`(기본 탑재, 정적, torch 불필요), `st`(학습된 sentence-transformers 모델), `openai`, `hash`. 설정하지 않으면 설치된 것 중 첫 번째를 고른다 |
| `TESSERAE_ST_MODEL` | `BAAI/bge-base-en-v1.5` | `st`가 로드하는 sentence-transformers 모델; Hugging Face 이름 아무거나 |

### `TESSERAE_VERIFY_BAND`

모든 `ask` 답변은 비용이 들지 않는 문장별 검토 플래그를 함께 싣는다. 이 플래그는
모델에게 묻는 것보다 덜 정확하다 — 검증에서 제외한 문장 755개에서 0.870 대 0.926 —
그리고 차이의 거의 전부는 충실한 다른 말 바꾸기에 대한 오경보다. 그런 문장은 출처와
공유하는 어휘가 거의 없다.

둘은 서로 다른 문장에서 틀리므로, 무료 검사가 확신하지 못하는 곳에서만 모델에 값을
치르면 비용의 일부로 정확도를 되찾는다. 커버리지 0.30–0.70을 넘기면 호출의 42%로
0.932를 기록했다. 모든 문장을 묻는 것과 구별되지 않으면서(McNemar p=0.52) 지출은
42%다.

```bash
export TESSERAE_VERIFY_BAND=on          # 측정된 0.30-0.70 구간
export TESSERAE_VERIFY_BAND=0.40-0.60   # 더 좁게: 호출의 22%, 0.914
```

`ask` 안에서는 기본값이 켜짐이다. 모델 클라이언트가 이미 손에 있고 답변에 토큰을 이미
썼으므로, 정확한 플래그의 추가 비용은 작다. 모델 없는 검사 변형은 어느 것도 그 격차를
혼자 메우지 못한다 — 어간 추출, 문자 n-gram, 희귀도 가중, 로컬 임베딩을 각각 측정했고
어느 것도 단순 커버리지를 이기지 못했다 — 그래서 기본값은 더 영리한 무료 검사가 아니라
캐스케이드다. 라이브러리 함수 `check_against_evidence`는 그대로이며 여전히 비용이 없다.
엔벨로프는 `adjudicated`를 보고한다. 캐스케이드가 실행되지 않았으면 `null`, 실행되었으면
개수다. 답하지 못한 모델은 무료 판정을 그대로 남겨 둔다 — 실패한 호출이 플래그된 문장을
깨끗하게 만들 수는 결코 없다.

### `TESSERAE_EMBEDDING_PREFER`

`hybrid_search`의 밀집 레인은 `active_embedding_backend`가 먼저 찾는 것으로
임베딩한다: 기본 탑재된 `model2vec` 정적 모델(8 MB, torch 불필요, 오프라인),
그다음 sentence-transformers, 그다음 해시 스텁. 정적 모델 덕분에
`pip install tesserae`가 작게 유지되고, 작은 코퍼스에서는 측정 가능한 비용이
없다. 큰 코퍼스에서는 이것이 병목이다: 논문 148편에서 서로 다른 문서
기준 재현율이 기본 모델로는 0.754 @10 / 0.914 @50, 같은 융합에
`BAAI/bge-base-en-v1.5`를 넣으면 0.791 / 0.962였다 — 밀집 레인 단독으로는
0.473에서 0.680 @10으로 올랐다. 같은 청크 위의 단순 벡터 저장소는 nomic-embed-text로
0.784 / 0.942, 같은 bge-base로 0.775 / 0.944다; 그래프의 우위는 57개 질문에서
잡음 범위 안이다(짝지은 부호 검정 p=1.0 @10, 0.51 @50). 학습된 인코더는
그래프를 그것보다 뒤처지지 않고 대등하게 만드는 요소다.

```bash
uv pip install sentence-transformers          # torch, ~2 GB with the model
export TESSERAE_EMBEDDING_PREFER=st
export TESSERAE_ST_MODEL=BAAI/bge-base-en-v1.5   # the default; any Hugging Face name
```

`auto`는 여전히 정적 모델을 먼저 고르므로, 이 변수를 설정한 적 없는 설치는
이전과 똑같이 동작한다. 선호값은 백엔드가 처음 결정될 때 한 번만 읽는다;
어떤 백엔드도 가리키지 않는 값은 해시 스텁으로 조용히 흘러가지 않고 보고된
뒤 무시된다. 학습된 인코더는 벡터를 캐시하지 않으면 질의마다 모든 노드를
다시 임베딩한다 — `compile_context`와 MCP 서버는 이미 프로젝트의
`VectorCache`를 넘기고, 이 캐시는 백엔드를 키로 쓰므로 모델을 바꿔도 낡은
벡터가 제공되는 일은 없다.

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
| `TESSERAE_SCHEMA_DRIFT_APPLY` | off | `.tesserae/schema-drift-proposals.json`의 **approved** 레코드를 컴파일 시점에 적용합니다 (결정론적, LLM 없음). `tesserae schema-drift`로 제안을 작성하며, 하나를 승인한다는 것은 먼저 `ResearchNodeType`을 편집하고 그다음 `"approved": true`로 설정하는 것을 의미합니다 — 해석할 수 없는 이름은 아무것도 다시 타이핑하지 않습니다. |

---

## 그래프를 읽은 사람

| 변수 | 기본값 | 비고 |
|---|---|---|
| `TESSERAE_READ_AUDIT` | **off** | 접근 카운트가 범프되는 위치 어디든 기록합니다 — `{tool, actor, node_ids, at, tesserae_version}` — `.tesserae/sqlite.db`의 `read_audit` 테이블에 있으며, `read_audit` 도구로 actor별 집계와 함께 다시 읽을 수 있습니다. 한 줄은 범프가 일어나는 곳에 기록되므로, 행 개수는 표면을 따릅니다: 노드 목록을 표면화하는 도구(`search_nodes`, `node_context`, `compile_context`, `graph_map`, `graph_ppr`, `ask` / `query`, `drill_down`, `find_session_findings`)는 호출당 **한 줄씩** 카운트한 모든 노드를 명명하고, `fresh_insights`는 자체 루프 안에서 범프하고 따라서 표면화한 각 노드당 **한 줄씩** 씁니다. 표면화한 것이 없는 호출은 아무것도 쓰지 않으며, 노드를 전혀 읽지 않는 도구 — `schema`, `graph_summary` — 는 절대 감사에 도달하지 않습니다. 행이 노드를 명명하지 않으면 어떤 접근 카운트도 설명하지 않기 때문입니다. 기본값이 off인 이유는 항상 켜진 감사가 모든 읽기 표면을 넘어 모든 읽기를 쓰기로 돌리기 때문입니다; 게이트는 스토어 열기 전에 앉으므로, 테이블을 만드는 것 자체가 쓰기이기 때문입니다. `graph.json`에는 절대 도달하지 않습니다 |
| `TESSERAE_ACTOR` | — | 호출이 agent 뷰를 전달하지 않을 때 읽기를 누구에게 돌릴지. actor는 호출이 하나를 해결하면 `agent` 인수이고, 아니면 이것; 미설정하면 읽기를 이름을 발명하는 대신 익명으로 기록합니다 |

`TESSERAE_READ_AUDIT`을 끄면 기록을 멈추지만 이미 기록된 것을 지우지는
않으며, 서버를 다시 시작할 필요 없이 효과를 냅니다. 이 감사가 *무엇을 위한
것인지*는 [사용하지 않음에 의한 망각](agent-memory.ko.md#망각--절대-삭제-아님)입니다:
접근 횟수가 무엇이 흡수되거나 강등되는지를 결정하며, actor가 없으면 한 노드를
계속 폴링하는 수다스러운 agent와 그것을 한 번 읽은 사람이 똑같은 입력이 됩니다.

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

### 후처리 패스 이전에 컴파일된 그래프

두 가지 수정은 모델이 추출한 내용은 바꾸지 않은 채 컴파일된 그래프의 모습을 바꾼다.
청크마다 하나가 아니라 문서마다 하나의 앵커(청크로 컴파일된 논문은 9.4개를 지니고
있었다), 그리고 철자와 타입마다 하나가 아니라 엔티티 이름마다 하나의 노드다. 둘 다
`compile` 안에서 실행되므로, 이미 디스크에 있는 그래프는 다시 컴파일하기 전까지 어느
것도 갖지 못한다. `graph-repair`는 같은 규칙을 그래프 바이트에 적용한다 — 모델도
네트워크도 없이 몇 초면 된다 — 그리고 복구된 그래프는 다시 컴파일한 그래프와 일치한다.

```sh
tesserae graph-repair --dry-run     # 무엇이 바뀔지 보고만 하고 아무것도 쓰지 않는다
tesserae graph-repair               # .tesserae/graph.json을 제자리에서 다시 쓴다
```

사이트와 볼트는 투영이므로 다시 만들지 않는다. 서비스 중이라면 이후에 `export site`를
실행하라.

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

`graph_write` (MCP)는 스키마 검증 타입 노드와 엣지를 필수 출처와 함께 받으므로, 에이전트는 발견을 추출기가 추측해야 할 산문이 아닌 *구조*로 기록합니다.

강제하지 않고 거부합니다: 타입 없는 엣지, 제어된 어휘 외 노드 또는 엣지 타입, 끝점 없음, 그리고 출처가 없는 쓰기는 모두 거부됩니다. 중복 쓰기는 멱등입니다. 에이전트가 쓴 노드는 전체 재컴파일, 삭제된 `graph.json`, `--limit`, 그리고 전체 말뭉치 삭제를 견딥니다.

## 그래프에 대해 주장 검증

`verify_claim` (MCP)는 그래프가 트리플을 허가하는지 답합니다. `(subject, predicate, object)`를 받습니다 — **자연 언어 매개변수가 없습니다**, 설계상 이유로, 파서가 이전 버전으로 하여금 자신이 지원했던 주장의 부정에 SUPPORTED로 답하게 했기 때문입니다.

판정은 그래프 바이트의 순수 함수입니다: LLM, 임베딩, 결정 경로의 어디든 퍼지 매칭이 없습니다.

| 판정 | 의미 |
|---|---|
| `SUPPORTED` | 엣지가 존재하고, 자체 증거를 전달하고, 그 텍스트는 소스 파일에 대해 재접지되었습니다 |
| `PRESENT_UNEVIDENCED` | 엣지가 존재하지만 문서 기반이 뒤를 서지 않습니다 |
| `CONTRADICTED` | 같은 두 끝점 사이에 문서 기반 `contradicts_claim` |
| `DISPUTED_UNEVIDENCED` | 주장된 불일치, 증거가 없습니다 |
| `CONFLICTING` | 둘 다 문서 기반 — 도구가 판정을 거절합니다 |
| `ABSENT` | 이 그래프는 트리플을 주장하지 않습니다. 반박이 아닙니다 |
| `NOT_RESOLVABLE` | 끝점 또는 술어를 정확하게 해결할 수 없습니다 |

의도적으로 하지 않는 두 가지가 있습니다. `supersedes`를 반박으로 간주하지 않습니다 — 그 관계는 트리플이 거짓이라는 뜻이 아니라 *노드*가 교체되었다는 뜻입니다. 그리고 에이전트 쓰기는 출처 클래스를 *약화*만 할 수 있고, 하나를 업그레이드할 수 없으므로, 에이전트가 주장하는 것은 문서 기반으로 제시될 수 없습니다.

결과를 읽을 때 알 만한 가치가 있습니다: 15,284개 엣지가 있는 실제 그래프에서 약 40%의 `SUPPORTED` 판정은 동어반복입니다 — 인용된 범위가 엣지의 자신의 목표인 `evidenced_by` 엣지. 참이지만, 정보가 아닙니다.

## 질문을 라우팅하기

`tesserae ask` 질문 형태로 검색 경로를 선택합니다: 단일 엔티티 조회는 저렴한 백엔드로 가고, 멀티홉 / "뭐가 바뀌었는가" / "왜" / 말뭉치 전체 질문은 그래프로 갑니다. 이 분기는 **측정이 아니라 가설**을 담고 있습니다: 순회가 멀티홉, 시간적, 종합 질문에서는 비용을 회수하고 단순 사실 조회에서는 낭비할 것이라고 예상합니다. 이 저장소에는 그것을 검증하는 것이 아무것도 없습니다 — 검색 벤치마크도 없고 라우팅 표를 뒷받침하는 공개된 수치도 없으므로, 이를 결과가 아니라 재정의할 만한 기본값으로 취급하십시오.

결정은 반환된 봉투에 나타나므로, 저렴한 답변은 감사할 수 있습니다. `--route`로 CLI에서 또는 MCP 도구의 `route` 매개변수로 재정의하세요.
