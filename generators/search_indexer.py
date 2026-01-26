#!/usr/bin/env python3
"""
Search Indexer for SPA Frontend

Builds a Lunr.js-compatible search index from JSON data files.
Outputs:
  - search-index.json: Pre-built Lunr.js index
  - search-documents.json: Document lookup for displaying results
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
from pathlib import Path

logger = logging.getLogger(__name__)

# Check if lunr is available (optional dependency)
try:
    from lunr import lunr
    from lunr.index import Index
    LUNR_AVAILABLE = True
except ImportError:
    LUNR_AVAILABLE = False
    logger.warning("lunr package not installed. Search indexing will be limited.")


class SearchIndexer:
    """Builds search indexes for the SPA frontend."""

    def __init__(self, output_dir: str, rolling_window_days: int = 30):
        """
        Initialize search indexer.

        Args:
            output_dir: Base output directory (typically web/)
            rolling_window_days: Number of days to include in search index
        """
        self.output_dir = output_dir
        self.data_dir = os.path.join(output_dir, 'data')
        self.rolling_window_days = rolling_window_days
        logger.info(f"Initialized search indexer with {rolling_window_days}-day rolling window")

    def update_index(self, result: Optional[Dict[str, Any]] = None) -> None:
        """
        Update search index with new data.

        Args:
            result: Optional new OrchestratorResult to include
        """
        if not LUNR_AVAILABLE:
            logger.warning("Lunr not available, generating simple document index only")
            self._generate_simple_index()
            return

        # Get all dates within rolling window
        dates = self._get_dates_in_window()
        logger.info(f"Building search index for {len(dates)} dates")

        # Collect all documents
        documents = []
        for date in dates:
            date_docs = self._extract_documents_for_date(date)
            documents.extend(date_docs)

        logger.info(f"Indexing {len(documents)} documents")

        if not documents:
            logger.warning("No documents to index")
            return

        # Build Lunr index
        try:
            idx = lunr(
                ref='id',
                fields=[
                    {'field_name': 'title', 'boost': 10},
                    {'field_name': 'summary', 'boost': 5},
                    {'field_name': 'source', 'boost': 2}
                ],
                documents=documents
            )

            # Serialize index
            index_path = os.path.join(self.data_dir, 'search-index.json')
            self._write_json(index_path, idx.serialize())

            logger.info(f"Generated search-index.json ({self._file_size_kb(index_path)} KB)")

        except Exception as e:
            logger.error(f"Failed to build Lunr index: {e}")
            self._generate_simple_index()
            return

        # Generate document lookup
        self._generate_document_lookup(documents)

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
                    doc = {
                        'id': f"{date}:{category}:{item.get('id', '')[:8]}",
                        'title': item.get('title', ''),
                        'summary': item.get('summary', ''),
                        'source': item.get('source', ''),
                        'category': category,
                        'date': date,
                        'url': item.get('url', ''),
                        'importance': item.get('importance_score', 50),
                        'item_id': item.get('id', '')
                    }
                    documents.append(doc)

            except Exception as e:
                logger.warning(f"Failed to load {category_path}: {e}")

        return documents

    def _generate_document_lookup(self, documents: List[Dict[str, Any]]) -> None:
        """Generate document lookup JSON for displaying search results."""
        lookup = {}

        for doc in documents:
            lookup[doc['id']] = {
                'id': doc['item_id'],
                'title': doc['title'],
                'summary': doc['summary'][:200] + '...' if len(doc.get('summary', '')) > 200 else doc.get('summary', ''),
                'url': doc['url'],
                'date': doc['date'],
                'category': doc['category'],
                'source': doc['source'],
                'importance': doc['importance']
            }

        output_path = os.path.join(self.data_dir, 'search-documents.json')
        self._write_json(output_path, lookup)

        logger.info(f"Generated search-documents.json ({self._file_size_kb(output_path)} KB)")

    def _generate_simple_index(self) -> None:
        """
        Generate a simple document index without Lunr.

        This allows basic search functionality in the frontend using
        simple string matching when Lunr is not available.
        """
        dates = self._get_dates_in_window()
        documents = []

        for date in dates:
            date_docs = self._extract_documents_for_date(date)
            documents.extend(date_docs)

        # Save as searchable documents
        self._generate_document_lookup(documents)

        # Also save a simple searchable text index
        simple_index = []
        for doc in documents:
            simple_index.append({
                'id': doc['id'],
                'searchText': f"{doc['title']} {doc['summary']} {doc['source']}".lower()
            })

        output_path = os.path.join(self.data_dir, 'search-simple.json')
        self._write_json(output_path, simple_index)

        logger.info(f"Generated search-simple.json ({self._file_size_kb(output_path)} KB)")

    def _write_json(self, path: str, data: Any) -> None:
        """Write JSON to file with consistent formatting."""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _file_size_kb(self, path: str) -> float:
        """Get file size in KB."""
        if os.path.exists(path):
            return round(os.path.getsize(path) / 1024, 1)
        return 0.0

    def rebuild_full_index(self) -> None:
        """Rebuild the complete search index from all available data."""
        logger.info("Rebuilding full search index...")
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
