#!/usr/bin/env python3
"""
JSON Generator for SPA Frontend

Generates JSON data files from OrchestratorResult for the Svelte SPA to consume.
Outputs:
  - index.json: Date manifest with all available dates
  - {date}/summary.json: Executive summary and top items per category
  - {date}/{category}.json: Full items for each category
"""

import json
import os
import re
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

import nh3

logger = logging.getLogger(__name__)

# HTML sanitization allowlist for XSS prevention
ALLOWED_TAGS = {'a', 'strong', 'em', 'p', 'ul', 'li', 'h2', 'h3', 'h4', 'br'}
ALLOWED_ATTRIBUTES = {
    'a': {'href', 'class', 'target'},  # 'rel' handled by nh3's link_rel parameter
}


def get_arxiv_notice(report_date: str) -> Optional[Dict[str, str]]:
    """
    Return notice for research page based on day of week.

    arXiv publishing schedule:
    - Announcements happen Sun-Thu at ~8PM ET
    - No announcements Friday or Saturday night
    - Saturday/Sunday reports have 0 arXiv papers (correct behavior)
    - Monday announcements cover all weekend submissions (Fri-Sun)

    Args:
        report_date: Date string in YYYY-MM-DD format

    Returns:
        Notice dict with type, title, message or None for regular weekdays
    """
    try:
        date_obj = datetime.strptime(report_date, '%Y-%m-%d')
        day_of_week = date_obj.strftime('%A')

        if day_of_week in ('Saturday', 'Sunday'):
            return {
                'type': 'info',
                'title': 'Weekend Edition',
                'message': 'arXiv papers are not collected on weekends. Any weekend papers will be included in Monday\'s report.'
            }
        elif day_of_week == 'Monday':
            return {
                'type': 'info',
                'title': 'Monday Edition',
                'message': "Today's arXiv papers cover submissions from the entire weekend (Friday through Sunday) due to arXiv's publishing schedule."
            }
    except ValueError:
        logger.warning(f"Invalid date format for arxiv notice: {report_date}")

    return None


def get_news_notice(report_date: str) -> Optional[Dict[str, str]]:
    """
    Return notice for news page for dates before collection began.

    News collection started on January 6th, 2026. Earlier dates may have
    incomplete or no news data.

    Args:
        report_date: Date string in YYYY-MM-DD format

    Returns:
        Notice dict with type, title, message or None for dates after collection started
    """
    NEWS_COLLECTION_START = '2026-01-06'
    try:
        if report_date < NEWS_COLLECTION_START:
            return {
                'type': 'info',
                'title': 'Limited Coverage',
                'message': 'News collection began on January 6th, 2026. Earlier dates may have incomplete or no news data.'
            }
    except ValueError:
        logger.warning(f"Invalid date format for news notice: {report_date}")

    return None


