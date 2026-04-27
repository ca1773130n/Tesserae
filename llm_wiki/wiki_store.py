"""Filesystem-backed store for the markdown wiki layer.

The store owns the markdown layer that lives at ``.llm-wiki/wiki/`` — the
Karpathy "Layer 2" between the validated graph and the rendered static site.

Pages are plain markdown files with an optional YAML-style frontmatter block
delimited by ``---`` lines. The frontmatter parser/serializer here is a small
stdlib-only subset (scalars + flat list values) — enough to round-trip the
frontmatter shapes used by ``SynthesisProjector`` and the page templates,
without dragging in a third-party dependency.

Idempotence is content-hashed on the body alone (excluding frontmatter), so
volatile fields like ``generated_at`` do not force unnecessary rewrites.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


_FRONTMATTER_DELIM = "---"


def _canonical_slug(value: str) -> str:
    """Stable, URL-safe slug. Matches ``llm_wiki.frontend.slug`` byte-for-byte.

    Lifted as a private helper here so we do not need to import from
    ``frontend`` (which Subagent A is forbidden to touch). The algorithm is
    identical: lowercase alphanumerics joined by single dashes, with a sha1
    suffix when the slug would exceed 96 UTF-8 bytes, and a sha1 fallback for
    inputs that produce no alphanumeric characters at all (e.g. "한글").
    """

    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    if len(safe.encode("utf-8")) > 96:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        safe = (
            safe.encode("utf-8")[:80].decode("utf-8", errors="ignore").strip("-")
            + "-"
            + digest
        )
    return safe or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _coerce_scalar(raw: str) -> object:
    """Best-effort scalar coercion for a frontmatter value."""

    text = raw.strip()
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    # int
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError:
            pass
    # float
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except ValueError:
            pass
    return text


def _parse_inline_list(raw: str) -> List[object]:
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return [_coerce_scalar(raw)]
    inner = inner[1:-1].strip()
    if not inner:
        return []
    # naive split on commas; values are scalars only (no nested structures).
    parts: List[str] = []
    buf: List[str] = []
    quote: str | None = None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [_coerce_scalar(p) for p in parts if p.strip() != ""]


def _parse_frontmatter(text: str) -> Dict[str, object]:
    """Parse a flat YAML-subset frontmatter block.

    Supports::
        key: scalar
        key: [a, b, c]
        key:
          - a
          - b
    """

    out: Dict[str, object] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value:
            if value.startswith("["):
                out[key] = _parse_inline_list(value)
            else:
                out[key] = _coerce_scalar(value)
            i += 1
            continue
        # Multi-line list?
        items: List[object] = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            stripped = nxt.lstrip()
            if not stripped:
                j += 1
                continue
            if not stripped.startswith("- "):
                break
            items.append(_coerce_scalar(stripped[2:]))
            j += 1
        if items:
            out[key] = items
            i = j
        else:
            out[key] = ""
            i += 1
    return out


def _format_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # Quote when the value would otherwise look like another YAML token,
    # contains leading/trailing whitespace, or contains characters that would
    # confuse our parser.
    needs_quotes = (
        text == ""
        or text.strip() != text
        or text.lower() in {"true", "false", "null", "~"}
        or any(ch in text for ch in (":", "#", "[", "]", "{", "}", ","))
    )
    if needs_quotes:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _format_frontmatter(data: Dict[str, object]) -> str:
    lines: List[str] = []
    for key in sorted(data):
        value = data[key]
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_format_scalar(item)}")
        else:
            lines.append(f"{key}: {_format_scalar(value)}")
    return "\n".join(lines)


def _split_frontmatter(text: str) -> tuple[Dict[str, object], str]:
    """Split a markdown blob into (frontmatter dict, body)."""

    if not text.startswith(_FRONTMATTER_DELIM):
        return {}, text
    # Find the closing delimiter.
    rest = text[len(_FRONTMATTER_DELIM):]
    if rest.startswith("\n"):
        rest = rest[1:]
    elif rest.startswith("\r\n"):
        rest = rest[2:]
    end_match = re.search(r"(^|\n)" + re.escape(_FRONTMATTER_DELIM) + r"(\n|\r\n|$)", rest)
    if not end_match:
        return {}, text
    fm_text = rest[: end_match.start()]
    body = rest[end_match.end():]
    if body.startswith("\n"):
        body = body[1:]
    return _parse_frontmatter(fm_text), body


def _first_h1(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("##"):
            return stripped[2:].strip()
    return None


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WikiPage:
    kind: str
    slug: str
    title: str
    body: str
    path: Path
    frontmatter: Dict[str, object] = field(default_factory=dict)


class WikiPageStore:
    """Filesystem-backed store for wiki markdown pages (one folder per kind)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ paths

    def path_for(self, kind: str, slug: str) -> Path:
        return self.root / kind / f"{slug}.md"

    def slug_for(self, name: str) -> str:
        return _canonical_slug(name)

    # ------------------------------------------------------------------ write

    def write_page(self, page: WikiPage) -> bool:
        """Write ``page`` to disk; return True iff the file changed.

        Idempotence is keyed on the sha256 of the body alone — frontmatter
        churn (e.g. a rewritten ``generated_at`` timestamp) does not force a
        rewrite. This keeps git diffs tight on every recompile.
        """

        target = page.path
        new_hash = _body_hash(page.body)

        if target.exists():
            try:
                existing_text = target.read_text(encoding="utf-8")
            except OSError:
                existing_text = ""
            _, existing_body = _split_frontmatter(existing_text)
            if _body_hash(existing_body) == new_hash:
                return False

        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = self._render(page)
        target.write_text(rendered, encoding="utf-8")
        return True

    @staticmethod
    def _render(page: WikiPage) -> str:
        body = page.body
        if not body.endswith("\n"):
            body = body + "\n"
        if not page.frontmatter:
            return body
        fm = _format_frontmatter(dict(page.frontmatter))
        return f"{_FRONTMATTER_DELIM}\n{fm}\n{_FRONTMATTER_DELIM}\n{body}"

    # ------------------------------------------------------------------- read

    def read_page(self, path: str | Path) -> WikiPage:
        target = Path(path)
        text = target.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(text)
        kind = target.parent.name
        slug = target.stem
        title_value = frontmatter.get("title") if isinstance(frontmatter, dict) else None
        if isinstance(title_value, str) and title_value.strip():
            title = title_value
        else:
            title = _first_h1(body) or slug
        return WikiPage(
            kind=kind,
            slug=slug,
            title=title,
            body=body,
            path=target,
            frontmatter=dict(frontmatter),
        )

    # ------------------------------------------------------------------- list

    def list_pages(self, kind: str) -> List[WikiPage]:
        directory = self.root / kind
        if not directory.exists():
            return []
        pages: List[WikiPage] = []
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if not entry.is_file() or entry.suffix.lower() != ".md":
                continue
            pages.append(self.read_page(entry))
        pages.sort(key=lambda p: p.slug)
        return pages
