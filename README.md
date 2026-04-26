# LLM-Wiki Research Intelligence Graph

This project is the workspace for building a research-domain LLM wiki: an evolving literature intelligence graph for a user's fields of interest.

The goal is **not** a generic noun-phrase knowledge graph. The graph should track:

- research fields/topics/problem areas
- papers, repositories, models, datasets, benchmarks, metrics, and results
- reusable concepts, mathematical ideas, algorithms, training/inference strategies, and technical terms
- contribution/performance/comparison/limitation/causal claims
- evidence spans grounding claims in source documents
- approach families and trends across time

## Current implementation

The first implementation is a deterministic guardrail extractor in `llm_wiki.research_graph`.
It defines controlled node/edge vocabularies and prevents arbitrary node types such as `software`, `technique`, `domain`, or generic `Entity` from becoming graph schema.

Run tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q
```

Install the CLI so shell commands work without `python3 -m`:

```bash
# From GitHub (recommended for a fresh machine)
curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

# Or from a local checkout
./scripts/install.sh --dir /path/to/LLM-Wiki
```

The installer clones or updates the repo, creates `.venv` by default, runs `pip install -e`, and writes `llm_wiki` / `llm-wiki` wrappers into `~/.local/bin`. Use `--help` to see options such as `--branch`, `--dir`, `--no-venv`, and `--skip-shell-config`.

Initialize a self-contained LLM-Wiki inside any project directory:

```bash
cd /path/to/my-project
llm_wiki project init \
  --name my_project_wiki \
  --source-kind Repository \
  --source README.md \
  --source docs \
  --source src
llm_wiki project compile --changed-only
llm_wiki project mcp-config \
  --server-name my_project_wiki
```

`project compile` uses the default `sources` saved in `.llm-wiki/config.json`, writes every durable artifact, and preserves the previous graph when `--changed-only` skips all files. Use `project ingest <paths>` when you want to compile ad-hoc paths without changing the saved source list.

This creates project-local artifacts under `.llm-wiki/`:

```text
.llm-wiki/
  config.json
  graph.json
  temporal_facts.jsonl
  manifest.json
  sqlite.db
  report.md
  competitive_report.md
  graphiti_episodes.jsonl
  markdown_projection/
  obsidian_vault/
  agent_harness/
  site/
  cognee_bundle/
```

Competitive hardening versus MegaMem/Graphiti-style systems:

- `temporal_facts.jsonl` projects every validated edge into a Graphiti-style fact with `valid_from`, `current`, `invalidated_by`, `confidence`, evidence, and source provenance.
- `graphiti_episodes.jsonl` exports those temporal facts as Graphiti-compatible episodes without requiring Graphiti at compile time; `project sync-graphiti` can optionally push them to Graphiti/Neo4j when `graphiti_core` is installed.
- `obsidian_vault/` is a ready-to-open Obsidian projection with `.obsidian` defaults, graph coloring, attachments under `raw/assets`, and a Dataview dashboard.
- `agent_harness/` writes shared context and target-specific harness files for Claude Code, Codex, Gemini CLI, Kiro, Cursor, and OpenCode so external coding agents can discover the graph and MCP server.
- `site/` is a dependency-light static frontend inspired by Pratiyush/llm-wiki: it writes `index.html`, `graph.json`, `search-index.json`, and `llms.txt` so humans and agents can browse research and development nodes together.
- `competitive_report.md` records what was absorbed from MegaMem, Graphiti/Zep, MCP graph servers, and agentic RAG systems while preserving LLM-Wiki's controlled ontology/no-API-key differentiators.
- MCP now exposes temporal tools as well as node tools: `search_facts` and `timeline` join `schema`, `graph_summary`, `search_nodes`, and `node_context`.

Paste the `project mcp-config` output into Hermes `~/.hermes/config.yaml` under `mcp_servers`, then restart Hermes/gateway. The project wiki will be exposed as native MCP tools such as `mcp_my_project_wiki_search_nodes` and `mcp_my_project_wiki_node_context`.

Export or optionally sync project-local temporal facts into Graphiti/Zep-style storage:

```bash
llm_wiki project export-graphiti \
  --project /path/to/my-project
llm_wiki project sync-graphiti \
  --project /path/to/my-project \
  --dry-run
# Live sync requires graphiti_core plus a reachable Neo4j backend:
llm_wiki project sync-graphiti \
  --project /path/to/my-project \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

`export-graphiti` is dependency-free and writes `.llm-wiki/graphiti_episodes.jsonl`. `sync-graphiti --dry-run` counts the same episodes without importing Graphiti, which is useful for local smoke tests.

Export coding-agent harnesses and an Obsidian vault projection:

```bash
llm_wiki project export-agent-harness \
  --project /path/to/my-project
# Or only selected agents:
llm_wiki project export-agent-harness \
  --project /path/to/my-project \
  --target claude-code \
  --target cursor \
  --target opencode
llm_wiki project export-obsidian \
  --project /path/to/my-project
# Write to a real Obsidian vault path instead of .llm-wiki/obsidian_vault:
llm_wiki project export-obsidian \
  --project /path/to/my-project \
  --vault "$OBSIDIAN_VAULT_PATH"
```

