#!/usr/bin/env python3
"""
Search Corpus Builder for SPA Frontend

Builds a compact search corpus from JSON data files. The frontend loads this
single file and builds a MiniSearch index in-browser (in a Web Worker), so no
serialized index is shipped.

Output:
  - search-corpus.json: Array of searchable/displayable documents (30-day window)
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

SUMMARY_CAP = 200


class SearchIndexer:
    """Builds the search corpus for the SPA frontend."""

    def __init__(self, output_dir: str, rolling_window_days: int = 30):
        """
        Initialize search indexer.

        Args:
            output_dir: Base output directory (typically web/)
            rolling_window_days: Number of days to include in the corpus
        """
        self.output_dir = output_dir
        self.data_dir = os.path.join(output_dir, 'data')
        self.rolling_window_days = rolling_window_days
        logger.info(f"Initialized search indexer with {rolling_window_days}-day rolling window")

    def update_index(self, result: Optional[Dict[str, Any]] = None) -> None:
        """
        Rebuild the search corpus from data within the rolling window.

        Args:
            result: Unused; accepted for backward-compatible call sites.
        """
        dates = self._get_dates_in_window()
        logger.info(f"Building search corpus for {len(dates)} dates")

        documents: List[Dict[str, Any]] = []
        for date in dates:
            documents.extend(self._extract_documents_for_date(date))

        logger.info(f"Corpus contains {len(documents)} documents")

        if not documents:
            logger.warning("No documents to index")

        output_path = os.path.join(self.data_dir, 'search-corpus.json')
        self._write_json(output_path, documents)
        logger.info(f"Generated search-corpus.json ({self._file_size_kb(output_path)} KB)")

        self._cleanup_legacy_outputs()

    def _get_dates_in_window(self) -> List[str]:
        """Get dates within the rolling window from index.json."""
        index_path = os.path.join(self.data_dir, 'index.json')
        if not os.path.exists(index_path):
            return []

        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)

        cutoff = datetime.now() - timedelta(days=self.rolling_window_days)
        cutoff_str = cutoff.strftime('%Y-%m-%d')

        dates = [
            d['date'] for d in index.get('dates', [])
            if d['date'] >= cutoff_str
        ]

        return sorted(dates, reverse=True)

    def _extract_documents_for_date(self, date: str) -> List[Dict[str, Any]]:
        """Extract searchable documents for a specific date."""
        documents = []

        for category in ['news', 'research', 'social', 'reddit']:
            category_path = os.path.join(self.data_dir, date, f'{category}.json')
            if not os.path.exists(category_path):
                continue

            try:
                with open(category_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                for item in data.get('items', []):
                    item_id = item.get('id', '')
                    summary = item.get('summary', '')
                    if len(summary) > SUMMARY_CAP:
                        summary = summary[:SUMMARY_CAP] + '...'

                    documents.append({
                        # Unique MiniSearch ref (item ids can recur across dates).
                        'ref': f"{date}:{category}:{item_id[:8]}",
                        # Display/navigation fields consumed by the frontend.
                        'id': item_id,
                        'title': item.get('title', ''),
                        'summary': summary,
                        'source': item.get('source', ''),
                        'category': category,
                        'date': date,
                        'url': item.get('url', ''),
                        'importance': item.get('importance_score', 50),
                    })

            except Exception as e:
                logger.warning(f"Failed to load {category_path}: {e}")

        return documents

    def _cleanup_legacy_outputs(self) -> None:
        """Remove obsolete Lunr-era output files if present."""
        for legacy in ('search-index.json', 'search-documents.json', 'search-simple.json'):
            path = os.path.join(self.data_dir, legacy)
            if os.path.exists(path):
                try:
                    os.remove(path)
                    logger.info(f"Removed legacy {legacy}")
                except OSError as e:
                    logger.warning(f"Could not remove legacy {legacy}: {e}")

    def _write_json(self, path: str, data: Any) -> None:
        """Write JSON to file. Compact separators keep the corpus small."""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

    def _file_size_kb(self, path: str) -> float:
        """Get file size in KB."""
        if os.path.exists(path):
            return round(os.path.getsize(path) / 1024, 1)
        return 0.0

    def rebuild_full_index(self) -> None:
        """Rebuild the complete search corpus from all available data."""
        logger.info("Rebuilding full search corpus...")
        self.update_index()


if __name__ == '__main__':
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python search_indexer.py <output_dir> [rolling_window_days]")
        sys.exit(1)

    output_dir = sys.argv[1]
    rolling_window = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    indexer = SearchIndexer(output_dir, rolling_window_days=rolling_window)
    indexer.rebuild_full_index()
