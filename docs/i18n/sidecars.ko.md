# `.tesserae/` — 안에 무엇이 있고, 지우면 무엇을 잃는가

<!-- translations:start -->
<p align="center"><a href="../sidecars.md">English</a> · <a href="sidecars.ko.md">한국어</a> · <a href="sidecars.zh.md">中文</a> · <a href="sidecars.ja.md">日本語</a> · <a href="sidecars.ru.md">Русский</a> · <a href="sidecars.es.md">Español</a> · <a href="sidecars.fr.md">Français</a> · <a href="sidecars.de.md">Deutsch</a></p>
<!-- translations:end -->
어느 정도 성숙한 프로젝트라면 `.tesserae/` 아래에 60개 남짓한 항목이 쌓입니다.
그런데 디렉터리 목록만 봐서는 그중 무엇이 컴파일로 공짜로 복구되고, 무엇이
LLM 호출 비용을 치러야 하며, 무엇이 그 무엇으로도 재구성할 수 없는 작업물인지
알 수 없습니다. `compile.lock`이나 0바이트짜리 고아 tmp 파일이, 사람이 내린
판정을 담고 있는 `candidate-same-as.json`과 똑같아 보입니다.

이 문서가 그 답이며, 결과(무엇을 잃는가)를 기준으로 정렬돼 있습니다. 분류 자체는
`tesserae/sidecars.py`에 있습니다 — 파일 하나당 레지스트리 항목 하나로, 소유자와
종류, 그리고 삭제 시 잃는 것을 기록합니다. 진실의 출처는 레지스트리이고 이
페이지는 사람이 읽을 수 있는 투영본이며, 실제 상태는 `tesserae doctor`가
출력합니다.

모든 항목은 서로 독립적인 두 개의 필드를 가집니다:

| 종류 | 바이트가 어디서 오는가 |
|---|---|
| `derived` | 컴파일이 소스로부터 다시 발행함 |
| `accumulated` | 시간에 걸쳐 누적됨. 어떤 컴파일도 다시 만들어내지 못함 |
| `cache` | 다시 물어볼 수 있는 질문에 대해 저장해 둔 답 |
| `scratch` | 프로세스 살림살이: 락, pid 파일, tmp 잔해 |

종류는 바이트의 출처를 말할 뿐, 삭제해도 되는지는 **말해 주지 않습니다**.
`safe_to_delete`는 별개의 필드이고, 둘은 충분히 자주 어긋납니다. 모델이 만들어낸
답을 담은 `cache`는 삭제해도 안전하지 않고, `derived` 파일이 사람의 승인을 담고
있기도 합니다. 아래 절들은 그 두 번째 필드를 기준으로 정렬돼 있습니다. 실제로
알고 싶은 것이 그쪽이기 때문입니다.

## 마음 놓고 지워도 되는 것 — 컴파일이 다시 만듭니다

아래 항목은 지워도 다음 컴파일이 모델 호출 없이 바이트 단위로 그대로
되돌려 놓습니다:

<!-- sidecars:safe-list -->
`agent_harness` · `code-graph.json` · `combined-graph.json` ·
`competitive_report.md` · `diverged-fields.md` · `doctor-report.json` ·
`doctor-report.md` · `graph.json` · `graph.kuzu` · `graphiti_episodes.jsonl` ·
`hierarchy.json` · `lint-report.json` · `lint-report.md` · `log.md` ·
`markdown_projection` · `merge-ledger.json` · `okf` ·
`okf-imported.graph.json` · `output-snapshot.json` · `report.md` · `research` ·
`schema-drift.md` · `site` · `summaries` · `temporal_facts.jsonl` · `wiki` ·
`.watch-cache.json` · `arxiv-cache.json` · `code-graph-cache.json` · `external`

