---
description: Run Tesserae setup — detect environment, propose a plan, apply with confirmation.
argument-hint: ""
allowed-tools:
  - "mcp__tesserae__tesserae_setup_plan"
  - "mcp__tesserae__tesserae_setup_apply"
---

Run `tesserae_setup_plan` for the current working directory. Show the
`rendered_summary` to the user verbatim. Then ask:

1. Should I install the flagged dependencies? (sets `confirm_install_actions`)
2. Should I run the post-setup refresh commands? (sets `confirm_run_actions`)

Call `tesserae_setup_apply` with the appropriate flags. Report
`actions_taken` and `warnings`. If `drift` is non-empty, surface it before
applying.
