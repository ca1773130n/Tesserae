"""Proactive ingestion: the engine goes and gets knowledge instead of waiting.

Pillar 2 of the mission says the engine "pulls in and reconstructs knowledge on
its own, continuously improving the base rather than waiting to be told". Every
trigger source the daemon had before this one waits for a local file to change:
the filesystem watcher, the vault watcher, the session tailer. They reconstruct
what you already put on disk. Nothing fetched.

This module is the part with no daemon and no network in it — feed parsing, the
ledger, and the budget — so the interesting logic is testable without either.
:meth:`tesserae.engine.daemon.Daemon._start_proactive_source` is the thin loop
that calls it on a timer, and ``fetch_to_source`` (already used by `tesserae
ingest <url>`) does the actual fetching.

Three constraints shape all of it, because this is the first thing in Tesserae
that spends money without a human at the keyboard:

* **Off unless asked.** ``tesserae engine --proactive``, never a default.
* **Budgeted per tick.** One arXiv category feed is thirty-odd entries; LLM
  extraction of all of them, unannounced, is a surprise bill.
* **Never fetched twice.** The ledger is on disk, so a restart does not
  re-ingest the corpus, and a URL that Tesserae *cannot read* is remembered as
  refused rather than retried hourly forever.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin
from xml.etree import ElementTree

logger = logging.getLogger("tesserae.proactive")

#: Ledger filename under ``.tesserae/``.
LEDGER_NAME = "proactive-ledger.json"

#: How many NEW documents one tick may fetch, across every configured feed.
#: Deliberately small. The cost of a low number is latency on a backlog, which
#: the next tick clears; the cost of a high one is an unattended bill.
DEFAULT_BUDGET = 5

#: Seconds between ticks. Feeds are published hourly at best.
DEFAULT_INTERVAL = 3600.0

#: Refuse a "feed" larger than this before parsing it. Real feeds are tens of
#: kilobytes. The cap bounds the quadratic-blowup class of XML attack by
#: denying it the input size it needs, and costs nothing legitimate.
MAX_FEED_BYTES = 5 * 1024 * 1024


def _localname(tag: str) -> str:
    """``{http://www.w3.org/2005/Atom}entry`` -> ``entry``."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _declares_a_dtd(document: str) -> bool:
    """True if the document's prolog carries a ``<!DOCTYPE``.

    This is the one place in Tesserae that parses XML fetched from a URL
    somebody else controls, so the two classic attacks apply: an external
    entity that makes the parser read a local file or reach an internal host,
    and nested internal entities that expand to gigabytes ("billion laughs").

    Both require a DTD — there is nowhere else to declare an entity — so
    refusing the DTD refuses the whole class, at the door, before expat sees
    it. A feed has no legitimate use for one. That needs no dependency, and it
    is narrower than defusedxml, which parses the DTD and then polices what it
    found.

    The scan walks the PROLOG rather than grepping the whole document, for two
    opposite reasons. Grepping the first N bytes can be slipped by padding the
    prolog with comments until the DOCTYPE sits past N. Grepping everything
    over-refuses: a perfectly ordinary feed entry may carry ``<!DOCTYPE html>``
    inside a CDATA description, and dropping that feed would be a bug nobody
    could diagnose from the outside. The prolog ends at the root element, and a
    DTD that is not in the prolog is not a DTD.
    """
    i, n = 0, len(document)
    while i < n:
        ch = document[i]
        if ch.isspace():
            i += 1
        elif document.startswith("<?", i):  # <?xml ...?> / processing instruction
            close = document.find("?>", i)
            if close == -1:
                return False
            i = close + 2
        elif document.startswith("<!--", i):
            close = document.find("-->", i)
            if close == -1:
                return False
            i = close + 3
        elif document[i : i + 9].upper() == "<!DOCTYPE":
            return True
        else:
            return False  # the root element (or junk): the prolog is over
    return False


def feed_item_urls(document: str, *, base_url: str = "") -> List[str]:
    """Item links from an RSS or Atom document, in feed order, deduplicated.

    Returns ``[]`` for anything that is not a parseable feed, INCLUDING a
    well-formed HTML page — the caller treats that as "this URL is itself the
    document" and ingests it once, which is what someone who put a plain
    article in ``proactive_sources`` meant. A document that is hostile rather
    than merely unparseable lands here too, and gets the same answer.
    """
    if len(document.encode("utf-8", "ignore")) > MAX_FEED_BYTES:
        logger.warning("refusing a feed larger than %d bytes", MAX_FEED_BYTES)
        return []
    if _declares_a_dtd(document):
        logger.warning("refusing a feed that declares a DTD (entity expansion)")
        return []
    try:
        root = ElementTree.fromstring(document)
    except (ElementTree.ParseError, ValueError):
        return []

    urls: List[str] = []
    for element in root.iter():
        name = _localname(element.tag)
        if name == "item":  # RSS 2.0 / RDF
            for child in element:
                if _localname(child.tag) == "link" and (child.text or "").strip():
                    urls.append(child.text.strip())
                    break
        elif name == "entry":  # Atom
            best: Optional[str] = None
            for child in element:
                if _localname(child.tag) != "link":
                    continue
                rel = child.get("rel") or "alternate"
                href = (child.get("href") or "").strip()
                if href and rel == "alternate":
                    best = href
                    break
                if href and best is None:
                    best = href
            if best:
                urls.append(best)

    seen, ordered = set(), []
    for url in urls:
        absolute = urljoin(base_url, url) if base_url else url
        if absolute not in seen:
            seen.add(absolute)
            ordered.append(absolute)
    return ordered


@dataclass(frozen=True)
class LedgerEntry:
    """One URL this project has already dealt with, and how it went."""

    #: ``ingested`` (a source file exists) or ``refused`` (Tesserae cannot read
    #: it). Both mean "do not fetch again". A TRANSPORT failure is neither and
    #: is never recorded, so a timeout or a 503 retries on the next tick.
    status: str
    at: str
    detail: str = ""


class FetchLedger:
    """What has already been pulled in, persisted beside the graph.

    Without it every restart re-fetches the whole feed, and every tick retries
    the arXiv PDF link that ``fetch_to_source`` correctly refuses — which alone
    would consume the budget forever and starve the readable entries behind it.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._entries: Dict[str, LedgerEntry] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return  # absent or corrupt: an empty ledger re-fetches, never crashes
        for url, raw in (payload.get("fetched") or {}).items():
            if isinstance(raw, dict) and isinstance(raw.get("status"), str):
                self._entries[url] = LedgerEntry(
                    status=raw["status"], at=raw.get("at", ""), detail=raw.get("detail", "")
                )

    def __contains__(self, url: object) -> bool:
        return url in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, url: str) -> Optional[LedgerEntry]:
        return self._entries.get(url)

    def record(self, url: str, status: str, detail: str = "") -> None:
        """Mark ``url`` handled. Only ``ingested`` and ``refused`` are terminal."""
        if status not in ("ingested", "refused"):
            raise ValueError(f"status must be 'ingested' or 'refused', not {status!r}")
        self._entries[url] = LedgerEntry(
            status=status,
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            detail=detail,
        )

    def save(self) -> None:
        """Write the ledger. Best-effort: losing it costs re-fetches, not data."""
        payload = {
            "version": "1",
            "fetched": {
                url: {"status": e.status, "at": e.at, **({"detail": e.detail} if e.detail else {})}
                for url, e in sorted(self._entries.items())
            },
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            logger.warning("could not write the proactive ledger at %s", self.path, exc_info=True)


def select_new(urls: Iterable[str], ledger: FetchLedger, budget: int) -> List[str]:
    """The next ``budget`` URLs this project has not already handled, in order."""
    if budget <= 0:
        return []
    picked: List[str] = []
    for url in urls:
        if url in ledger or url in picked:
            continue
        picked.append(url)
        if len(picked) >= budget:
            break
    return picked


def configured_feeds(config: dict) -> List[str]:
    """``proactive_sources`` from a project config: http(s) entries only.

    Shares the config file with ``sources`` (local compile directories) and is
    kept separate from it on purpose: a directory is walked on every compile,
    a feed is polled and fetched once per item.
    """
    from .ingest.fetch import is_url

    raw = config.get("proactive_sources") or []
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, str) and is_url(entry)]


@dataclass
class TickResult:
    """What one proactive pass actually did."""

    ingested: List[str]
    refused: List[str]
    #: Feeds or documents that failed in a way worth RETRYING — a timeout, a
    #: 503, a DNS blip. Counted, never recorded in the ledger.
    errors: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.ingested)


def run_tick(
    *,
    feeds: Iterable[str],
    ledger: FetchLedger,
    dest_dir: Path,
    budget: int = DEFAULT_BUDGET,
    fetch_feed=None,
    fetch_document=None,
) -> TickResult:
    """Poll every feed, fetch up to ``budget`` unseen documents, record them.

    The two fetchers are injected so the whole pass is testable without a
    network: by default they are the ones `tesserae ingest <url>` already uses,
    which is also what keeps a proactively-fetched file byte-identical to a
    hand-ingested one — same frontmatter, same provenance, same refusals.
    """
    from .ingest.fetch import UnsupportedSourceError, fetch_to_source, http_get_text

    fetch_feed = fetch_feed or http_get_text
    fetch_document = fetch_document or fetch_to_source

    candidates: List[str] = []
    result = TickResult(ingested=[], refused=[])
    for feed_url in feeds:
        try:
            document = fetch_feed(feed_url)
        except Exception:  # noqa: BLE001 - one dead feed must not stop the others
            logger.warning("proactive: could not read feed %s", feed_url, exc_info=True)
            result.errors += 1
            continue
        # A URL that is not a feed IS the document: somebody who put a plain
        # article in proactive_sources meant "ingest this", and the ledger
        # makes that a one-shot rather than an hourly re-fetch.
        items = feed_item_urls(document, base_url=feed_url) or [feed_url]
        candidates.extend(items)

    for url in select_new(candidates, ledger, budget):
        try:
            path = fetch_document(url, dest_dir)
        except UnsupportedSourceError as exc:
            # Terminal: Tesserae cannot read this and never will. Recording it
            # is what stops an arXiv PDF link consuming one slot of the budget
            # every hour, forever, ahead of entries that would have worked.
            logger.info("proactive: refusing %s (%s)", url, str(exc).split(chr(10))[0])
            ledger.record(url, "refused", detail=str(exc).split(chr(10))[0][:200])
            result.refused.append(url)
        except Exception:  # noqa: BLE001 - transport: retry on the next tick
            logger.warning("proactive: fetch failed for %s", url, exc_info=True)
            result.errors += 1
        else:
            ledger.record(url, "ingested", detail=str(path))
            result.ingested.append(str(path))

    ledger.save()
    if result.ingested:
        logger.info(
            "proactive: ingested %d new document(s) into %s", len(result.ingested), dest_dir
        )
    return result
