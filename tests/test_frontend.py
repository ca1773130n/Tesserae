import json

from llm_wiki.frontend import StaticSiteBuilder
from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def frontend_sample_graph():
    project = ResearchNode(id="CodeProject:demo", name="demo-app", type=ResearchNodeType.CODE_PROJECT, description="Demo application")
    file_node = ResearchNode(id="SourceFile:api", name="src/api.py", type=ResearchNodeType.SOURCE_FILE, source_path="src/api.py", metadata={"language": "python"})
    symbol = ResearchNode(id="CodeFunction:route", name="route", type=ResearchNodeType.CODE_FUNCTION)
    paper = ResearchNode(id="Paper:demo", name="Demo Paper", type=ResearchNodeType.PAPER)
    return ResearchGraph(
        nodes=[project, file_node, symbol, paper],
        edges=[
            ResearchEdge(source=project.id, target=file_node.id, type="contains"),
            ResearchEdge(source=file_node.id, target=symbol.id, type="defines"),
        ],
    )


def test_static_site_builder_writes_frontend_assets(tmp_path):
    out = tmp_path / "site"

    result = StaticSiteBuilder(site_title="Demo Wiki").write_site(frontend_sample_graph(), out)

    assert result["site_path"] == str(out)
    assert (out / "index.html").exists()
    assert (out / "graph.json").exists()
    assert (out / "search-index.json").exists()
    assert (out / "llms.txt").exists()
    assert (out / "llms-full.txt").exists()
    assert (out / "manifest.json").exists()
    assert (out / "assets" / "style.css").exists()
    assert (out / "assets" / "app.js").exists()
    assert (out / "nodes" / "index.html").exists()
    assert (out / "sources" / "index.html").exists()
    source_pages = sorted((out / "sources").glob("*.html"))
    assert len(source_pages) > 1
    assert any("src-api-py" in page.name for page in source_pages)
    assert "Nodes from this source" in next(page for page in source_pages if "src-api-py" in page.name).read_text(encoding="utf-8")
    graph_html = (out / "graph" / "index.html")
    assert graph_html.exists()
    graph_source = graph_html.read_text(encoding="utf-8")
    assert "vis-network" in graph_source
    assert "Cluster:" in graph_source
    assert "Find neighbours" in graph_source
    assert "stats-overlay" in graph_source
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Demo Wiki" in html
    assert "LLM-Wiki" in html
    assert "Research" in html
    assert "Development" in html
    assert "Command palette" in html
    assert "Browse nodes" in html
    assert "Source files" in html
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    assert graph["nodes"][0]["type"]


def test_static_site_search_index_includes_code_and_research_nodes(tmp_path):
    out = tmp_path / "site"
    StaticSiteBuilder(site_title="Demo Wiki").write_site(frontend_sample_graph(), out)

    entries = json.loads((out / "search-index.json").read_text(encoding="utf-8"))
    names = {entry["title"] for entry in entries}
    assert {"demo-app", "src/api.py", "route", "Demo Paper"}.issubset(names)


def test_static_site_embeds_parseable_search_json(tmp_path):
    out = tmp_path / "site"
    StaticSiteBuilder(site_title="Demo Wiki").write_site(frontend_sample_graph(), out)

    html = (out / "index.html").read_text(encoding="utf-8")
    marker = '<script id="search-data" type="application/json">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    embedded = html[start:end]

    assert "&quot;" not in embedded
    payload = json.loads(embedded)
    assert payload[0]["title"] == "demo-app"
