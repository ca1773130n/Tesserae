"""Proactive ingestion: feed parsing, the ledger, the budget, and the source.

No network anywhere here. ``run_tick`` takes both fetchers as arguments for
exactly this reason, so the pass that decides what to spend money on is tested
without spending any.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tesserae.ingest.fetch import UnsupportedSourceError
from tesserae.proactive import (
    DEFAULT_BUDGET,
    LEDGER_NAME,
    FetchLedger,
    configured_feeds,
    feed_item_urls,
    run_tick,
    select_new,
)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>
<item><title>A</title><link>https://example.org/a</link></item>
<item><title>B</title><link>https://example.org/b</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><link rel="alternate" href="https://arxiv.org/abs/2501.1"/></entry>
<entry><link href="https://arxiv.org/abs/2501.2"/></entry>
</feed>"""


# ----------------------------------------------------------------- parsing


def test_rss_and_atom_links_come_back_in_feed_order():
    assert feed_item_urls(RSS) == ["https://example.org/a", "https://example.org/b"]
    assert feed_item_urls(ATOM) == ["https://arxiv.org/abs/2501.1", "https://arxiv.org/abs/2501.2"]


def test_an_atom_entry_prefers_its_alternate_link():
    doc = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <link rel="edit" href="https://example.org/edit"/>
    <link rel="alternate" href="https://example.org/read"/>
    </entry></feed>"""
    assert feed_item_urls(doc) == ["https://example.org/read"]


def test_relative_links_resolve_against_the_feed():
    doc = '<?xml version="1.0"?><rss><channel><item><link>/post/1</link></item></channel></rss>'
    assert feed_item_urls(doc, base_url="https://blog.example/feed.xml") == [
        "https://blog.example/post/1"
    ]


def test_duplicate_links_collapse():
    doc = (
        '<?xml version="1.0"?><rss><channel>'
        "<item><link>https://x.org/a</link></item>"
        "<item><link>https://x.org/a</link></item></channel></rss>"
    )
    assert feed_item_urls(doc) == ["https://x.org/a"]


def test_a_page_that_is_not_a_feed_yields_nothing():
    """The caller reads [] as 'this URL is itself the document'."""
    assert feed_item_urls("<html><body><p>hello</p></body></html>") == []
    assert feed_item_urls("not xml at all") == []
    assert feed_item_urls("") == []


# ------------------------------------------------------- hostile documents


def test_a_billion_laughs_feed_is_refused_not_expanded():
    doc = """<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">
    <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
    <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>
    <rss><channel><item><link>&lol3;</link></item></channel></rss>"""
    assert feed_item_urls(doc) == []


def test_an_external_entity_feed_is_refused():
    doc = """<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>
    <rss><channel><item><link>&x;</link></item></channel></rss>"""
    assert feed_item_urls(doc) == []


def test_a_doctype_hidden_behind_padding_is_still_refused():
    """Grepping the first N bytes is bypassed by padding the prolog. Don't."""
    doc = (
        '<?xml version="1.0"?>'
        + "<!-- filler -->" * 2000
        + '<!DOCTYPE r [<!ENTITY x "boom">]>'
        + "<rss><channel><item><link>&x;</link></item></channel></rss>"
    )
    assert feed_item_urls(doc) == []


def test_a_doctype_inside_cdata_does_not_refuse_a_real_feed():
    """The over-refusal a whole-document grep would cause: a post about HTML."""
    doc = """<?xml version="1.0"?><rss><channel><item>
    <description><![CDATA[<!DOCTYPE html><p>a post about html</p>]]></description>
    <link>https://example.org/post</link></item></channel></rss>"""
    assert feed_item_urls(doc) == ["https://example.org/post"]


def test_an_enormous_feed_is_refused_before_parsing():
    from tesserae.proactive import MAX_FEED_BYTES

    assert feed_item_urls("<rss>" + "x" * (MAX_FEED_BYTES + 1) + "</rss>") == []


