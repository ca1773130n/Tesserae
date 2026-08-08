"""Fetch a URL into a tracked markdown source. Behind the [ingest-url] extra."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional


class UnsupportedSourceError(ValueError):
    """A source exists and is reachable, but nothing in Tesserae can read it.

    Distinct from :class:`FileNotFoundError` (the input is not there) and from a
    transport error (the fetch failed): the bytes arrived, and refusing them is
    the correct outcome. ``tesserae.cli.main`` catches this centrally and turns
    it into a one-line message plus exit 2, never a traceback.
    """


def is_url(value: str) -> bool:
    """True only for http(s) URLs — everything else is treated as a local path."""
    return value.startswith("http://") or value.startswith("https://")


_SLUG_MAX = 80


def _slugify(value: str) -> str:
    """Filesystem-safe, deterministic slug. Long values get a hash suffix to avoid collisions."""
    stripped = re.sub(r"^https?://", "", value)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", stripped).strip("-").lower()
    if len(slug) <= _SLUG_MAX:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return slug[: _SLUG_MAX - 9].rstrip("-") + "-" + digest


# A bare YAML scalar is unsafe if it contains a newline, a colon acting as a
# mapping indicator (``:`` followed by whitespace or at end of value), a comment
# indicator (whitespace then ``#``), a quote, or has leading/trailing whitespace.
# A colon inside a token like ``https://x.com`` is safe and stays unquoted.
_UNSAFE_SCALAR_RE = re.compile(r"""[\n"']|:(?:\s|$)|(?:^|\s)#""")


def _yaml_scalar(value: str) -> str:
    """Render a string as a YAML scalar, quoting only when it would be unsafe bare.

    Unsafe values (newlines, mapping/comment indicators, quotes, edge whitespace)
    are emitted as a JSON string — valid, round-trippable YAML. Simple values pass
    through unquoted so common front-matter (URLs, hashes, timestamps) stays readable.
    """
    if value != value.strip() or _UNSAFE_SCALAR_RE.search(value):
        return json.dumps(value)
    return value


def _render_frontmatter(meta: Dict[str, str]) -> str:
    """Render a deterministic YAML front-matter block (keys sorted)."""
    lines = ["---"]
    for key in sorted(meta):
        lines.append(f"{key}: {_yaml_scalar(meta[key])}")
    lines.append("---")
    return "\n".join(lines) + "\n"


_ARXIV_RE = re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5})")


def _arxiv_id_from_url(url: str) -> Optional[str]:
    m = _ARXIV_RE.search(url)
    return m.group(1) if m else None


# Non-``text/*`` media types whose bodies are still character data we can
# reasonably persist as markdown. Anything else declared is binary to us.
_TEXTY_APPLICATION_TYPES = (
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/atom+xml",
    "application/rss+xml",
    "application/ld+json",
    "application/javascript",
)


def _is_texty(content_type: str) -> bool:
    """True when a declared content-type names character data, not bytes."""
    media = content_type.split(";", 1)[0].strip().lower()
    if media.startswith("text/"):
        return True
    if media.endswith("+json") or media.endswith("+xml"):
        return True
    return media in _TEXTY_APPLICATION_TYPES


# Leading signatures of formats we would otherwise decode into mojibake. The
# declared content-type is not enough on its own: a server can omit it, or get
# it wrong, and either way the bytes are what we end up writing.
#
# Every entry must be long enough, or odd enough, that no English sentence
# starts with it. ``BM`` (bmp) and ``RIFF`` (webp/wav) were here and failed that
# test: they refused a text/markdown body opening "BM25 is a bag-of-words
# ranking function...", which on an IR/RAG project is a live opening word, plus
# "BMW's..." and "RIFF codes are...". Neither loses detection by being absent —
# a real BMP has reserved NUL bytes at offset 6 and a real RIFF header is NUL-
# padded, so the first-kilobyte NUL check below catches both. Pinned by
# ``test_fetch_still_refuses_real_bmp_and_riff_bodies``.
_BINARY_MAGIC = (
    b"%PDF-",            # PDF
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",     # JPEG
    b"GIF87a",
    b"GIF89a",
    b"PK\x03\x04",       # zip, and everything built on it (docx, xlsx, pptx)
    b"\x1f\x8b",         # gzip
    b"\x00\x00\x01\x00",  # ico
    b"\xd0\xcf\x11\xe0",  # legacy MS Office (doc, xls, ppt)
    b"%!PS",             # postscript
    b"\x7fELF",
)