`graph.json`이 이 목록에 있는 것은 의도된 것입니다. 컴파일된 그래프는 소스와
아래의 누적 사이드카들의 순수 함수입니다 — 바로 그렇기 때문에 지켜야 할 것은
*그쪽*이고, "`.tesserae/`를 통째로 지우고 다시 컴파일하면 되지"라는 반사적인
판단은 가장 눈에 띄는 파일이 일회용이라는 사실과 무관하게 틀렸습니다.

## 모델 호출 비용이 들고, `graph.json` 바이트가 바뀝니다

여기 있는 것들은 LLM이 준 답을 저장해 둔 것입니다. 다시 만들려면 호출 비용이
들고, 모델은 같은 문장을 두 번 돌려주지 않으므로 그 아래에 딸린 산출물의
바이트도 함께 바뀝니다.

| 항목 | 종류 | 다시 만들 때의 비용 |
|---|---|---|
| `session_findings` | `cache` | 가장 날카로운 사례입니다. 이 발견들은 그래프 **노드**가 되므로, 캐시를 지우면 비결정적 추출기가 다시 돌고 다음 `graph.json`의 바이트가 달라집니다 — 이 저장소가 네 번이나 겪은 바이트 멱등성 붕괴입니다 |
| `community_summaries` | `cache` | 구성원 해시를 키로 하는, LLM이 쓴 커뮤니티 요약 |
| `distill_cache` | `cache` | 에이전트 증류 결과 |
| `distillation_cache` | `cache` | 증류 결과 |
| `extraction_guidance_cache` | `cache` | 피드백 클러스터마다 LLM이 문장으로 다듬은 항목 하나 |
| `schema_drift_cache` | `cache` | 호스트 타입별 LLM 서브타입 제안 |
| `supersede_cache` | `cache` | LLM 대체(supersede) 판정 |
| `schema-drift-proposals.json` | `derived` | 바이트는 파생이지만 내용은 파생 불가입니다. 레코드가 사람의 `approved` 게이트와 편집 가능한 `proposed_type`을 함께 담고 있어, 다시 만들면 호출 비용이 들고 **그 위에** 승인까지 버려집니다 |

## 복구 불가 — 무엇으로도 다시 만들 수 없습니다

여기 있는 것은 어떤 컴파일도 다시 유도해내지 못합니다. 하나를 지우는 것은
지연이 아니라 데이터 손실입니다.

| 항목 | 종류 | 잃는 것 |
|---|---|---|
| `candidate-same-as.json` | `accumulated` | 사람이 내린 same-as 판정. 이 파일을 찾지 못한 컴파일은 실패하지 않고, 사람이 이미 답한 질문을 조용히 다시 묻습니다. 거부된 쌍이 거부되지 않은 채로 돌아옵니다 |
| `sqlite.db` | `accumulated` | 혼합형. 아래 참고 |
| `agent-writes.jsonl` | `accumulated` | 에이전트가 쓴 오버레이. 매 컴파일마다 다섯 번째 생산자로 재생되며, 지우면 모든 에이전트 기록이 사라집니다 |
| `vault_snapshot.json` | `accumulated` | `vault_pull`이 비교 대상으로 삼는 기준선. 편집 중에 지우면 다음 컴파일이 사용자의 편집과 자신이 이전에 투영한 것을 구분하지 못합니다 — vault 재정의 메커니즘 전체가 여기에 달려 있습니다 |
| `obsidian_vault` | `accumulated` | 양방향이며 사용자 소유입니다. 여기서의 편집이 그래프로 다시 끌려 들어오므로, 그냥 다시 그리면 되는 투영본이 아닙니다 |
| `config.json` | `accumulated` | `obsidian.vault_path`를 포함한 프로젝트 설정 — 사용자 입력이며 재생성되지 않습니다 |
| `charter` | `accumulated` | 프로젝트 charter는 추출된 것이 아니라 사람이 쓴 것입니다 |
| `agents` | `accumulated` | 에이전트별 `registry.json`과 손으로 쓴 `purpose.md` |
| `discovered_links.json` | `accumulated` | 연관 오버레이는 여러 실행에 걸쳐 점수가 매겨진 링크를 누적합니다. 한 번의 실행으로는 복원되지 않습니다 |
| `extraction-feedback.jsonl` | `accumulated` | vault 오버레이와 review-apply 과정에서 수집된 사람의 교정 |
| `extraction-guidance.md` | `accumulated` | 손으로 편집한 지침이며, evolve 패스가 여기에 병합해 넣습니다 |
| `harness_sessions` | `accumulated` | 가져온 세션 상태 |
| `harness_sessions.db` | `accumulated` | 가져온 에이전트 세션. 원본 트랜스크립트는 순환하며 사라지므로 재임포트로 복원되지 않습니다 |
| `session_chunks.db` | `accumulated` | 데몬의 tailer가 실시간으로 기록한 정규화된 턴. 원본 트랜스크립트는 계속 남아 있지 않습니다 |
| `manifest.json` | `accumulated` | 소스별 인제스트 상태. 이것이 없으면 다음 배치가 전부 다시 인제스트하고, 이미 읽은 소스에 대해 추출을 다시 돌립니다 |
| `.build-history.jsonl` | `accumulated` | 빌드마다 한 줄씩, 컴파일 시점의 `git_head`를 기록합니다. 지우면 그래프의 신선도를 영구히 알 수 없게 됩니다 |