# ------------------------------------------------------------------ ledger


def test_the_ledger_round_trips(tmp_path):
    path = tmp_path / LEDGER_NAME
    ledger = FetchLedger(path)
    ledger.record("https://x.org/a", "ingested", detail="data/ingested/a.md")
    ledger.record("https://x.org/b", "refused", detail="served application/pdf")
    ledger.save()

    reloaded = FetchLedger(path)
    assert "https://x.org/a" in reloaded and "https://x.org/b" in reloaded
    assert reloaded.get("https://x.org/a").status == "ingested"
    assert reloaded.get("https://x.org/b").status == "refused"
    assert json.loads(path.read_text())["version"] == "1"


def test_a_missing_or_corrupt_ledger_is_empty_not_fatal(tmp_path):
    assert len(FetchLedger(tmp_path / "absent.json")) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert len(FetchLedger(bad)) == 0


def test_only_terminal_statuses_are_recordable(tmp_path):
    ledger = FetchLedger(tmp_path / LEDGER_NAME)
    with pytest.raises(ValueError):
        ledger.record("https://x.org/a", "timeout")


# ------------------------------------------------------------------ budget


def test_select_new_takes_the_budget_in_order_and_skips_what_is_known(tmp_path):
    ledger = FetchLedger(tmp_path / LEDGER_NAME)
    ledger.record("https://x.org/b", "ingested")
    urls = [f"https://x.org/{c}" for c in "abcde"]
    assert select_new(urls, ledger, 2) == ["https://x.org/a", "https://x.org/c"]
    assert select_new(urls, ledger, 0) == []
    assert len(select_new(urls, ledger, 99)) == 4  # b is already known


def test_a_refused_url_is_never_selected_again(tmp_path):
    ledger = FetchLedger(tmp_path / LEDGER_NAME)
    ledger.record("https://x.org/paper.pdf", "refused")
    assert select_new(["https://x.org/paper.pdf"], ledger, 5) == []


# -------------------------------------------------------------------- tick


def _tick(tmp_path, feeds, documents, *, budget=DEFAULT_BUDGET, ledger=None, fetched=None):
    """Run a tick with both fetchers faked. ``documents`` maps url -> body."""
    fetched = fetched if fetched is not None else []

    def fetch_feed(url):
        if url not in documents:
            raise ConnectionError(f"no route to {url}")
        return documents[url]

    def fetch_document(url, dest_dir):
        if url.endswith(".pdf"):
            raise UnsupportedSourceError(f"cannot read {url} — it served application/pdf")
        if url.endswith("-flaky"):
            raise TimeoutError("read timed out")
        fetched.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / (url.rsplit("/", 1)[-1] + ".md")
        path.write_text(f"# {url}\n", encoding="utf-8")
        return path

    return run_tick(
        feeds=feeds,
        ledger=ledger if ledger is not None else FetchLedger(tmp_path / LEDGER_NAME),
        dest_dir=tmp_path / "data" / "ingested",
        budget=budget,
        fetch_feed=fetch_feed,
        fetch_document=fetch_document,
    )


def test_a_tick_ingests_new_documents_and_marks_the_graph_changed(tmp_path):
    result = _tick(tmp_path, ["https://f/rss"], {"https://f/rss": RSS})
    assert len(result.ingested) == 2
    assert result.changed is True
    assert (tmp_path / "data" / "ingested" / "a.md").is_file()


def test_the_budget_bounds_what_one_tick_costs(tmp_path):
    result = _tick(tmp_path, ["https://f/rss"], {"https://f/rss": RSS}, budget=1)
    assert len(result.ingested) == 1


