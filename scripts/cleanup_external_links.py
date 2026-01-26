#!/usr/bin/env python3
"""
Clean up external links from topic descriptions.

Strips all external links and re-runs enrichment to add internal links only.
"""

import json
import re
import asyncio
import sys
from pathlib import Path

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

from agents.llm_client import AsyncAnthropicClient, ThinkingLevel


def strip_all_links(text):
    """Remove all markdown links, keeping just the link text."""
    return re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)


def markdown_links_to_html(text):
    def link_replacer(match):
        link_text, url = match.groups()
        if url.startswith('/') or url.startswith('#'):
            return f'<a href="{url}" class="internal-link">{link_text}</a>'
        else:
            return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{link_text}</a>'
    return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_replacer, text)


async def enrich_text(client, text, items, date, context_name):
    if not text or not items:
        return text

    items_json = json.dumps(items[:40], indent=2, ensure_ascii=False)

    prompt = f"""You are a link enrichment agent. Add contextual "read more" links to summary text.

LINKING STRATEGY:
1. Link the ACTION/EVENT phrase, NOT the leading company/entity name
2. ONE link per distinct story/development
3. Link to the HIGHEST-RANKED item that covers that story

LINK FORMAT:
[descriptive phrase](/?date={date}&category=CATEGORY#item-ITEMID)

DATE: {date}

AVAILABLE ITEMS:
{items_json}

TEXT TO ENRICH:
{text}

OUTPUT (JSON only):
{{
  "enriched_text": "Text with internal links only",
  "links": [{{"phrase": "linked phrase", "item_id": "id", "category": "cat"}}]
}}"""

    response = await client.call_with_thinking(
        messages=[{"role": "user", "content": prompt}],
        budget_tokens=ThinkingLevel.STANDARD,
        caller=f"cleanup.{context_name}"
    )

    content = response.content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    if not content.startswith("{"):
        start = content.find("{")
        if start != -1:
            content = content[start:]
    if not content.endswith("}"):
        end = content.rfind("}")
        if end != -1:
            content = content[:end + 1]

    result = json.loads(content)
    return result.get('enriched_text', text)


async def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-01-07"
    web_dir = Path("web")

    print(f"Cleaning external links for {date}...")

    # Load summary
    summary_path = web_dir / "data" / date / "summary.json"
    with open(summary_path) as f:
        summary = json.load(f)

    # Build items for enrichment
    items = []
    for cat, data in summary.get('categories', {}).items():
        for item in data.get('top_items', [])[:40]:
            if item.get('id') and item.get('title'):
                items.append({
                    'id': item['id'],
                    'title': item['title'],
                    'category': cat,
                    'summary': item.get('summary', '')[:200]
                })

    client = AsyncAnthropicClient()

    # Process each topic
    cleaned_count = 0
    for i, topic in enumerate(summary['top_topics']):
        desc = topic.get('description', '')
        # Check if has external links (https:// or http://)
        if 'https://' in desc or 'http://' in desc:
            print(f"  Topic {i+1}: {topic['name']} - has external links, cleaning...")
            # Strip all links
            clean_desc = strip_all_links(desc)
            # Re-enrich
            enriched = await enrich_text(client, clean_desc, items, date, topic['name'])
            topic['description'] = enriched
            topic['description_html'] = markdown_links_to_html(enriched)
            cleaned_count += 1
            print(f"    Re-enriched with internal links")
        else:
            print(f"  Topic {i+1}: {topic['name']} - OK (no external links)")

    if cleaned_count == 0:
        print("No topics needed cleaning")
        return

    # Save
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {summary_path}")

    print(f"\nCleaned {cleaned_count} topics")


if __name__ == "__main__":
    asyncio.run(main())
