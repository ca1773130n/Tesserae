# Combined showcase: setup -> compile -> ask, in one scrolling clip.
# Renders to docs/screencasts/showcase.gif. Run with: vhs docs/screencasts/showcase.tape
#
# Uses the same /tmp/llm-wiki-demo-rec workspace as the per-feature tapes.
# Re-create the workspace before recording:
#
#     rm -rf /tmp/llm-wiki-demo-rec/myproject/.llm-wiki

Output "docs/screencasts/showcase.gif"

Set FontSize 14
Set Width 1100
Set Height 460
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

Type "source /tmp/llm-wiki-demo-rec/venv/bin/activate"
Enter
Sleep 200ms
Type "cd /tmp/llm-wiki-demo-rec/myproject && clear"
Enter
Sleep 500ms

# --- Setup -------------------------------------------------------------
Type "# 1) Point LLM-Wiki at a research notes directory"
Sleep 350ms
Enter

Type@30ms "llm_wiki project setup --yes --no-color --source ./research --no-cognee --skip-raganything --skip-install-cognee --skip-install-raganything --skip-install-understand-anything"
Sleep 250ms
Enter
Sleep 3s

# --- Compile -----------------------------------------------------------
Type "# 2) Compile the typed knowledge graph + build the static site"
Sleep 400ms
Enter

Type@30ms "llm_wiki project compile && llm_wiki project build-site"
Sleep 250ms
Enter
Sleep 8s

# --- Ask ---------------------------------------------------------------
Type "# 3) Ask the compiled wiki a question"
Sleep 400ms
Enter

Type@30ms `llm_wiki project ask --backend wiki --top-k 3 "What is 3D Gaussian Splatting?"`
Sleep 250ms
Enter
Sleep 4500ms
