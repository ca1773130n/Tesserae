# Demonstrates `llm_wiki project ask` against the compiled wiki.
# Renders to docs/screencasts/ask.gif. Run with: vhs docs/screencasts/ask.tape
#
# Assumes `compile.tape` (or equivalent) has already run in the workspace.

Output "docs/screencasts/ask.gif"

Set FontSize 14
Set Width 1100
Set Height 720
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Type "source /tmp/llm-wiki-demo-rec/venv/bin/activate"
Enter
Sleep 200ms
Type "cd /tmp/llm-wiki-demo-rec/myproject && clear"
Enter
Sleep 600ms

Type "# Ask the compiled wiki a question — deterministic graph lookup"
Sleep 700ms
Enter

Type@30ms `llm_wiki project ask --backend wiki --top-k 3 "What is 3D Gaussian Splatting?"`
Sleep 400ms
Enter
Sleep 5s

Type "clear"
Enter
Sleep 200ms

Type "# Structural query — which repos implement Gaussian Splatting?"
Sleep 700ms
Enter

Type@30ms `llm_wiki project ask --backend wiki --top-k 3 "Which repos implement Gaussian Splatting?"`
Sleep 400ms
Enter
Sleep 5s