def test_the_second_tick_fetches_nothing_new(tmp_path):
    ledger_path = tmp_path / LEDGER_NAME
    _tick(tmp_path, ["https://f/rss"], {"https://f/rss": RSS}, ledger=FetchLedger(ledger_path))
    again = _tick(
        tmp_path, ["https://f/rss"], {"https://f/rss": RSS}, ledger=FetchLedger(ledger_path)
    )
    assert again.ingested == []
    assert again.changed is False


def test_an_unreadable_document_is_refused_once_and_never_retried(tmp_path):
    """The arXiv PDF link that would otherwise eat a budget slot every hour."""
    feed = (
        '<?xml version="1.0"?><rss><channel>'
        "<item><link>https://x.org/paper.pdf</link></item>"
        "<item><link>https://x.org/good</link></item></channel></rss>"
    )
    ledger_path = tmp_path / LEDGER_NAME
    first = _tick(tmp_path, ["https://f/rss"], {"https://f/rss": feed}, ledger=FetchLedger(ledger_path))
    assert first.refused == ["https://x.org/paper.pdf"]
    assert first.ingested == [str(tmp_path / "data" / "ingested" / "good.md")]

    retried = []
    second = _tick(
        tmp_path,
        ["https://f/rss"],
        {"https://f/rss": feed},
        ledger=FetchLedger(ledger_path),
        fetched=retried,
    )
    assert second.refused == [] and second.ingested == [] and retried == []


def test_a_transport_failure_is_retried_on_the_next_tick(tmp_path):
    """A timeout is not a verdict about the document. It must not be recorded."""
    feed = '<?xml version="1.0"?><rss><channel><item><link>https://x.org/a-flaky</link></item></channel></rss>'
    ledger_path = tmp_path / LEDGER_NAME
    first = _tick(tmp_path, ["https://f/rss"], {"https://f/rss": feed}, ledger=FetchLedger(ledger_path))
    assert first.errors == 1 and first.ingested == []
    assert "https://x.org/a-flaky" not in FetchLedger(ledger_path)


def test_one_dead_feed_does_not_stop_the_others(tmp_path):
    result = _tick(
        tmp_path,
        ["https://dead/rss", "https://f/rss"],
        {"https://f/rss": RSS},
    )
    assert result.errors == 1
    assert len(result.ingested) == 2


def test_a_url_that_is_not_a_feed_is_ingested_as_the_document(tmp_path):
    """Putting a plain article in proactive_sources means 'ingest this, once'."""
    page = "https://blog.example/post"
    result = _tick(tmp_path, [page], {page: "<html><body>hi</body></html>"})
    assert result.ingested == [str(tmp_path / "data" / "ingested" / "post.md")]


# ------------------------------------------------------------------ config


def test_only_http_entries_count_as_feeds():
    cfg = {
        "proactive_sources": [
            "https://good.example/rss",
            "http://also-good.example/rss",
            "/etc/passwd",
            "ftp://nope.example/x",
            42,
        ]
    }
    assert configured_feeds(cfg) == ["https://good.example/rss", "http://also-good.example/rss"]
    assert configured_feeds({}) == []
    assert configured_feeds({"proactive_sources": "not-a-list"}) == []


# ----------------------------------------------------------- daemon source


def _daemon(tmp_path, **kwargs):
    from tesserae.engine.daemon import Daemon

    d = Daemon(tmp_path, enable_watch=False, enable_vault=False, enable_session_tail=False, **kwargs)
    loop = asyncio.new_event_loop()
    d._loop = loop
    d._queue = asyncio.Queue()
    return d, loop


def _seed_project(root: Path, feeds=()):
    from tesserae.project import ProjectWiki

    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "a.md").write_text("# A\n\nseed.\n", encoding="utf-8")
    wiki = ProjectWiki.init(root, name="proactive")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["proactive_sources"] = list(feeds)
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return wiki


def test_the_proactive_source_is_off_unless_asked(tmp_path):
    """It is the only source that spends money on its own."""
    _seed_project(tmp_path, ["https://f/rss"])
    d, loop = _daemon(tmp_path)
    d._start_sources(loop)
    assert all(t.name != "proactive-source" for t in d._threads)


