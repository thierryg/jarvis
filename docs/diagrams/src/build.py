#!/usr/bin/env python3
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : docs/diagrams/src/build.py
# Purpose : Build the PDFs (network flows, BOM, interconnection, software manual)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Build every Jarvis PDF, in technical US English.

Outputs:

- ``docs/diagrams/jarvis-network-flows.pdf``   (JRV-DWG-001, A4 landscape)
- ``docs/diagrams/jarvis-bom.pdf``             (JRV-BOM-001, A4 landscape)
- ``docs/diagrams/jarvis-interconnection.pdf`` (JRV-DWG-002, A4 landscape)
- ``docs/manuals/jarvis-software-documentation.pdf`` (JRV-TM-001, A4 portrait, from docs/SOFTWARE.md)

Every PDF follows the same technical-manual layout: cover page with the document control block and
the record of revisions, table of contents with page numbers, numbered chapters, DANGER / CAUTION /
NOTE callouts, then the appendices (glossary, bibliography, index with page numbers). Running header
and footer (document number, revision, page "n of N") come from CSS page margin boxes.

The page numbers are resolved with a two-pass render: pass 1 prints the document with invisible
markers in the headings, ``pdftotext`` locates the markers and the index terms page by page, and
pass 2 prints the final document with the numbers filled in. Page numbers occupy fixed-width
boxes, so pass 2 has exactly the same pagination as pass 1.

Dependencies: Graphviz (``dot``), Google Chrome / Chromium (headless PDF printing, CSS page margin
boxes: Chrome 131 or newer), poppler-utils (``pdftotext``) and, for the manual, the ``markdown``
Python package.

Usage: uv run --no-project --with markdown python3 docs/diagrams/src/build.py [diagrams] [manuals]
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, quote_plus

from backmatter import BOOKS, GLOSSARY, INDEX_EXTRA, PAPERS

HERE = Path(__file__).resolve().parent
OUT = HERE.parent                      # docs/diagrams
DOCS_DIR = OUT.parent                  # docs
REPO = DOCS_DIR.parent
MANUALS_DIR = DOCS_DIR / "manuals"
# Software version printed on every cover page: single source jarvis/VERSION.
VERSION = (REPO / "jarvis" / "VERSION").read_text(encoding="utf-8").strip()
AUTHOR = "Thierry Gayet <thierry.gayet@labworks.fr>"
LICENSE_ID = "0BSD"
COPYRIGHT = f"Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: {LICENSE_ID}"
# Record of revisions shared by every document: (revision, ISO date, description).
REVISIONS = [
    ("A", "2026-09-29", "Initial release (French edition)."),
    ("B", "2026-09-30", "US English edition. Content aligned with the current repository (web front end with "
                        "Anubis, MQTT, remote syslog, monitoring, NTP, TLS certificate lifecycle, camera power "
                        "and end-of-life data). Contents with page numbers, glossary, bibliography and index added."),
]
REV, DATE = REVISIONS[-1][0], REVISIONS[-1][1]
FONT = "DejaVu Sans"

# Shared color palette (network zones / hardware families)
C = {
    "inet": "#6b7280", "inet_bg": "#f3f4f6",
    "lan": "#1d4ed8", "lan_bg": "#eef4ff",
    "cam": "#b45309", "cam_bg": "#fff6e8",
    "pc": "#15803d", "pc_bg": "#effaf2",
    "hw": "#6d28d9", "hw_bg": "#f5f0ff",
    "pwr": "#b91c1c", "pwr_bg": "#fff0f0",
    "audio": "#0e7490", "audio_bg": "#ecfbfd",
    "ink": "#111827", "muted": "#4b5563", "rule": "#9ca3af",
}


def e(s: str) -> str:
    """Escape a string for HTML text and attribute values."""
    return html.escape(s, quote=True)


def log(msg: str) -> None:
    """Print a progress message."""
    print(msg, flush=True)


def dot_svg(src: str) -> str:
    """Render Graphviz source to an inline SVG whose size is driven by CSS."""
    svg = subprocess.run(["dot", "-Tsvg"], input=src.encode(), capture_output=True, check=True).stdout.decode()
    svg = svg[svg.index("<svg"):]
    # Size is driven by CSS (page width); aspect ratio is preserved through the viewBox.
    head, rest = svg.split(">", 1)
    head = " ".join(p for p in head.split(" ") if not p.startswith(("width=", "height=")))
    return head + ' preserveAspectRatio="xMidYMid meet">' + rest


def legend(items: list[tuple[str, str]]) -> str:
    """Return a one-line color legend."""
    return '<div class="legend">' + "".join(
        f'<span><i class="sw" style="background:{c}"></i>{e(t)}</span>' for c, t in items) + "</div>"


