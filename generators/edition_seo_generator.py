#!/usr/bin/env python3
"""edition_seo_generator.py — Server-side edition SEO fragment for example.xyz.

Two things the current SPA shell lacks for crawlers that don't run JS:
  1. A real <h1> + a story headline list (content structure), and
  2. NewsArticle JSON-LD (headline/datePublished/author/publisher/image) — the
     schema Google News and AI answer engines expect on a news/edition page.

nginx SSI injects this into the raw HTML a crawler receives (same pattern as
quick-answer.html). Complementary, not a replacement: quick-answer carries the
day's distilled answer; this carries structured edition content + schema.

"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SEC_CSS = """<style>
.se-block{max-width:1100px;margin:0 auto 24px;padding:20px 20px 12px;background:#0f1117;border:1px solid #232833;border-radius:10px;font-family:ui-sans-serif,system-ui,-apple-system,sans-serif}
.se-block .se-kicker{display:block;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#00FFFF;font-weight:700;margin-bottom:10px}
.se-block h1{color:#f8f8f8;font-size:26px;line-height:1.3;font-weight:800;margin:0 0 14px}
.se-block .se-stories{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.se-block .se-stories li{margin:0;padding:6px 0;border-top:1px solid #1c212b}
.se-block .se-stories a{color:#FFD700;text-decoration:none;font-weight:600;font-size:15px;line-height:1.4;display:block}
.se-block .se-stories .se-src{display:block;color:#7e8494;font-size:12px;margin-top:2px}
</style>
"""


class EditionSeoGenerator:
    """Writes web/data/edition-seo.html — a crawler-visible edition section."""

    def __init__(self, web_dir, base_url="https://example.com", data_dir=None):
        self.web_dir = Path(web_dir)
        self.base_url = base_url.rstrip("/")
        self.data_dir = Path(data_dir) if data_dir else self.web_dir / "data"

    def latest_date(self):
        idx_path = self.data_dir / "index.json"
        if not idx_path.exists():
            return None
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            dates = [d["date"] for d in idx.get("dates", [])]
            return max(dates) if dates else None
        except Exception:
            return None

    def load_summary(self, date_str):
        p = self.data_dir / date_str / "summary.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def load_stories(self, date_str, limit=8):
        """Pull the top stories (by importance_score) from news.json."""
        p = self.data_dir / date_str / "news.json"
        if not p.exists():
            return []
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = d.get("items", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
        if not items:
            return []
        # sort by importance_score desc, take top N
        def score(x):
            try:
                return float(x.get("importance_score", 0) or 0)
            except (TypeError, ValueError):
                return 0
        items = sorted(items, key=score, reverse=True)[:limit]
        out = []
        for it in items:
            title = (it.get("title") or "").strip()
            url = (it.get("url") or "").strip()
            src = (it.get("source") or "").strip()
            author = (it.get("author") or "").strip()
            published = (it.get("published") or "").strip()
            if not title or not url:
                continue
            out.append({"title": title, "url": url, "source": src,
                        "author": author, "published": published})
        return out

    @staticmethod
    def _esc(s):
        import html as H
        return H.escape(s or "", quote=True)

    def generate(self, date_str=None):
        date = date_str or self.latest_date()
        if not date:
            logger.warning("edition-seo: no edition date; skipping")
            return "", ""

        summary = self.load_summary(date) or {}
        stories = self.load_stories(date)
        exec_summary = (
            summary.get("executive_summary")
            or summary.get("executive_summary_html")
            or "Daily curated news about the Hermes Agent ecosystem."
        )

        def _to_plain(text: str) -> str:
            """Strip markdown to clean single-line plain text for meta/schema."""
            import html as H
            t = re.sub(r"[*_]{1,2}([^*_]+?)[*_]{1,2}", r"\1", text or "")
            t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
            t = re.sub(r"^#{1,6}\s*", "", t, flags=re.MULTILINE)
            t = re.sub(r"\s+", " ", t).strip()
            return t[:300]

        exec_plain = _to_plain(exec_summary)
        title = self._esc(f"Example News — {date}")
        hero = (summary.get("hero_image_url") or "").strip()
        head = summary.get("top_topics")
        if isinstance(head, list) and head:
            # each entry is a dict {"name": ..., "description": ...} -> use the name
            first = head[0]
            headline = first.get("name") if isinstance(first, dict) else str(first)
            headline = str(headline).strip()
        else:
            headline = self._esc(summary.get("coverage_date") or f"Daily news digest — {date}")

        # 1. Story headline list (HTML)
        stories_html = ""
        for s in stories:
            src_mark = f'<span class="se-src">{self._esc(s["source"] or s["author"] or s["published"] or "")}</span>'
            stories_html += (
                f'<li><a href="{self._esc(s["url"])}">{self._esc(s["title"])}</a>{src_mark}</li>'
            )
        if not stories_html:
            stories_html = '<li>Top stories load in the interactive edition below.</li>'

        se = (
            SEC_CSS
            + f'<section class="se-block" aria-label="Latest edition">'
            + f'<span class="se-kicker">Latest edition {date}</span>'
            + f"<h1>{headline}</h1>"
            + f'<ul class="se-stories">{stories_html}</ul>'
            + "</section>"
        )

        # 2. NewsArticle JSON-LD
        article_schema = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": headline,
            "datePublished": date,
            "dateModified": date,
            "mainEntityOfPage": {
                "@type": "WebPage",
                "@id": f"{self.base_url}/?date={date}",
            },
            "author": {
                "@type": "Organization",
                "name": "JP Technologies",
                "url": "https://example.com",
            },
            "publisher": {
                "@type": "Organization",
                "name": "Example News",
                "url": self.base_url,
            },
            "description": exec_plain,
        }
        if hero:
            article_schema["image"] = hero if hero.startswith("http") else f"{self.base_url}{hero}"
        ld = f'<script type="application/ld+json">{json.dumps(article_schema, ensure_ascii=False)}</script>'

        out_html = ld + "\n" + se
        out = self.data_dir / "edition-seo.html"
        out.write_text(out_html, encoding="utf-8")
        logger.info("edition-seo: wrote %s for %s (%d bytes, %d stories)", out, date, len(out_html), len(stories))
        return date, out_html


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Generate the edition SEO + NewsArticle fragment.")
    p.add_argument("--web-dir", default="web")
    p.add_argument("--date", default=None)
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO)
    from pathlib import Path
    g = EditionSeoGenerator(a.web_dir, data_dir=Path(a.web_dir) / "data")
    d, h = g.generate(a.date)
    if d:
        print(f"wrote edition-seo for {d}: {len(h)} bytes -> web/data/edition-seo.html")
    else:
        print("no edition produced")