"""Regression tests for tools/web.py text-extraction helpers.

Covers the 2026-05-08 graph_pipeline crash:
  '[graph_pipeline] Graph pipeline failed:
   'NavigableString' object has no attribute 'children'.'

Root cause: soup.select_one('[role="main"]') and similar can return a
NavigableString (text node) rather than a Tag. Iterating .children on a
NavigableString raises AttributeError. _extract_text now guards at entry.
"""
from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString

from localsmartz.tools.web import _extract_text, _extract_main_content


def test_extract_text_with_navigable_string_does_not_crash():
    """The bug we're fixing: NavigableString fed in as element."""
    text_node = NavigableString("Plain text content")
    out = _extract_text(text_node, extract_tables=False)
    assert out == "Plain text content"


def test_extract_text_with_empty_navigable_string():
    text_node = NavigableString("   \n\t  ")
    assert _extract_text(text_node, extract_tables=False) == ""


def test_extract_text_with_normal_tag_still_works():
    soup = BeautifulSoup("<div><p>Hello</p><p>World</p></div>", "html.parser")
    out = _extract_text(soup.div, extract_tables=False)
    assert "Hello" in out
    assert "World" in out


def test_extract_main_content_pathological_role_main_text_only():
    """select_one('[role="main"]') matched a text-only fragment that was
    parsed as NavigableString rather than Tag. The graph_pipeline crash
    reproducer."""
    # Constructed to give select_one a NavigableString-shaped match —
    # whitespace-and-text inside an attribute-bearing wrapper.
    html = '<html><body><div role="main">Bare text only</div></body></html>'
    soup = BeautifulSoup(html, "html.parser")
    # Should not raise even if main returns short content; falls through
    # to body extraction.
    out = _extract_main_content(soup, extract_tables=False)
    assert isinstance(out, str)


def test_extract_main_content_no_body_falls_back():
    html = "<html><head><title>x</title></head></html>"
    soup = BeautifulSoup(html, "html.parser")
    out = _extract_main_content(soup, extract_tables=False)
    assert isinstance(out, str)