def table(headers: list[str], rows: list[list[str]], num_cols: tuple[int, ...] = (), cls: str = "") -> str:
    """Return an HTML table; ``num_cols`` are right-aligned, cells are raw HTML."""
    th = "".join(f'<th class="{"num" if i in num_cols else ""}">{h}</th>' for i, h in enumerate(headers))
    trs = "".join("<tr>" + "".join(f'<td class="{"num" if i in num_cols else ""}">{c}</td>'
                                   for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def callout(kind: str, body: str) -> str:
    """Return a DANGER / CAUTION / NOTE callout (ANSI Z535 / MIL-STD-38784 style)."""
    return (f'<div class="callout {kind.lower()}"><div class="cl-head">{kind.upper()}</div>'
            f'<div class="cl-body">{body}</div></div>')


def link(text: str, url: str) -> str:
    """Return a hyperlink whose text is raw HTML."""
    return f'<a href="{e(url)}">{text}</a>'


# =========================================================================================
# Document model and two-pass rendering
# =========================================================================================

@dataclass
class Doc:
    """One PDF document: identification, layout and body HTML.

    The body uses ``<h1>`` for chapters and ``<h2>`` for sections; both appear in the contents.
    """

    ref: str                  # document number, e.g. JRV-DWG-001
    title: str
    subtitle: str
    kicker: str               # document type printed above the title
    short: str                # short title for the running footer (no indexed term in it)
    body: str
    pdf: Path
    topics: set[str]          # bibliography topics
    landscape: bool = True
    number: bool = True       # automatically number chapters and sections


def slug(value: str) -> str:
    """GitHub-compatible anchor: lowercase, punctuation stripped, each space becomes '-'."""
    import unicodedata

    value = unicodedata.normalize("NFC", re.sub(r"<[^>]+>", "", value)).strip().lower()
    value = re.sub(r"[^\w\s-]", "", value)
    return value.replace(" ", "-")


def ensure_ids(body: str) -> str:
    """Give every h1/h2 without an id a unique slug id."""
    seen: set[str] = set(re.findall(r'<h[1-6][^>]*\bid="([^"]+)"', body))

    def add(m: re.Match[str]) -> str:
        tag, attrs, inner = m.group(1), m.group(2), m.group(3)
        if 'id="' in attrs:
            return m.group(0)
        base = slug(inner) or "section"
        hid, n = base, 1
        while hid in seen:
            n += 1
            hid = f"{base}-{n}"
        seen.add(hid)
        return f'<{tag} id="{hid}"{attrs}>{inner}</{tag}>'

    return re.sub(r"<(h[12])((?:\s[^>]*)?)>(.*?)</\1>", add, body, flags=re.S)


def number_headings(body: str, chapters: bool = True) -> str:
    """Number chapters (1, 2...) and sections (1.1...); appendices get letters (Appendix A, A.1...).

    With ``chapters=False`` (pre-numbered Markdown), only the appendices are numbered.
    """
    state = {"ch": 0, "sec": 0, "app": 0, "in_app": False}

    def num(m: re.Match[str]) -> str:
        tag, attrs, inner = m.group(1), m.group(2), m.group(3)
        if "nonum" in attrs:
            return m.group(0)
        if tag == "h1":
            state["sec"] = 0
            if "appendix" in attrs:
                state["in_app"] = True
                label = f"Appendix {chr(ord('A') + state['app'])}"
                state["app"] += 1
                return f'<h1{attrs}><span class="hn">{label}</span>{inner}</h1>'
            state["ch"] += 1
            return f'<h1{attrs}><span class="hn">{state["ch"]}</span>{inner}</h1>' if chapters else m.group(0)
        state["sec"] += 1
        if state["in_app"]:
            return f'<h2{attrs}><span class="hn">{chr(ord("A") + state["app"] - 1)}.{state["sec"]}</span>{inner}</h2>'
        return f'<h2{attrs}><span class="hn">{state["ch"]}.{state["sec"]}</span>{inner}</h2>' if chapters else m.group(0)

    return re.sub(r"<(h[12])((?:\s[^>]*)?)>(.*?)</\1>", num, body, flags=re.S)


def headings(body: str) -> list[tuple[int, str, str]]:
    """Return (level, id, inner HTML) for every h1/h2 of the body."""
    return [(int(m.group(1)), m.group(2), m.group(3))
            for m in re.finditer(r'<h([12])\s[^>]*?id="([^"]+)"[^>]*>(.*?)</h\1>', body, flags=re.S)]


def plain_text(fragment: str) -> str:
    """Strip tags and entities from an HTML fragment and collapse whitespace."""
    text = re.sub(r"<style.*?</style>", " ", fragment, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text))


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """Compile an index/glossary search pattern (rules in backmatter.py).

    ``=word`` forces a case-sensitive match of a lowercase word; a trailing ``*`` marks a stem.
    """
    exact = pattern.startswith("=")
    core = pattern.lstrip("=")
    stem = core.endswith("*")
    core = core.rstrip("*")
    flags = 0 if exact or any(ch.isupper() for ch in core) else re.I
    left = r"(?<![\w])" if core[0].isalnum() else ""
    right = "" if stem or not core[-1].isalnum() else r"(?![\w])"
    return re.compile(left + re.escape(core) + right, flags)


def found(patterns: tuple[str, ...], text: str) -> bool:
    """True when one of the patterns occurs in the text."""
    return any(pattern_regex(p).search(text) for p in patterns)


def sort_key(value: str) -> str:
    """Accent-insensitive, case-insensitive sort key ("Åström" sorts with "A")."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)).lower()


def page_ranges(pages: list[int]) -> str:
    """Format sorted page numbers as '3, 5-7, 12' (en dash for ranges)."""
    out: list[str] = []
    pages = sorted(set(pages))
    i = 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}–{pages[j]}")
        i = j + 1
    return ", ".join(out)


# ---------------------------------------------------------------- back matter
def glossary_html(entries: list[tuple[str, str, tuple[str, ...]]]) -> str:
    """Glossary appendix: the entries used by the document, sorted alphabetically."""
    items = "".join(f"<dt>{e(t)}</dt><dd>{e(d)}</dd>"
                    for t, d, _ in sorted(entries, key=lambda g: sort_key(g[0])))
    return (f'<h1 class="appendix" id="glossary">Glossary</h1>'
            f'<p class="lead">Technical terms and abbreviations used in this document.</p>'
            f'<dl class="gloss">{items}</dl>')


def book_line(authors: str, title: str, details: str, isbn: str) -> str:
    """One bibliography entry in author-title-edition-publisher-year-ISBN order."""
    tail = f" ISBN {isbn}." if isbn else ""
    dot = "" if authors.endswith(".") else "."
    return f"<li>{e(authors)}{dot} <i>{e(title)}</i>. {e(details)}.{tail}</li>"


def bibliography_html(topics: set[str]) -> str:
    """Bibliography appendix: books in English, books in French, then standards and papers."""
    def pick(lang: str) -> str:
        rows = [b for b in BOOKS if b[0] == lang and b[1] & topics]
        return "".join(book_line(*b[2:]) for b in sorted(rows, key=lambda b: sort_key(b[2])))

    papers = "".join(f"<li>{ref}</li>" for t, ref in PAPERS if t & topics)
    return (f'<h1 class="appendix" id="bibliography">Bibliography</h1>'
            f'<p class="lead">Further reading. Titles are given as published; ISBNs are those of the edition '
            f'cited and are omitted when they could not be verified.</p>'
            f'<h2 id="books-in-english">Books in English</h2><ol class="bib">{pick("en")}</ol>'
            f'<h2 id="books-in-french">Books in French</h2><ol class="bib">{pick("fr")}</ol>'
            f'<h2 id="standards-and-papers">Standards, specifications and papers</h2>'
            f'<ol class="bib">{papers}</ol>')


INDEX_SLOT = "<!--index-entries-->"
INDEX_HEAD = ('<h1 class="appendix" id="index">Index</h1><p class="lead">Page numbers refer to the body of the '
              'document (chapters, not appendices).</p>' + INDEX_SLOT)


def index_html(terms: list[tuple[str, tuple[str, ...]]], pages: dict[str, list[int]] | None) -> str:
    """Index entries grouped by initial letter, with page numbers (placeholders in pass 1)."""
    blocks: list[str] = []
    letter = ""
    for term, _ in sorted(terms, key=lambda t: sort_key(t[0])):
        if pages is not None and not pages.get(term):
            continue
        first = term[0].upper() if term[0].isalpha() else "#"
        if first != letter:
            letter = first
            blocks.append(f'<div class="ix-letter">{e(letter)}</div>')
        nums = page_ranges(pages[term]) if pages is not None else "0"
        blocks.append(f'<div class="ix"><span class="ix-t">{e(term)}</span>, {nums}</div>')
    return f'<div class="index">{"".join(blocks)}</div>'


def toc_html(heads: list[tuple[int, str, str]], pages: dict[str, int] | None) -> str:
    """Table of contents with dot leaders and page numbers (placeholders in pass 1)."""
    rows = []
    for level, hid, inner in heads:
        num = str(pages.get(hid, "")) if pages is not None else "0"
        rows.append(f'<a class="toc-l{level}" href="#{hid}"><span class="tt">{inner}</span>'
                    f'<span class="dots"></span><span class="pg">{num}</span></a>')
    return f'<section class="toc"><div class="toc-title">Contents</div>{"".join(rows)}</section>'


# ---------------------------------------------------------------- page furniture
def css_str(s: str) -> str:
    """Quote a string for a CSS ``content`` property."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def base_css(doc: Doc) -> str:
    """Stylesheet shared by all documents (page size depends on the orientation)."""
    size = "A4 landscape" if doc.landscape else "A4"
    margin = "14mm 13mm 14mm 13mm" if doc.landscape else "17mm 16mm 17mm 16mm"
    head_l = css_str("JARVIS · LOCAL SMART GATEKEEPER")
    head_r = css_str(f"{doc.ref} · Rev {REV}")
    foot_l = css_str(f"{doc.ref} · Rev {REV} · Software {VERSION} · {DATE}")
    foot_c = css_str(doc.short)
    box = f"font-family: '{FONT}', sans-serif; font-size: 7pt; color: {C['muted']};"
    return f"""
@page {{ size: {size}; margin: {margin};
  @top-left {{ content: {head_l}; {box} vertical-align: bottom; padding-bottom: 1.5mm;
              border-bottom: 0.6pt solid {C['rule']}; letter-spacing: .06em; }}
  @top-right {{ content: {head_r}; {box} vertical-align: bottom; padding-bottom: 1.5mm;
               border-bottom: 0.6pt solid {C['rule']}; }}
  @bottom-left {{ content: {foot_l}; {box} vertical-align: top; padding-top: 1.5mm;
                 border-top: 0.6pt solid {C['rule']}; }}
  @bottom-center {{ content: {foot_c}; {box} vertical-align: top; padding-top: 1.5mm;
                   border-top: 0.6pt solid {C['rule']}; }}
  @bottom-right {{ content: "Page " counter(page) " of " counter(pages); {box} vertical-align: top;
                  padding-top: 1.5mm; border-top: 0.6pt solid {C['rule']}; font-weight: bold; }}
}}
@page :first {{ @top-left {{ content: none; border: 0; }} @top-right {{ content: none; border: 0; }}
  @bottom-left {{ content: none; border: 0; }} @bottom-center {{ content: none; border: 0; }}
  @bottom-right {{ content: none; border: 0; }} }}
* {{ box-sizing: border-box; }}
body {{ font-family: '{FONT}', sans-serif; color: {C['ink']}; font-size: {'8.6pt' if doc.landscape else '9.2pt'};
       line-height: 1.4; margin: 0; }}
a {{ color: {C['lan']}; text-decoration: none; }}
p {{ margin: 1.4mm 0; }}
ul, ol {{ margin: 1.2mm 0 1.2mm 5mm; padding-left: 3mm; }}
li {{ margin: 0.6mm 0; }}
code {{ font-family: 'DejaVu Sans Mono', monospace; font-size: 0.88em; background: #f2f4f7; padding: 0 1px;
        border-radius: 2px; overflow-wrap: anywhere; }}
pre {{ font-family: 'DejaVu Sans Mono', monospace; font-size: {'7pt' if doc.landscape else '6.1pt'}; line-height: 1.3; background: #f6f7f9;
       border: 0.6px solid #cfd4db; border-left: 2.5px solid {C['muted']}; padding: 2mm 3mm; margin: 1.5mm 0;
       white-space: pre-wrap; overflow-wrap: anywhere; break-inside: avoid; }}
pre code {{ background: none; padding: 0; font-size: 1em; }}
table {{ border-collapse: separate; border-spacing: 0; border-top: 0.6px solid #aeb4bd;
        border-left: 0.6px solid #aeb4bd; width: 100%; font-size: {'7.6pt' if doc.landscape else '8pt'}; margin: 1.5mm 0 3mm; }}
/* Separate borders: collapsed borders make Chrome emit a blank page after a table ending at a page foot. */
th, td {{ border-right: 0.6px solid #aeb4bd; border-bottom: 0.6px solid #aeb4bd; padding: 1.1mm 1.5mm; vertical-align: top; text-align: left; }}
th {{ background: #e5e7eb; font-weight: bold; text-transform: none; }}
tr {{ break-inside: avoid; }}
thead {{ display: table-header-group; }}
td.num, th.num {{ text-align: right; white-space: nowrap; }}
td {{ overflow-wrap: anywhere; }}
td code {{ overflow-wrap: break-word; }}
table.bom td:first-child {{ white-space: nowrap; overflow-wrap: normal; }}
h1 {{ font-size: 14pt; margin: 0 0 3mm; padding: 1.2mm 0 1.2mm; border-top: 2.5px solid {C['ink']};
      border-bottom: 0.8px solid {C['ink']}; text-transform: uppercase; letter-spacing: .03em;
      break-before: page; break-after: avoid; position: relative; }}
h2 {{ font-size: 11pt; margin: 4.5mm 0 1.8mm; break-after: avoid; position: relative;
      border-bottom: 0.6px solid {C['rule']}; padding-bottom: 0.6mm; }}
h3 {{ font-size: 9.8pt; margin: 3.5mm 0 1.2mm; break-after: avoid; }}
h4 {{ font-size: 9.2pt; margin: 3mm 0 1mm; break-after: avoid; font-style: italic; }}
.hn {{ display: inline-block; min-width: 9mm; margin-right: 2.5mm; }}
h1 .hn {{ min-width: 0; margin-right: 4mm; }}
.pm {{ position: absolute; left: 0; top: 0; font-size: 2pt; color: #fff; white-space: nowrap; }}
.lead {{ color: {C['muted']}; margin: 0 0 2.5mm; }}
.sub {{ color: {C['muted']}; margin: 0 0 2mm; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; }}
.grid > div {{ min-width: 0; }}
.legend {{ margin: 1mm 0 2mm; font-size: 7.6pt; }}
.legend span {{ display: inline-block; margin-right: 5mm; }}
.sw {{ display: inline-block; width: 3.2mm; height: 3.2mm; border-radius: 0.6mm; vertical-align: -0.5mm;
       margin-right: 1mm; }}
.fig {{ width: 100%; display: flex; align-items: center; justify-content: center; break-inside: avoid; }}
.fig svg {{ width: 100%; height: 100%; }}
.tag {{ display: inline-block; padding: 0 1.4mm; border-radius: 1mm; font-size: 6.8pt; font-weight: bold;
        color: #fff; white-space: nowrap; }}
.req {{ background: {C['pc']}; }} .opt {{ background: {C['muted']}; }} .rec {{ background: {C['lan']}; }}
.buy {{ margin-top: 0.6mm; font-size: 6.9pt; }}
.buy a {{ border-bottom: 0.4px solid {C['lan']}; }}
.callout {{ border: 1px solid; margin: 2.2mm 0; break-inside: avoid; }}
.callout .cl-head {{ font-weight: bold; letter-spacing: .12em; font-size: 8pt; padding: 0.8mm 2.5mm; color: #fff; }}
.callout .cl-body {{ padding: 1.6mm 3mm; }}
.callout .cl-body p {{ margin: 0.8mm 0; }}
.callout.danger {{ border-color: {C['pwr']}; background: {C['pwr_bg']}; }}
.callout.danger .cl-head {{ background: {C['pwr']}; }}
.callout.caution {{ border-color: #d97706; background: #fffbeb; }}
.callout.caution .cl-head {{ background: #f59e0b; color: #111827; }}
.callout.note {{ border-color: {C['lan']}; background: {C['lan_bg']}; }}
.callout.note .cl-head {{ background: {C['lan']}; }}
/* Cover */
.cover {{ height: {'178mm' if doc.landscape else '258mm'}; display: flex; flex-direction: column;
          break-after: page; }}
.cover .band {{ background: {C['ink']}; color: #fff; padding: 2.5mm 4mm; display: flex; justify-content: space-between;
               font-size: 8.5pt; letter-spacing: .12em; font-weight: bold; }}
.cover .main {{ flex: 1; display: grid; grid-template-columns: {'1.15fr 1fr' if doc.landscape else '1fr'};
               gap: 8mm; padding: {'8mm 2mm 0' if doc.landscape else '16mm 2mm 0'}; align-content: start; }}
.cover .kicker {{ color: {C['pc']}; font-weight: bold; letter-spacing: .14em; text-transform: uppercase;
                 font-size: 10pt; }}
.cover h1 {{ font-size: {'28pt' if doc.landscape else '30pt'}; margin: 3mm 0 3mm; border: 0; padding: 0;
            line-height: 1.08; text-transform: none; letter-spacing: 0; break-before: auto; }}
.cover .subtitle {{ font-size: 12pt; color: {C['muted']}; max-width: 150mm; }}
.cover .hwline {{ margin-top: 6mm; font-size: 8.6pt; color: {C['muted']}; }}
.cover table {{ font-size: 7.8pt; }}
.cover th {{ width: 34%; }}
.cover .rev th {{ width: auto; }}
.cover .rev td:nth-child(2) {{ white-space: nowrap; }}
.cover .blk-title {{ font-weight: bold; letter-spacing: .1em; font-size: 8pt; margin: 0 0 1mm;
                    text-transform: uppercase; }}
.cover .foot {{ border-top: 1.5px solid {C['ink']}; padding-top: 2mm; font-size: 7.6pt; color: {C['muted']};
               display: flex; justify-content: space-between; }}
/* Contents */
.toc {{ break-after: page; }}
.toc-title {{ font-size: 14pt; font-weight: bold; text-transform: uppercase; letter-spacing: .03em;
             border-top: 2.5px solid {C['ink']}; border-bottom: 0.8px solid {C['ink']}; padding: 1.2mm 0;
             margin-bottom: 3mm; }}
.toc a {{ display: flex; align-items: baseline; color: {C['ink']}; break-inside: avoid; }}
.toc .tt {{ flex: 0 1 auto; }}
.toc .tt .hn {{ min-width: 11mm; margin-right: 1mm; }}
.toc .dots {{ flex: 1 1 auto; border-bottom: 0.8px dotted {C['muted']}; margin: 0 1.5mm; min-width: 6mm;
             transform: translateY(-0.8mm); }}
.toc .pg {{ flex: 0 0 9mm; text-align: right; font-variant-numeric: tabular-nums; }}
.toc-l1 {{ font-weight: bold; margin-top: 1.8mm; font-size: 9pt; }}
.toc-l2 {{ padding-left: 11mm; font-size: 8.2pt; margin-top: 0.4mm; color: {C['muted']}; }}
.toc-l2 .tt .hn {{ min-width: 9mm; }}
.toc-l1 .tt .hn {{ min-width: 0; margin-right: 2.5mm; }}
.toc code {{ background: none; }}
/* Back matter */
dl.gloss {{ columns: {2 if doc.landscape else 1}; column-gap: 8mm; margin: 0; font-size: 8pt; }}
dl.gloss dt {{ font-weight: bold; break-after: avoid; margin-top: 1.4mm; }}
dl.gloss dd {{ margin: 0 0 0 4mm; break-inside: avoid; }}
ol.bib {{ font-size: 8pt; margin-left: 4mm; }}
ol.bib li {{ margin: 0.9mm 0; break-inside: avoid; }}
.index {{ columns: {3 if doc.landscape else 2}; column-gap: 8mm; font-size: 8pt; }}
.ix-letter {{ font-weight: bold; font-size: 10pt; margin: 2.2mm 0 0.6mm; border-bottom: 0.6px solid {C['rule']};
             break-after: avoid; }}
.ix {{ padding-left: 3mm; text-indent: -3mm; break-inside: avoid; }}
.ix-t {{ font-weight: 600; }}
"""


def cover_html(doc: Doc) -> str:
    """Cover page: title block, document control block and record of revisions."""
    rows = [("Document number", f"<b>{e(doc.ref)}</b>"), ("Revision", f"<b>{REV}</b>"),
            ("Software version", f"Jarvis {e(VERSION)} (jarvis/VERSION)"), ("Date of issue", DATE),
            ("Author", e(AUTHOR)), ("License", f"{LICENSE_ID} (Zero-Clause BSD)"), ("Language", "US English")]
    control = '<table class="kv">' + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows) + "</table>"
    revs = table(["Rev", "Date", "Description"], [[r, d, e(t)] for r, d, t in REVISIONS], cls="rev")
    return f"""<section class="cover">
<div class="band"><span>JARVIS · LOCAL SMART GATEKEEPER</span><span>{e(doc.ref)} · REV {REV}</span></div>
<div class="main">
  <div><div class="kicker">{e(doc.kicker)}</div><h1 class="cover-title">{e(doc.title)}</h1>
    <div class="subtitle">{doc.subtitle}</div>
    <div class="hwline">Reference installation: Lenovo ThinkCentre M73 Tiny · Ubuntu Server 26.04 LTS ·
    ONVIF PTZ camera · Novoferm Novomatic 200 garage door operator. 100 % on-premises processing.</div></div>
  <div><div class="blk-title">Document control</div>{control}
    <div class="blk-title" style="margin-top:4mm">Record of revisions</div>{revs}</div>
</div>
<div class="foot"><span>Prepared by {e(AUTHOR)}</span><span>{e(COPYRIGHT)}</span></div>
</section>"""


# ---------------------------------------------------------------- rendering
def chrome() -> str:
    """Path of the headless browser used to print the PDFs."""
    exe = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not exe:
        raise SystemExit("Chrome/Chromium not found (needed to print the PDFs)")
    return exe


def print_pdf(html_doc: str, pdf: Path) -> None:
    """Print an HTML document to PDF with headless Chrome."""
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        src = Path(tmp) / "doc.html"
        src.write_text(html_doc, encoding="utf-8")
        subprocess.run([chrome(), "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                        "--print-to-pdf-no-header", f"--print-to-pdf={pdf}", src.as_uri()],
                       check=True, capture_output=True)


def pdf_pages_text(pdf: Path) -> list[str]:
    """Text of each PDF page (pdftotext, pages separated by form feeds)."""
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, check=True).stdout
    pages = out.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def assemble(doc: Doc, body: str, toc: str) -> str:
    """Complete HTML document: cover, contents, body and appendices."""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(doc.title)} ({doc.ref})</title>
