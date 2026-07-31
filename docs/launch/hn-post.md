# HN / Twitter launch post draft

This file is committed locally for review. It is NOT part of the demo site.
Update before posting; this is a starting point, not a final draft.

Source: derived from `docs/superpowers/specs/2026-05-13-competitive-positioning-research.md`
section 5.4. The HN body is reproduced verbatim from that section so the
research framing is the framing that ships. The Twitter thread is composed
fresh against the same angle (the compile model, not the feature list).

---

## Hacker News post

**Suggested title:** *Show HN: Tesserae — compile your sources into a typed wiki agents can read*

**Body (verbatim from research doc section 5.4, "Draft post copy"):**

> I have been building Tesserae, a knowledge-graph compiler for projects that have outgrown their docs but not yet earned a custom UI. Point it at a folder of markdown, code, and PDFs; it returns a typed graph (41 node types, controlled edge vocabulary), a static HTML wiki, and an MCP server that lets agents query the same graph the humans read. It uses Codex or Claude CLI over OAuth, so the default path is free of API keys. It composes Understand Anything for code graphs and RAG-Anything for multimodal sources rather than reinventing those.
>
> The compile model is the part I think is interesting. Most knowledge tools are editors (Logseq, Obsidian, AppFlowy) or runtimes (Cognee, Mem0, Letta). Tesserae is a build tool: deterministic output, reruns produce byte-identical pages, the graph view and the wiki and the MCP server are three views of the same artifact. The wiki is meant to be regenerated, not maintained. I would love feedback on whether this framing lands — and on whether the next thing I should build is streaming compile (watch the wiki render in real time) or per-page ask boxes (chat with any concept page using your local memory backend). Repo, comparison table, and a demo wiki of the project's own source are linked.

**Links to include in the first comment, not the post body:**

- Repo: `https://github.com/<org>/<repo>` (fill in before posting)
- Live demo: `https://ca1773130n.github.io/Tesserae` (the dogfood compile of Tesserae's own source). The `build-demo` workflow that redeployed it on every push to `main` was removed, so this page is frozen at its last run — refresh it manually before linking it anywhere public.
- Comparison table: link to the README anchor `#how-it-compares`
- Research doc: `docs/superpowers/specs/2026-05-13-competitive-positioning-research.md`

**Author notes (do not post):**

- Lead with the compile model, not the feature triple. The HN audience pattern-matches "another RAG" or "another KG" on feature lists; the compile framing is the only thing that survives skimming.
- Do not soften the comparison table. The point of section 5.2 is that Tesserae picks trades, and the trades are legible.
- Be ready for "this is just GraphRAG with a static site" and for "this is just Quartz with extraction." Both are partially true; the answer is the typed schema + MCP + multi-source composition, which neither has.
- Be ready for "why not Obsidian." The answer is that Tesserae does not edit. It compiles. Users keep Obsidian; Tesserae reads the vault.
- If the post lands well: don't oversell the per-page ask widget. It works locally with `tesserae project serve`; on the static GH Pages demo it gracefully collapses to a footer.

---

## Twitter thread (6 tweets)

Compose loosely; each tweet ends with a soft handoff to the next. Last tweet
ends with the demo link. Lead with the compile-model framing, not the feature
list — same angle as the HN body. All tweets verified under 280 characters.

1. Most "AI memory" tools are editors or runtimes. I built a third thing: a compiler. Tesserae points at a folder of markdown, code, and PDFs and returns a typed wiki you regenerate, the way you regenerate a binary from source. Open source. Thread below.

2. The compile model means determinism. Same sources in, byte-identical wiki out. The graph view, the static HTML pages, and the MCP server agents call are three views of one artifact — not three databases you keep in sync.

3. The graph is typed. 41 node types across 5 layers — Field / Source / Concept / Assertion / Synthesis — plus a controlled edge vocabulary. Closer to an academic ontology than a free-form note graph. Schema is exposed over MCP so agents can ask for the controlled terms.

4. The default path uses no API keys. Codex CLI or Claude CLI over OAuth handles the LLM extraction; embeddings default to a deterministic in-process provider. You can swap in Ollama or an OpenAI-compatible endpoint when you want better recall.

5. Multimodal and code-graph are first-class adjuncts, not random forks. RAG-Anything handles PDFs and Office docs through MinerU/Docling. Understand Anything handles code. Tesserae merges both into the same typed graph.

6. The site is a static HTML build — host it on GitHub Pages, S3, or any laptop. Live dogfood demo (Tesserae compiled against its own source) here: https://ca1773130n.github.io/Tesserae

---

## Posting checklist

Before posting on HN:

- [ ] Replace `https://ca1773130n.github.io/Tesserae` in the README and in tweet 6 with the real Pages URL.
- [ ] Rebuild and redeploy the demo site by hand (`tesserae export site`), then confirm it is reachable and reflects current `main` — no workflow does this any more.
- [ ] Have a comment ready listing repo URL, comparison-table anchor, and the research doc.
- [ ] Decide which of B1 (streaming compile) or B3 (per-page ask) to ask the audience about — the HN body asks both; pick one if the comments converge.
- [ ] Pre-write replies to the predictable threads: "why not Obsidian", "this is just GraphRAG", "what does typed buy me".

Do not post on a Friday. Aim for a Tuesday or Wednesday morning, US time.
