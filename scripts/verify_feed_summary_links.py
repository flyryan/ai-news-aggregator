#!/usr/bin/env python3
"""
Focused assertions for summary-entry Atom links.

This exercises the feed generator through feedparser so the primary permalink
behavior matches what common readers expose.
"""

import tempfile
import sys
from pathlib import Path

import feedparser

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from generators.feed_generator import FeedGenerator


BASE_URL = "https://news.aatf.ai"
DATE = "2026-05-24"
SITE_URL = f"{BASE_URL}/?date={DATE}"
EXTERNAL_URL = "https://example.com/source-article"


def build_summary_feed() -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        generator = FeedGenerator(tmpdir, base_url=BASE_URL)
        return generator._build_atom_feed(
            items=[
                {
                    "_is_summary": True,
                    "_feed_date": DATE,
                    "_external_url": EXTERNAL_URL,
                    "_hero_image_url": f"/data/{DATE}/hero.webp",
                    "title": "Daily Briefing: May 24, 2026",
                    "summary_html": '<p>Executive text with <a href="/archive">archive</a>.</p>',
                    "url": SITE_URL,
                    "published": f"{DATE}T06:00:00Z",
                }
            ],
            feed_id="urn:ainews:test",
            title="Test Feed",
            subtitle="Summary link behavior",
            feed_url=f"{BASE_URL}/data/feeds/test.xml",
            site_url=BASE_URL,
        )


def assert_summary_link_behavior() -> None:
    parsed = feedparser.parse(build_summary_feed())
    assert not parsed.bozo, parsed.bozo_exception
    assert len(parsed.entries) == 1

    entry = parsed.entries[0]
    links = entry.get("links", [])

    assert entry.link == SITE_URL
    assert any(
        link.get("href") == SITE_URL
        and link.get("rel") == "alternate"
        and link.get("type") == "text/html"
        for link in links
    )
    assert any(
        link.get("href") == SITE_URL
        and link.get("rel") == "canonical"
        and link.get("type") == "text/html"
        for link in links
    )
    assert any(
        link.get("href") == EXTERNAL_URL
        and link.get("rel") == "alternate"
        and link.get("type") == "text/html; charset=utf-8"
        for link in links
    )
    assert any(
        link.get("href") == EXTERNAL_URL
        and link.get("rel") == "via"
        and link.get("type") == "text/html"
        for link in links
    )

    assert "Executive text" in entry.summary
    assert entry.content and "Executive text" in entry.content[0].value
    assert entry.media_thumbnail
    assert entry.media_thumbnail[0]["url"] == f"{BASE_URL}/data/{DATE}/hero.webp"


if __name__ == "__main__":
    assert_summary_link_behavior()
    print("summary feed link assertions passed")
