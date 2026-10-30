#!/usr/bin/env python3
"""
Quick Answer Generator — GEO (Generative Engine Optimization)

Writes web/quick-answer.html: a dated, crawler-visible "quick answer" distilled
from the latest edition's executive summary. nginx SSI injects it into the SPA
shell at request time so ChatGPT/Perplexity/Claude/Google see the day's answer in
raw HTML without executing JS.

Recipe step 3 of the GEO playbook (James Brooks): a short, dated quick answer at
the top of key pages — "every day's news should be found by any crawler".

"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Brand palette from the app (matches Tailwind theme) — keep it minimal; the
# fragment sits above the hydrated SPA and must not fight it for layout.
QA_CSS = """<style>
.qa-block{max-width:1100px;margin:0 auto 4px;padding:14px 20px;background:#111318;border:1px solid #2a2f3a;border-left:4px solid #FFD700;border-radius:8px;font-family:ui-sans-serif,system-ui,-apple-system,sans-serif}
.qa-block .qa-kicker{display:block;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#00FFFF;font-weight:700;margin-bottom:6px}
.qa-block .qa-headline{color:#f8f8f8;font-size:18px;line-height:1.35;font-weight:700;margin:0 0 8px}
.qa-block .qa-body{color:#c9cdd6;font-size:14px;line-height:1.45;margin:0}
.qa-block .qa-body a{color:#FFD700;text-decoration:underline}
.qa-block .qa-meta{display:block;color:#7e8494;font-size:12px;margin-top:8px}
</style>
"""


class QuickAnswerGenerator:
    """Generates the dated quick-answer HTML fragment from the latest summary."""

    def __init__(self, web_dir, base_url="https://example.com", data_dir=None):
        self.web_dir = Path(web_dir)
        self.base_url = base_url.rstrip("/")
        # A mounted-volume deploy (web/data mounted, web/ baked) writes the
        # fragment into the same volume as the data. Accept an explicit
        # data_dir (e.g. "web/data") so index.json + summary.json are found
        # while the fragment still lands in the mounted dir.
        # web_dir is the web ROOT; data lives in /data (the mounted deploy volume).
        self.data_dir = Path(data_dir) if data_dir else self.web_dir / "data"

    def latest_date(self) -> str | None:
        """Read the newest edition date from data/index.json."""
        idx_path = self.data_dir / "index.json"
        if not idx_path.exists():
            return None
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            dates = [d["date"] for d in idx.get("dates", [])]
            return max(dates) if dates else None
        except Exception:
            return None

    def load_summary(self, date_str: str) -> dict | None:
        p = self.data_dir / date_str / "summary.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _clean_markdown(text: str) -> str:
        """Strip the executive summary to clean, readable plain text.

        GEO purpose: give crawlers a crisp dated answer as readable text, not
        markdown noise. Links stay in the page body; the quick-answer block
        carries the *answer* in plain text (simpler + more robust than
        re-rendering nested markdown, and crawlers index it perfectly).
        """
        import html as html_mod

        # Code spans/tick escapes first
        text = re.sub(r"`([^`]+)`", r"\1", text)
        # Bold/italic markers -> plain (nested-safe: strip * characters that
        # belong to markdown emphasis, keep literal ones when clearly not)
        text = re.sub(r"\*{1,2}([^*]+?)\*{1,2}", r"\1", text)
        # Links [label](url) -> label
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # Residual link/ref syntax: [label] and bare (url)
        text = re.sub(r"\[([^\]]+)\]", r"\1", text)
        text = re.sub(r"\(https?://[^)]+\)", "", text)
        # Headers, blockquotes, bullets, hr
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"^[\s]*>+\s?", "", text, flags=re.MULTILINE)
        text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        # Escape residual raw markup for safety
        return html_mod.escape(text, quote=True)

    def _first_paragraph(self, exec_summary: str) -> tuple[str, str | None]:
        """Return (headline, body) from the markdown executive summary.

        Falls back gracefully: first non-empty line becomes the headline, the
        rest becomes the body. Never raises — the fragment should never take
        the site down because a summary shape changed.
        """
        lines = [ln.strip() for ln in exec_summary.splitlines() if ln.strip()]
        if not lines:
            return "Daily update — Hermes Agent news.", "Curated release news, community updates, and ecosystem research for the Hermes Agent ecosystem."
        headline = re.sub(r"^[#>\s*-]+", "", lines[0]).strip()
        body_lines = [ln for ln in lines[1:] if not ln.startswith("#")]
        body = " ".join(re.sub(r"^[#>\s*-]+", "", ln).strip() for ln in body_lines[:4])
        return headline, body

    def generate(self, date_str: str | None = None) -> tuple[str, str]:
        """Write web/quick-answer.html. Returns (date, html) for verification."""
        date = date_str or self.latest_date()
        if not date:
            logger.warning("quick-answer: no edition date found; skipping")
            return "", ""

        summary = self.load_summary(date)
        if summary and summary.get("executive_summary"):
            headline, body = self._first_paragraph(summary["executive_summary"])
            body = self._clean_markdown(body)
        else:
            headline = "Daily update — Hermes Agent news."
            body = ""

        # Resolve relative links (like /?date=...) to absolute so crawlers leave
        # the SPA and land on real content URLs.
        body = re.sub(r'href="(/[^"]*)"', f'href="{self.base_url}\\1"', body)

        html = (
            QA_CSS
            + f'<section class="qa-block" aria-label="Today&#39;s quick answer">'
            + f'<span class="qa-kicker">Quick answer {date} — updated daily</span>'
            + f'<p class="qa-headline">{headline}</p>'
            + f'<p class="qa-body">{body}</p>'
            + f'<span class="qa-meta">Source: {self.base_url} · Example News</span>'
            + "</section>"
        )

        out = self.data_dir / "quick-answer.html"
        out.write_text(html, encoding="utf-8")
        logger.info("quick-answer: wrote %s for %s (%d bytes)", out, date, len(html))
        return date, html


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate the GEO quick-answer fragment.")
    parser.add_argument("--web-dir", default="web", help="Path to the web root (contains data/)")
    parser.add_argument("--base-url", default="https://example.com")
    parser.add_argument("--date", default=None, help="Specific edition date (default: latest)")
    parser.add_argument("--data-dir", default=None, help="Path to data/ (defaults to <web-dir>/data)")
    args = parser.parse_args()

    gen = QuickAnswerGenerator(args.web_dir, args.base_url, args.data_dir)
    d, _ = gen.generate(args.date)
    print(f"OK {d} -> {gen.data_dir / 'quick-answer.html'}" if d else "NO DATA")