### `sqlite.db`는 혼합형이며, 가장 값진 테이블을 기준으로 분류됩니다

이 안의 그래프 미러는 파생이고 `node_vectors`는 버려도 되는 벡터 캐시입니다 —
그러나 같은 파일이 `node_memory`(감쇠, 접근 횟수, 강화된 신뢰도),
`fact_observed`(트랜잭션 시간 — 앞으로만 흐르는 실제 벽시계), `read_audit`을
함께 담고 있고, 이들 중 무엇도 복구할 수 없습니다. 벡터 캐시 공간을 되찾겠다고
파일을 지우면 모든 사실의 "언제 알게 되었는가"가 지금으로 초기화됩니다. 공간은
데이터베이스를 지워서가 아니라, vacuum을 수행하는 `tesserae doctor --fix`로
되찾으십시오.

## 락, pid 파일, 잔해

| 항목 | 종류 | 지우기 전에 |
|---|---|---|
| `compile.lock` | `scratch` | 컴파일 뮤텍스. 어떤 자동 경로도 **절대** 지우지 않습니다 — 기록된 실패 양상은 SessionEnd 컴파일 적체이고, doctor의 `compile_lock` 검사가 보고 전용인 것도 같은 이유입니다 |
| `.recompile.lock.d` | `scratch` | mkdir 기반 훅 뮤텍스. 잡혀 있는 것을 지우면 두 개의 재컴파일이 경합합니다 |
| `session_chunks.lock` | `scratch` | 백필의 "잡혀 있으면 건너뛰기" flock. 잡혀 있는 것을 지우면 두 백필이 같은 날짜에 씁니다 |
| `daemon*.pid` | `scratch` | 엔진 pid 파일. `daemon.<host>.pid` 형태로 호스트 범위를 가집니다. doctor는 기록된 소유자가 **이 머신에서** 죽었음을 확인한 뒤에만 지웁니다 |
| `graph.json.bak-*` | `scratch` | Tesserae의 어떤 코드 경로도 이것을 쓰지 않습니다. 복구 작업 중 사람이 손으로 만든 사본이므로, 보고만 하고 절대 지우지 않습니다 |
| `*.tmp*` | `scratch` | tmp+replace 쓰기의 고아가 된 반쪽으로, 이름은 `<target>.tmp.<pid>.<hex>`입니다. 소유 pid가 사라진 뒤에만 지울 수 있습니다. 살아 있는 기록자는 rename 도중이기 때문입니다 |
| `.*-hook.log*` | `scratch` | 셸 훅 진단 로그. 너무 커진 것은 doctor가 회전시킵니다 |

