---
description: Run the autoresearch loop — hypothesis -> experiment -> measure -> learn -> revise — on a research question
argument-hint: "\"<question>\" [--max-iterations N] [--no-gates] | resume <id> | status [<id>]"
---

Run GRD's autoresearch loop on a research question:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js research [user-provided arguments]
```

The loop runs a hypothesis-centric scientific cycle to a verdict, persisting a research
thread under `.planning/research/threads/<id>/`:

1. GROUND — read prior findings from the Tesserae KG + local LANDSCAPE/KNOWHOW
2. HYPOTHESIZE — generate one ranked, testable hypothesis
3. DESIGN — write an experiment plan + runnable script
4. RUN — execute the script in a subprocess (behind an execution gate)
5. MEASURE — compare the metric against the plan's target -> verdict
6. LEARN — extract a typed takeaway (with H2/H3/H4 failure classification)
7. DECIDE — supported -> finalize; refuted/inconclusive -> revise hypothesis, loop
8. PERSIST — write FINDING.md and (behind a gate) sync to the shared Tesserae KG

## Subcommands
- `gd research "<question>"` — start a new thread
- `gd research resume <id>` — resume a gate-paused thread
- `gd research status [<id>]` — list threads or show one thread

## Flags
- `--max-iterations N` — cap loop iterations (default 5)
- `--no-gates` — run fully unattended (skip the execute + kg_write gates)