def _response_bytes(response: object) -> bytes:
    """The raw body, when the response object carries one.

    A real ``httpx.Response`` always has ``.content``; hand-rolled test doubles
    in this repo carry only a ``str`` ``.text``. Falling back to re-encoding
    that keeps those doubles working without pretending we sniffed the wire
    bytes — a NUL or a magic number that survived the decode still shows up.
    """
    raw = getattr(response, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.encode("utf-8", errors="replace")
    return b""


def _looks_binary(body: bytes) -> bool:
    """True when ``body`` is bytes we must not decode as text.

    Two tells, both conservative, and NEITHER a guarantee. A known magic number
    is decisive. Failing that, a NUL byte in the first kilobyte: no real
    text/markdown source contains one, and most binary containers do — measured
    on 1,293 local files that fail a utf-8 decode, this misses 18 of them
    (1.4%), mostly Apple bplists and raw UUID blobs. Those are only reachable
    when the server ALSO omits or mislabels its content-type, which is why the
    residue is accepted rather than chased. Deliberately NOT "contains
    non-ASCII" — that would reject every non-English source.
    """
    if not body:
        return False
    if body.startswith(_BINARY_MAGIC):
        return True
    return b"\x00" in body[:1024]


_http_get: Optional[Callable] = None
_html_to_markdown: Optional[Callable] = None


def _load_url_deps() -> None:
    """Bind _http_get / _html_to_markdown from the optional extra, or raise."""
    global _http_get, _html_to_markdown
    if _http_get is not None and _html_to_markdown is not None:
        return
    try:
        import httpx
        from markdownify import markdownify as _md
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "URL ingest requires the optional extra: pip install tesserae[ingest-url]"
        ) from exc
    if _http_get is None:
        _http_get = lambda url, timeout=None, follow_redirects=True, headers=None: httpx.get(
            url, timeout=timeout, follow_redirects=follow_redirects, headers=headers or {}
        )
    if _html_to_markdown is None:
        _html_to_markdown = lambda html: _md(html)


def fetch_to_source(url: str, dest_dir: Path, *, title: Optional[str] = None) -> Path:
    """Fetch ``url``, convert to markdown, persist under ``dest_dir`` with provenance.

    Returns the written file path. Raises on non-2xx, on a missing [ingest-url] extra,
    and writes nothing on failure.
    """
    if _http_get is None or _html_to_markdown is None:
        _load_url_deps()

    response = _http_get(url, timeout=30.0, follow_redirects=True, headers={"User-Agent": "tesserae-ingest"})
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")

    # Refuse BEFORE touching ``.text``. Decoding a PDF/image body produced
    # mojibake, hashed the mojibake as ``content_sha256`` (a hash over garbage,
    # not over what the server sent), and wrote the result under a ``.md``
    # suffix — so unlike a local PDF, which the markdown walker at least
    # ignores, this one WAS picked up and fed to the extractor as prose.
    #
    # Both the label and the bytes get a vote, because either can be wrong on
    # its own: arxiv.org/pdf/... declares application/pdf, but a server that
    # omits content-type entirely, or mislabels a PDF as text/plain, reached
    # the decode below and wrote the same mojibake. Refusing every response
    # with no content-type would be simpler and wrong — plenty of plain-text
    # sources omit it and are perfectly readable — so sniff instead.
    declared = content_type.split(";", 1)[0].strip()
    reason = ""
    if content_type and not _is_texty(content_type):
        reason = f"it served {declared}, not text"
    elif _looks_binary(_response_bytes(response)):
        reason = (
            f"its body is binary (declared {declared or 'nothing'})"
            if not declared
            else f"its body is binary despite being declared {declared}"
        )
    if reason:
        raise UnsupportedSourceError(
            f"tesserae ingest cannot read this URL — {reason}:\n"
            f"  - {url}: Download it and convert it to markdown, then "
            f"`tesserae ingest <file>.md`. RAG-Anything parses PDFs and images, "
            f"but only from local files under the project root, via "
            f"`tesserae refresh raganything`."
        )

    raw = response.text
    # A 200 carrying nothing is a failure that answered politely: a paywall, a
    # JS-only page, or a soft error. Writing it produced a source file holding
    # frontmatter and the sha256 of the empty string
    # (e3b0c442...), which compiles cleanly, reads as nothing, and reports
    # success — the same silent-success shape the binary refusal above removed.
    # Checked on the DECODED body so a whitespace-only one is caught too.
    if not raw.strip():
        raise UnsupportedSourceError(
            "tesserae ingest cannot read this URL — it answered 200 with an "
            "empty body (a paywall, a JS-rendered page, or a soft failure "
            "all look like this):\n"
            f"  - {url}: Open it in a browser, save the rendered text as "
            "markdown, then `tesserae ingest <file>.md`."
        )
    if "html" in content_type:
        body = _html_to_markdown(raw)
    else:
        body = raw

    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    meta = {
        "source_url": url,
        "content_sha256": sha,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if title:
        meta["title"] = title

    arxiv_id = _arxiv_id_from_url(url)
    if arxiv_id:
        meta["arxiv_id"] = arxiv_id

    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{_slugify(url)}.md"
    path.write_text(_render_frontmatter(meta) + "\n" + body.strip() + "\n", encoding="utf-8")
    return path