The agent harness currently emits:

- Claude Code: `claude/CLAUDE.md`, `claude/.claude/settings.json`
- Codex: `codex/AGENTS.md`, `codex/mcp.toml`
- Gemini: `gemini/GEMINI.md`, `gemini/.gemini/settings.json`
- Kiro: `kiro/.kiro/steering/llm-wiki.md`, `kiro/.kiro/settings/mcp.json`
- Cursor: `cursor/.cursor/rules/llm-wiki.mdc`, `cursor/.cursor/mcp.json`
- OpenCode: `opencode/AGENTS.md`, `opencode/opencode.json`

Build and serve the local frontend:

```bash
llm_wiki project build-site \
  --project /path/to/my-project
llm_wiki project serve \
  --project /path/to/my-project \
  --port 8765
```

For development projects, initialize with `--source-kind CodeProject` or `Repository` and include code directories. Code files become graph nodes (`CodeProject`, `SourceFile`, `CodeClass`, `CodeFunction`, `Dependency`) alongside research nodes, while source files remain raw evidence and generated markdown/site outputs remain projections:

```bash
llm_wiki project init \
  --project /path/to/my-app \
  --name my_app_wiki \
  --source-kind CodeProject \
  --source README.md \
  --source docs \
  --source src
llm_wiki project compile --project /path/to/my-app
```

Optional graph/storage packages currently used by the local environment:

```bash
python3 -m pip install --user kuzu cognee graphiti-core
```

Extract a JSON graph from a paper note:

```bash
llm_wiki data/research/daily/2026-04-26/papers/2601.17835/paper.md \
  --source-kind Paper \
  --pretty \
  -o output/research_graph_sample.json
```

Extract multiple notes and add corpus-level trend nodes for concepts that recur across sources:

```bash
llm_wiki \
  data/research/daily/2026-04-25/papers/2604.00538/paper.md \
  data/research/daily/2026-04-26/papers/2601.17835/paper.md \
  --source-kind Paper \
  --trends \
  --min-trend-sources 2 \
  --pretty \
  -o output/research_graph_trends_smoke.json
```

Use Claude CLI/OAuth instead of API-key LLM calls for higher-quality candidate extraction. The Claude output is still validated against the controlled node/edge whitelist before it becomes a `ResearchGraph`:

```bash
llm_wiki output/claude_cli_smoke_note.md \
  --source-kind Paper \
  --extractor claude-cli \
  --claude-config-dir /Users/neo/.claude-personal1 \
  --claude-config-dir /Users/neo/.claude-personal2 \
  --claude-model sonnet \
  --pretty \
  -o output/claude_cli_smoke_graph.json
```

Canonicalize high-confidence aliases and write a review queue for ambiguous near-duplicates:

```bash
llm_wiki \
  data/research/daily/2026-04-25/papers/2604.00538/paper.md \
  data/research/daily/2026-04-26/papers/2601.17835/paper.md \
  --source-kind Paper \
  --trends \
  --canonicalize \
  --review-output output/research_graph_review_queue.json \
  --pretty \
  -o output/research_graph_canonical_trends_smoke.json
```

Run the full local pipeline: typed graph extraction, trend projection, canonicalization, review queue, markdown projection, and SQLite persistence:

```bash
llm_wiki \
  data/research/daily/2026-04-25/papers/2604.00538/paper.md \
  data/research/daily/2026-04-26/papers/2601.17835/paper.md \
  --source-kind Paper \
  --trends \
  --canonicalize \
  --review-output output/research_graph_review_queue.json \
  --project-markdown output/markdown_projection \
  --sqlite-output output/research_graph.sqlite \
  --pretty \
  -o output/research_graph_full_pipeline_smoke.json
```

Apply reviewed merge decisions:

```bash
llm_wiki path/to/papers \
  --source-kind Paper \
  --canonicalize \
  --apply-review-decisions output/review_decisions.json \
  -o output/research_graph_reviewed.json
```

Incrementally ingest a corpus in batches. Unchanged files are skipped using content hashes in the manifest; with `--limit`, the runner keeps scanning past skipped files until it processes up to that many changed files:

```bash
llm_wiki data/research/daily \
  --source-kind Paper \
  --batch-manifest output/research_batch_manifest.json \
  --changed-only \
  --limit 5 \
  --trends \
  --canonicalize \
  --review-output output/research_batch_review_queue.json \
  --project-markdown output/batch_markdown_projection \
  --sqlite-output output/research_batch.sqlite \
  --pretty \
  -o output/research_graph_batch_smoke.json
```

Persist to Kuzu, export a Cognee bundle, write review UX files, and generate a richer markdown report:

```bash
python3 -m pip install --user kuzu cognee
llm_wiki data/research/daily \
  --source-kind Paper \
  --limit 5 \
  --trends \
  --canonicalize \
  --review-output output/kuzu_review_queue.json \
  --review-markdown-output output/kuzu_review_queue.md \
  --review-jsonl-output output/kuzu_review_queue.jsonl \
  --review-decisions-template output/kuzu_review_decisions.template.json \
  --project-markdown output/kuzu_markdown_projection \
  --sqlite-output output/kuzu_research_graph.sqlite \
  --kuzu-output output/research_graph.kuzu \
  --cognee-output output/cognee_bundle \
  --report-output output/research_graph_report.md \
  --pretty \
  -o output/research_graph_kuzu_full_smoke.json
```

