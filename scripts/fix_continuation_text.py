#!/usr/bin/env python3
"""
Fix continuation reference text in existing data.

Ensures all continuation reference texts have:
1. Links to original items (category placeholders or time phrases linked)
2. Comma after the reference text before the main content
3. Both summary (markdown) and summary_html (HTML) fields are updated

Also updates summary.json to match.
"""

import argparse
import json
import re
from pathlib import Path
from html import escape as html_escape

CATEGORIES = ['news', 'research', 'social', 'reddit']

# Time phrases that should be linked in same-category follow-ups
TIME_PHRASES = ['yesterday', 'earlier this week', 'last week', 'earlier today']


def markdown_to_html_link(md_text: str) -> str:
    """Convert markdown links to HTML links."""
    # [text](url) -> <a href="url">text</a>
    pattern = r'\[([^\]]+)\]\(([^)]+)\)'
    return re.sub(pattern, r'<a href="\2">\1</a>', md_text)


def fix_continuation_summary(summary: str, continuation: dict, is_html: bool = False) -> str:
    """
    Fix a summary that has a continuation without proper link/comma.

    Args:
        summary: The current summary text (markdown or HTML)
        continuation: The continuation info dict
        is_html: Whether this is HTML (summary_html) or markdown (summary)

    Returns:
        Fixed summary with link and comma
    """
    ref_text = continuation.get('reference_text', '')
    if not ref_text:
        return summary

    # Build the link
    link = (
        f"/?date={continuation['original_date']}"
        f"&category={continuation['original_category']}"
        f"#item-{continuation['original_item_id']}"
    )

    # Start with original reference text
    new_ref_text = ref_text

    # Replace category placeholders with links (e.g., **Social** -> [Social](link))
    link_added = False
    for cat in CATEGORIES:
        cat_formatted = f"**{cat.capitalize()}**"
        if cat_formatted in new_ref_text:
            new_ref_text = new_ref_text.replace(
                cat_formatted,
                f"[{cat.capitalize()}]({link})"
            )
            link_added = True

    # Handle time phrase linking for same-category follow-ups
    if not link_added:
        for phrase in TIME_PHRASES:
            if phrase in new_ref_text.lower():
                # Find the actual case in the text
                idx = new_ref_text.lower().find(phrase)
                actual_phrase = new_ref_text[idx:idx+len(phrase)]
                new_ref_text = new_ref_text.replace(actual_phrase, f'[{actual_phrase}]({link})')
                link_added = True
                break

    # Ensure comma at the end if not present
    if not new_ref_text.rstrip().endswith((',', '.', ':', ';')):
        new_ref_text = new_ref_text.rstrip() + ','

    # Convert to HTML if needed
    if is_html:
        new_ref_text_final = markdown_to_html_link(new_ref_text)
    else:
        new_ref_text_final = new_ref_text

    # For HTML, strip <p> tags for matching
    summary_to_match = summary
    prefix = ''
    suffix = ''
    if is_html and summary.startswith('<p>'):
        prefix = '<p>'
        summary_to_match = summary[3:]
        if summary_to_match.endswith('</p>'):
            suffix = '</p>'
            summary_to_match = summary_to_match[:-4]

    # Case 1: Summary starts with original ref_text (no links yet)
    if summary_to_match.startswith(ref_text):
        rest = summary_to_match[len(ref_text):].lstrip()
        return f"{prefix}{new_ref_text_final} {rest}{suffix}"

    # Case 2: Summary has links but maybe no comma - build pattern to match
    # For HTML, we need to match <a href="...">text</a> patterns
    # For markdown, we need to match [text](link) patterns

    if is_html:
        # Build HTML pattern
        linked_ref_pattern = ref_text
        for cat in CATEGORIES:
            cat_formatted = f"**{cat.capitalize()}**"
            linked_ref_pattern = linked_ref_pattern.replace(
                cat_formatted,
                f'<a[^>]*>{cat.capitalize()}</a>'
            )
        for phrase in TIME_PHRASES:
            if phrase in ref_text.lower():
                linked_ref_pattern = re.sub(
                    re.escape(phrase),
                    f'<a[^>]*>{phrase}</a>',
                    linked_ref_pattern,
                    flags=re.IGNORECASE
                )
    else:
        # Build markdown pattern
        linked_ref_pattern = ref_text
        for cat in CATEGORIES:
            cat_formatted = f"**{cat.capitalize()}**"
            linked_ref_pattern = linked_ref_pattern.replace(
                cat_formatted,
                f"\\[{cat.capitalize()}\\]\\([^)]+\\)"
            )
        for phrase in TIME_PHRASES:
            if phrase in ref_text.lower():
                linked_ref_pattern = re.sub(
                    re.escape(phrase),
                    f'\\[{phrase}\\]\\([^)]+\\)',
                    linked_ref_pattern,
                    flags=re.IGNORECASE
                )

    # Try to match at start of summary
    match = re.match(f'^({linked_ref_pattern})(,?)\\s*', summary_to_match)
    if match:
        matched_text = match.group(1)
        existing_comma = match.group(2)
        rest = summary_to_match[match.end():].lstrip()

        # Check if comma already present
        if existing_comma or matched_text.rstrip().endswith((',', '.', ':', ';')):
            return summary  # Already fixed

        # Add comma
        return f"{prefix}{matched_text.rstrip()}, {rest}{suffix}"

    return summary