<meta name="author" content="{e(AUTHOR)}"><style>{base_css(doc)}</style></head><body>
{cover_html(doc)}{toc}<main class="doc">{body}</main></body></html>"""


def render_document(doc: Doc) -> None:
    """Two-pass render of a document with page-numbered contents and index."""
    body = ensure_ids(doc.body)
    text = plain_text(body)
    gloss = [g for g in GLOSSARY if found(g[2], text)]
    terms = [(t, p) for t, _, p in gloss] + [(t, p) for t, p in INDEX_EXTRA if found(p, text)]
    full = number_headings(body + glossary_html(gloss) + bibliography_html(doc.topics) + INDEX_HEAD,
                           chapters=doc.number)
    heads = headings(full)
    ids = [h[1] for h in heads]

    # Pass 1: invisible markers in the headings, placeholder page numbers.
    marked = full.replace(INDEX_SLOT, index_html(terms, None))
    for n, hid in enumerate(ids):
        marked = re.sub(rf'(<h[12]\s[^>]*?id="{re.escape(hid)}"[^>]*>)', rf'\1<span class="pm">@@M{n:04d}@@</span>',
                        marked, count=1)
    with tempfile.TemporaryDirectory() as tmp:
        pass1 = Path(tmp) / "pass1.pdf"
        print_pdf(assemble(doc, marked, toc_html(heads, None)), pass1)
        pages = pdf_pages_text(pass1)

    head_page: dict[str, int] = {}
    for n, hid in enumerate(ids):
        for i, txt in enumerate(pages):
            if f"@@M{n:04d}@@" in txt:
                head_page[hid] = i + 1
                break
        else:
            log(f"  warning: heading '{hid}' not located in {doc.pdf.name}")
    first_body = head_page.get(ids[0], 3)
    back_start = head_page.get("glossary", len(pages) + 1)
    furniture = [f"{doc.ref} · Rev {REV} · Software {VERSION} · {DATE}", doc.short, f"{doc.ref} · Rev {REV}",
                 "JARVIS · LOCAL SMART GATEKEEPER"]
    index_pages: dict[str, list[int]] = {}
    for i in range(first_body - 1, back_start - 1):
        txt = re.sub(r"@@M\d{4}@@", " ", pages[i])
        for f in furniture:
            txt = txt.replace(f, " ")
        txt = re.sub(r"Page \d+ of \d+", " ", re.sub(r"\s+", " ", txt))
        for term, pats in terms:
            if found(pats, txt):
                index_pages.setdefault(term, []).append(i + 1)

    # Pass 2: final document.
    final = assemble(doc, full.replace(INDEX_SLOT, index_html(terms, index_pages)), toc_html(heads, head_page))
    print_pdf(final, doc.pdf)
    (HERE / f"{doc.pdf.stem}.html").write_text(final, encoding="utf-8")  # human-readable source, versioned
    check_pages(doc, heads, head_page)
    n_pages = len(pdf_pages_text(doc.pdf))
    log(f"wrote {doc.pdf.relative_to(REPO)} ({n_pages} pages, {len(heads)} headings, "
        f"{len(gloss)} glossary entries, {len(index_pages)} index entries)")


def check_pages(doc: Doc, heads: list[tuple[int, str, str]], head_page: dict[str, int]) -> None:
    """Verify on the final PDF that each heading is printed on the page given in the contents."""
    pages = [re.sub(r"\s+", " ", p) for p in pdf_pages_text(doc.pdf)]
    for _, hid, inner in heads:
        page = head_page.get(hid)
        title = plain_text(re.sub(r'<span class="hn">.*?</span>', "", inner)).strip()[:40]
        if page and title and title.lower() not in pages[page - 1].lower():
            log(f"  warning: '{title}' not found on page {page} of {doc.pdf.name}")


# =========================================================================================
# 1. Network flows (JRV-DWG-001)
# =========================================================================================

NET_DOT = f"""
digraph net {{
  graph [rankdir=TB, fontname="{FONT}", fontsize=12, nodesep=0.25, ranksep=0.32, pad=0.1, newrank=true,
         splines=spline, compound=true];
  node  [fontname="{FONT}", fontsize=11, shape=box, style="rounded,filled", fillcolor=white, color="{C['ink']}",
         penwidth=1.1, margin="0.12,0.05"];
  edge  [fontname="{FONT}", fontsize=9.5, penwidth=1.2, arrowsize=0.65];

  subgraph cluster_inet {{
    label=<<b>Internet </b>· installation, updates, options>; style="rounded,dashed";
    color="{C['inet']}"; bgcolor="{C['inet_bg']}"; fontcolor="{C['inet']}";
    remote [label=<<b>Phone away from home</b><br/><font point-size="9">WireGuard / Tailscale client (option)</font>>, color="{C['inet']}", style="rounded,dashed,filled"];
    repos  [label=<<b>Software and model repositories</b><br/><font point-size="9">Ubuntu · uv · PyPI · PyTorch CPU<br/>GitHub · Hugging Face · alphacephei</font>>, color="{C['inet']}"];
    ntpsrv [label=<<b>NTP servers</b><br/><font point-size="9">clock synchronization</font>>, color="{C['inet']}"];
    acme   [label=<<b>Let's Encrypt ACME API</b><br/><font point-size="9">certificate issuance (option)</font>>, color="{C['inet']}", style="rounded,dashed,filled"];
    dnsapi [label=<<b>DNS provider API</b><br/><font point-size="9">_acme-challenge TXT record (DNS-01)</font>>, color="{C['inet']}", style="rounded,dashed,filled"];
  }}

  box [label=<<b>Internet router </b>192.168.1.1<br/><font point-size="9">NAT · no port forwarding (except UDP 51820 for the VPN)</font>>,
       color="{C['lan']}", fillcolor="{C['lan_bg']}"];

  subgraph cluster_lan {{
    label=<<b>Home LAN </b>192.168.1.0/24>; style=rounded; color="{C['lan']}"; bgcolor="{C['lan_bg']}"; fontcolor="{C['lan']}";
    browser [label=<<b>Browser</b><br/><font point-size="9">household PC / smartphone<br/>https://jarvis.local</font>>, color="{C['lan']}"];
    admin   [label=<<b>Administration workstation</b><br/><font point-size="9">SSH with key · Ansible</font>>, color="{C['lan']}"];
    sw      [label=<<b>Switch / router ports</b>>, color="{C['lan']}", penwidth=1.6];
    ha      [label=<<b>Smart home (option)</b><br/><font point-size="9">Home Assistant · Node-RED · ntfy<br/>MQTT broker · webhooks</font>>, color="{C['lan']}", style="rounded,dashed,filled"];
    mon     [label=<<b>Monitoring server (option)</b><br/><font point-size="9">Prometheus :9090 · Loki :3100<br/>Grafana :3000</font>>, color="{C['lan']}", style="rounded,dashed,filled"];
    slog    [label=<<b>syslog server (option)</b><br/><font point-size="9">rsyslog / syslog-ng</font>>, color="{C['lan']}", style="rounded,dashed,filled"];
  }}

  subgraph cluster_pc {{
    label=<<b>Mini-PC Lenovo ThinkCentre M73 Tiny </b>· Ubuntu Server 26.04 LTS>; style=rounded; color="{C['pc']}";
    bgcolor="{C['pc_bg']}"; fontcolor="{C['pc']}"; penwidth=1.8;
    eth0 [label=<<b>eth0 </b>· RJ45 Intel I217-V<br/><font point-size="9">192.168.1.10 (DHCP reservation)</font>>, fillcolor="{C['lan_bg']}", color="{C['lan']}"];
    fw   [label=<<b>nftables</b><br/><font point-size="9">input: default drop<br/>forward: drop (no routing)</font>>, shape=octagon, style=filled, fillcolor="#fde8e8", color="{C['pwr']}"];
    sshd [label=<<b>sshd </b>:22>];
    wg   [label=<<b>WireGuard </b>wg0<br/><font point-size="9">UDP 51820 (option)</font>>, style="rounded,dashed,filled"];
    web  [label=<<b>nginx → Anubis → jarvis-api</b><br/><font point-size="9">:80 / :443 · 127.0.0.1:8923 · 127.0.0.1:8000<br/>(details: chapter 3)</font>>, penwidth=1.6];
    agents [label=<<b>Agents (option)</b><br/><font point-size="9">node_exporter :9100 · Alloy / Promtail</font>>, style="rounded,dashed,filled"];
    tsync [label=<<b>systemd-timesyncd</b><br/><font point-size="9">NTP client</font>>];
    cert [label=<<b>jarvis-cert </b>(certbot)<br/><font point-size="9">renewal timer, twice a day</font>>];
    core [label=<<b>jarvis-core</b><br/><font point-size="9">vision · voice · decision · hardware<br/>MQTT bridge · webhooks · syslog</font>>, penwidth=2];
    eth1 [label=<<b>eth1 </b>· USB 3.0 → GbE<br/><font point-size="9">192.168.50.1/24, no gateway</font>>, fillcolor="{C['cam_bg']}", color="{C['cam']}"];
  }}

  subgraph cluster_cam {{
    label=<<b>Isolated camera network </b>192.168.50.0/24<br/>no gateway, no Internet>; style=rounded; color="{C['cam']}";
    bgcolor="{C['cam_bg']}"; fontcolor="{C['cam']}";
    poe [label=<<b>PoE injector</b><br/><font point-size="9">802.3bt / Hi-PoE 60 W<br/>(or camera DC/AC supply)</font>>, color="{C['cam']}"];
    cam [label=<<b>PTZ camera </b>192.168.50.64<br/><font point-size="9">RTSP :554 · ONVIF (HTTP) :80<br/>dedicated ONVIF account<br/>built-in auto-tracking off</font>>, color="{C['cam']}", penwidth=2];
  }}

  {{rank=same; remote; repos; ntpsrv; acme; dnsapi;}}
  {{rank=same; browser; admin; sw; ha; mon; slog;}}
  {{rank=same; sshd; wg; fw; agents;}}
  {{rank=same; tsync; cert; web; core; eth1; poe; cam;}}

  // --- Internet
  remote -> box [label="F7 WireGuard", style=dashed, color="{C['inet']}", fontcolor="{C['inet']}"];
  repos -> box [label="F11 HTTPS", style=dashed, color="{C['inet']}", fontcolor="{C['inet']}", dir=back];
  ntpsrv -> box [label="F21 NTP", color="{C['inet']}", fontcolor="{C['inet']}", dir=back];
  acme -> box [label="F22 HTTPS", style=dashed, color="{C['inet']}", fontcolor="{C['inet']}", dir=back];
  dnsapi -> box [label="F23 HTTPS", style=dashed, color="{C['inet']}", fontcolor="{C['inet']}", dir=back];

  // --- LAN
  box -> sw [dir=both, color="{C['lan']}", penwidth=1.6];
  browser -> sw [label="F4 · F5", color="{C['lan']}", fontcolor="{C['lan']}"];
  admin -> sw [label="F6 SSH", color="{C['lan']}", fontcolor="{C['lan']}"];
  sw -> ha [headlabel="F12 · F16", labeldistance=3.2, labelangle=25, color="{C['lan']}", fontcolor="{C['lan']}", style=dashed];
  sw -> mon [headlabel="F18 to F20", labeldistance=3.2, labelangle=25, color="{C['lan']}", fontcolor="{C['lan']}", style=dashed, dir=both];
  sw -> slog [label="F17", color="{C['lan']}", fontcolor="{C['lan']}", style=dashed];
  sw -> eth0 [dir=both, color="{C['lan']}", penwidth=1.8];

  // --- Inside the mini-PC
  eth0 -> fw [dir=both, color="{C['lan']}", penwidth=1.6];
  fw -> sshd [label="TCP 22", color="{C['lan']}", fontcolor="{C['lan']}"];
  fw -> wg [label="UDP 51820", color="{C['inet']}", fontcolor="{C['inet']}", style=dashed];
  fw -> agents [label="TCP 9100", color="{C['lan']}", fontcolor="{C['lan']}", style=dashed];
  fw -> web [label="TCP 80/443", color="{C['lan']}", fontcolor="{C['lan']}", penwidth=1.6];
  fw -> tsync [color="{C['pc']}", dir=back];
  fw -> cert [color="{C['pc']}", style=dashed, dir=back];
  fw -> core [label="MQTT · webhook · syslog", color="{C['pc']}", fontcolor="{C['pc']}", style=dashed, dir=back];
  web -> core [label="F8 · F9", color="{C['pc']}", fontcolor="{C['pc']}", dir=both];
  core -> eth1 [label="F1 · F2", color="{C['cam']}", fontcolor="{C['cam']}", penwidth=2.2];

  // --- Camera
  eth1 -> poe [color="{C['cam']}", penwidth=2.2, label="patch", fontcolor="{C['cam']}"];
  poe -> cam [label="Cat6 F/UTP + PoE", color="{C['cam']}", fontcolor="{C['cam']}", penwidth=2.2];
}}
"""

WEB_DOT = f"""
digraph web {{
  graph [rankdir=LR, fontname="{FONT}", fontsize=12, nodesep=0.3, ranksep=0.38, pad=0.1, newrank=true, splines=spline];
  node  [fontname="{FONT}", fontsize=10.5, shape=box, style="rounded,filled", fillcolor=white, color="{C['ink']}",
         penwidth=1.1, margin="0.12,0.05"];
  edge  [fontname="{FONT}", fontsize=9, penwidth=1.2, arrowsize=0.65];

  client [label=<<b>Browser (LAN / VPN)</b><br/><font point-size="8.5">Prometheus scrape (F19)</font>>, color="{C['lan']}", fillcolor="{C['lan_bg']}"];
  subgraph cluster_pc {{
    label=<<b>Mini-PC </b>· loopback services>; style=rounded; color="{C['pc']}"; bgcolor="{C['pc_bg']}"; fontcolor="{C['pc']}";
    nginx  [label=<<b>nginx </b>:80 / :443<br/><font point-size="8.5">TLS 1.2/1.3 · HSTS · CSP<br/>bot user agents → 403 · robots.txt<br/>limit_req on /api/login</font>>];
    anubis [label=<<b>Anubis </b>anubis@jarvis<br/><font point-size="8.5">127.0.0.1:8923 · proof-of-work<br/>metrics 127.0.0.1:9091</font>>];
    api    [label=<<b>jarvis-api </b>(FastAPI)<br/><font point-size="8.5">127.0.0.1:8000 · PrivateDevices</font>>, penwidth=1.6];
    sock   [label=<<font point-size="9"><b>F8 · /run/jarvis/core.sock</b><br/>Unix socket, JSON lines</font>>, shape=cds, style=filled, fillcolor="#f1f5f9"];
    frame  [label=<<font point-size="9"><b>F9 · /run/jarvis/frame.jpg</b><br/>tmpfs, served as MJPEG</font>>, shape=note, style=filled, fillcolor="#f1f5f9"];
    db     [label=<<font point-size="9"><b>F10 · SQLite WAL</b><br/>/var/lib/jarvis/jarvis.db</font>>, shape=cylinder, style=filled, fillcolor="#f1f5f9"];
    core   [label=<<b>jarvis-core</b><br/><font point-size="8.5">vision · voice · decision · hardware</font>>, penwidth=2];
    certs  [label=<<font point-size="9"><b>/etc/jarvis/tls/</b><br/>jarvis.crt · jarvis.key</font>>, shape=note, style=filled, fillcolor="#f1f5f9"];
    jcert  [label=<<b>jarvis-cert</b><br/><font point-size="8.5">local CA or Let's Encrypt</font>>];
  }}
  out [label=<<b>Outbound (eth0)</b><br/><font point-size="8.5">F12 webhook · F16 MQTT<br/>F17 syslog · F18 Loki</font>>, color="{C['lan']}", fillcolor="{C['lan_bg']}", style="rounded,dashed,filled"];
  camn [label=<<b>PTZ camera </b>(eth1)<br/><font point-size="8.5">F1 RTSP · F2 ONVIF</font>>, color="{C['cam']}", fillcolor="{C['cam_bg']}"];

  client -> nginx [label="F4 HTTPS", color="{C['lan']}", fontcolor="{C['lan']}", penwidth=1.6];
  nginx -> anubis [label="F13 upstream jarvis_front", color="{C['pc']}", fontcolor="{C['pc']}"];
  anubis -> api [label="F14", color="{C['pc']}", fontcolor="{C['pc']}"];
  nginx -> api [label="F15 bypass: /api/stream.mjpg, /metrics", color="{C['pc']}", fontcolor="{C['pc']}", style=dashed];
  api -> sock [color="{C['pc']}"];
  sock -> core [color="{C['pc']}"];
  core -> frame [color="{C['pc']}"];
  frame -> api [color="{C['pc']}", constraint=false];
  core -> db [dir=both, color="{C['pc']}"];
  api -> db [dir=both, color="{C['pc']}", constraint=false];
  jcert -> certs [color="{C['pc']}"];
  certs -> nginx [color="{C['pc']}", style=dashed, constraint=false];
  core -> out [color="{C['lan']}", style=dashed];
  core -> camn [color="{C['cam']}", penwidth=2];
}}
"""

# (number, source, destination, protocol / port, direction, content, indicative rate)
FLOWS = [
    ["F1", "jarvis-core (eth1)", "camera 192.168.50.64", "RTSP over TCP 554", "outbound, long session",
     "Secondary H.264 stream 1280×720, 10 to 15 fps. Digest authentication.", "≈ 1 to 2 Mbit/s in"],
    ["F2", "jarvis-core (eth1)", "camera 192.168.50.64", "ONVIF SOAP / HTTP TCP 80", "outbound, on demand",
     "PTZ ContinuousMove / Stop / GotoPreset, at most 4 commands/s, WS-Security.", "&lt; 20 kbit/s"],
    ["F3", "camera", "mini-PC 192.168.50.1", "NTP UDP 123", "inbound (option)",
     "Camera time without Internet access; needs an NTP server (e.g. chrony) on eth1, not deployed by default.",
     "negligible"],
    ["F4", "browser (LAN / VPN)", "nginx :443", "HTTPS, TLS 1.2/1.3", "inbound",
     "Web UI, REST API, MJPEG stream of the Live tab, photo and voice uploads (16 MB max.).",
     "≈ 2 to 5 Mbit/s per open Live tab"],
    ["F5", "browser (LAN)", "nginx :80", "HTTP", "inbound",
     "301 redirect to HTTPS; ACME HTTP-01 files under /.well-known/acme-challenge/ only.", "negligible"],
    ["F6", "admin workstation", "sshd :22", "SSH", "inbound",
     "Administration and Ansible. Key authentication, admin networks only, 15 new connections/min per source.",
     "—"],
    ["F7", "remote phone", "router → mini-PC :51820", "WireGuard UDP 51820", "inbound (option)",
     "Remote access. The only port that may be forwarded. With Tailscale: no forwarding at all.", "as F4"],
    ["F8", "jarvis-api", "jarvis-core", "Unix socket /run/jarvis/core.sock", "local",
     "One JSON line per request, closed command list: status, reload_*, enroll_*, embed_face, garage_pulse, "
     "ptz_home, reload_settings, system, restart, say.", "—"],
    ["F9", "jarvis-core", "jarvis-api", "file /run/jarvis/frame.jpg (tmpfs)", "local",
     "Annotated preview, replaced atomically. The RTSP stream is decoded only once.", "5 fps"],
    ["F10", "jarvis-core and jarvis-api", "SQLite WAL", "file /var/lib/jarvis/jarvis.db", "local",
     "Persons, embeddings, unknown faces, sightings, log, accounts, sessions, settings.", "—"],
    ["F11", "mini-PC (eth0)", "Internet", "HTTPS TCP 443 (+ DNS 53)", "outbound, installation",
     "apt, uv, PyPI, PyTorch CPU, models (GitHub, Hugging Face, alphacephei), security updates.",
     "≈ 3 GB once"],
    ["F12", "jarvis-core (eth0)", "Home Assistant / Node-RED / ntfy", "HTTP(S) POST JSON", "outbound (option)",
     "Notifications (unknown visitor, watchlist, out-of-schedule access, voice denied, garage pulse), "
     "<code>notifications.webhook_url</code>.", "a few KB per event"],
    ["F13", "nginx", "Anubis 127.0.0.1:8923", "HTTP, loopback", "local",
     "Upstream <code>jarvis_front</code>: every request except the bypass paths (F15). Without Anubis, the "
     "upstream points to the API directly.", "as F4"],
    ["F14", "Anubis", "jarvis-api 127.0.0.1:8000", "HTTP, loopback", "local",
     "Requests that passed the bot policy (browser clients solve the proof-of-work once, cookie).", "as F4"],
    ["F15", "nginx", "jarvis-api 127.0.0.1:8000", "HTTP, loopback", "local",
     "Upstream <code>jarvis_api</code>: <code>/api/stream.mjpg</code> (no buffering, session checked by the "
     "API) and <code>/metrics</code> (bearer token and source address checked by the API).", "as F4"],
    ["F16", "jarvis-core", "MQTT broker (LAN)", "MQTT TCP 1883, 8883 with TLS", "outbound (option)",
     "Retained status/state topics, event topics, Home Assistant discovery; inbound commands (garage_pulse, "
     "ptz_home, say) on the same connection only with <code>mqtt.allow_commands</code> and the command token.",
     "a few KB per event"],
    ["F17", "jarvis-core, jarvis-api", "rsyslog / syslog-ng server", "syslog RFC 3164, UDP or TCP 514",
     "outbound (option)", "Copy of the application log (<code>logging.syslog_*</code>).", "low"],
    ["F18", "Alloy / Promtail agent", "Loki :3100", "HTTP push /loki/api/v1/push", "outbound (option)",
     "<code>/var/log/jarvis/*.log</code> and the systemd journal (<code>monitoring.loki_url</code>).", "low"],
    ["F19", "Prometheus (monitoring server)", "nginx :443 /metrics", "HTTPS, bearer token", "inbound (option)",
     "Jarvis metrics (vision, camera, sightings, sessions, audit, disk), every 30 s.", "low"],
    ["F20", "Prometheus (monitoring server)", "node_exporter :9100", "HTTP", "inbound (option)",
     "Host metrics; the firewall accepts TCP 9100 from the configured monitoring server only.", "low"],
    ["F21", "systemd-timesyncd", "NTP servers", "NTP UDP 123", "outbound",
     "Clock synchronization (certificates, audit timestamps).", "negligible"],
    ["F22", "jarvis-cert (certbot)", "Let's Encrypt ACME API", "HTTPS TCP 443", "outbound (option)",
     "Issuance and renewal of the certificate in <code>letsencrypt</code> mode (renewal check twice a day).",
     "negligible"],
    ["F23", "certbot DNS plugin", "DNS provider API", "HTTPS TCP 443", "outbound (option)",
     "DNS-01 challenge: TXT record <code>_acme-challenge</code>. No inbound port is opened on the Internet.",
     "negligible"],
    ["F24", "LAN devices", "mini-PC UDP 5353", "mDNS (Avahi)", "inbound",
     "Name resolution of <code>&lt;hostname&gt;.local</code>, LAN only (option <code>jarvis_mdns</code>).",
     "negligible"],
]

NFT = """table inet jarvis_filter {
  chain input {
    type filter hook input priority filter; policy drop;
    iif "lo" accept
    ct state invalid counter drop
    ct state established,related accept
    # ICMP / ICMPv6 essentials, echo rate-limited (10/s); DHCP / DHCPv6 client replies
    ip saddr @lan_v4 udp dport 5353 accept                          # F24 mDNS (jarvis_mdns)
    ip saddr @admin_v4 tcp dport 22 ct state new \\
       add @ssh_meter_v4 { ip saddr limit rate 15/minute } accept   # F6
    ip saddr @lan_v4 tcp dport { 80, 443 } accept                   # F4, F5
    ip saddr <monitoring server> tcp dport 9100 accept              # F20 (option)
    # jarvis_firewall_extra_rules, e.g. F3 or F7:
    #   iifname "eth1" ip saddr 192.168.50.64 udp dport 123 accept
    #   udp dport 51820 accept
    limit rate 6/minute log prefix "jarvis-fw-drop: " level info
    counter drop comment "jarvis dropped"
  }
  chain forward { type filter hook forward priority filter; policy drop; }   # no routing
  chain output  { type filter hook output priority filter; policy accept; }  # F1, F2, F11, F12, F16 to F18, F21 to F23
}"""


def net_body() -> str:
    """Body of the network flows document."""
    zones = legend([(C['lan'], "home LAN"), (C['cam'], "isolated camera network"), (C['pc'], "inside the mini-PC"),
                    (C['inet'], "Internet / option (dashed)"), (C['pwr'], "filtering")])
    return f"""
<h1 id="scope">Scope</h1>
<p>This document describes every network flow of the Jarvis smart gatekeeper: the network zones, the flow matrix,
the web request path through nginx and Anubis, the host firewall and the configuration of the camera and of the
mini-PC. All processing (vision, voice, decision) runs on the mini-PC: in operation, no flow goes to a cloud
service. Internet access is only needed for the installation, the updates and the optional services marked as
such.</p>
{callout("NOTE", "The addresses are examples. Report the actual values in <code>/etc/jarvis/config.yaml</code> "
         "(<code>camera.rtsp_url</code>, <code>ptz.host</code>) and in the Ansible inventory "
         "(<code>jarvis_lan_networks</code>, <code>jarvis_admin_networks</code>, "
         "<code>jarvis_monitoring_server</code>).")}
{callout("CAUTION", "Never forward port 443 (or 80) from the Internet router to Jarvis. Remote access goes through "
         "a VPN only (F7). The Let's Encrypt DNS-01 challenge (F23) needs no inbound port; the HTTP-01 "
         "challenge would require port 80 reachable from the Internet.")}
<h2 id="flow-numbering">Flow numbering</h2>
<p>Flows F1 to F12 keep the numbers of the previous revision. F13 to F24 cover the web front end (nginx, Anubis),
the smart-home bridge (MQTT), the log and metrics exports (syslog, Loki, Prometheus, node_exporter), the time
synchronization (NTP) and the TLS certificate lifecycle (ACME, DNS-01).</p>
{table(["Group", "Flows"], [
        ["Camera (eth1)", "F1 RTSP, F2 ONVIF, F3 NTP (option)"],
        ["Users and administration", "F4 HTTPS, F5 HTTP, F6 SSH, F7 WireGuard (option), F24 mDNS"],
        ["Inside the mini-PC", "F8 control socket, F9 preview file, F10 database, F13 to F15 web front end"],
        ["Integrations (LAN)", "F12 webhooks, F16 MQTT, F17 syslog, F18 Loki, F19 metrics, F20 node_exporter"],
        ["Internet", "F11 installation and updates, F21 NTP, F22 ACME, F23 DNS-01"],
    ])}

<h1 id="network-overview">Network overview</h1>
{zones}
<div class="fig" style="height:150mm">{dot_svg(NET_DOT)}</div>

<h1 id="web-request-path">Web request path and local channels</h1>
<p class="sub">nginx terminates TLS and filters bots; Anubis (proof-of-work) protects the login page and the UI;
the API only listens on the loopback interface and reaches the hardware through the closed command set of the
control socket.</p>
<div class="fig" style="height:108mm">{dot_svg(WEB_DOT)}</div>
<pre>client ─▶ nginx :443 (TLS, headers, bot UA 403, robots.txt, limit_req on /api/login)
            ├─▶ upstream jarvis_front ─▶ Anubis 127.0.0.1:8923 ─▶ API 127.0.0.1:8000 ─▶ /run/jarvis/core.sock ─▶ jarvis-core
            └─▶ /api/stream.mjpg, /metrics ─▶ upstream jarvis_api ─▶ API 127.0.0.1:8000</pre>

<h1 id="flow-matrix">Flow matrix</h1>
{table(["No.", "Source", "Destination", "Protocol / port", "Direction", "Content", "Indicative rate"], FLOWS)}
{callout("NOTE", "<b>Why a second physical network?</b> The Tiny has a single RJ45 port. A USB 3.0 to Gigabit "
         "adapter (Realtek RTL8153, <code>r8152</code> kernel driver, or ASIX AX88179) gives the camera a dedicated "
         "segment with no gateway and no DNS: the camera can neither reach the Internet nor be reached from the "
         "LAN, and the RTSP stream does not load the home Wi-Fi. <b>Alternative</b>: a managed switch with a camera "
         "VLAN (802.1Q) and an <code>eth0.50</code> interface on the mini-PC.")}

<h1 id="host-firewall">Host firewall (nftables)</h1>
<h2 id="deployed-ruleset">Deployed ruleset</h2>
<pre>{e(NFT)}</pre>
<p class="sub">Simplified from <code>deploy/ansible/roles/firewall/templates/nftables.conf.j2</code> (IPv6 rules
omitted). The ruleset is validated with <code>nft -c</code> before it replaces <code>/etc/nftables.conf</code>; only
the <code>jarvis_filter</code> table is replaced, fail2ban keeps its own table.</p>
<div class="grid">
  <div>
    <h2 id="firewall-notes">Notes</h2>
    <ul>
      <li>Input policy <b>default-deny</b>; SSH only from <code>jarvis_admin_networks</code>, HTTP/HTTPS only from
      <code>jarvis_lan_networks</code>, node_exporter only from <code>jarvis_monitoring_server</code>.</li>
      <li>Forwarding is dropped (<code>ip_forward = 0</code>): no routing between the LAN and the camera
      network.</li>
      <li>Output is accepted: camera RTSP/ONVIF, apt, NTP, MQTT, webhooks, syslog, Loki, ACME.</li>
    </ul>
  </div>
  <div>
    <ul style="margin-top:9mm">
      <li>fail2ban (nftables actions) adds the <code>sshd</code>, <code>jarvis-login</code>,
      <code>nginx-botsearch</code> and <code>recidive</code> jails.</li>
      <li>Anubis (127.0.0.1:8923, metrics 127.0.0.1:9091) and the API (127.0.0.1:8000) listen on the loopback
      interface only: no firewall rule exposes them.</li>
    </ul>
    {callout("CAUTION", "The camera NTP flow (F3) and the WireGuard port (F7) are not accepted by the deployed "
             "ruleset. Add them through <code>jarvis_firewall_extra_rules</code> only when they are used.")}
  </div>
</div>

<h1 id="configuration">Camera and host configuration</h1>
<div class="grid">
  <div>
    <h2 id="camera-side">Camera side</h2>
    <ul>
      <li>Fixed IP <b>192.168.50.64/24</b>, <b>no gateway</b>, empty DNS; NTP server 192.168.50.1 when F3 is used.</li>
      <li>Disable UPnP, vendor P2P / cloud services, Telnet/SSH and unused ports.</li>
      <li>Create a dedicated <b>ONVIF</b> user (PTZ and stream rights), distinct from the admin account.</li>
      <li>Secondary stream <b>1280×720 H.264</b>, 10 to 15 fps, constant bit rate 1 to 2 Mbit/s.</li>
      <li>Preset 1 = view of the entrance. Built-in auto-tracking <b>disabled</b>. Mask the public road.</li>
    </ul>
  </div>
  <div>
    <h2 id="host-and-lan-side">Mini-PC and LAN side</h2>
    <ul>
      <li>DHCP reservation (or static IP) for <b>eth0</b>; name <code>jarvis.local</code> through Avahi/mDNS.</li>
      <li><b>eth1</b> with the static address 192.168.50.1/24 (netplan), no gateway.</li>
      <li>nginx listens on 80/443; Anubis and FastAPI stay on the loopback interface, never exposed.</li>
      <li>Certificate: <code>jarvis-cert</code> in <code>local</code> mode (private name-constrained CA, imported
      once on the clients) or <code>letsencrypt</code> mode (DNS-01 by default, F22 and F23).</li>
      <li>Docker variant: nginx publishes 80/443, the API stays on the internal Docker network, the core and the
      API share the <code>jarvis-data</code> and <code>jarvis-run</code> volumes.</li>
      <li>After the installation, F11 can be blocked: operation is fully offline (F21 remains advisable).</li>
    </ul>
  </div>
</div>
"""


# =========================================================================================
# 2. BOM (bill of materials, JRV-BOM-001)
# =========================================================================================
# (ref, qty, item, example / specification, role, min price EUR, max price EUR, status)
REQ, REC, OPT = "req", "rec", "opt"
BOM = {
    "Compute": [
        ("PC-1", 1, "Mini-PC Lenovo ThinkCentre M73 Tiny",
         "Machine types 10AX/10AY/10DK-10DN. <b>Quad-core AVX2 CPU: i5-4590T, i5-4460T, i7-4785T or i7-4765T.</b> "
         "The i5-4570T has only 2 cores: usable with <code>detector.imgsz</code> 416. "
         "Celeron / Pentium excluded (no AVX2).",
         "Vision, voice, decision, web UI", 80, 140, REQ),
        ("PC-2", 1, "Memory 2 × 4 GB DDR3 SO-DIMM 204-pin PC3-12800",
         "8 GB recommended, 16 GB max. Dual channel = 2 identical modules.",
         "Models loaded in RAM (≈ 2.5 GB)", 0, 20, REQ),
        ("PC-3", 1, "2.5\" SATA SSD, 240 to 500 GB", "Replaces an original 5400 rpm hard disk if present.",
         "System, models (≈ 1 GB), photos, log", 0, 45, REQ),
        ("PC-4", 1, "Lenovo 65 W (20 V) power adapter", "Normally supplied. Round or rectangular plug depending on "
         "the model.", "Mini-PC power", 0, 25, REQ),
    ],
    "Video": [
        ("CAM-1", 1, "Outdoor ONVIF Profile S PTZ dome camera",
         "See chapter 3. Requirements: PTZ ContinuousMove / Stop / GotoPreset, secondary H.264 stream, "
         "IR or starlight, IP66.", "Video acquisition and tracking", 900, 4000, REQ),
        ("CAM-2", 1, "PoE injector 802.3bt / Hi-PoE 60 W, or the camera's DC/AC supply",
         "The PTZ cameras draw up to 51 W: <b>PoE+ 802.3at (30 W) is not enough</b>. Axis ships a High PoE 60 W "
         "midspan; Hikvision: 802.3bt or 24 V AC; Dahua: Hi-PoE or 36 V DC. Check the camera datasheet.",
         "Power + data on one cable", 0, 90, REQ),
        ("CAM-3", 1, "Wall / corner mount for PTZ dome", "Camera manufacturer's reference.", "Mounting", 30, 120, REQ),
    ],
    "Network": [
        ("NET-1", 1, "USB 3.0 to Gigabit Ethernet adapter", "Realtek RTL8153 (r8152 driver) or ASIX AX88179.",
         "Second interface dedicated to the camera network", 12, 25, REC),
        ("NET-2", 1, "Outdoor Cat6 F/UTP cable, UV-resistant PE jacket", "Length per installation (100 m max.).",
         "PoE link to the camera", 20, 60, REQ),
        ("NET-3", 4, "Shielded Cat6 RJ45 connectors + boots", "To crimp, or use a pre-terminated cable.",
         "Terminations", 5, 12, REQ),
        ("NET-4", 1, "Cat6 patch cable 0.5 m", "USB-GbE adapter to PoE injector.", "Short link", 3, 6, REQ),
        ("NET-5", 1, "PoE Ethernet surge protector (RJ45, earthed)", "802.3bt compatible.",
         "Lightning protection of the outdoor cable", 20, 45, OPT),
    ],
    "Audio": [
        ("AUD-1", 1, "USB microphone array Seeed ReSpeaker USB Mic Array v2.0 (XMOS XVF-3000)",
         "4 microphones, echo cancellation, 3.5 mm jack output, direction of arrival (future use). "
         "Alternative: directional USB microphone.", "Wake word, voice commands, speaker", 60, 90, REC),
        ("AUD-2", 1, "Class-D mini amplifier 2 × 3 W, 5 V (PAM8403 type)",
         "USB 5 V powered, input on the jack output of AUD-1.", "TTS amplification", 4, 10, REC),
        ("AUD-3", 1, "Weatherproof outdoor loudspeaker 4 to 8 Ω, 3 to 5 W", "IP65 or inside a protected enclosure.",
         "Greetings and spoken answers", 12, 30, REC),
        ("AUD-4", 1, "Active USB 2.0 extension 5 to 10 m", "If the microphone is more than 3 m from the mini-PC "
         "(beyond 5 m, a passive cable is no longer reliable).", "Microphone near the entrance", 12, 25, OPT),
        ("AUD-5", 1, "Weatherproof enclosure with acoustic grille", "For AUD-1 to AUD-3 outdoors.",
         "Rain protection", 10, 25, OPT),
    ],
    "Control and inputs": [
        ("IO-1", 1, "4-channel CH340 USB relay board \"LCUS-4\"",
         "USB 1a86:7523 → /dev/jarvis-relay. 10 A contacts. Protocol A0 channel state checksum.",
         "Garage (CH1), green LED (CH2), red LED (CH3)", 10, 18, REQ),
        ("IO-2", 1, "FTDI FT232RL USB-to-serial adapter, TTL levels (3.3/5 V), pin headers",
         "USB 0403:6001 → /dev/jarvis-door. CTS# input with a <b>weak internal pull-up (about 200 kΩ)</b>: add "
         "IO-9. <b>Not an RS-232 DB9 cable</b> (see JRV-DWG-002).", "Door sensor input", 5, 12, REC),
        ("IO-3", 1, "Wired magnetic reed contact for garage door, NO",
         "\"Wide gap\" model for sectional doors (floor or surface mounted).",
         "Open/closed state: prevents \"open\" from closing the door", 10, 25, REC),
        ("IO-4", 1, "2-pair alarm cable 0.22 mm² (10 to 20 m)", "Reed → FT232RL. Twisted pair, away from 230 V.",
         "Sensor link", 5, 12, REC),
        ("IO-5", 1, "2-conductor low-voltage cable, 2 × AWG 22 or larger",
         "Relay CH1 → terminal F (external pulse input) of the Novomatic 200; AWG 22 per the Novoferm manual "
         "(fig. 13a).", "Garage pulse command", 3, 8, REQ),
        ("IO-6", 2, "12 V LED indicators Ø 16/22 mm IP65 (1 green, 1 red)", "Panel indicators with built-in LED.",
         "GO / NO_GO signal visible from outside", 5, 12, REQ),
        ("IO-7", 1, "12 V DC 1 A power supply", "Enclosed, CE-certified adapter.", "Powers the LEDs via CH2/CH3",
         8, 15, REQ),
        ("IO-8", 1, "IP55 junction box + lever terminal blocks (Wago 221 type)", "For the low-voltage connections.",
         "Protection of the connections", 8, 20, REQ),
        ("IO-9", 1, "External pull-up resistor, a few kΩ, CTS# to VCCIO",
         "Recommended in <code>docs/hardware/ftdi-ft232rl/</code> for a long sensor cable (with filtering).",
         "Reliable door sensor level", 0, 1, REC),
    ],
    "Power and miscellaneous": [
        ("PWR-1", 1, "UPS 600 to 850 VA", "Keeps the mini-PC and the PoE injector running through short outages.",
         "Availability", 60, 110, OPT),
        ("PWR-2", 1, "Surge-protected power strip", "If there is no UPS.", "Protection", 10, 20, OPT),
        ("MISC-1", 1, "VESA mount or wall bracket for the Tiny", "Lenovo 75/100 mm VESA kit or shelf.",
         "Clean installation, ventilation", 0, 20, OPT),
        ("MISC-2", 1, "DisplayPort/VGA monitor + USB keyboard (borrowed)", "Only to install the system.",
         "Installation", 0, 0, OPT),
    ],
}


def amazon(q: str) -> tuple[str, str]:
    """Prefilled Amazon search link."""
    return "Amazon", "https://www.amazon.fr/s?k=" + quote_plus(q)


def aliexpress(q: str) -> tuple[str, str]:
    """Prefilled AliExpress search link."""
    return "AliExpress", "https://www.aliexpress.com/w/wholesale-" + quote(q.replace(" ", "-")) + ".html"


def ldlc(q: str) -> tuple[str, str]:
    """Prefilled LDLC search link."""
    return "LDLC", "https://www.ldlc.com/recherche/" + quote(q) + "/"


# Purchase links (prefilled searches, no hard-coded vendor listing) and official product pages.
LINKS: dict[str, list[tuple[str, str]]] = {
    "PC-1": [("Back Market", "https://www.backmarket.fr/fr-fr/search?q=" + quote("thinkcentre m73 tiny")),
             amazon("Lenovo ThinkCentre M73 Tiny i5-4590T"),
             ("Lenovo PSREF sheet", "https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M73_Tiny/"
                                    "ThinkCentre_M73_Tiny_Spec.PDF")],
    "PC-2": [amazon("SO-DIMM DDR3 PC3-12800 2x4GB"), ldlc("so-dimm ddr3 1600")],
    "PC-3": [amazon("SSD SATA 2.5 500GB"), ldlc("ssd sata 2.5")],
    "PC-4": [amazon("Lenovo 65W 20V ThinkCentre Tiny adapter")],
    "CAM-1": [("Axis Q6078-E", "https://www.axis.com/products/axis-q6078-e"),
              ("Axis Q6075-E (EOL)", "https://www.axis.com/products/axis-q6075-e"),
              ("Hikvision", "https://www.hikvision.com/en/products/IP-Products/PTZ-Cameras/Ultra-Series/"
                            "ds-2df8c842ixs-ael-t5-/"),
              ("Dahua", "https://www.dahuasecurity.com/products/PTZ-Cameras/WizMind-Series/SD6A65F/4MP/SD6AL445XA-HNR")],
    "CAM-2": [amazon("PoE injector 802.3bt 60W"), aliexpress("hi-poe injector 60w")],
    "CAM-3": [amazon("PTZ dome camera wall mount")],
    "NET-1": [amazon("USB 3.0 gigabit ethernet adapter RTL8153"), ldlc("adaptateur usb ethernet rtl8153")],
    "NET-2": [amazon("outdoor Cat6 FTP cable 30m")],
    "NET-3": [amazon("shielded RJ45 connector Cat6 FTP")],
    "NET-4": [amazon("Cat6 patch cable 0.5m")],
    "NET-5": [amazon("PoE ethernet surge protector RJ45")],
    "AUD-1": [("Seeed Studio", "https://www.seeedstudio.com/ReSpeaker-Mic-Array-v2-0.html"),
              ("Seeed wiki", "https://wiki.seeedstudio.com/ReSpeaker_Mic_Array_v2.0/"),
              amazon("ReSpeaker USB Mic Array v2.0")],
    "AUD-2": [amazon("PAM8403 amplifier module 5V"), aliexpress("pam8403 amplifier"),
              ("datasheet", "https://www.diodes.com/assets/Datasheets/PAM8403.pdf")],
    "AUD-3": [amazon("outdoor waterproof speaker 8 ohm 5W")],
    "AUD-4": [amazon("active USB 2.0 extension 10m")],
    "AUD-5": [amazon("IP65 waterproof electronics enclosure")],
    "IO-1": [aliexpress("lcus-4 usb relay"), amazon("4 channel USB relay module CH340")],
    "IO-2": [amazon("FT232RL USB TTL adapter"), ("Mouser (chip)", "https://www.mouser.com/c/?q=FT232RL")],
    "IO-3": [amazon("wired magnetic contact garage door")],
    "IO-4": [amazon("alarm cable 4 core 0.22mm2")],
    "IO-5": [amazon("2 core cable 22 AWG")],
    "IO-6": [amazon("LED indicator 12V 22mm IP65")],
    "IO-7": [amazon("12V 1A power adapter")],
    "IO-8": [amazon("IP55 junction box"), amazon("Wago 221")],
    "PWR-1": [amazon("UPS 850VA"), ldlc("onduleur")],
    "PWR-2": [amazon("surge protected power strip")],
    "MISC-1": [amazon("VESA mount Lenovo ThinkCentre Tiny")],
}


def links_html(items: list[tuple[str, str]]) -> str:
    """Purchase / product links under a BOM line."""
    return '<div class="buy">' + " · ".join(f'<a href="{e(u)}">{e(t)}</a>' for t, u in items) + "</div>"


EOL = '<span class="tag opt">END OF LIFE</span>'
NOT_OK = '<span class="tag" style="background:#b91c1c">NOT SUITABLE</span>'
CAMERAS = [
    [link("<b>Axis Q6078-E</b>", "https://www.axis.com/products/axis-q6078-e"),
     "4K 2160p (3840×2160)", "ONVIF + VAPIX API",
     "High PoE 60 W midspan supplied; 16 W typical, 51 W max. RJ45 10/100 only.",
     "Most faithful ONVIF implementation, the most expensive.", "≈ 2,500 to 4,000 €"],
    [link("<b>Axis Q6075-E</b>", "https://www.axis.com/products/axis-q6075-e") + "<br/>" + EOL,
     "HDTV 1080p, 40× optical zoom", "ONVIF Profiles G, S, T + VAPIX",
     "IEEE 802.3bt, max. 51 W; High PoE 60 W midspan supplied. PoE+ 802.3at not enough.",
     "<b>End of life</b>: last order date May 10, 2026, service/RMA until May 10, 2032. Official replacement "
     "per Axis: <b>AXIS Q6086-E</b>.", "≈ 2,500 to 4,000 €"],
    [link("<b>Hikvision DS-2DF8C842IXS-AEL(T5)</b>",
          "https://www.hikvision.com/en/products/IP-Products/PTZ-Cameras/Ultra-Series/ds-2df8c842ixs-ael-t5-/"),
     "8 MP (3840×2160), 42× zoom, IR 500 m", "secondary stream /Streaming/Channels/102",
     "24 V AC (62 W max.) or IEEE 802.3bt PoE (51 W max.)", "Good value for money; IP67, about 9.6 kg.",
     "≈ 1,200 to 1,900 €"],
    [link("<b>Dahua SD6AL445XA-HNR</b>",
          "https://www.dahuasecurity.com/products/PTZ-Cameras/WizMind-Series/SD6A65F/4MP/SD6AL445XA-HNR"),
     "4 MP (2560×1440), 45× zoom, starlight + laser", "secondary stream subtype=1",
     "36 V DC / 2.23 A or Hi-PoE (36 W max.); no IEEE 802.3at/bt claim in the datasheet",
     "Equivalent to Hikvision.", "≈ 900 to 1,500 €"],
    ["<b>Reolink Argus PT</b> (owner's camera)<br/>" + NOT_OK,
     "5 MP, fixed lens 90°, no optical zoom", "no RTSP / ONVIF standalone",
     "21.6 Wh battery + 3 W solar panel, Wi-Fi; no PoE",
     "<b>Not suitable without a Reolink Home Hub</b>: through the Hub, RTSP sessions last 5 min at most and PTZ "
     "is not usable by Jarvis (<code>ptz.enabled: false</code>, fixed-camera mode).", "already owned"],
]

GH = "https://github.com/"
SOFTWARE = [
    [link("Ubuntu Server 26.04 LTS", "https://ubuntu.com/download/server"), "operating system",
     "free (GPL and others)", "reference platform; 24.04 accepted only with <code>jarvis_allow_ubuntu_2404</code>"],
    [link("Python 3.11", "https://www.python.org/downloads/") + " (via " + link("uv", "https://docs.astral.sh/uv/") + ")",
     "runtime", "PSF", "3.12 not usable (tflite-runtime, piper-phonemize wheels)"],
    [link("OpenVINO", GH + "openvinotoolkit/openvino"), "YOLO inference on CPU", "Apache-2.0", "—"],
    [link("Ultralytics YOLO11n", GH + "ultralytics/ultralytics") + " + ByteTrack", "person detection and tracking",
     link("<b>AGPL-3.0</b>", "https://www.ultralytics.com/license"),
     "personal use OK; distribution or commercial service = Ultralytics license"],
    [link("InsightFace", GH + "deepinsight/insightface") + " buffalo_s (SCRFD + ArcFace) on "
     + link("ONNX Runtime", GH + "microsoft/onnxruntime"), "faces",
     "code MIT, ONNX Runtime MIT; <b>models: non-commercial research</b>", "non-commercial household use"],
    [link("openWakeWord", GH + "dscripka/openWakeWord") + " \"hey jarvis\"", "wake word",
     "code Apache-2.0; models CC BY-NC-SA 4.0", "non-commercial"],
    [link("Porcupine", GH + "Picovoice/porcupine") + " (option \"jarvis\")", "wake word",
     "proprietary, free key for personal use", "Picovoice account"],
    [link("Vosk", GH + "alphacep/vosk-api") + " + " + link("vosk-model-small-fr-0.22", "https://alphacephei.com/vosk/models"),
     "speech recognition (French commands)", "Apache-2.0", "—"],
    [link("Piper", GH + "OHF-Voice/piper1-gpl") + " + voice "
     + link("fr_FR-siwis-medium", "https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/siwis/medium"),
     "speech synthesis (French voice)", "<b>GPL-3.0</b> (piper-tts ≥ 1.3); voice: see its model card",
     "personal use OK"],
    [link("SpeechBrain ECAPA-TDNN", "https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb") + " (option)",
     "speaker verification", "Apache-2.0", "installs CPU torch (≈ 700 MB)"],
    [link("FastAPI", "https://fastapi.tiangolo.com/") + ", Uvicorn, argon2-cffi, " + link("nginx", "https://nginx.org/")
     + ", " + link("SQLite", "https://sqlite.org/"), "web API, TLS, storage", "MIT / BSD / public domain", "—"],
    [link("Anubis", GH + "TecharoHQ/anubis"), "anti-bot proof-of-work proxy", "MIT",
     "pinned version and checksum (Ansible <code>anubis</code> role)"],
]

SBOM_CSV = DOCS_DIR / "sbom" / "jarvis-sbom.csv"
PERMISSIVE = "permissive (MIT, BSD, Apache, PSF...)"


def license_family(lic: str) -> str:
    """Classify a declared license into a license family."""
    low = lic.lower()
    if any(k in low for k in ("non-commercial", "-nc-", "nc-sa", "research only")):
        return "non-commercial"
    if "agpl" in low or ("gpl" in low and "lgpl" not in low):
        return "strong copyleft (GPL/AGPL)"
    if "lgpl" in low or "mpl" in low:
        return "weak copyleft (LGPL/MPL)"
    if "non déclarée" in low or "undeclared" in low or not low.strip():
        return "undeclared"
    return PERMISSIVE


def sbom_section() -> str:
    """SBOM summary chapter, from docs/sbom/jarvis-sbom.csv."""
    import csv
    from collections import Counter

    if not SBOM_CSV.exists():
        return '<h1 id="sbom">Software bill of materials (SBOM)</h1><p>SBOM missing: run <code>scripts/jarvis-gen-sbom.py</code>.</p>'
    rows = list(csv.DictReader(SBOM_CSV.open(encoding="utf-8")))

    def col(r: dict[str, str], *names: str) -> str:
        for n in names:
            if n in r:
                return r[n] or ""
        return ""

    lic = [col(r, "license", "licence") for r in rows]
    types = Counter(col(r, "type") for r in rows)
    fams = Counter(license_family(x) for x in lic)
    watch = [r for r, x in zip(rows, lic) if license_family(x) != PERMISSIVE]
    type_rows = [[e(t), str(n)] for t, n in types.most_common()]
    fam_rows = [[e(f), str(n)] for f, n in fams.most_common()]
    watch_rows = [[e(col(r, "name", "nom")), e(col(r, "version")), e(col(r, "type")),
                   e(col(r, "license", "licence") or "undeclared"),
                   e(license_family(col(r, "license", "licence")))] for r in watch]
    return f"""
<h1 id="sbom">Software bill of materials (SBOM)</h1>
<p class="sub">Generated by <code>scripts/jarvis-gen-sbom.py</code> from the Python environment actually installed
(<code>cyclonedx-py</code>), completed with the AI models (SHA-256 of each file) and the system components.
Files: <code>docs/sbom/jarvis-sbom.cdx.json</code> (machine format, validated against the CycloneDX 1.6 schema,
importable into Dependency-Track or readable by Grype/Trivy for vulnerability scanning) and
<code>docs/sbom/jarvis-sbom.csv</code> (human-readable). <b>{len(rows)} components.</b></p>
<div class="grid">
  <div><h2 id="sbom-by-type">Components by type</h2>{table(["CycloneDX type", "Count"], type_rows, num_cols=(1,))}</div>
  <div><h2 id="sbom-by-license">Components by license family</h2>{table(["Family", "Count"], fam_rows, num_cols=(1,))}</div>
</div>
<h2 id="licenses-to-watch">Licenses to watch (everything that is not permissive)</h2>
{table(["Component", "Version", "Type", "Declared license", "Family"], watch_rows)}
{callout("NOTE", "The delivered SBOM was produced on the test environment. Regenerate it on the installed machine "
         "(<code>sudo python3 scripts/jarvis-gen-sbom.py</code>) to reflect the exact OS, FFmpeg and nginx in place, and "
         "after every dependency update.")}"""


def fmt_eur(lo: float, hi: float) -> str:
    """Format an indicative price range in euros."""
    if hi == 0:
        return "existing"
    if lo == hi:
        return f"{lo:,.0f} €"
    return f"{lo:,.0f} to {hi:,.0f} €"


def bom_body() -> str:
    """Body of the bill of materials document."""
    labels = {REQ: '<span class="tag req">REQUIRED</span>', REC: '<span class="tag rec">RECOMMENDED</span>',
              OPT: '<span class="tag opt">OPTIONAL</span>'}
    totals = {k: [0.0, 0.0] for k in (REQ, REC, OPT)}
    sections = []
    for name, items in BOM.items():
        rows = []
        for ref, qty, des, spec, role, lo, hi, st in items:
            totals[st][0] += lo
            totals[st][1] += hi
            rows.append([f"<b>{ref}</b>", str(qty), f"<b>{des}</b><br/>{spec}{links_html(LINKS.get(ref, []))}", role,
                         labels[st], fmt_eur(lo, hi)])
        sections.append(f'<h2 id="parts-{slug(name)}">{e(name)}</h2>'
                        + table(["Ref.", "Qty", "Item / specification", "Role", "Status", "Indicative price"],
                                rows, num_cols=(1, 5), cls="bom"))
    cam_lo, cam_hi = BOM["Video"][0][5], BOM["Video"][0][6]
    req_wo = (totals[REQ][0] - cam_lo, totals[REQ][1] - cam_hi)
    summary = table(["Item", "Indicative amount"], [
        ["Required, camera excluded", fmt_eur(*req_wo)],
        ["PTZ camera (CAM-1)", fmt_eur(cam_lo, cam_hi)],
        ["Recommended (dedicated network, audio, door sensor)", fmt_eur(*totals[REC])],
        ["Options (UPS, surge protection, USB extension...)", fmt_eur(*totals[OPT])],
        ["<b>Total recommended configuration (required + recommended)</b>",
         f"<b>{fmt_eur(totals[REQ][0] + totals[REC][0], totals[REQ][1] + totals[REC][1])}</b>"],
    ], num_cols=(1,))
    return f"""
<h1 id="scope">Scope and safety summary</h1>
<p>Reference configuration: Lenovo ThinkCentre M73 <b>Tiny</b> (1-liter form factor, no PCIe slot and no DB9 port
as standard), 100 % CPU inference, USB relay board, ONVIF PTZ camera on an isolated network. Prices are
<b>indicative</b> (September 2026, new or refurbished for the PC) and must be checked with the supplier.
"existing" = already supplied or already present. The <span style="color:{C['lan']}">blue</span> links are
clickable: prefilled searches at retailers (no vendor imposed) and official product pages.</p>
<table><thead><tr><th>Status</th><th>Meaning</th></tr></thead><tbody>
<tr><td>{labels[REQ]}</td><td>Needed for the base function (vision, face recognition, garage pulse).</td></tr>
<tr><td>{labels[REC]}</td><td>Strongly advised: dedicated camera network, voice, door state.</td></tr>
<tr><td>{labels[OPT]}</td><td>Comfort, availability or protection.</td></tr></tbody></table>
{callout("DANGER", "Jarvis never switches 230 V. The relay only closes the low-voltage dry contact of terminal F of "
         "the Novomatic 200. Cut the power of the door operator before any wiring (see JRV-DWG-002).")}
{callout("CAUTION", "PoE budget: the PTZ cameras draw up to 51 W (Axis, Hikvision) or 36 W (Dahua). A PoE+ "
         "802.3at switch or injector (30 W) is not enough: use an 802.3bt / Hi-PoE 60 W injector or the camera's "
         "own 24 V AC / 36 V DC supply, per its datasheet.")}

<h1 id="parts-list">Parts list</h1>
{"".join(sections)}

<h1 id="camera-selection">Camera selection (CAM-1)</h1>
{table(["Model", "Sensor / zoom", "Integration", "Power", "Remarks", "Indicative price"], CAMERAS)}
{callout("NOTE", "4K is not needed: the analysis runs on the 720p secondary stream and the optical zoom compensates. "
         "Check the exact resolution and the power class of the purchased model on the manufacturer's datasheet. "
         "The documents and key points of each camera are in <code>docs/hardware/</code>.")}
{callout("CAUTION", "Reolink Argus PT: battery-powered Wi-Fi camera with no RTSP, RTMP or ONVIF when used "
         "standalone. It is usable only as a degraded, experimental source through a Reolink Home Hub (extra "
         "purchase), with <code>ptz.enabled: false</code>, and suffers regular blind gaps (5-minute sessions, "
         "wake-up of up to about 20 s). See <code>docs/hardware/reolink-argus-pt/README.md</code>.")}

<h1 id="costs-and-attention-points">Cost summary and points of attention</h1>
<div class="grid">
  <div><h2 id="cost-summary">Cost summary</h2>{summary}
    {callout("CAUTION", "Ultralytics (AGPL-3.0), InsightFace and openWakeWord (non-commercial models) suit "
             "<b>private</b> use. Resale or a commercial service requires other models or commercial licenses.")}
    <h2 id="not-counted">Not counted</h2>
    <p>Existing equipment: <b><a href="https://www.novoferm.fr/">Novoferm Novomatic 200</a></b> door operator with
    its photocells and remote control, LAN router / switch, installation monitor and keyboard. Labor and small
    supplies (wall plugs, conduits, cable ties) are not included.</p>
  </div>
  <div><h2 id="hardware-attention-points">Hardware points of attention</h2>
  <ul>
    <li><b>CPU</b>: check with <code>lscpu</code> (4 cores and the <code>avx2</code> flag). The budget of 6 analyses
    per second assumes 4 cores; with an i5-4570T (2 cores), aim at 4 analyses per second and
    <code>imgsz: 416</code>.</li>
    <li><b>No GPU card possible</b> on the Tiny (no PCIe); the mini-PCIe slot holds the Wi-Fi card. The architecture
    stays 100 % CPU, with VAAPI H.264 decoding.</li>
    <li><b>USB ports</b>: 2 × USB 3.0 at the front (one always powered), 3 × USB 2.0 at the back. The BOM uses 4 of
    them (see the port allocation in JRV-DWG-002).</li>
    <li><b>Door sensor</b>: the FT232R internal pull-up (about 200 kΩ) is weak; fit the external pull-up IO-9.</li>
    <li><b>Novomatic terminal F</b>: its voltage is not documented by Novoferm; measure it before wiring.</li>
    <li><b>Ventilation</b>: the Tiny gets hot under continuous load. Do not enclose it, dust it, watch
    <code>sensors</code>; install it indoors (garage), not outdoors.</li>
  </ul></div>
</div>

<h1 id="software-and-models">Software and models</h1>
<p class="sub">No license cost for household use.</p>
{table(["Component", "Role", "License", "Remarks"], SOFTWARE)}
{sbom_section()}
"""


# =========================================================================================
# 3. Interconnection (wiring, JRV-DWG-002)
# =========================================================================================

def port(pid: str, text: str, color: str) -> str:
    """One port row of the mini-PC Graphviz table."""
    return f'<td port="{pid}" bgcolor="{color}" align="left"><font point-size="8.5">{text}</font></td>'


TINY = f"""<<table border="1" cellborder="1" cellspacing="0" cellpadding="4" color="{C['pc']}" bgcolor="white">
<tr><td bgcolor="{C['pc']}"><font color="white" point-size="11"><b>Lenovo ThinkCentre M73 Tiny</b></font></td></tr>
<tr><td bgcolor="{C['pc_bg']}"><font point-size="9">Core i5/i7 "T" quad-core · 8 GB DDR3 · SSD · Ubuntu Server 26.04</font></td></tr>
<tr><td bgcolor="#e5e7eb"><font point-size="9"><b>FRONT</b></font></td></tr>
<tr>{port("f_usb2", "USB 3.0 #2 → GbE adapter (<b>eth1 </b>camera)", C['cam_bg'])}</tr>
<tr>{port("f_usb1", "USB 3.0 #1 (always powered): free, installation keyboard", "#f9fafb")}</tr>
<tr>{port("f_jack", "3.5 mm microphone and headphone jacks: unused", "#f9fafb")}</tr>
<tr><td bgcolor="#e5e7eb"><font point-size="9"><b>REAR</b></font></td></tr>
<tr>{port("r_eth", "RJ45 GbE Intel I217-V → <b>eth0 </b>LAN", C['lan_bg'])}</tr>
<tr>{port("r_usb1", "USB 2.0 #1 → ReSpeaker microphone", C['audio_bg'])}</tr>
<tr>{port("r_usb3", "USB 2.0 #3 → FT232RL (door sensor)", C['hw_bg'])}</tr>
<tr>{port("r_usb2", "USB 2.0 #2 → LCUS-4 relay board", C['hw_bg'])}</tr>
<tr>{port("r_dc", "20 V DC input ← Lenovo 65 W adapter (230 V)", C['pwr_bg'])}</tr>
<tr>{port("r_dp", "DisplayPort / VGA → monitor (installation)", "#f9fafb")}</tr>
<tr>{port("r_opt", "Optional serial / USB 2.0 port (model dependent)", "#f9fafb")}</tr>
</table>>"""

RELAY = f"""<<table border="1" cellborder="1" cellspacing="0" cellpadding="3" color="{C['hw']}" bgcolor="white">
<tr><td colspan="2" bgcolor="{C['hw']}"><font color="white"><b>LCUS-4 USB relay board (CH340)</b></font></td></tr>
<tr><td port="usb" colspan="2" bgcolor="{C['hw_bg']}"><font point-size="9">USB → /dev/jarvis-relay · 10 A contacts</font></td></tr>
<tr><td rowspan="2"><font point-size="9"><b>CH1 </b>garage</font></td><td port="c1" bgcolor="#f9fafb"><font point-size="9">COM</font></td></tr>
<tr><td port="n1" bgcolor="#f9fafb"><font point-size="9">NO</font></td></tr>
<tr><td rowspan="2"><font point-size="9"><b>CH2 </b>green LED</font></td><td port="c2" bgcolor="#f9fafb"><font point-size="9">COM</font></td></tr>
<tr><td port="n2" bgcolor="#f9fafb"><font point-size="9">NO</font></td></tr>
<tr><td rowspan="2"><font point-size="9"><b>CH3 </b>red LED</font></td><td port="c3" bgcolor="#f9fafb"><font point-size="9">COM</font></td></tr>
<tr><td port="n3" bgcolor="#f9fafb"><font point-size="9">NO</font></td></tr>
<tr><td><font point-size="9"><b>CH4 </b>spare</font></td><td bgcolor="#f9fafb"><font point-size="9">COM / NO</font></td></tr>
</table>>"""

FTDI = f"""<<table border="1" cellborder="1" cellspacing="0" cellpadding="3" color="{C['hw']}" bgcolor="white">
<tr><td colspan="3" bgcolor="{C['hw']}"><font color="white"><b>FT232RL (TTL)</b></font></td></tr>
<tr><td port="usb" colspan="3" bgcolor="{C['hw_bg']}"><font point-size="9">USB → /dev/jarvis-door</font></td></tr>
<tr><td port="vcc" bgcolor="#f9fafb"><font point-size="9">VCCIO</font></td><td port="cts" bgcolor="#f9fafb"><font point-size="9">CTS#</font></td><td port="gnd" bgcolor="#f9fafb"><font point-size="9">GND</font></td></tr>
</table>>"""

NOVO = f"""<<table border="1" cellborder="1" cellspacing="0" cellpadding="3" color="{C['ink']}" bgcolor="white">
<tr><td port="t1" bgcolor="#f9fafb"><font point-size="9">F (1)</font></td><td port="t2" bgcolor="#f9fafb"><font point-size="9">F (2)</font></td></tr>
<tr><td colspan="2" bgcolor="{C['ink']}"><font color="white"><b>Novoferm Novomatic 200</b></font></td></tr>
<tr><td colspan="2"><font point-size="9">terminal F: external pulse input<br/>(potential-free dry contact)</font></td></tr>
<tr><td colspan="2"><font point-size="8.5" color="{C['muted']}">existing wall button in parallel</font></td></tr>
</table>>"""

WIRE_DOT = f"""
digraph wire {{
  graph [rankdir=LR, fontname="{FONT}", fontsize=10, nodesep=0.3, ranksep=0.75, pad=0.15, splines=spline, newrank=true];
  node  [fontname="{FONT}", fontsize=10.5, shape=box, style="rounded,filled", fillcolor=white, penwidth=1.1, margin="0.1,0.05"];
  edge  [fontname="{FONT}", fontsize=9.5, penwidth=1.5, arrowsize=0.6];

  psu12 [label=<<b>12 V DC 1 A power supply</b><br/><font point-size="9">230 V → 12 V: + to COM CH2/CH3,<br/>0 V to the (−) of both LEDs</font>>, color="{C['pwr']}", fillcolor="{C['pwr_bg']}"];

  tiny [shape=plain, label={TINY}];

  subgraph cluster_net {{
    label="Networks"; style="rounded"; color="{C['lan']}"; fontcolor="{C['lan']}"; bgcolor="#fbfcff";
    lan  [label=<<b>LAN router / switch</b><br/><font point-size="9">192.168.1.0/24</font>>, color="{C['lan']}", fillcolor="{C['lan_bg']}"];
    usbe [label=<<b>USB 3.0 → GbE adapter</b><br/><font point-size="9">RTL8153 · 192.168.50.1</font>>, color="{C['cam']}", fillcolor="{C['cam_bg']}"];
    poe  [label=<<b>PoE injector 802.3bt / Hi-PoE 60 W</b><br/><font point-size="9">230 V · DATA IN / PoE OUT</font>>, color="{C['cam']}", fillcolor="{C['cam_bg']}"];
    cam  [label=<<b>PTZ camera</b><br/><font point-size="9">192.168.50.64 · IP66<br/>view of the garage entrance</font>>, color="{C['cam']}", fillcolor="{C['cam_bg']}", penwidth=1.8];
  }}

  subgraph cluster_audio {{
    label="Audio (near the entrance)"; style="rounded"; color="{C['audio']}"; fontcolor="{C['audio']}"; bgcolor="#fbfeff";
    resp [label=<<b>ReSpeaker USB Mic Array v2.0</b><br/><font point-size="9">4 microphones · AEC · jack output</font>>, color="{C['audio']}", fillcolor="{C['audio_bg']}"];
    amp  [label=<<b>Class-D amplifier 2 × 3 W</b><br/><font point-size="9">USB 5 V</font>>, color="{C['audio']}", fillcolor="{C['audio_bg']}"];
    hp   [label=<<b>Weatherproof loudspeaker</b><br/><font point-size="9">4 to 8 Ω, 3 to 5 W</font>>, color="{C['audio']}", fillcolor="{C['audio_bg']}"];
  }}

  subgraph cluster_io {{
    label="Control and sensor (extra-low voltage only)"; style="rounded"; color="{C['hw']}"; fontcolor="{C['hw']}"; bgcolor="#fdfbff";
    relay [shape=plain, label={RELAY}];
    ftdi  [shape=plain, label={FTDI}];
    rpu   [label=<<b>Pull-up resistor</b><br/><font point-size="9">a few kΩ, CTS# → VCCIO</font>>, color="{C['hw']}", fillcolor="{C['hw_bg']}"];
    reed  [label=<<b>NO reed contact</b><br/><font point-size="9">closed = door closed (magnet facing)</font>>, color="{C['hw']}", fillcolor="{C['hw_bg']}"];
    ledg  [label=<<b>12 V LED indicator, green</b><br/><font point-size="9">(−) → 0 V of the 12 V supply</font>>, color="{C['pc']}", fillcolor="#dcfce7"];
    ledr  [label=<<b>12 V LED indicator, red</b><br/><font point-size="9">(−) → 0 V of the 12 V supply</font>>, color="{C['pwr']}", fillcolor="#fee2e2"];
  }}
  novo [shape=plain, label={NOVO}];

  // Network
  tiny:r_eth:e -> lan [dir=both, color="{C['lan']}", label="Cat6", fontcolor="{C['lan']}"];
  tiny:f_usb2:e -> usbe [color="{C['cam']}", label="USB 3.0", fontcolor="{C['cam']}"];
  usbe -> poe [dir=both, color="{C['cam']}", label="0.5 m patch", fontcolor="{C['cam']}"];
  poe -> cam [dir=both, color="{C['cam']}", penwidth=2.4, label=<outdoor Cat6 F/UTP ≤ 100 m<br/>PoE + data<br/>(optional surge protector)>, fontcolor="{C['cam']}"];

  // Audio
  tiny:r_usb1:e -> resp [color="{C['audio']}", label=<USB 2.0<br/>(active extension if &gt; 3 m)>, fontcolor="{C['audio']}"];
  resp -> amp [color="{C['audio']}", label="3.5 mm jack", fontcolor="{C['audio']}"];
  amp -> hp [color="{C['audio']}", label="2 wires"];

  // I/O
  tiny:r_usb2:e -> relay:usb:w [color="{C['hw']}", label="USB 2.0", fontcolor="{C['hw']}"];
  tiny:r_usb3:e -> ftdi:usb:w [color="{C['hw']}", label="USB 2.0", fontcolor="{C['hw']}"];
  relay:c1:e -> novo:t1:n [color="{C['ink']}", dir=none, label="2 × AWG 22"];
  relay:n1:e -> novo:t2:n [color="{C['ink']}", dir=none];
  relay:c2:e -> psu12 [color="{C['pwr']}", dir=none, label="+12 V", fontcolor="{C['pwr']}"];
  relay:c3:e -> psu12 [color="{C['pwr']}", dir=none];
  relay:n2:e -> ledg [color="{C['pc']}", dir=none, label="+"];
  relay:n3:e -> ledr [color="{C['pwr']}", dir=none, label="+"];
  ftdi:cts:e -> reed [color="{C['hw']}", dir=none, label="twisted pair"];
  ftdi:gnd:e -> reed [color="{C['hw']}", dir=none];
  ftdi:vcc:e -> rpu [color="{C['hw']}", dir=none];
  ftdi:cts:e -> rpu [color="{C['hw']}", dir=none];

  {{rank=same; psu12; ledg; ledr; novo; reed;}}
}}
"""

PORTS = [
    ["Front USB 3.0 #1 (always powered)", "free", "—", "Installation keyboard / USB stick."],
    ["Front USB 3.0 #2", "USB → GbE adapter", "eth1 (e.g. <code>enx…</code>)",
     "Camera network 192.168.50.0/24. USB 3.0 is required for 1 Gbit/s."],
    ["Rear RJ45", "LAN router / switch", "eth0 (<code>enp0s25</code>…)", "Web UI, SSH, integrations."],
    ["Rear USB 2.0 #1", "ReSpeaker USB Mic Array v2.0", "ALSA \"ReSpeaker 4 Mic Array\"",
     "<code>audio.input_device</code> and <code>audio.output_device</code> = this device."],
    ["Rear USB 2.0 #2", "LCUS-4 relay board (CH340 1a86:7523)", "<code>/dev/jarvis-relay</code>",
     "udev rule <code>deploy/udev/99-jarvis.rules</code>."],
    ["Rear USB 2.0 #3", "FT232RL (0403:6001)", "<code>/dev/jarvis-door</code>",
     "<code>door_sensor: {backend: serial_cts, device: /dev/jarvis-door}</code>"],
    ["Rear DisplayPort / VGA", "Monitor", "—", "Installation only."],
    ["Front 3.5 mm jacks", "unused", "—", "An analog microphone picks up poorly at a distance; the ReSpeaker is preferred."],
    ["Optional serial port (model dependent)", "alternative to the FT232RL", "<code>/dev/ttyS0</code>",
     "RS-232 levels: wire the reed between DTR (pin 4) and CTS (pin 8), not to GND."],
]


def wire_body() -> str:
    """Body of the interconnection document."""
    return f"""
<h1 id="scope">Scope and safety summary</h1>
<p>This document gives the wiring of every element around the Lenovo ThinkCentre M73 Tiny mini-PC: networks,
PoE camera, audio chain, USB relay board, garage door operator, LED indicators and door sensor, with the
allocation of the ports of the Tiny. Everything connected to the door, the indicators and the sensor is
<b>extra-low voltage</b>; Jarvis never switches 230 V.</p>
{callout("DANGER", "<p><b>Electric shock hazard.</b> Disconnect the Novomatic 200 from the mains before opening it or "
         "wiring terminal F. Never connect 230 V to the relay board, to the FT232RL or to the LED circuit.</p>"
         "<p>The anti-crushing safety remains the job of the door operator itself (photocells and force "
         "detection): never bypass them.</p>")}
{callout("CAUTION", "The voltage present on terminal F (external pulse input) is not documented by Novoferm. Measure "
         "it with a multimeter before wiring and confirm that it is a low-voltage, potential-free input.")}
{callout("CAUTION", "A single pulse cycles through open / stop / close. Without the door sensor, “open” sent to an "
         "open door closes it: fit the reed contact.")}
{callout("NOTE", "The PoE injector and the 12 V supply are the only mains-powered items of the installation besides "
         "the mini-PC adapter. Use enclosed, CE-certified units.")}

<h1 id="wiring-diagram">Wiring diagram</h1>
{legend([(C['pwr'], "power"), (C['lan'], "LAN"), (C['cam'], "camera / PoE"), (C['audio'], "audio"),
         (C['hw'], "USB I/O, relay, sensor"), (C['ink'], "door command")])}
<div class="fig" style="height:150mm">{dot_svg(WIRE_DOT)}</div>

<h1 id="port-allocation">Port allocation of the Tiny</h1>
{table(["Port", "Connected to", "Linux name", "Configuration / remarks"], PORTS)}

<h1 id="wiring-details">Wiring details</h1>
<div class="grid">
  <div>
    <h2 id="garage-door">Novomatic 200 garage door (CH1)</h2>
    <pre>Relay CH1  COM ─────────────┐
                             ├── terminal F "external pulse input"
Relay CH1  NO  ─────────────┘    of the Novomatic 200 (Novoferm manual, fig. 13)
                (in parallel with the existing wall button)</pre>
    <ul>
      <li>Dry contact: the relay closes the contact for <b>500 ms</b> (<code>hardware.pulse_ms</code>), like a button
      press, with a 5 s cooldown between two pulses.</li>
      <li>Cable: 2 × AWG 22 per the Novoferm manual (fig. 13a); Wago 221 lever terminals accept 0.14 to 4 mm².</li>
      <li>One pulse = open / stop / close, in that order: hence the door sensor.</li>
    </ul>
    <h2 id="indicators">LED indicators (CH2, CH3)</h2>
    <pre>+12 V ──► COM CH2 ── NO CH2 ──► (+) green LED (−) ──┐
+12 V ──► COM CH3 ── NO CH3 ──► (+) red LED   (−) ──┴──► 0 V of the 12 V supply</pre>
  </div>
  <div>
    <h2 id="door-sensor">Door sensor (reed contact)</h2>
    <pre>FT232RL VCCIO ──[ pull-up, a few kΩ ]──┐
FT232RL CTS# ───────────────────────────┼── NO reed contact
FT232RL GND  ───────────────────────────┘   (closed = magnet facing)</pre>
    <ul>
      <li>Door closed → reed closed → CTS# pulled to ground → <code>cts = True</code> → "closed"
      (<code>active_means_closed: true</code>, the default).</li>
      <li>The FT232R internal pull-up is only <b>about 200 kΩ</b> (DS_FT232R v2.16): with a long cable, add an
      <b>external pull-up of a few kΩ to VCCIO</b> and filtering (IO-9).</li>
      <li>Input threshold 1.0 to 1.5 V; never apply a voltage outside −0.5 V to VCC + 0.5 V.</li>
      <li><b>With an RS-232 (DB9) cable</b>, CTS tied to GND does not work (0 V is not a valid level): wire the reed
      between <b>DTR</b> (pin 4, asserted by pyserial when the port is opened) and <b>CTS</b> (pin 8).</li>
      <li>Twisted pair, away from 230 V cables; 20 to 30 m are no problem for this slow signal.</li>
    </ul>
    <h2 id="lengths-and-limits">Lengths and limits</h2>
    <ul>
      <li>Ethernet / PoE: <b>100 m max.</b> between the injector and the camera, UV-resistant outdoor cable, drip
      loop.</li>
      <li>USB 2.0 passive: <b>5 m max.</b>; beyond, an active extension or a USB-over-RJ45 extender.</li>
      <li>PoE power: 802.3bt / Hi-PoE 60 W (up to 51 W at the camera); PoE+ 802.3at (30 W) is not enough.</li>
    </ul>
  </div>
</div>
"""


# =========================================================================================
# 4. Software manual (Markdown -> A4 portrait PDF, JRV-TM-001)
# =========================================================================================

GRAPHS: dict[str, Callable[[], str]] = {"NET": lambda: NET_DOT, "WEB": lambda: WEB_DOT, "WIRE": lambda: WIRE_DOT}


def markdown_body(md_file: Path) -> str | None:
    """Convert a Markdown manual to body HTML (chapters as h1, sections as h2)."""
    try:
        import markdown
    except ImportError:
        log(f"skipped: {md_file.name} (the 'markdown' package is missing: uv run --no-project --with markdown ...)")
        return None
    text = md_file.read_text(encoding="utf-8")
    # Drop the file-header HTML comment (it precedes the "# " title); <!-- svg:... --> markers are kept.
    text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, count=1, flags=re.S)
    # The level-1 title is on the cover; the hand-written contents list is replaced by the generated one.
    text = re.sub(r"^# .+\n", "", text, count=1, flags=re.M)
    text = re.sub(r"^## Contents\s*\n.*?(?=^## )", "", text, count=1, flags=re.M | re.S)
    text = re.sub(r"^---\s*$", "", text, flags=re.M)
    # Python-Markdown needs a blank line between a paragraph and a list that follows it.
    text = re.sub(r"^((?![-*] |\d+\. |\s|\||#|>|```).+)\n(?=[-*] |\d+\. )", r"\1\n\n", text, flags=re.M)

    def svg(m: re.Match[str]) -> str:
        return f'<div class="fig">{dot_svg(GRAPHS[m.group(1)]())}</div>'

    text = re.sub(r"<!-- svg:(\w+)(:landscape)? -->", svg, text)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "attr_list", "md_in_html", "sane_lists"],
                           extension_configs={"toc": {"slugify": lambda v, sep: slug(v)}})
    body = md.convert(text)

    # Relative image paths -> absolute URIs (the HTML is printed from a temporary directory).
    def img(m: re.Match[str]) -> str:
        src = m.group(2)
        if re.match(r"^[a-z]+:", src):
            return m.group(0)
        return f'{m.group(1)}{(md_file.parent / src).resolve().as_uri()}"'

    body = re.sub(r'(<img[^>]*?src=")([^"]+)"', img, body)
    # Blockquotes starting with a signal word become callouts.
    body = re.sub(r"<blockquote>\s*<p>\s*(?:<strong>)?(DANGER|CAUTION|WARNING|NOTE)(?::)?(?:</strong>)?:?\s*(.*?)</blockquote>",
                  lambda m: callout("CAUTION" if m.group(1) == "WARNING" else m.group(1), "<p>" + m.group(2)),
                  body, flags=re.S | re.I)
    # Shift the heading levels: "## 1. ..." chapters become h1, "### 1.1 ..." sections become h2.
    body = re.sub(r"<(/?)h([2-6])\b", lambda m: f"<{m.group(1)}h{int(m.group(2)) - 1}", body)
    # Text before the first chapter becomes an unnumbered "About this document" chapter.
    first = body.find("<h1")
    if first > 0 and body[:first].strip():
        body = '<h1 id="about-this-document" class="nonum">About this document</h1>' + body
    return body


def build_manuals() -> None:
    """Build the software manual from docs/SOFTWARE.md."""
    MANUALS_DIR.mkdir(exist_ok=True)
    body = markdown_body(DOCS_DIR / "SOFTWARE.md")
    if body is None:
        return
    render_document(Doc(
        ref="JRV-TM-001", title="Software Documentation", kicker="Technical manual",
        subtitle="Architecture, algorithms, parameters, REST API, data and operations of the whole Jarvis software.",
        short="Software Documentation", body=body, pdf=MANUALS_DIR / "jarvis-software-documentation.pdf",
        topics={"vision", "face", "ml", "voice", "control", "network", "linux", "python", "security", "privacy",
                "sbom", "monitoring", "iot", "devops", "web"},
        landscape=False, number=False))


def build_diagrams() -> None:
    """Build the three landscape documents of docs/diagrams/."""
    render_document(Doc(
        ref="JRV-DWG-001", title="Network Flows", kicker="Technical drawing set",
        subtitle="Network zones, flow matrix, web request path, host firewall and camera configuration.",
        short="Network Flows", body=net_body(), pdf=OUT / "jarvis-network-flows.pdf",
        topics={"network", "security", "linux", "monitoring", "iot", "web", "devops"}))
    render_document(Doc(
        ref="JRV-BOM-001", title="Bill of Materials", kicker="Parts list",
        subtitle="Hardware parts with indicative prices, camera selection, software, models and licenses, SBOM.",
        short="Bill of Materials", body=bom_body(), pdf=OUT / "jarvis-bom.pdf",
        topics={"electronics", "vision", "face", "voice", "ml", "sbom", "security", "privacy"}))
    render_document(Doc(
        ref="JRV-DWG-002", title="Interconnection", kicker="Technical drawing set",
        subtitle="Wiring of the mini-PC, camera, audio, relay board, garage door, indicators and door sensor.",
        short="Interconnection", body=wire_body(), pdf=OUT / "jarvis-interconnection.pdf",
        topics={"electronics", "linux", "control"}))


def main(argv: list[str]) -> int:
    """Entry point: ``build.py [diagrams] [manuals]`` (all documents by default)."""
    for tool in ("dot", "pdftotext"):
        if not shutil.which(tool):
            raise SystemExit(f"'{tool}' not found: install graphviz and poppler-utils")
    only = set(argv)
    if not only or "diagrams" in only:
        build_diagrams()
    if not only or "manuals" in only:
        build_manuals()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
