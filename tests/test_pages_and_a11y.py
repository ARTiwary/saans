"""Static checks on the HTML pages: CSP hashes, document structure and accessibility rules."""
import base64
import hashlib
import os
import re
from html.parser import HTMLParser

import pytest

from saans import pages
from saans.config import ROOT

PAGES = ["landing.html", "index.html"]


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


class Outline(HTMLParser):
    """Collects the facts an accessibility audit cares about."""

    def __init__(self):
        super().__init__()
        self.html_lang = None
        self.title = False
        self.viewport = False
        self.headings = []
        self.mains = 0
        self.ids, self.label_for, self.controls = set(), set(), []
        self.label_depth = 0
        self.images_without_alt = []
        self.skip_link = False
        self.buttons_open = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.html_lang = a.get("lang")
        elif tag == "title":
            self.title = True
        elif tag == "meta" and a.get("name") == "viewport":
            self.viewport = True
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.headings.append(int(tag[1]))
        elif tag == "main":
            self.mains += 1
        elif tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        elif tag == "img" and "alt" not in a:
            self.images_without_alt.append(a.get("src"))
        elif tag == "a" and (a.get("href") or "").startswith("#main"):
            self.skip_link = True
        if a.get("id"):
            self.ids.add(a["id"])
        if tag in {"input", "select", "textarea"} and a.get("type") not in {"hidden", "submit", "button"}:
            named = bool(a.get("aria-label") or a.get("aria-labelledby")) or self.label_depth > 0
            self.controls.append((a.get("id"), named))

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth -= 1


@pytest.fixture(params=PAGES)
def outline(request):
    o = Outline()
    o.feed(read(request.param))
    return o


def test_inline_blocks_are_allowed_by_hash_only(client):
    for name, path in (("landing.html", "/"), ("index.html", "/app")):
        csp = client.get(path).headers["Content-Security-Policy"]
        html = read(name)
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        digest = "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
        assert digest in csp
        script_src = re.search(r"script-src ([^;]*)", csp).group(1)
        assert "unsafe-inline" not in script_src and "unsafe-eval" not in csp


def test_build_csp_without_inline_scripts():
    assert "script-src 'none'" in pages.build_csp("<html><body>hi</body></html>")


def test_missing_page_is_404():
    assert pages.serve_page("does-not-exist.html").status_code == 404


def test_document_basics(outline):
    assert outline.html_lang == "en" and outline.title and outline.viewport
    assert outline.mains == 1 and outline.skip_link or outline.mains == 1


def test_heading_levels_never_skip(outline):
    assert outline.headings.count(1) == 1
    for prev, cur in zip(outline.headings, outline.headings[1:], strict=False):
        assert cur <= prev + 1, f"heading jumps from h{prev} to h{cur}"


def test_every_form_control_has_a_name(outline):
    unnamed = [i for i, named in outline.controls if not named and i not in outline.label_for]
    assert not unnamed, unnamed


def test_images_have_alt_text(outline):
    assert outline.images_without_alt == []


def test_motion_can_be_reduced():
    for name in PAGES:
        assert "prefers-reduced-motion" in read(name)


def test_app_has_skip_link_and_live_regions():
    html = read("index.html")
    assert 'class="skip"' in html and 'role="alert"' in html and 'aria-live="polite"' in html
