---
description: Run an ad-hoc multi-backend discussion on a topic
argument-hint: <topic>
---

<purpose>
Run a multi-backend discussion using configured participants. Each backend contributes a perspective, a synthesizer backend summarizes the round, and the result is written to the discussion history. Useful for getting diverse AI perspectives on architecture decisions, design questions, or open-ended research topics.
</purpose>

<process>

<step name="resolve_topic">
If an argument was provided, use it as the discussion topic.

If no argument was provided, read `.planning/ROADMAP.md` to identify the current phase goal and use it as the topic:
```
"What is the best approach for the current phase goal: [goal from ROADMAP.md]?"
```
</step>

<step name="run_discussion">
Call the `grd_discussion_run` MCP tool with the topic:

```
grd_discussion_run(
  topic: <resolved topic>,
  // participants: omit to use all available backends from config
  // rounds: omit to use default (2)
  // synthesizer: omit to use default (claude)
)
```

The tool dispatches to all configured participants sequentially (from `backend_roles` in config, or all available backends), runs the configured number of rounds, and synthesizes a final answer.
</step>

<step name="present_results">
Present the discussion results clearly:

1. **Participants** — List which backends participated vs were skipped (unavailable)
2. **Round responses** — For each round, show each backend's name and a brief excerpt (first 200 chars) of their response
3. **Synthesis** — Show the synthesizer's full response prominently under a `## Synthesis` header
4. **History file** — Report the path to the saved discussion markdown file (`discussion_file` field in result)

If a backend was skipped (not available on PATH), note it as "skipped — not available".
</step>

</process>

<success_criteria>
- [ ] Topic resolved (from argument or current phase goal)
- [ ] grd_discussion_run tool called with topic
- [ ] Round responses presented with backend names
- [ ] Synthesis displayed prominently
- [ ] History file path reported
</success_criteria>
