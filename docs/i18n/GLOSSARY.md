# Translation glossary

**This file is written in English and is not mirrored into the seven languages.**
It is an instruction *to* whoever translates `docs/`, human or agent, not a
document for readers of the localized docs. Deliberately excluded from the
mirroring rule by living under `docs/i18n/`, which
`tests/test_docs_i18n.py::test_all_docs_have_localized_counterparts_except_superpowers`
skips.

Every rendering below was read out of the mirrors that exist today; nothing here
was invented, and a *see note* cell means the corpus has no answer yet rather
than that one was guessed. Where the corpus contradicts itself, the entry says so and
names the file and line to fix — those are the terms that have been
mistranslated more than once, which is why they are on this page at all.

Its companion is mechanical: the parity ratchet in `tests/test_docs_i18n.py`
compares code spans, bullet and heading counts, table rows and columns, fenced
blocks, and bold spans between each English doc and each mirror. That check finds
*dropped* content. It is blind to everything on this page, because a term
rendered as its opposite has a perfect structural signature. The two halves do
not overlap.

---

## Standing rules

These are the ones the passes kept breaking, in the order they cost the most.

1. **Never translate anything inside backticks.** `` `--brief-budget` ``,
   `` `graph_map` ``, `` `SUPPORTED` `` and every other code span is an identifier
   a reader will type or grep. Translating one produces a command that does not
   exist. The ratchet fails the build when a mirror loses one.
2. **Never escape a backtick.** `` \`ABSENT\` `` renders as literal backtick
   characters, not as code. There are 390 of these across ten mirrors right now
   (all seven `tuning.*`, `release-notes/v0.25.1.{es,fr}`,
   `release-notes/v0.21.0.zh`) and zero in any English source.
3. **Keep the table shape.** Same number of rows, same number of columns, same
   order. A dropped row is a dropped claim, and it is invisible in a diff between
   two languages.
4. **Keep the bullet count.** A four-bullet block stays four bullets. Merging two
   because they read better joined loses one of them.
5. **Keep bold spans.** `**...**` marks the sentence's claim. A mirror that drops
   the bold has quietly de-emphasised the thing the paragraph exists to say.
6. **Numerals stay numerals.** `8`, `0`, `96`, `26-week`. Never spelled out,
   never localized into another numeral system, never rounded.
7. **Headings keep their level.** `###` stays `###`; the anchor is a link target.
8. **A term that is house style in English stays in English.** If a mirror already
   keeps `prompt`, `serve`, `sidecar` untranslated, keep doing that — do not
   improve it into a local word for one file only.

---

## Terms

"keep `x`" means the mirrors leave the English word standing and you should too.
*see note* means the corpus has no usable answer: either the term has not
appeared in that language yet, or what is there is the defect. Read the note
below before choosing; do not improvise a new rendering into one file.

| term | de | es | fr | ja | ko | ru | zh |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **tick** (engine cycle) | `Tick` | keep `tick` | keep `tick` | ティック | 틱 | тик | keep `tick` |
| **warm / warming** (a cache) | vorwärmen | precalentar | préchauffer | ウォーミング | 예열 | прогревание | 预热 |
| **cold** (a cache) | kalt | frío | froid | 冷たい | 차가운 | холодный | 冷 |
| **brief** (the artifact) | *see note* | *see note* | brève | ブリーフ | 브리프 | краткая справка | 简报 |
| **digest** (the artifact) | `Digest` | keep `digest` | keep `digest` | ダイジェスト | 다이제스트 | keep `digest` | keep `digest` |
| **prompt** | `Prompt` | keep `prompt` | keep `prompt` | プロンプト | keep `prompt` | keep `prompt` | keep `prompt` |
| **stood down** (yielded) | zurückgestellt | *see note* | abandonné | 撤退した | 포기 | *see note* | 让路 |
| **breadth-first** | Breitensuche | primero en amplitud | en largeur d'abord | 幅優先 | 너비 우선 | обход в ширину | 广度优先 |
| **lossless** | verlustlos | sin pérdida | sans perte | 損失がありません | 손실이 없습니다 | без потерь | 无损 |
| **refill** (a cache) | wieder füllen | rellenar | *see note* | 再び埋まります | *see note* | заполнить | *see note* |
| **serve** (an answer) | *see note* | sirve | sert | keep `serve` | keep `serve` | keep `serve` | keep `serve` |
| **retired** (domain/layer) | ausrangiert | retirado | retiré du service | 廃止された | 폐기된 | выведенный из эксплуатации | 已退役 |
| **sidecar** (the file) | `Sidecar` | keep `sidecar` | side-car | サイドカー | 사이드카 | сайдкар | 边车 |
| **row** (table / DB) | Zeile | fila | ligne | 行 | 행 | строка | 行 |
| **a fraction of a percent** | Bruchteil eines Prozents | una fracción de un por ciento | une fraction d'un pour cent | 1 パーセントにも満たない | 1퍼센트에도 못 미치는 | доля процента | 不到百分之一 |
| **graph** (the knowledge graph) | `Graph` | grafo | graphe | グラフ | 그래프 | граф | 图谱 |

