#!/usr/bin/env python3
"""
Sitemap Generator for AI News Aggregator

Generates sitemap.xml from index.json (every published edition + static pages)
so Google Search Console can discover and monitor all content.

Written for MONITORING: GSC discovers new editions as they publish, which is
exactly what Joerg wants ("start monitoring it via Google search console").

Outputs:
  - web/data/sitemap.xml: sitemap index of every dated edition + static pages.

Dated pages are /?date=YYYY-MM-DD (the SPA's canonical daily route). Newest
edition first, lastmod = the edition's coverage date so GSC sees freshness.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

# Static discoverable pages (change rarely)
STATIC_PAGES = [
    ("/", "0.9", "daily"),
    ("/about", "0.5", "monthly"),
    ("/archive", "0.6", "daily"),
    ("/feeds", "0.4", "monthly"),
]


class SitemapGenerator:
    def __init__(self, web_dir, base_url="https://example.com"):
        # web_dir is the web ROOT; the data lives in its /data subdir (which is
        # the mounted volume on the deploy). sitemap.xml must land IN the data
        # dir so it survives rebuilds and is served via the nginx alias.
        self.web_dir = Path(web_dir)
        self.data_dir = self.web_dir / "data"
        self.base_url = base_url.rstrip("/")

    def latest_date(self) -> str | None:
        idx = self.data_dir / "index.json"
        if not idx.exists():
            return None
        try:
            d = json.loads(idx.read_text(encoding="utf-8"))
            dates = [x["date"] for x in d.get("dates", [])]
            return max(dates) if dates else None
        except Exception:
            return None

    def generate(self) -> str | None:
        idx = self.data_dir / "index.json"
        if not idx.exists():
            logger.warning("sitemap: no index.json at %s; skipping", idx)
            return None
        try:
            manifest = json.loads(idx.read_text(encoding="utf-8"))
            dates = [x["date"] for x in manifest.get("dates", [])]
        except Exception as e:
            logger.warning("sitemap: couldn't parse index.json: %s", e)
            return None

        dates = sorted(dates, reverse=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        urls = []
        for path, priority, changefreq in STATIC_PAGES:
            urls.append(
                f"  <url>\n"
                f"    <loc>{self.base_url}{path}</loc>\n"
                f"    <lastmod>{now}</lastmod>\n"
                f"    <changefreq>{changefreq}</changefreq>\n"
                f"    <priority>{priority}</priority>\n"
                f"  </url>"
            )

        for date in dates:
            qs = urlencode({"date": date})
            urls.append(
                f"  <url>\n"
                f"    <loc>{self.base_url}/?{escape(qs)}</loc>\n"
                f"    <lastmod>{date}</lastmod>\n"
                f"    <changefreq>daily</changefreq>\n"
                f"    <priority>0.8</priority>\n"
                f"  </url>"
            )

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>\n"
        )

        out = self.data_dir / "sitemap.xml"
        out.write_text(xml, encoding="utf-8")
        logger.info("sitemap: wrote %s (%d urls)", out, len(urls))
        return xml


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Generate sitemap.xml from index.json")
    p.add_argument("--web-dir", default="web", help="Web root (data lives in its /data subdir)")
    p.add_argument("--base-url", default="https://example.com")
    args = p.parse_args()
    gen = SitemapGenerator(args.web_dir, args.base_url)
    xml = gen.generate()
    print(f"OK -> {args.web_dir}/sitemap.xml ({xml.count('<url>')} urls)" if xml else "NO DATA")