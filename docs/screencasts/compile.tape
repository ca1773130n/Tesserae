# Demonstrates `tesserae project compile` on the demo workspace.
# Renders to docs/screencasts/compile.gif. Run with: vhs docs/screencasts/compile.tape
#
# Assumes `setup.tape` (or equivalent) has already run in the same workspace.

Output "docs/screencasts/compile.gif"

Set Shell "zsh"
Set FontSize 14
Set Width 1100
Set Height 360
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

Type "# Compile the typed knowledge graph (deterministic, no LLM, no API keys)"
Sleep 700ms
Enter

Type@30ms "tesserae project compile"
Sleep 400ms
Enter
Sleep 7s

Type "# Then build the static site"
Sleep 600ms
Enter

Type@30ms "tesserae project build-site"
Sleep 400ms
Enter
Sleep 8s

Type "ls .tesserae/site/"
Enter
Sleep 2s