---

## Banned renderings, and why

Each of these shipped. Several are still in the tree, so the file and line is
recorded and the next pass can fix it rather than rediscover it.

| term | lang | never | because |
| --- | --- | --- | --- |
| tick | zh | 检查周期 | That is what `--consolidate-check` means. Using it for `tick` made a cost table contradict itself. Fixed: zero occurrences remain; `tick` is kept in English 27×. |
| cold | ko | 추운 | Cold *weather*. A cache is 차가운. |
| warm | ko | 데워 *as a noun* | 데워 is a connective form and cannot take a particle. The noun is 예열; the adverbial "미리 데워 두는" (`agent-memory.ko.md:53`) is correct and may stay. |
| digest | fr | digestion | Food. **Still present**: `engine-consolidation.fr.md:50` and `:72` say "valide en termes de digestion" for *digest-valid*. French keeps `digest` 23× elsewhere. |
| prompt | es | indicativo / indicación | `indicativo` is a radio call sign. **Still present**: `tuning.es.md:121` says "firmas de indicación" — the only one of 35 occurrences that is not the English word. |
| stood down | ko | 대기 | "Standing by" — the opposite. The tick *gives up* its slot. |
| stood down | zh | 待命 | "Awaiting orders" — the opposite. `engine-consolidation.zh.md:79` now has 为管道让路, which is right. |
| breadth-first | ru | широкий-первый | A calque that means nothing. `обход в ширину` is the term. |
| lossless | ru | безвредна | "Harmless". |
| refill | ru | переполнит | "Overflow" — the opposite of refilling a cache that was emptied. |
| serve | zh | 服用 | To take medicine. |
| serve | de | servieren | Food service. |
| retired | ru | вышедшие на пенсию | On a pension. |
| retired | ja | 引き出された | Withdrawn (as from a bank). |
| retired | ko | 은퇴한 | Retirement from a job. **Still present** at `engine-consolidation.ko.md:60`; 폐기된 is used correctly 10× elsewhere, including `doctor.ko.md:47` for the same English phrase. |
| sidecar | ru | боковой автомобиль | A motorcycle sidecar. |
| row | ru | ряд | A row of physical objects. `строка` is the table/DB row and outnumbers it 78 to 9 in these files. |
| graph | de | Diagramm | A chart. **Still present** 12× — `engine-consolidation.de.md:20, 24, 44, 60, 72` among them. `Graph` is used 444× and is correct. |
| brief | de | Brief | A letter in the post. **Still present** 13× — `engine-consolidation.de.md:52, 60, 69, 81, 101`. German has no settled rendering yet; `Briefing` appears once (`release-notes/v0.30.0.de.md:76`) and is the only candidate the corpus offers. Pick one and apply it everywhere. |

---

## Notes on the unsettled entries

**brief — de.** No house rendering exists. See the banned table above.

**brief — es.** `engine-consolidation.es.md` uses `Breve` as a bare noun in the
section heading and cost table, and `resúmenes` in the body. But `resumen` is
already Spanish for *summary*, which is a different artifact in the same
paragraph. Two artifacts, one word. Settle this before the next pass.

**brief / digest / summary — zh.** 摘要 currently covers all three (109
occurrences). The engine's Brief operation and its Summarize operation are
separate budgets with separate flags, and `engine-consolidation.zh.md:52` reads
"摘要——预热宪章的领域摘要" for "Brief — pre-warm the charter's domain briefs".
简报 exists in the corpus (5×) and is the distinct word available for *brief*.

**stood down — es, ru.** Not present in either. The Russian mirrors do use
`вытеснен-` but for a *superseded* finding, which is a different idea; do not
borrow it. The sense here is a tick *giving up its remaining budget* so a
pipeline can run — not standing by, not awaiting orders, not being dismissed.

**refill — fr, ko, zh.** Not present. The sense is a deleted cache being
repopulated by the next compile: filled again, not overflowed.

**serve — de.** Not settled. `servieren` is banned (food); the sense is a
function returning a card to a caller.

**a fraction of a percent.** All three CJK mirrors produced non-phrases for this
in one pass. The renderings in the table are the ones that survived review; use
them verbatim rather than composing a new one.
