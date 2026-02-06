#!/usr/bin/env python3
"""
Atom Feed Generator for AI News Aggregator

Generates Atom 1.0 feeds from JSON data files for RSS reader consumption.
Outputs:
  - feeds/main.xml: Top stories + top 5 from each category (mirrors homepage)
  - feeds/news.xml: All news items
  - feeds/{category}-{size}.xml: Category feeds with size variants (25, 50, 100, full)
"""

import json
import os
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from urllib.parse import quote, urlparse, urlunparse
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)


def encode_url_for_xml(url: str) -> str:
    """
    Encode a URL to be valid as an IRI in XML/Atom feeds.

    Handles:
    - Non-ASCII characters in path (like Erdős)
    - Special characters that need percent-encoding
    - XML entity escaping for the final output
    """
    if not url:
        return ''

    try:
        parsed = urlparse(url)
        # Encode the path component (handles non-ASCII chars)
        encoded_path = quote(parsed.path, safe='/:@!$&\'()*+,;=')
        # Encode query string if present
        encoded_query = quote(parsed.query, safe='/:@!$&\'()*+,;=?') if parsed.query else ''
        # Reconstruct URL
        encoded_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            encoded_path,
            parsed.params,
            encoded_query,
            parsed.fragment
        ))
        # Escape for XML (handles &, <, >, etc.)
        return escape(encoded_url)
    except Exception:
        # Fallback: just escape for XML
        return escape(url)


def strip_html_from_text(text: str) -> str:
    """Remove HTML tags from text for plain text fields."""
    if not text:
        return ''
    # Remove HTML tags
    clean = re.sub(r'<[^>]+>', '', text)
    # Decode common entities
    clean = clean.replace('&nbsp;', ' ')
    clean = clean.replace('&amp;', '&')
    clean = clean.replace('&lt;', '<')
    clean = clean.replace('&gt;', '>')
    clean = clean.replace('&quot;', '"')
    return clean.strip()


def make_urls_absolute(html: str, base_url: str) -> str:
    """
    Convert relative URLs in HTML to absolute URLs.

    RSS readers have no base URL context, so all hrefs must be absolute.
    Per RSS Best Practices: "Avoid relative URLs in descriptions."
    """
    if not html:
        return ''
    # Convert href="/ to href="https://ainews.aatf.dev/
    html = re.sub(r'href="/', f'href="{base_url}/', html)
    # Convert href="# to href="https://ainews.aatf.dev/#
    html = re.sub(r'href="#', f'href="{base_url}/#', html)
    return html


