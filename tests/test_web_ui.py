# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_web_ui.py
# Purpose : Static checks of the web UI: i18n completeness, assets, CSP rules, icon sprite
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""en-US.json is the reference locale: every other locale has exactly its keys and placeholders,
and every key used by index.html / app.js exists in it. Every referenced image exists, the
markup stays CSP-compatible, and img/icons.svg matches the inline sprite."""

import json
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "jarvis" / "web" / "static"
I18N = STATIC / "i18n"
# Markup without its HTML comments (they document the conventions and quote attribute examples).
HTML = re.sub(r"<!--.*?-->", "", (STATIC / "index.html").read_text(encoding="utf-8"), flags=re.S)
JS = (STATIC / "app.js").read_text(encoding="utf-8")
REFERENCE = json.loads((I18N / "en-US.json").read_text(encoding="utf-8"))
LOCALES = sorted(p for p in I18N.glob("*.json") if p.name != "en-US.json")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_there_is_a_single_reference_and_eleven_translations():
    assert not (I18N / "_source.en.json").exists()
    assert [p.stem for p in LOCALES] == ["de-DE", "es-ES", "fr-FR", "id-ID", "it-IT", "ja-JP", "ko-KR",
                                         "nl-NL", "ru-RU", "th-TH", "zh-CN"]


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_locale_matches_the_reference(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == set(REFERENCE), f"missing {sorted(set(REFERENCE) - set(data))[:5]}, extra {sorted(set(data) - set(REFERENCE))[:5]}"
    for key, text in data.items():
        assert text.strip(), f"{path.stem}: empty translation for {key}"
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(REFERENCE[key])), f"{path.stem}: placeholders of {key}"


def test_every_key_used_by_the_ui_is_in_the_reference():
    html_keys = set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', HTML))
    js_keys = set(re.findall(r'\bt\("([a-z_]+\.[a-z0-9_.]+)"', JS))
    missing = sorted((html_keys | js_keys) - set(REFERENCE))
    assert not missing, missing
    for key in ("password.title", "password.forced", "password.rule_length", "login.file_mode", "login.unreachable"):
        assert key in REFERENCE


def test_referenced_images_exist():
    refs = set(re.findall(r'(?:href|src)="(img/[^"]+)"', HTML))
    refs |= {i["src"] for i in json.loads((STATIC / "manifest.webmanifest").read_text())["icons"]}
    refs |= set(re.findall(r'url\("(img/[^"]+)"\)', (STATIC / "style.css").read_text()))
    assert {"img/favicon.svg", "img/favicon.ico", "img/apple-touch-icon.png", "img/login-bg.svg"} <= refs
    assert all((STATIC / r).is_file() for r in refs), [r for r in refs if not (STATIC / r).is_file()]
    assert 'rel="manifest"' in HTML and 'name="robots"' in HTML


def test_markup_is_csp_compatible():
    # default-src 'self', no 'unsafe-inline': no inline script body, no on* handler, no style attribute.
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", HTML)
    assert not re.search(r"\son[a-z]+=", HTML)
    assert not re.search(r'\sstyle="', HTML)
    assert "data:image" not in HTML.split("</head>")[0]


def test_icon_sprite_file_matches_the_inline_sprite():
    def symbols(text):
        return {m[1]: re.sub(r"\s+", " ", m[2]).strip()
                for m in re.finditer(r'<symbol id="([^"]+)"[^>]*>(.*?)</symbol>', text, re.S)}
    inline = symbols(HTML.split('<svg class="sprite"', 1)[1].split("</svg>", 1)[0])
    inline.pop("i-logo")                       # the logo is img/favicon.svg
    assert inline == symbols((STATIC / "img" / "icons.svg").read_text())


def test_password_dialog_mirrors_the_server_policy():
    assert 'id="password-dialog"' in HTML and 'id="change-password"' in HTML
    for rule in ("length", "distinct", "common", "changed", "match"):
        assert f'data-rule="{rule}"' in HTML and f"{rule}:" in JS
    assert "pw.length >= 12" in JS and "new Set(pw).size >= 5" in JS
    app = (STATIC.parent / "app.py").read_text()
    assert "len(new) < 12" in app and "len(set(new)) < 5" in app
