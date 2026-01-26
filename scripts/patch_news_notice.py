#!/usr/bin/env python3
"""Patch existing news.json files to add collection start notice."""

import json
from pathlib import Path

NEWS_COLLECTION_START = '2026-01-06'
WEB_DATA_DIR = Path(__file__).parent.parent / 'web' / 'data'


def main():
    notice = {
        'type': 'info',
        'title': 'Limited Coverage',
        'message': 'News collection began on January 6th, 2026. Earlier dates may have incomplete or no news data.'
    }

    patched = 0
    for date_dir in sorted(WEB_DATA_DIR.iterdir()):
        if not date_dir.is_dir():
            continue

        date_str = date_dir.name
        if not date_str.startswith('202'):  # Skip non-date dirs
            continue

        if date_str >= NEWS_COLLECTION_START:
            continue  # Skip dates after collection started

        news_file = date_dir / 'news.json'
        if not news_file.exists():
            continue

        data = json.loads(news_file.read_text())
        if 'notice' not in data:
            data['notice'] = notice
            news_file.write_text(json.dumps(data, indent=2))
            print(f"Patched: {date_str}/news.json")
            patched += 1
        else:
            print(f"Skipped (already has notice): {date_str}/news.json")

    print(f"\nDone. Patched {patched} files.")


if __name__ == '__main__':
    main()