def process_item(item: dict, dry_run: bool = False) -> bool:
    """
    Process a single item, fixing both summary and summary_html.

    Returns True if item was modified.
    """
    continuation = item.get('continuation')
    if not continuation:
        return False

    ref_text = continuation.get('reference_text', '')
    if not ref_text:
        return False

    modified = False

    # Fix summary (markdown)
    old_summary = item.get('summary', '')
    new_summary = fix_continuation_summary(old_summary, continuation, is_html=False)
    if new_summary != old_summary:
        if not dry_run:
            item['summary'] = new_summary
        modified = True

    # Fix summary_html if present
    old_html = item.get('summary_html', '')
    if old_html:
        new_html = fix_continuation_summary(old_html, continuation, is_html=True)
        if new_html != old_html:
            if not dry_run:
                item['summary_html'] = new_html
            modified = True

    return modified


def process_category_file(category_file: Path, dry_run: bool = False) -> int:
    """
    Process a category JSON file and fix continuation texts.

    Returns number of items fixed.
    """
    if not category_file.exists():
        return 0

    data = json.loads(category_file.read_text())
    items = data.get('items', [])
    fixed_count = 0

    for item in items:
        if process_item(item, dry_run):
            print(f"  Fixed: {item.get('title', 'Unknown')[:60]}...")
            fixed_count += 1

    if fixed_count > 0 and not dry_run:
        category_file.write_text(json.dumps(data, indent=2))

    return fixed_count


def process_summary_file(summary_file: Path, dry_run: bool = False) -> int:
    """
    Process summary.json and fix continuation texts in top_items.

    Returns number of items fixed.
    """
    if not summary_file.exists():
        return 0

    data = json.loads(summary_file.read_text())
    fixed_count = 0

    # Handle both old format (data.news) and new format (data.categories.news)
    categories = data.get('categories', data)
    for category_key in CATEGORIES:
        category_data = categories.get(category_key, {})
        top_items = category_data.get('top_items', [])

        for item in top_items:
            if process_item(item, dry_run):
                fixed_count += 1

    if fixed_count > 0 and not dry_run:
        summary_file.write_text(json.dumps(data, indent=2))

    return fixed_count


def process_search_documents(search_file: Path, dry_run: bool = False) -> int:
    """
    Process search-documents.json and fix continuation texts.

    Returns number of items fixed.
    """
    if not search_file.exists():
        return 0

    data = json.loads(search_file.read_text())
    fixed_count = 0

    for doc_id, doc in data.items():
        # Search docs don't have full continuation info, so we need to detect and fix directly
        summary = doc.get('summary', '')

        # Check for unlinked/uncomma'd continuation patterns
        for phrase in TIME_PHRASES + ['**News**', '**Research**', '**Social**', '**Reddit**']:
            # This is a simplified fix - just ensure comma after common patterns
            pass

        # For search docs, we just need to ensure consistency with the source files
        # The source files should already be fixed, so we skip this for now

    return fixed_count


def main():
    parser = argparse.ArgumentParser(
        description='Fix continuation reference text in existing data'
    )
    parser.add_argument(
        '--web-dir',
        type=Path,
        default=Path(__file__).parent.parent / 'web',
        help='Path to web directory (default: ./web)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be fixed without making changes'
    )
    args = parser.parse_args()

    data_dir = args.web_dir / 'data'
    if not data_dir.exists():
        print(f"Error: Data directory not found: {data_dir}")
        return 1

    total_fixed = 0

    for date_dir in sorted(data_dir.iterdir()):
        if not date_dir.is_dir():
            continue

        date_str = date_dir.name
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            continue

        date_fixed = 0
        print(f"\nProcessing {date_str}:")

        # Process category files
        for category in CATEGORIES:
            category_file = date_dir / f'{category}.json'
            fixed = process_category_file(category_file, args.dry_run)
            date_fixed += fixed

        # Process summary.json
        summary_file = date_dir / 'summary.json'
        fixed = process_summary_file(summary_file, args.dry_run)
        date_fixed += fixed

        if date_fixed == 0:
            print("  No items need fixing")
        else:
            total_fixed += date_fixed

    action = "Would fix" if args.dry_run else "Fixed"
    print(f"\n{action} {total_fixed} items total.")

    if args.dry_run and total_fixed > 0:
        print("\nRun without --dry-run to apply fixes.")

    return 0


if __name__ == '__main__':
    exit(main())