def test_no_configured_feeds_starts_no_thread(tmp_path):
    _seed_project(tmp_path, [])
    d, loop = _daemon(tmp_path, enable_proactive=True)
    d._start_sources(loop)
    assert all(t.name != "proactive-source" for t in d._threads)


def test_an_unloadable_project_is_logged_not_fatal(tmp_path, caplog):
    import logging

    d, loop = _daemon(tmp_path / "never-initialised", enable_proactive=True)
    with caplog.at_level(logging.WARNING, logger="tesserae.daemon"):
        d._start_sources(loop)
    assert all(t.name != "proactive-source" for t in d._threads)
    assert any("proactive source not started" in r.getMessage() for r in caplog.records)


def test_the_source_starts_and_stops_on_the_stop_event(tmp_path):
    _seed_project(tmp_path, ["https://f/rss"])
    d, loop = _daemon(tmp_path, enable_proactive=True, proactive_interval=0.05)
    d._start_sources(loop)
    threads = [t for t in d._threads if t.name == "proactive-source"]
    assert len(threads) == 1 and threads[0].daemon is True
    d._stop_event.set()
    threads[0].join(timeout=5)
    assert not threads[0].is_alive()


# -------------------------------------------------------------- sources CLI


def test_sources_add_routes_a_url_to_the_feed_list(tmp_path, capsys, monkeypatch):
    """One verb for "where knowledge comes from"; the argument picks the list."""
    from tesserae.cli import main as cli_main

    _seed_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli_main(["sources", "add", "https://feeds.example/rss"]) == 0
    cfg = json.loads((tmp_path / ".tesserae" / "config.json").read_text())
    assert cfg["proactive_sources"] == ["https://feeds.example/rss"]
    assert "docs" not in cfg.get("proactive_sources", [])

    # ...and a directory still goes to the compile scope, untouched.
    (tmp_path / "notes").mkdir()
    assert cli_main(["sources", "add", "notes"]) == 0
    cfg = json.loads((tmp_path / ".tesserae" / "config.json").read_text())
    assert "notes" in cfg["sources"]
    assert cfg["proactive_sources"] == ["https://feeds.example/rss"]


def test_adding_the_same_feed_twice_is_a_no_op(tmp_path, capsys, monkeypatch):
    from tesserae.cli import main as cli_main

    _seed_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    cli_main(["sources", "add", "https://feeds.example/rss"])
    capsys.readouterr()
    assert cli_main(["sources", "add", "https://feeds.example/rss"]) == 0
    assert "already a feed" in capsys.readouterr().out
    cfg = json.loads((tmp_path / ".tesserae" / "config.json").read_text())
    assert cfg["proactive_sources"] == ["https://feeds.example/rss"]


def test_sources_list_shows_feeds_beside_directories(tmp_path, capsys, monkeypatch):
    from tesserae.cli import main as cli_main

    _seed_project(tmp_path, ["https://feeds.example/rss"])
    monkeypatch.chdir(tmp_path)
    assert cli_main(["sources", "list"]) == 0
    out = capsys.readouterr().out
    assert "[feed  ] https://feeds.example/rss" in out
    assert "engine --proactive" in out


def test_sources_remove_takes_a_feed_and_refuses_an_unknown_one(tmp_path, capsys, monkeypatch):
    from tesserae.cli import main as cli_main

    _seed_project(tmp_path, ["https://feeds.example/rss"])
    monkeypatch.chdir(tmp_path)
    assert cli_main(["sources", "remove", "https://feeds.example/rss"]) == 0
    assert json.loads((tmp_path / ".tesserae" / "config.json").read_text())["proactive_sources"] == []
    capsys.readouterr()
    assert cli_main(["sources", "remove", "https://feeds.example/other"]) == 1
    assert "not a feed" in capsys.readouterr().err