class FeedGenerator:
    """Generates Atom feeds from JSON data files."""

    # Size variants for category feeds
    SIZE_VARIANTS = [25, 50, 100, None]  # None = full/all items

    FEED_TITLE = "AATF AI News Aggregator"
    FEED_SUBTITLE = "Daily AI/ML news powered by Claude Opus 4.6"

    def _extract_first_external_link(self, html: str) -> Optional[str]:
        """
        Extract first external href from HTML content.

        Used to find an external link for summary entries so Feedly doesn't
        filter them out (Feedly filters entries with same-domain alternate links).
        """
        if not html:
            return None
        # Match href="http(s)://..." but exclude our own domain
        matches = re.findall(r'href="(https?://[^"]+)"', html)
        for url in matches:
            if self.base_url not in url:
                return url
        return None

    def __init__(self, output_dir: str, rolling_window_days: int = 7, base_url: str = None):
        """
        Initialize feed generator.

        Args:
            output_dir: Base output directory (typically web/)
            rolling_window_days: Number of days to include in feeds
            base_url: Base URL for feed links (defaults to http://localhost:8080)
        """
        self.output_dir = output_dir
        self.data_dir = os.path.join(output_dir, 'data')
        self.feeds_dir = os.path.join(self.data_dir, 'feeds')
        self.rolling_window_days = rolling_window_days
        self.base_url = (base_url or "http://localhost:8080").rstrip('/')

        os.makedirs(self.feeds_dir, exist_ok=True)
        logger.info(f"FeedGenerator initialized with {rolling_window_days}-day window, base_url={self.base_url}")

    def generate_feeds(self) -> None:
        """Generate all feed files."""
        logger.info("Generating Atom feeds...")

        # Get dates within rolling window
        dates = self._get_dates_in_window()
        if not dates:
            logger.warning("No dates found for feed generation")
            return

        logger.info(f"Generating feeds for {len(dates)} dates")

        # Generate main feed (mirrors homepage)
        self._generate_main_feed(dates)

        # Generate news feed (all items, typically small)
        news_items, news_summaries = self._collect_category_items(dates, 'news')
        self._generate_category_feed('news', news_items, news_summaries, limit=None)

        # Generate category feeds with size variants
        for category in ['research', 'social', 'reddit']:
            all_items, summaries = self._collect_category_items(dates, category)
            for size in self.SIZE_VARIANTS:
                self._generate_category_feed(category, all_items, summaries, limit=size)

        # Generate summary-only feeds
        self._generate_executive_summary_feed(dates)
        self._generate_all_summaries_feed(dates)
        for category in ['news', 'research', 'social', 'reddit']:
            self._generate_summary_only_feed(dates, category)

        logger.info("Feed generation complete")

    def _get_dates_in_window(self) -> List[str]:
        """Get dates within rolling window from index.json."""
        index_path = os.path.join(self.data_dir, 'index.json')
        if not os.path.exists(index_path):
            return []

        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)

        cutoff = datetime.now() - timedelta(days=self.rolling_window_days)
        cutoff_str = cutoff.strftime('%Y-%m-%d')

        return sorted(
            [d['date'] for d in index.get('dates', []) if d['date'] >= cutoff_str],
            reverse=True
        )

    def _generate_main_feed(self, dates: List[str]) -> None:
        """
        Generate the main feed that mirrors the homepage.

        Includes:
        - Daily executive summary entry for each date
        - Items from top_topics (cross-category top stories)
        - Top 5 items from each category

        Entries are interleaved by date (newest first) so RSS readers
        display them correctly even if they don't sort by published date.
        """
        all_entries = []
        seen_ids = set()
        summary_count = 0

        for date in dates:
            summary_path = os.path.join(self.data_dir, date, 'summary.json')
            if not os.path.exists(summary_path):
                continue

            try:
                with open(summary_path, 'r', encoding='utf-8') as f:
                    summary = json.load(f)

                date_items = []  # Items for this specific date

                # Add executive summary as a special entry
                exec_summary = summary.get('executive_summary_html', summary.get('executive_summary', ''))
                hero_image_url = summary.get('hero_image_url', '')
                if exec_summary:
                    # Find first external URL from top items for Feedly compatibility
                    # (Feedly filters entries where alternate link matches feed domain)
                    first_external_url = None
                    for category, cat_data in summary.get('categories', {}).items():
                        for item in cat_data.get('top_items', [])[:5]:
                            url = item.get('url', '')
                            if url and self.base_url not in url:
                                first_external_url = url
                                break
                        if first_external_url:
                            break

                    # Add summary first for this date
                    all_entries.append({
                        '_is_summary': True,
                        '_feed_date': date,
                        '_external_url': first_external_url,
                        '_hero_image_url': hero_image_url,
                        'title': f"Daily Briefing: {self._format_date_title(date)}",
                        'summary_html': exec_summary,
                        'url': f"{self.base_url}/?date={date}",
                        'published': f"{date}T06:00:00Z",  # Morning briefing time
                        'importance_score': 1000,  # Ensure summaries appear first
                    })
                    summary_count += 1

                # Get items from top_topics
                for topic in summary.get('top_topics', []):
                    for item_id in topic.get('representative_items', []):
                        if item_id not in seen_ids:
                            # Find the item in category data
                            item = self._find_item_by_id(date, item_id)
                            if item:
                                item['_feed_date'] = date
                                date_items.append(item)
                                seen_ids.add(item_id)

                # Get top 5 from each category
                for category, cat_data in summary.get('categories', {}).items():
                    for item in cat_data.get('top_items', [])[:5]:
                        item_id = item.get('id', '')
                        if item_id and item_id not in seen_ids:
                            item['_feed_date'] = date
                            item['_feed_category'] = category
                            date_items.append(item)
                            seen_ids.add(item_id)

                # Sort this date's items by importance, then add to all_entries
                date_items.sort(key=lambda x: -x.get('importance_score', 0))
                all_entries.extend(date_items)

            except Exception as e:
                logger.warning(f"Failed to load {summary_path}: {e}")

        feed_xml = self._build_atom_feed(
            items=all_entries,
            feed_id="urn:ainews:main",
            title=self.FEED_TITLE,
            subtitle=self.FEED_SUBTITLE,
            feed_url=f"{self.base_url}/data/feeds/main.xml",
            site_url=self.base_url
        )

        output_path = os.path.join(self.feeds_dir, 'main.xml')
        self._write_feed(output_path, feed_xml)
        item_count = len(all_entries) - summary_count
        logger.info(f"Generated main.xml ({summary_count} summaries, {item_count} items)")

    def _find_item_by_id(self, date: str, item_id: str) -> Optional[Dict[str, Any]]:
        """Find an item by ID across all categories for a date."""
        for category in ['news', 'research', 'social', 'reddit']:
            path = os.path.join(self.data_dir, date, f'{category}.json')
            if not os.path.exists(path):
                continue

            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                for item in data.get('items', []):
                    if item.get('id') == item_id:
                        item['_feed_category'] = category
                        return item
            except Exception:
                continue

        return None

    def _format_date_title(self, date: str) -> str:
        """Format a date string as a human-readable title."""
        try:
            dt = datetime.strptime(date, '%Y-%m-%d')
            return dt.strftime('%B %d, %Y')  # e.g., "January 12, 2026"
        except ValueError:
            return date

    def _collect_category_items(self, dates: List[str], category: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Collect items and summaries for a specific category across dates.

        Returns:
            Tuple of (items, summary_entries)
        """
        items = []
        summary_entries = []

        for date in dates:
            # Load category data for items
            path = os.path.join(self.data_dir, date, f'{category}.json')
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # Get category summary
                    cat_summary = data.get('category_summary_html', data.get('category_summary', ''))
                    if cat_summary:
                        # Find first external URL from items for Feedly compatibility
                        first_external_url = None
                        for item in data.get('items', [])[:10]:
                            url = item.get('url', '')
                            if url and self.base_url not in url:
                                first_external_url = url
                                break

                        summary_entries.append({
                            '_is_summary': True,
                            '_feed_date': date,
                            '_feed_category': category,
                            '_external_url': first_external_url,
                            'title': f"{category.capitalize()} Summary: {self._format_date_title(date)}",
                            'summary_html': cat_summary,
                            'url': f"{self.base_url}/?date={date}&category={category}",
                            'published': f"{date}T06:00:00Z",
                            'importance_score': 1000,
                        })

                    for item in data.get('items', []):
                        item['_feed_date'] = date
                        item['_feed_category'] = category
                        items.append(item)
                except Exception as e:
                    logger.warning(f"Failed to load {path}: {e}")

        # Sort items by importance
        items.sort(key=lambda x: -x.get('importance_score', 0))
        return items, summary_entries

    def _generate_category_feed(
        self,
        category: str,
        items: List[Dict[str, Any]],
        summaries: List[Dict[str, Any]],
        limit: Optional[int]
    ) -> None:
        """Generate a category feed with optional limit."""
        # Determine filename
        if limit is None:
            filename = f'{category}-full.xml' if category != 'news' else f'{category}.xml'
            feed_items = items
        else:
            filename = f'{category}-{limit}.xml'
            feed_items = items[:limit]

        # For news, just use news.xml (no size variants)
        if category == 'news' and limit is not None:
            return

        category_title = f"{self.FEED_TITLE} - {category.capitalize()}"
        if limit:
            category_title += f" (Top {limit})"
        elif category != 'news':
            category_title += " (Full)"

        # Interleave entries by date (newest first) so RSS readers display correctly
        # Group items by date
        items_by_date: Dict[str, List[Dict[str, Any]]] = {}
        for item in feed_items:
            date = item.get('_feed_date', '')
            if date not in items_by_date:
                items_by_date[date] = []
            items_by_date[date].append(item)

        # Build summaries lookup by date
        summaries_by_date = {s.get('_feed_date', ''): s for s in summaries}

        # Get all dates, sorted newest first
        all_dates = sorted(set(items_by_date.keys()) | set(summaries_by_date.keys()), reverse=True)

        # Interleave: for each date, add summary then items (sorted by importance)
        all_entries = []
        for date in all_dates:
            if date in summaries_by_date:
                all_entries.append(summaries_by_date[date])
            if date in items_by_date:
                # Sort this date's items by importance
                date_items = sorted(items_by_date[date], key=lambda x: -x.get('importance_score', 0))
                all_entries.extend(date_items)

        feed_xml = self._build_atom_feed(
            items=all_entries,
            feed_id=f"urn:ainews:{category}" + (f":{limit}" if limit else ":full"),
            title=category_title,
            subtitle=f"{category.capitalize()} items from AI News Aggregator",
            feed_url=f"{self.base_url}/data/feeds/{filename}",
            site_url=f"{self.base_url}/?category={category}"
        )

        output_path = os.path.join(self.feeds_dir, filename)
        self._write_feed(output_path, feed_xml)
        logger.info(f"Generated {filename} ({len(summaries)} summaries, {len(feed_items)} items)")

    def _generate_executive_summary_feed(self, dates: List[str]) -> None:
        """
        Generate a feed with only daily executive summaries.

        This is the most concise feed - just one entry per day with the
        executive summary and hero image. No category summaries or items.
        """
        entries = []

        for date in dates:
            summary_path = os.path.join(self.data_dir, date, 'summary.json')
            if not os.path.exists(summary_path):
                continue

            try:
                with open(summary_path, 'r', encoding='utf-8') as f:
                    summary = json.load(f)

                exec_summary = summary.get('executive_summary_html', summary.get('executive_summary', ''))
                hero_image_url = summary.get('hero_image_url', '')

                if exec_summary:
                    # Find first external URL for Feedly compatibility
                    first_external_url = None
                    for category, cat_data in summary.get('categories', {}).items():
                        for item in cat_data.get('top_items', [])[:5]:
                            url = item.get('url', '')
                            if url and self.base_url not in url:
                                first_external_url = url
                                break
                        if first_external_url:
                            break

                    entries.append({
                        '_is_summary': True,
                        '_feed_date': date,
                        '_external_url': first_external_url,
                        '_hero_image_url': hero_image_url,
                        'title': f"Daily Briefing: {self._format_date_title(date)}",
                        'summary_html': exec_summary,
                        'url': f"{self.base_url}/?date={date}",
                        'published': f"{date}T06:00:00Z",
                        'importance_score': 1000,
                    })
            except Exception as e:
                logger.warning(f"Failed to load {summary_path}: {e}")

        feed_xml = self._build_atom_feed(
            items=entries,
            feed_id="urn:ainews:summaries:executive",
            title=f"{self.FEED_TITLE} - Daily Briefings",
            subtitle="Daily executive summaries with hero images",
            feed_url=f"{self.base_url}/data/feeds/summaries-executive.xml",
            site_url=self.base_url
        )

        output_path = os.path.join(self.feeds_dir, 'summaries-executive.xml')
        self._write_feed(output_path, feed_xml)
        logger.info(f"Generated summaries-executive.xml ({len(entries)} entries)")

    def _generate_summary_only_feed(self, dates: List[str], category: str) -> None:
        """
        Generate a feed with only category summary entries (no individual items).

        Args:
            dates: List of dates to include
            category: Category name (news, research, social, reddit)
        """
        entries = []

        for date in dates:
            # Load category data
            path = os.path.join(self.data_dir, date, f'{category}.json')
            if not os.path.exists(path):
                continue

            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                cat_summary = data.get('category_summary_html', data.get('category_summary', ''))
                if cat_summary:
                    # Find first external URL for Feedly compatibility
                    first_external_url = None
                    for item in data.get('items', [])[:10]:
                        url = item.get('url', '')
                        if url and self.base_url not in url:
                            first_external_url = url
                            break

                    entries.append({
                        '_is_summary': True,
                        '_feed_date': date,
                        '_feed_category': category,
                        '_external_url': first_external_url,
                        'title': f"{category.capitalize()} Summary: {self._format_date_title(date)}",
                        'summary_html': cat_summary,
                        'url': f"{self.base_url}/?date={date}&category={category}",
                        'published': f"{date}T06:00:00Z",
                        'importance_score': 1000,
                    })
            except Exception as e:
                logger.warning(f"Failed to load {path}: {e}")

        feed_xml = self._build_atom_feed(
            items=entries,
            feed_id=f"urn:ainews:summaries:{category}",
            title=f"{self.FEED_TITLE} - {category.capitalize()} Summaries",
            subtitle=f"Daily {category} category summaries",
            feed_url=f"{self.base_url}/data/feeds/summaries-{category}.xml",
            site_url=f"{self.base_url}/?category={category}"
        )

        output_path = os.path.join(self.feeds_dir, f'summaries-{category}.xml')
        self._write_feed(output_path, feed_xml)
        logger.info(f"Generated summaries-{category}.xml ({len(entries)} entries)")

    def _generate_all_summaries_feed(self, dates: List[str]) -> None:
        """
        Generate a feed with executive summary + all category summaries.

        For each date, includes:
        1. Executive summary (with hero image)
        2. News summary
        3. Research summary
        4. Social summary
        5. Reddit summary

        No individual items are included.
        """
        all_entries = []

        for date in dates:
            summary_path = os.path.join(self.data_dir, date, 'summary.json')
            if not os.path.exists(summary_path):
                continue

            try:
                with open(summary_path, 'r', encoding='utf-8') as f:
                    summary = json.load(f)

                # Add executive summary first
                exec_summary = summary.get('executive_summary_html', summary.get('executive_summary', ''))
                hero_image_url = summary.get('hero_image_url', '')

                if exec_summary:
                    # Find first external URL for Feedly compatibility
                    first_external_url = None
                    for category, cat_data in summary.get('categories', {}).items():
                        for item in cat_data.get('top_items', [])[:5]:
                            url = item.get('url', '')
                            if url and self.base_url not in url:
                                first_external_url = url
                                break
                        if first_external_url:
                            break

                    all_entries.append({
                        '_is_summary': True,
                        '_feed_date': date,
                        '_external_url': first_external_url,
                        '_hero_image_url': hero_image_url,
                        'title': f"Daily Briefing: {self._format_date_title(date)}",
                        'summary_html': exec_summary,
                        'url': f"{self.base_url}/?date={date}",
                        'published': f"{date}T06:00:00Z",
                        'importance_score': 1000,
                    })

                # Add category summaries
                for category in ['news', 'research', 'social', 'reddit']:
                    cat_path = os.path.join(self.data_dir, date, f'{category}.json')
                    if not os.path.exists(cat_path):
                        continue

                    try:
                        with open(cat_path, 'r', encoding='utf-8') as f:
                            cat_data = json.load(f)

                        cat_summary = cat_data.get('category_summary_html', cat_data.get('category_summary', ''))
                        if cat_summary:
                            # Find first external URL
                            first_external_url = None
                            for item in cat_data.get('items', [])[:10]:
                                url = item.get('url', '')
                                if url and self.base_url not in url:
                                    first_external_url = url
                                    break

                            all_entries.append({
                                '_is_summary': True,
                                '_feed_date': date,
                                '_feed_category': category,
                                '_external_url': first_external_url,
                                'title': f"{category.capitalize()} Summary: {self._format_date_title(date)}",
                                'summary_html': cat_summary,
                                'url': f"{self.base_url}/?date={date}&category={category}",
                                'published': f"{date}T06:00:00Z",
                                'importance_score': 999 - ['news', 'research', 'social', 'reddit'].index(category),
                            })
                    except Exception as e:
                        logger.warning(f"Failed to load {cat_path}: {e}")

            except Exception as e:
                logger.warning(f"Failed to load {summary_path}: {e}")

        feed_xml = self._build_atom_feed(
            items=all_entries,
            feed_id="urn:ainews:summaries:all",
            title=f"{self.FEED_TITLE} - All Summaries",
            subtitle="Executive summary plus all category summaries",
            feed_url=f"{self.base_url}/data/feeds/summaries.xml",
            site_url=self.base_url
        )

        output_path = os.path.join(self.feeds_dir, 'summaries.xml')
        self._write_feed(output_path, feed_xml)
        exec_count = len([e for e in all_entries if not e.get('_feed_category')])
        cat_count = len([e for e in all_entries if e.get('_feed_category')])
        logger.info(f"Generated summaries.xml ({exec_count} executive, {cat_count} category summaries)")

    def _build_atom_feed(
        self,
        items: List[Dict[str, Any]],
        feed_id: str,
        title: str,
        subtitle: str,
        feed_url: str,
        site_url: str
    ) -> str:
        """Build Atom 1.0 XML feed."""
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        entries = []
        for item in items:
            entry = self._build_atom_entry(item)
            if entry:
                entries.append(entry)

        feed = f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <title>{escape(title)}</title>
  <subtitle>{escape(subtitle)}</subtitle>
  <link href="{escape(site_url)}" rel="alternate" type="text/html"/>
  <link href="{escape(feed_url)}" rel="self" type="application/atom+xml"/>
  <id>{escape(feed_id)}</id>
  <updated>{now}</updated>
  <icon>{self.base_url}/assets/logo.webp</icon>
  <author>
    <name>AATF AI News Aggregator</name>
    <uri>{self.base_url}</uri>
  </author>
  <generator>AATF AI News Aggregator</generator>
{''.join(entries)}
</feed>'''

        return feed

    def _build_atom_entry(self, item: Dict[str, Any]) -> Optional[str]:
        """Build a single Atom entry."""
        date = item.get('_feed_date', '')
        category = item.get('_feed_category', '')

        # Handle summary entries (daily briefings)
        if item.get('_is_summary'):
            return self._build_summary_entry(item, date, category)

        item_id = item.get('id', '')
        if not item_id:
            return None

        # Get and clean title (strip any HTML, escape for XML)
        title = item.get('title', 'Untitled')
        title = strip_html_from_text(title)

        url = item.get('url', '')
        author = item.get('author', '')
        importance = item.get('importance_score', 50)
        summary_html = item.get('summary_html', item.get('summary', ''))
        summary_html = make_urls_absolute(summary_html, self.base_url)
        themes = item.get('themes', [])

        # Build site URL for this item
        site_item_url = f"{self.base_url}/?date={date}&category={category}#item-{item_id}"

        # Normalize published date to report date
        # Spread items from 00:00 to 04:00 based on importance (0-100)
        # Higher importance = later time = appears higher in Feedly
        # This ensures all items for a report are grouped under the same date
        hour = int((importance / 100) * 4)  # 0-4 hours
        minute = int(((importance / 100) * 4 % 1) * 60)  # 0-59 minutes
        published = f"{date}T{hour:02d}:{minute:02d}:00Z"

        # Build category elements
        category_elements = ''.join(
            f'\n    <category term="{escape(theme)}"/>'
            for theme in themes[:5]  # Limit to 5 themes
        )

        # Author element (optional)
        author_element = ''
        if author:
            # Clean author name of any HTML
            clean_author = strip_html_from_text(author)
            author_element = f'\n    <author><name>{escape(clean_author)}</name></author>'

        # Encode URLs for valid IRI (handles special chars like Erdős)
        url_encoded = encode_url_for_xml(url)
        site_url_encoded = encode_url_for_xml(site_item_url)

        entry = f'''
  <entry>
    <id>urn:ainews:{date}:{category}:{item_id}</id>
    <title>{escape(title)}</title>
    <link href="{url_encoded}" rel="alternate" type="text/html"/>
    <link href="{site_url_encoded}" rel="related" type="text/html"/>
    <published>{published}</published>
    <updated>{published}</updated>{author_element}
    <summary type="html"><![CDATA[{summary_html}]]></summary>{category_elements}
  </entry>'''

        return entry

    def _build_summary_entry(self, item: Dict[str, Any], date: str, category: str) -> str:
        """Build an Atom entry for a daily summary (executive or category)."""
        title = item.get('title', f'Daily Summary: {date}')
        site_url = item.get('url', f"{self.base_url}/?date={date}")
        published = item.get('published', f"{date}T06:00:00Z")
        summary_html = item.get('summary_html', '')
        summary_html = make_urls_absolute(summary_html, self.base_url)
        hero_image_url = item.get('_hero_image_url', '')

        # Prepend hero image to summary HTML for readers that support HTML content
        if hero_image_url:
            # Remove query params from hero URL for cleaner display
            hero_url_clean = hero_image_url.split('?')[0]
            hero_full_url = f"{self.base_url}{hero_url_clean}"
            hero_img_html = f'<p><img src="{hero_full_url}" alt="Daily briefing hero image" style="max-width:100%;height:auto;border-radius:8px;margin-bottom:16px;"/></p>\n'
            summary_html = hero_img_html + summary_html

        # Use external URL from first top item to bypass Feedly's same-domain filtering
        # (Feedly filters entries where alternate link matches the feed's domain)
        external_url = item.get('_external_url')
        alternate_url = external_url or site_url

        # Generate a unique ID for the summary
        summary_type = 'category' if category else 'executive'
        entry_id = f"urn:ainews:{date}:{summary_type}-summary" + (f":{category}" if category else "")

        alternate_url_encoded = encode_url_for_xml(alternate_url)
        site_url_encoded = encode_url_for_xml(site_url)

        # Build links - alternate points to external (for Feedly), related points to site
        links = f'<link href="{alternate_url_encoded}" rel="alternate" type="text/html"/>'
        if external_url:
            links += f'\n    <link href="{site_url_encoded}" rel="related" type="text/html"/>'

        # Add media:thumbnail for RSS readers that support Media RSS (e.g., Feedly)
        media_element = ''
        if hero_image_url:
            hero_url_clean = hero_image_url.split('?')[0]
            hero_full_url = f"{self.base_url}{hero_url_clean}"
            media_element = f'\n    <media:thumbnail url="{encode_url_for_xml(hero_full_url)}"/>'

        entry = f'''
  <entry>
    <id>{entry_id}</id>
    <title>{escape(title)}</title>
    {links}
    <published>{published}</published>
    <updated>{published}</updated>
    <author><name>AATF AI News Aggregator</name></author>
    <summary type="html"><![CDATA[{summary_html}]]></summary>
    <category term="daily-summary"/>{media_element}
  </entry>'''

        return entry

    def _write_feed(self, path: str, content: str) -> None:
        """Write feed content to file."""
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _file_size_kb(self, path: str) -> float:
        """Get file size in KB."""
        if os.path.exists(path):
            return round(os.path.getsize(path) / 1024, 1)
        return 0.0


if __name__ == '__main__':
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python feed_generator.py <output_dir> [rolling_window_days]")
        sys.exit(1)

    output_dir = sys.argv[1]
    rolling_window = int(sys.argv[2]) if len(sys.argv) > 2 else 7

    generator = FeedGenerator(output_dir, rolling_window_days=rolling_window)
    generator.generate_feeds()
