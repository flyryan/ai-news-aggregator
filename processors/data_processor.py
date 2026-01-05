#!/usr/bin/env python3
"""
Data Processor
Normalizes, deduplicates, and enriches collected data from all sources.
"""

import json
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Set
import logging
from collections import defaultdict
import re
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DataProcessor:
    """Processes and normalizes collected data."""
    
    def __init__(self):
        """Initialize data processor."""
        self.seen_ids: Set[str] = set()
        self.seen_urls: Set[str] = set()
        self.url_to_items: Dict[str, List[Dict]] = defaultdict(list)
        
    def _normalize_url(self, url: str) -> str:
        """Normalize URL for comparison."""
        if not url:
            return ""
        
        try:
            # Remove query parameters and fragments
            parsed = urlparse(url)
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            # Remove trailing slash
            normalized = normalized.rstrip('/')
            return normalized.lower()
        except:
            return url.lower()
    
    def _calculate_content_similarity(self, text1: str, text2: str) -> float:
        """Calculate simple similarity between two text strings."""
        if not text1 or not text2:
            return 0.0
        
        # Simple word-based similarity
        words1 = set(re.findall(r'\w+', text1.lower()))
        words2 = set(re.findall(r'\w+', text2.lower()))
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
    
    def _extract_keywords(self, text: str, top_n: int = 10) -> List[str]:
        """Extract keywords from text (simple frequency-based)."""
        if not text:
            return []
        
        # Common stop words to filter out
        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
            'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'could', 'should', 'may', 'might', 'can', 'this', 'that', 'these',
            'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'what', 'which',
            'who', 'when', 'where', 'why', 'how', 'all', 'each', 'every', 'both',
            'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
            'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just'
        }
        
        # Extract words
        words = re.findall(r'\b[a-z]{3,}\b', text.lower())
        
        # Count frequencies
        word_freq = defaultdict(int)
        for word in words:
            if word not in stop_words:
                word_freq[word] += 1
        
        # Get top N
        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [word for word, freq in top_words]
    
    def _normalize_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a single item to standard format."""
        # Determine source type
        source_type = item.get('source_type', 'unknown')
        
        # Create normalized item
        normalized = {
            'id': item.get('id', ''),
            'title': '',
            'content': '',
            'url': '',
            'author': '',
            'published': item.get('published', datetime.now().isoformat()),
            'source': '',
            'source_type': source_type,
            'tags': [],
            'metadata': {},
            'collected_at': item.get('collected_at', datetime.now().isoformat())
        }
        
        # Normalize based on source type
        if source_type == 'rss':
            normalized['title'] = item.get('title', '')
            normalized['content'] = item.get('content', '') or item.get('summary', '')
            normalized['url'] = item.get('url', '')
            normalized['author'] = item.get('author', '')
            normalized['source'] = item.get('source', '')
            normalized['tags'] = item.get('tags', [])
            
        elif source_type == 'arxiv':
            normalized['title'] = item.get('title', '')
            normalized['content'] = item.get('abstract', '')
            normalized['url'] = item.get('url', '')
            normalized['author'] = ', '.join(item.get('authors', []))
            normalized['source'] = f"arXiv ({item.get('category_name', '')})"
            normalized['tags'] = [item.get('category', '')]
            normalized['metadata'] = {
                'arxiv_id': item.get('arxiv_id', ''),
                'pdf_url': item.get('pdf_url', ''),
                'category': item.get('category', '')
            }
            
        elif source_type == 'twitter':
            normalized['title'] = item.get('content', '')[:100] + '...'
            normalized['content'] = item.get('content', '')
            normalized['url'] = item.get('url', '')
            normalized['author'] = f"@{item.get('author', '')}"
            normalized['source'] = 'Twitter'
            normalized['metadata'] = {
                'platform_id': item.get('platform_id', ''),
                'engagement': item.get('engagement', {})
            }
            
        elif source_type == 'reddit':
            normalized['title'] = item.get('title', '')
            normalized['content'] = item.get('content', '')
            normalized['url'] = item.get('url', '')
            normalized['author'] = f"u/{item.get('author', '')}"
            normalized['source'] = f"r/{item.get('subreddit', '')}"
            normalized['metadata'] = {
                'platform_id': item.get('platform_id', ''),
                'subreddit': item.get('subreddit', ''),
                'engagement': item.get('engagement', {})
            }

        elif source_type == 'bluesky':
            content = item.get('content', '')
            normalized['title'] = content[:100] + '...' if len(content) > 100 else content
            normalized['content'] = content
            normalized['url'] = item.get('url', '')
            normalized['author'] = f"@{item.get('author', '')}"
            normalized['source'] = 'Bluesky'
            normalized['metadata'] = {
                'platform_id': item.get('platform_id', ''),
                'author_display_name': item.get('author_display_name', ''),
                'engagement': item.get('engagement', {})
            }

        elif source_type == 'mastodon':
            content = item.get('content', '')
            normalized['title'] = content[:100] + '...' if len(content) > 100 else content
            normalized['content'] = content
            normalized['url'] = item.get('url', '')
            normalized['author'] = f"@{item.get('author', '')}"
            normalized['source'] = f"Mastodon ({item.get('instance', '')})"
            normalized['metadata'] = {
                'platform_id': item.get('platform_id', ''),
                'instance': item.get('instance', ''),
                'author_display_name': item.get('author_display_name', ''),
                'engagement': item.get('engagement', {})
            }

        # Extract keywords from title and content
        text_for_keywords = f"{normalized['title']} {normalized['content']}"
        normalized['keywords'] = self._extract_keywords(text_for_keywords)
        
        return normalized
    
    def deduplicate(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicate items based on ID and URL.
        
        Args:
            items: List of items to deduplicate
            
        Returns:
            List of unique items
        """
        unique_items = []
        seen_ids = set()
        seen_urls = set()
        
        for item in items:
            item_id = item.get('id', '')
            item_url = self._normalize_url(item.get('url', ''))
            
            # Skip if we've seen this ID or URL
            if item_id in seen_ids:
                logger.debug(f"Skipping duplicate ID: {item_id}")
                continue
            
            if item_url and item_url in seen_urls:
                logger.debug(f"Skipping duplicate URL: {item_url}")
                continue
            
            # Add to unique items
            unique_items.append(item)
            seen_ids.add(item_id)
            if item_url:
                seen_urls.add(item_url)
        
        logger.info(f"Deduplicated {len(items)} items to {len(unique_items)} unique items")
        return unique_items
    
    def process(self, data_files: List[str]) -> List[Dict[str, Any]]:
        """
        Process data from multiple JSON files.
        
        Args:
            data_files: List of paths to JSON data files
            
        Returns:
            List of processed and deduplicated items
        """
        all_items = []
        
        logger.info(f"Processing {len(data_files)} data files")
        
        # Load and normalize all items
        for data_file in data_files:
            try:
                logger.info(f"Loading {data_file}")
                with open(data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Extract items based on file structure
                items = []
                if 'articles' in data:
                    items = data['articles']
                elif 'papers' in data:
                    items = data['papers']
                elif 'items' in data:
                    items = data['items']
                else:
                    logger.warning(f"Unknown data structure in {data_file}")
                    continue
                
                # Normalize each item
                for item in items:
                    normalized = self._normalize_item(item)
                    all_items.append(normalized)
                
                logger.info(f"Loaded {len(items)} items from {data_file}")
                
            except Exception as e:
                logger.error(f"Error processing {data_file}: {e}")
        
        # Deduplicate
        unique_items = self.deduplicate(all_items)
        
        # Sort by published date (newest first)
        unique_items.sort(key=lambda x: x.get('published', ''), reverse=True)
        
        logger.info(f"Processing complete. Total unique items: {len(unique_items)}")
        
        return unique_items
    
    def save_to_file(self, items: List[Dict[str, Any]], output_path: str):
        """Save processed items to JSON file."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'processed_at': datetime.now().isoformat(),
                'count': len(items),
                'items': items
            }, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved {len(items)} processed items to {output_path}")


if __name__ == '__main__':
    import sys
    import glob
    
    if len(sys.argv) < 3:
        print("Usage: python data_processor.py <input_dir> <output_file>")
        print("Example: python data_processor.py ./data/raw ./data/processed.json")
        sys.exit(1)
    
    input_dir = sys.argv[1]
    output_file = sys.argv[2]
    
    # Find all JSON files in input directory
    data_files = glob.glob(f"{input_dir}/*.json")
    logger.info(f"Found {len(data_files)} JSON files in {input_dir}")
    
    # Process data
    processor = DataProcessor()
    items = processor.process(data_files)
    
    # Save results
    processor.save_to_file(items, output_file)
