/*
 * content.js — dependency-free readable-content extractor for Clip to Tesserae.
 *
 * Responds to {type:'EXTRACT'} messages (from the worker or popup) with:
 *   { url, title, meta:{byline,siteName,publishedTime}, content, selection:boolean }
 *
 * Designed to run both as a persistent content script AND when injected once via
 * chrome.scripting.executeScript — so it must be idempotent and self-contained.
 */
(() => {
  // Avoid double-binding the message listener if injected more than once.
  if (window.__tesseraeClipperLoaded) return;
  window.__tesseraeClipperLoaded = true;

  const MAX_BYTES = 200 * 1024; // ~200KB cap on emitted content.

  const STRIP_SELECTOR = [
    "script", "style", "noscript", "nav", "aside", "footer", "header",
    "iframe", "form", "button", "svg", "canvas", "video", "audio",
    "[role='navigation']", "[role='banner']", "[role='complementary']",
    "[aria-hidden='true']", ".ad", ".ads", ".advert", ".advertisement",
    "[id*='ad-']", "[class*='-ad']", ".cookie", ".cookies", ".newsletter",
    ".share", ".social", ".comments", "#comments", ".sidebar", ".promo"
  ].join(",");

  // ---- metadata --------------------------------------------------------------
  function metaContent(...names) {
    for (const name of names) {
      const el =
        document.querySelector(`meta[property="${name}"]`) ||
        document.querySelector(`meta[name="${name}"]`) ||
        document.querySelector(`meta[itemprop="${name}"]`);
      if (el && el.content && el.content.trim()) return el.content.trim();
    }
    return "";
  }

  function gatherMeta() {
    return {
      byline:
        metaContent("author", "article:author", "og:author", "twitter:creator") ||
        (document.querySelector("[rel='author']")?.textContent || "").trim(),
      siteName:
        metaContent("og:site_name", "application-name") || location.hostname,
      publishedTime: metaContent(
        "article:published_time",
        "datePublished",
        "og:published_time",
        "date"
      )
    };
  }

  // ---- candidate selection ---------------------------------------------------
  function textLength(el) {
    return (el.textContent || "").replace(/\s+/g, " ").trim().length;
  }

  // Find the densest plausible content block when no <article>/<main> exists.
  function densestBlock(root) {
    const candidates = root.querySelectorAll(
      "article, main, section, div, [role='main']"
    );
    let best = null;
    let bestScore = 0;
    candidates.forEach((el) => {
      const len = textLength(el);
      if (len < 200) return;
      const paras = el.querySelectorAll("p").length;
      const links = el.querySelectorAll("a").length;
      // Reward text + paragraphs, penalise link-heavy (nav-like) blocks.
      const score = len + paras * 50 - links * 25;
      if (score > bestScore) {
        bestScore = score;
        best = el;
      }
    });
    return best;
  }

  function pickContentRoot(clone) {
    return (
      clone.querySelector("article") ||
      clone.querySelector("[role='main']") ||
      clone.querySelector("main") ||
      densestBlock(clone) ||
      clone.body ||
      clone
    );
  }

  // ---- HTML -> Markdown ------------------------------------------------------
  function inlineText(node) {
    return convertChildren(node).replace(/\s+/g, " ");
  }

  function convertChildren(node) {
    let out = "";
    node.childNodes.forEach((child) => {
      out += convertNode(child);
    });
    return out;
  }

  function convertNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return node.textContent.replace(/\s+/g, " ");
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return "";

    const tag = node.tagName.toLowerCase();

    switch (tag) {
      case "h1": return `\n\n# ${inlineText(node).trim()}\n\n`;
      case "h2": return `\n\n## ${inlineText(node).trim()}\n\n`;
      case "h3": return `\n\n### ${inlineText(node).trim()}\n\n`;
      case "h4": return `\n\n#### ${inlineText(node).trim()}\n\n`;
      case "h5": return `\n\n##### ${inlineText(node).trim()}\n\n`;
      case "h6": return `\n\n###### ${inlineText(node).trim()}\n\n`;

      case "p":
        return `\n\n${inlineText(node).trim()}\n\n`;

      case "br":
        return "  \n";

      case "hr":
        return "\n\n---\n\n";

      case "strong":
      case "b": {
        const t = inlineText(node).trim();
        return t ? `**${t}**` : "";
      }
      case "em":
      case "i": {
        const t = inlineText(node).trim();
        return t ? `*${t}*` : "";
      }

      case "a": {
        const text = inlineText(node).trim();
        const href = node.getAttribute("href") || "";
        if (!text) return "";
        if (!href || href.startsWith("#") || href.startsWith("javascript:"))
          return text;
        let abs = href;
        try { abs = new URL(href, location.href).href; } catch (_) {}
        return `[${text}](${abs})`;
      }

      case "code": {
        // Inline code unless inside a <pre> (handled below).
        if (node.closest && node.closest("pre")) return node.textContent;
        const t = node.textContent.trim();
        return t ? "`" + t + "`" : "";
      }

      case "pre": {
        const code = node.textContent.replace(/\n+$/, "");
        return `\n\n\`\`\`\n${code}\n\`\`\`\n\n`;
      }

      case "blockquote": {
        const inner = convertChildren(node).trim();
        const quoted = inner
          .split("\n")
          .map((l) => (l.length ? `> ${l}` : ">"))
          .join("\n");
        return `\n\n${quoted}\n\n`;
      }

      case "ul":
      case "ol": {
        const ordered = tag === "ol";
        let i = 1;
        let out = "\n\n";
        node.childNodes.forEach((child) => {
          if (child.nodeType === Node.ELEMENT_NODE &&
              child.tagName.toLowerCase() === "li") {
            const marker = ordered ? `${i++}. ` : "- ";
            const itemText = convertChildren(child)
              .replace(/\n{2,}/g, "\n")
              .trim();
            // Indent wrapped lines under the marker.
            const indented = itemText
              .split("\n")
              .map((l, idx) => (idx === 0 ? l : "  " + l))
              .join("\n");
            out += `${marker}${indented}\n`;
          }
        });
        return out + "\n";
      }

      case "li":
        // Standalone <li> (shouldn't normally hit here).
        return `- ${inlineText(node).trim()}\n`;

      case "img": {
        const alt = node.getAttribute("alt") || "";
        const src = node.getAttribute("src") || "";
        if (!src) return "";
        let abs = src;
        try { abs = new URL(src, location.href).href; } catch (_) {}
        return alt ? `![${alt}](${abs})` : `![](${abs})`;
      }

      case "table":
        // Flatten tables to plain text rows to stay dependency-free.
        return `\n\n${inlineText(node).trim()}\n\n`;

      default:
        return convertChildren(node);
    }
  }

  function htmlToMarkdown(rootEl) {
    let md = convertNode(rootEl);
    // Collapse excess blank lines and trim.
    md = md
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .replace(/^\s+|\s+$/g, "");
    return md;
  }

  function capBytes(str, maxBytes) {
    const enc = new TextEncoder();
    if (enc.encode(str).length <= maxBytes) return str;
    // Binary-trim to fit the byte budget without splitting a code point.
    let lo = 0;
    let hi = str.length;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (enc.encode(str.slice(0, mid)).length <= maxBytes - 20) lo = mid;
      else hi = mid - 1;
    }
    return str.slice(0, lo).trimEnd() + "\n\n… [truncated]";
  }

  // ---- extraction entry points ----------------------------------------------
  function extractSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) return null;
    const container = document.createElement("div");
    for (let i = 0; i < sel.rangeCount; i++) {
      container.appendChild(sel.getRangeAt(i).cloneContents());
    }
    container.querySelectorAll(STRIP_SELECTOR).forEach((n) => n.remove());
    const md = htmlToMarkdown(container);
    return md.trim() || sel.toString().trim();
  }

  function extractArticle() {
    const clone = document.cloneNode(true);
    clone.querySelectorAll(STRIP_SELECTOR).forEach((n) => n.remove());
    const root = pickContentRoot(clone);
    return htmlToMarkdown(root);
  }

  function extract(preferSelection = true) {
    let content = null;
    let selection = false;

    if (preferSelection) {
      const selMd = extractSelection();
      if (selMd) {
        content = selMd;
        selection = true;
      }
    }
    if (content == null) {
      content = extractArticle();
    }

    content = capBytes(content || "", MAX_BYTES);

    return {
      url: location.href,
      title: (document.title || "").trim() || location.href,
      meta: gatherMeta(),
      content,
      selection
    };
  }

  // ---- messaging -------------------------------------------------------------
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "EXTRACT") {
      try {
        const preferSelection =
          msg.mode === "selection-first" ? true : msg.preferSelection !== false;
        sendResponse(extract(preferSelection));
      } catch (err) {
        sendResponse({ error: String(err && err.message ? err.message : err) });
      }
      return true; // synchronous response already sent.
    }
    return false;
  });
})();