## `~/.tesserae/` — 머신 전역, 이름만 같은 디렉터리

사용자 범위 디렉터리는 프로젝트 쪽과 이름이 같지만 뜻이 다릅니다.
`config.json`은 양쪽에 모두 존재합니다. 프로젝트에서는 프로젝트 설정이고,
여기서는 이 머신의 모든 프로젝트에 적용되는 LLM 설정입니다.

| 항목 | 종류 | 잃는 것 |
|---|---|---|
| `registry.json` | `accumulated` | 프로젝트 레지스트리. 지우면 이 머신의 모든 프로젝트 등록이 해제됩니다 |
| `config.json` | `accumulated` | 머신 전역 LLM 설정. 사용자 입력입니다 |
| `host_id` | `accumulated` | 이 머신의 정체성. 다시 생성하면 공유 스토리지 위의 모든 호스트 범위 pid 파일과 세션 기록이 낯선 것으로 보이게 됩니다 |
| `harness_sessions` | `accumulated` | 머신 전역 세션 임포트 상태 |
| `llm_cache` | `cache` | 캐시된 LLM 응답. 다시 만들면 모델을 호출하며 같은 것을 재현하지 못합니다 |
| `federation` | `cache` | 프로젝트 간 링크 및 벡터 캐시 — 지워도 안전합니다 |
| `wiki` | `derived` | 머신 범위 serve 스크래치 — 지워도 안전합니다 |
| `engine.pid` | `scratch` | 플릿 pid 파일. 한 번은 죽은 지 6일 된 pid를 붙들고 있었고, pidlock이 신뢰 대신 검증을 하는 이유가 그것입니다 |
| `engine.pid.lock` | `scratch` | 플릿 pid 파일 뮤텍스. 잡혀 있는 것을 지우면 두 플릿이 기동합니다 |
| `*.bak*` | `scratch` | `registry.json`과 `config.json`의 마이그레이션 이전 사본. 어떤 코드 경로도 쓰지 않으므로, 누군가 남겨 두고 싶어서 존재하는 것입니다 |

## 실제 분류 확인하기

```bash
tesserae doctor          # the `sidecars` check, under hygiene
tesserae doctor --fix    # removes orphaned tmp halves, nothing else
```

`sidecars` 검사는 실제 `.tesserae/`를 레지스트리와 대조해, 세 부류를 각각
따로 보고합니다: 고아가 된 tmp 반쪽, 사람이 손으로 만든 `graph.json.bak-*`
사본, 그리고 어떤 레지스트리 항목도 소유를 주장하지 않는 항목입니다. `--fix`는
첫 번째만, 그것도 기록자 pid가 죽었고 파일이 24시간 이상 지난 경우에만
지웁니다 — 살아 있는 기록자는 `write_text`와 `replace` 사이에 있고, 여러 호스트가
하나의 `.tesserae/`를 마운트할 수 있는 상황에서 `os.kill(pid, 0)`은 로컬 프로세스
테이블에 대해서만 답하기 때문입니다.

**분류되지 않은 항목은 보고만 하고 절대 건드리지 않습니다.** 레지스트리가
소유를 주장하지 않는 항목은 Tesserae의 버그이기보다 다른 누군가의 파일 — 사용자의
메모, 다른 도구의 캐시 — 일 가능성이 큽니다. 그래서 그런 것을 발견했을 때의
답은 지우는 것이 아니라 이름을 불러 주는 것입니다. 등록을 건너뛴 새 Tesserae
사이드카가 눈에 띄게 되는 경로이기도 합니다.

Tesserae에는 일괄 `reset` 명령이 없습니다. 이 분류는 그런 명령을 가능하게 하는
전제이지만, 분류를 적어 두는 변경과 그것을 근거로 한 파괴적 명령을 같은 호흡에
내놓는 것은 순서가 잘못된 일입니다.
