# Demonstrates `tesserae project ask` against the compiled wiki.
# Renders to docs/screencasts/ask.gif. Run with: vhs docs/screencasts/ask.tape
#
# Assumes `compile.tape` (or equivalent) has already run in the workspace.

Output "docs/screencasts/ask.gif"

Set Shell "zsh"
Set FontSize 14
Set Width 1100
Set Height 480
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Hide
Type "source /tmp/tesserae-rec/init.zsh && export PATH=/tmp/tesserae-demo-rec/venv/bin:$PATH && cd /tmp/tesserae-demo-rec/myproject"
Enter
Sleep 500ms
Type "clear"
Enter
Sleep 200ms
Show

Type "# Ask the compiled wiki a question — deterministic graph lookup"
Sleep 700ms
Enter

Type@30ms `tesserae project ask --backend wiki --top-k 3 "What is 3D Gaussian Splatting?"`
Sleep 400ms
Enter
Sleep 5s

Type "# Structural query — which repos implement Gaussian Splatting?"
Sleep 700ms
Enter

Type@30ms `tesserae project ask --backend wiki --top-k 3 "Which repos implement Gaussian Splatting?"`
Sleep 400ms
Enter
Sleep 5s
