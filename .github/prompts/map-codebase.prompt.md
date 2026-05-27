---
description: Analyze codebase architecture with parallel mapper agents
---

<purpose>
Orchestrate parallel codebase mapper agents to analyze codebase and produce structured documents in the codebase directory

Each agent has fresh context, explores a specific focus area, and writes documents directly. The orchestrator only receives confirmation + line counts, then writes a summary.

Output: ${codebase_dir}/ folder with 7 structured documents about the codebase state.
</purpose>

<shell_safety>
NEVER use inline `node -e` or `python3 -c` with `!=` or `!==` — zsh escapes `!` and breaks them.
Use `grd-tools.js` pre-formatted output directly. Do NOT pipe `--raw` JSON through inline one-liners.
If JSON field extraction is needed, write a temp .js file or use inverted equality (`=== "x"` + negate).
</shell_safety>

<process>

<step name="init_context" priority="first">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init map-codebase)
```

Extract: `mapper_model`, `commit_docs`, `codebase_dir`, `existing_maps`, `has_maps`, `codebase_dir_exists`.
</step>

<step name="check_existing">
If `codebase_dir_exists` is true: Offer Refresh/Update/Skip.
If doesn't exist: Continue to create_structure.
</step>

<step name="create_structure">
```bash
mkdir -p ${codebase_dir}
```

Expected: STACK.md, INTEGRATIONS.md, ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md
</step>

<step name="spawn_agents">
Spawn 4 parallel grd-codebase-mapper agents:

**Agent 1: Tech Focus** — STACK.md, INTEGRATIONS.md
**Agent 2: Architecture Focus** — ARCHITECTURE.md, STRUCTURE.md
**Agent 3: Quality Focus** — CONVENTIONS.md, TESTING.md
**Agent 4: Concerns Focus** — CONCERNS.md

Each uses `subagent_type="grd:grd-codebase-mapper"`, `model="{mapper_model}"`, `run_in_background=true`.

Each agent prompt includes:
```
PATHS:
codebase_dir: ${codebase_dir}
```
</step>

<step name="verify_output">
```bash
ls -la ${codebase_dir}/
wc -l ${codebase_dir}/*.md
```

All 7 documents exist, no empty documents.
</step>

<step name="scan_for_secrets">
```bash
grep -E '(sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|AKIA[A-Z0-9]{16}|-----BEGIN.*PRIVATE KEY)' ${codebase_dir}/*.md 2>/dev/null && SECRETS_FOUND=true || SECRETS_FOUND=false
```

If SECRETS_FOUND: Alert and pause before commit.
</step>

<step name="commit_codebase_map">
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: map existing codebase" --files ${codebase_dir}/*.md
```
</step>

<step name="offer_next">
```
Codebase mapping complete.

Created ${codebase_dir}/:
- STACK.md, ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, INTEGRATIONS.md, CONCERNS.md

---

## Next Up

**Initialize project** — use codebase context for planning

`/grd:init`

<sub>`/clear` first -> fresh context window</sub>

---
```
</step>

</process>

<success_criteria>
- ${codebase_dir}/ directory created
- 4 parallel grd-codebase-mapper agents spawned
- All 7 codebase documents exist
- Clear completion summary with line counts
- User offered clear next steps
</success_criteria>
