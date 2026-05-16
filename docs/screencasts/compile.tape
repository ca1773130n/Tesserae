# Demonstrates `llm_wiki project compile` on the demo workspace.
# Renders to docs/screencasts/compile.gif. Run with: vhs docs/screencasts/compile.tape
#
# Assumes `setup.tape` (or equivalent) has already run in the same workspace.

Output "docs/screencasts/compile.gif"

Set FontSize 14
Set Width 1100
Set Height 600
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Type "source /tmp/llm-wiki-demo-rec/venv/bin/activate"
Enter
Sleep 200ms
Type "cd /tmp/llm-wiki-demo-rec/myproject && clear"
Enter
Sleep 500ms

Type "# Compile the typed knowledge graph (deterministic, no LLM, no API keys)"
Sleep 700ms
Enter

Type@30ms "llm_wiki project compile"
Sleep 400ms
Enter
Sleep 7s

Type "# Then build the static site"
Sleep 600ms
Enter

Type@30ms "llm_wiki project build-site"
Sleep 400ms
Enter
Sleep 8s

Type "ls .llm-wiki/site/"
Enter
Sleep 2s
