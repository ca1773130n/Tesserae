---
description: Discover unwired features and test integration wiring end-to-end
argument-hint: "[--target <feature>] [--dry-run]"
---

Run the wireup command to discover integration gaps and validate feature wiring end-to-end:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js wireup run [user-provided arguments]
```

The wireup loop uses a pipeline architecture per iteration:
1. Discovers features that exist in the codebase but lack full integration (exported-but-uncalled, config-without-surface, endpoint-without-integration-test)
2. Generates executable test scenarios for each unwired feature (HTTP, CLI, and assert steps)
3. Executes HTTP and CLI scenarios against the running system
4. Detects missing connections from failed scenario results
5. Reports a pass/fail summary with issue counts and recommended fixes

Flags:
- `--target <feature>` — Focus wireup on a specific feature area (filters discovered features by name match)
- `--dry-run` — Discover and generate scenarios without executing them (useful for previewing what would run)
- `--timeout N` — Timeout per subprocess in minutes
- `--max-turns N` — Max turns per subprocess

All operations enforce a sonnet model ceiling — no opus-class models are used.

IMPORTANT: This command is long-running (spawns Claude subprocesses for scenario execution). You MUST run it in the background using `run_in_background: true` on the Bash tool to avoid hitting the Bash tool's default timeout. Use `TaskOutput` with `block: false` to check progress periodically.

Report the JSON results. If any scenarios failed, explain what the missing connections are and suggest remediation steps.
