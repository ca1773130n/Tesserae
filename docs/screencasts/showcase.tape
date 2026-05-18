# Combined showcase: setup -> compile -> ask, in one scrolling clip.
# Renders to docs/screencasts/showcase.gif. Run with:
#
#     vhs docs/screencasts/showcase.tape
#     gifsicle --optimize=3 --colors=128 docs/screencasts/showcase.gif \
#         -o docs/screencasts/showcase.gif
#
# Configures all three optional integrations (Cognee, RAG-Anything,
# Understand-Anything) but skips their pip/curl installers so the
# recording doesn't have to wait on slow network IO. The setup output
# still lists them as configured external tools.
#
# Uses the /tmp/tesserae-demo-rec workspace. Re-create before recording:
#
#     rm -rf /tmp/tesserae-demo-rec/myproject/.tesserae

Output "docs/screencasts/showcase.gif"

Set Shell "zsh"
Set FontSize 14
Set Width 1100
Set Height 460
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "source /tmp/tesserae-rec/init.zsh && export PATH=/tmp/tesserae-demo-rec/venv/bin:$PATH && cd /tmp/tesserae-demo-rec/myproject"
Enter
Sleep 500ms
Type "clear"
Enter
Sleep 200ms
Show

# --- Setup -------------------------------------------------------------
Type "# 1) Set up — Cognee, RAG-Anything, Understand-Anything all configured"
Sleep 300ms
Enter

Type@25ms "tesserae project setup --yes --source ./research --with-understand-anything --with-raganything --skip-install-cognee --skip-install-raganything --skip-install-understand-anything"
Sleep 200ms
Enter
Sleep 3s

# --- Compile -----------------------------------------------------------
Type "# 2) Compile the typed knowledge graph + build the static site"
Sleep 300ms
Enter

Type@25ms "tesserae project compile && tesserae project build-site"
Sleep 200ms
Enter
Sleep 7s

# --- Ask (three questions) --------------------------------------------
Type "# 3) Ask the compiled wiki — deterministic graph lookup"
Sleep 300ms
Enter

Type@25ms `tesserae project ask --backend wiki --top-k 3 "What is 3D Gaussian Splatting?"`
Sleep 200ms
Enter
Sleep 3500ms

Type@25ms `tesserae project ask --backend wiki --top-k 3 "Which repos implement NeRF?"`
Sleep 200ms
Enter
Sleep 3500ms

Type@25ms `tesserae project ask --backend wiki --top-k 3 "How does DreamFusion generate 3D from text?"`
Sleep 200ms
Enter
Sleep 4500ms