Add an exported bundle directly to Cognee without running `cognify`:

```bash
llm_wiki data/research/daily/2026-04-26/papers/2601.17835/paper.md \
  --source-kind Paper \
  --cognee-output output/cognee_direct_data_test_bundle \
  --cognee-add \
  --cognee-dataset llm_wiki_data_test \
  -o output/cognee_direct_data_test_graph.json
```

`--cognee-cognify` is available, but it may invoke configured LLM/embedding providers, so the default direct path is add-only.

Run Cognee `cognify` through Codex CLI/OAuth instead of API-key LLM calls. This runtime-patches Cognee's LLM client to call `codex exec` via stdin. For smoke-only runs you can use deterministic local embeddings, but for real no-API-key retrieval quality prefer Ollama `qwen3-embedding:0.6b`:

```bash
ollama serve
ollama pull qwen3-embedding:0.6b
llm_wiki data/research/daily/2026-04-26/papers/2601.17835/paper.md \
  --source-kind Paper \
  --cognee-output output/cognee_qwen_embedding_sample_bundle \
  --cognee-codex-cognify \
  --cognee-codex-model gpt-5.4 \
  --cognee-codex-timeout 300 \
  --cognee-embedding-provider ollama \
  --cognee-ollama-embedding-model qwen3-embedding:0.6b \
  --cognee-local-embedding-dimensions 1024 \
  --cognee-system-root output/cognee_qwen_embedding_sample_system \
  --cognee-data-root output/cognee_qwen_embedding_sample_data \
  --cognee-dataset llm_wiki_qwen_embedding_sample \
  -o output/cognee_qwen_embedding_sample_graph.json
```

Important: keep `--cognee-system-root` isolated when changing embedding dimensions. Previous deterministic runs create 128-dim LanceDB tables; Qwen3 embeddings are 1024-dim, so reusing the same Cognee system root causes LanceDB/Arrow dimension errors.

Expose a compiled ResearchGraph JSON as a local stdio MCP server:

```bash
python3 -m llm_wiki.mcp_server \
  --graph output/cognee_qwen_embedding_full_graph.json
```

The server implements JSON-RPC/MCP `initialize`, `tools/list`, and `tools/call` without requiring the Python MCP SDK. Available tools:

- `schema` — return the controlled node/edge type whitelist
- `graph_summary` — return node/edge counts and type distributions
- `search_nodes` — search node names, aliases, descriptions, types, and metadata
- `node_context` — return a node with incident edges and neighboring nodes

Example Hermes MCP config when the project is not installed as a package:

```yaml
mcp_servers:
  llm_wiki:
    command: "python3"
    args:
      - "-m"
      - "llm_wiki.mcp_server"
      - "--graph"
      - "/Users/neo/Developer/Projects/LLM-Wiki/output/cognee_qwen_embedding_full_graph.json"
    env:
      PYTHONPATH: "/Users/neo/Developer/Projects/LLM-Wiki"
```

Restart Hermes/gateway after adding the config so the native MCP client discovers tools such as `mcp_llm_wiki_search_nodes`.

Run a full deterministic corpus ingest without `--limit`:

```bash
llm_wiki data/research/daily \
  --source-kind Paper \
  --trends \
  --canonicalize \
  --review-output output/full_corpus_review_queue.json \
  --review-markdown-output output/full_corpus_review_queue.md \
  --review-jsonl-output output/full_corpus_review_queue.jsonl \
  --review-decisions-template output/full_corpus_review_decisions.template.json \
  --project-markdown output/full_corpus_markdown_projection \
  --sqlite-output output/full_corpus.sqlite \
  --kuzu-output output/full_corpus.kuzu \
  --cognee-output output/full_corpus_cognee \
  --report-output output/full_corpus_report.md \
  --batch-manifest output/full_corpus_manifest.json \
  --pretty \
  -o output/full_corpus_graph.json
```

Use cost-aware selective Claude enrichment only for matching paths:

```bash
llm_wiki data/research/daily \
  --source-kind Paper \
  --extractor selective-claude \
  --claude-include '*/2601.17835/*' \
  --claude-limit 2 \
  --claude-config-dir /Users/neo/.claude-personal1 \
  --claude-config-dir /Users/neo/.claude-personal2 \
  --trends \
  --canonicalize \
  -o output/selective_claude_graph.json
```

## Architecture direction

This baseline should become the validation and schema layer around Cognee/Claude extraction:

1. Claude extracts a `ResearchKnowledgeGraph` matching this ontology.
2. The graph is validated against the controlled node/edge vocabularies.
3. Concepts are canonicalized and merged by alias/definition.
4. Claims are grounded to `EvidenceSpan` nodes.
5. Papers/repositories are assigned to `ApproachFamily` candidates for review.
6. Trends are derived from changes in concept/family/result frequency over time.