class JSONGenerator:
    """Generates JSON data files for the SPA frontend."""

    def __init__(self, output_dir: str):
        """
        Initialize JSON generator.

        Args:
            output_dir: Base output directory (typically web/)
        """
        self.output_dir = output_dir
        self.data_dir = os.path.join(output_dir, 'data')

        os.makedirs(self.data_dir, exist_ok=True)

        logger.info(f"Initialized JSON generator with output dir: {self.data_dir}")

    def generate_from_orchestrator_result(self, result: Dict[str, Any]) -> None:
        """
        Generate all JSON files from orchestrator result.

        Args:
            result: OrchestratorResult as dictionary
        """
        date = result.get('date', '')
        if not date:
            logger.error("No date in orchestrator result")
            return

        logger.info(f"Generating JSON data for date: {date}")

        # Create date-specific directory
        date_dir = os.path.join(self.data_dir, date)
        os.makedirs(date_dir, exist_ok=True)

        # Generate files
        self._generate_summary_json(date_dir, result)
        self._generate_category_files(date_dir, result)
        self._copy_hero_image(date_dir, date)
        self._update_date_index(result)

        logger.info(f"JSON generation complete for {date}")

    def _generate_summary_json(self, date_dir: str, result: Dict[str, Any]) -> None:
        """Generate summary.json with executive summary and top items."""
        category_reports = result.get('category_reports', {})

        # Build category summaries with only top items
        categories = {}
        for category, report in category_reports.items():
            top_items = report.get('top_items', [])[:10]
            category_summary = report.get('category_summary', '')
            categories[category] = {
                'count': len(report.get('all_items', [])),
                'category_summary': category_summary,
                'category_summary_html': self._markdown_to_html(category_summary),
                'themes': report.get('themes', []),
                'top_items': self._simplify_items(top_items)
            }

        executive_summary = result.get('executive_summary', '')
        collection_status = result.get('collection_status', {})

        summary = {
            'date': result.get('date', ''),
            'coverage_date': result.get('coverage_date', ''),
            'coverage_start': result.get('coverage_start', ''),
            'coverage_end': result.get('coverage_end', ''),
            'executive_summary': executive_summary,
            'executive_summary_html': self._markdown_to_html(executive_summary),
            'top_topics': result.get('top_topics', []),
            'total_items_collected': result.get('total_items_collected', 0),
            'total_items_analyzed': result.get('total_items_analyzed', 0),
            'collection_status': self._format_collection_status(collection_status),
            'hero_image_url': result.get('hero_image_url'),
            'hero_image_prompt': result.get('hero_image_prompt'),
            'generated_at': result.get('generated_at', datetime.now().isoformat()),
            'categories': categories
        }

        output_path = os.path.join(date_dir, 'summary.json')
        self._write_json(output_path, summary)
        logger.info(f"Generated summary.json ({self._file_size_kb(output_path)} KB)")

    def _generate_category_files(self, date_dir: str, result: Dict[str, Any]) -> None:
        """Generate individual category JSON files."""
        category_reports = result.get('category_reports', {})
        report_date = result.get('date', '')

        for category, report in category_reports.items():
            all_items = report.get('all_items', [])
            category_summary = report.get('category_summary', '')

            category_data = {
                'category': category,
                'date': report_date,
                'category_summary': category_summary,
                'category_summary_html': self._markdown_to_html(category_summary),
                'themes': report.get('themes', []),
                'total_items': len(all_items),
                'items': self._simplify_items(all_items)
            }

            # Add notice for research category on weekends/Mondays
            if category == 'research':
                notice = get_arxiv_notice(report_date)
                if notice:
                    category_data['notice'] = notice

            # Add notice for news category before collection started
            if category == 'news':
                notice = get_news_notice(report_date)
                if notice:
                    category_data['notice'] = notice

            output_path = os.path.join(date_dir, f'{category}.json')
            self._write_json(output_path, category_data)
            logger.info(f"Generated {category}.json ({self._file_size_kb(output_path)} KB, {len(all_items)} items)")

    def _copy_hero_image(self, date_dir: str, date: str) -> None:
        """Copy hero image - no-op since we now serve from single data dir."""
        pass

    def _update_date_index(self, result: Dict[str, Any]) -> None:
        """Update the master index.json with this date."""
        index_path = os.path.join(self.data_dir, 'index.json')

        # Load existing index or create new
        if os.path.exists(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                index = json.load(f)
        else:
            index = {'version': '1.0', 'dates': []}

        date = result.get('date', '')
        category_reports = result.get('category_reports', {})

        # Build date entry
        date_entry = {
            'date': date,
            'total_items': result.get('total_items_analyzed', 0),
            'categories': {}
        }

        for category, report in category_reports.items():
            all_items = report.get('all_items', [])
            category_file = os.path.join(self.data_dir, date, f'{category}.json')
            file_size = os.path.getsize(category_file) if os.path.exists(category_file) else 0

            date_entry['categories'][category] = {
                'count': len(all_items),
                'file_size': file_size
            }

        # Update or add date entry
        existing_idx = next(
            (i for i, d in enumerate(index['dates']) if d['date'] == date),
            None
        )
        if existing_idx is not None:
            index['dates'][existing_idx] = date_entry
        else:
            index['dates'].append(date_entry)

        # Sort by date descending
        index['dates'].sort(key=lambda x: x['date'], reverse=True)
        index['latestDate'] = index['dates'][0]['date'] if index['dates'] else None
        index['generatedAt'] = datetime.now().isoformat()
        index['totalDates'] = len(index['dates'])

        self._write_json(index_path, index)
        logger.info(f"Updated index.json with {len(index['dates'])} dates")

    def _simplify_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Simplify item structure for frontend consumption.

        Flattens the nested item structure, deduplicates by ID, and removes
        extended thinking to reduce file size.
        """
        simplified = []
        seen_ids = set()  # Track seen IDs for deduplication

        for item in items:
            # Handle both nested (item.item) and flat structures
            if 'item' in item and isinstance(item['item'], dict):
                base = item['item']
                simplified_item = {
                    'id': base.get('id', ''),
                    'title': base.get('title', ''),
                    'content': base.get('content', ''),
                    'url': base.get('url', ''),
                    'author': base.get('author', ''),
                    'published': base.get('published', ''),
                    'source': base.get('source', ''),
                    'source_type': base.get('source_type', ''),
                    'tags': base.get('tags', []),
                    'summary': item.get('summary', ''),
                    'importance_score': item.get('importance_score', 50),
                    'reasoning': item.get('reasoning', ''),
                    'themes': item.get('themes', []),
                    'continuation': item.get('continuation')  # Story continuation info
                }
            else:
                # Flat structure
                simplified_item = {
                    'id': item.get('id', ''),
                    'title': item.get('title', ''),
                    'content': item.get('content', ''),
                    'url': item.get('url', ''),
                    'author': item.get('author', ''),
                    'published': item.get('published', ''),
                    'source': item.get('source', ''),
                    'source_type': item.get('source_type', ''),
                    'tags': item.get('tags', []),
                    'summary': item.get('summary', item.get('llm_summary', '')),
                    'importance_score': item.get('importance_score', 50),
                    'reasoning': item.get('reasoning', ''),
                    'themes': item.get('themes', []),
                    'continuation': item.get('continuation')  # Story continuation info
                }

            # Convert summary and content to HTML for frontend rendering
            simplified_item['summary_html'] = self._markdown_to_html(simplified_item.get('summary', ''))
            simplified_item['content_html'] = self._markdown_to_html(simplified_item.get('content', ''))

            # Skip duplicates by ID
            item_id = simplified_item.get('id', '')
            if item_id:
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

            simplified.append(simplified_item)

        return simplified

    def _write_json(self, path: str, data: Any) -> None:
        """Write JSON to file with consistent formatting."""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _file_size_kb(self, path: str) -> float:
        """Get file size in KB."""
        if os.path.exists(path):
            return round(os.path.getsize(path) / 1024, 1)
        return 0.0

    def _format_collection_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format collection status for frontend display.

        Returns structured data including:
        - overall: 'success' | 'partial' | 'failed'
        - sources: list of source statuses
        - warnings: human-readable warning messages
        """
        if not status:
            return {'overall': 'unknown', 'sources': [], 'warnings': []}

        sources = []
        warnings = []
        has_failures = False
        has_partial = False

        # Main sources (not sub-platforms)
        main_sources = ['news', 'research', 'social', 'reddit']

        for source in main_sources:
            source_status = status.get(source, {})
            status_val = source_status.get('status', 'unknown')
            count = source_status.get('count', 0)
            error = source_status.get('error')

            sources.append({
                'name': source,
                'display_name': source.capitalize(),
                'status': status_val,
                'count': count,
                'error': error
            })

            if status_val == 'failed':
                has_failures = True
                warnings.append(f"{source.capitalize()} collection failed: {error}")
            elif status_val == 'partial':
                has_partial = True
                warnings.append(f"{source.capitalize()} had partial collection: {error}")

        # Add social platform breakdown as sub-sources
        social_platforms = []
        for key, val in status.items():
            if key.startswith('social_'):
                platform_name = key.replace('social_', '')
                status_val = val.get('status', 'unknown')
                count = val.get('count', 0)
                error = val.get('error')

                social_platforms.append({
                    'name': platform_name,
                    'display_name': platform_name.capitalize(),
                    'status': status_val,
                    'count': count,
                    'error': error
                })

                if status_val == 'failed':
                    has_failures = True
                    warnings.append(f"{platform_name.capitalize()} collection failed: {error}")
                elif status_val == 'partial':
                    has_partial = True
                    warnings.append(f"{platform_name.capitalize()} had partial collection: {error}")

        # Determine overall status
        if has_failures:
            overall = 'partial' if any(s['status'] == 'success' for s in sources) else 'failed'
        elif has_partial:
            overall = 'partial'
        else:
            overall = 'success'

        return {
            'overall': overall,
            'sources': sources,
            'social_platforms': social_platforms if social_platforms else None,
            'warnings': warnings
        }

    def _sanitize_html(self, html: str) -> str:
        """
        Sanitize HTML to prevent XSS attacks.

        Uses nh3 (a Rust-based HTML sanitizer) with an allowlist approach.
        Only explicitly permitted tags and attributes are allowed.
        Blocks javascript:, data: URLs.
        """
        if not html:
            return ''

        return nh3.clean(
            html,
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            link_rel="noopener noreferrer",
            url_schemes={'http', 'https', 'mailto'},
        )

    def _markdown_to_html(self, text: str) -> str:
        """
        Convert markdown formatting to HTML for summaries.

        Handles:
        - [text](url) -> <a> (internal vs external links)
        - **bold** -> <strong>
        - #### headers -> <h4>
        - - bullet lists -> <ul><li>
        - Paragraphs (double newlines)

        Output is sanitized to prevent XSS attacks.
        """
        if not text:
            return ''

        # Convert markdown links FIRST (before bold, since links may be inside bold)
        # Differentiate internal vs external links
        def link_replacer(match):
            link_text, url = match.groups()
            if url.startswith('/') or url.startswith('#'):
                # Internal link - no target="_blank"
                return f'<a href="{url}" class="internal-link">{link_text}</a>'
            else:
                # External link - opens in new tab
                return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{link_text}</a>'

        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_replacer, text)

        # Convert **bold** to <strong>
        text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)

        # Convert markdown headers to HTML (h2-h4)
        text = re.sub(r'^####\s+(.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
        text = re.sub(r'^###\s+(.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
        text = re.sub(r'^##\s+(.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)

        # Convert bullet lists to <ul><li>
        lines = text.split('\n')
        in_list = False
        result = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('- '):
                if not in_list:
                    result.append('<ul>')
                    in_list = True
                # Handle the bullet content (may contain HTML like <strong>)
                bullet_content = stripped[2:]
                result.append(f'<li>{bullet_content}</li>')
            else:
                if in_list:
                    result.append('</ul>')
                    in_list = False
                if stripped:
                    # Check if it's already an HTML tag (like h4)
                    if stripped.startswith(('<h2>', '<h3>', '<h4>', '<ul>')):
                        result.append(stripped)
                    else:
                        result.append(f'<p>{stripped}</p>')

        # Close any open list
        if in_list:
            result.append('</ul>')

        html = '\n'.join(result)

        # Sanitize before returning to prevent XSS
        return self._sanitize_html(html)

    def get_all_dates(self) -> List[str]:
        """Get list of all dates with data (for search indexing)."""
        index_path = os.path.join(self.data_dir, 'index.json')
        if os.path.exists(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                index = json.load(f)
                return [d['date'] for d in index.get('dates', [])]
        return []

    def get_date_data(self, date: str) -> Optional[Dict[str, Any]]:
        """Load all data for a specific date (for search indexing)."""
        date_dir = os.path.join(self.data_dir, date)
        if not os.path.exists(date_dir):
            return None

        data = {'date': date, 'categories': {}}

        for category in ['news', 'research', 'social', 'reddit']:
            category_path = os.path.join(date_dir, f'{category}.json')
            if os.path.exists(category_path):
                with open(category_path, 'r', encoding='utf-8') as f:
                    data['categories'][category] = json.load(f)

        return data


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 3:
        print("Usage: python json_generator.py <result_file> <output_dir>")
        sys.exit(1)

    result_file = sys.argv[1]
    output_dir = sys.argv[2]

    logging.basicConfig(level=logging.INFO)

    with open(result_file, 'r', encoding='utf-8') as f:
        result = json.load(f)

    generator = JSONGenerator(output_dir)
    generator.generate_from_orchestrator_result(result)
