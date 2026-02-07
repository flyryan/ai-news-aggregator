#!/usr/bin/env python3
"""
Regenerate just the executive summary for an existing day's data.
Uses the updated prompt with previous days' context to avoid repetition.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.llm_client import AsyncAnthropicClient, ThinkingLevel
from agents.config.prompts import PromptAccessor, load_prompts
from agents.config import load_config


async def regenerate_summary(target_date: str, web_dir: str = './web', config_dir: str = './config'):
    """Regenerate executive summary for a given date."""
    
    print(f"Regenerating executive summary for {target_date}...")
    
    # Load prompt config and accessor
    prompt_config = load_prompts(config_dir)
    prompt_accessor = PromptAccessor(prompt_config)
    
    # Load provider config
    provider_config = load_config(config_dir)
    
    # Initialize async client from config
    async_client = AsyncAnthropicClient.from_config(provider_config.llm)
    
    # Load existing summary.json
    summary_path = os.path.join(web_dir, 'data', target_date, 'summary.json')
    if not os.path.exists(summary_path):
        print(f"ERROR: No summary.json found for {target_date}")
        return False
    
    with open(summary_path, 'r', encoding='utf-8') as f:
        summary_data = json.load(f)
    
    # Load previous days' summaries (3 days lookback)
    previous_summaries = []
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    
    for days_ago in range(1, 4):
        check_date = target_dt - timedelta(days=days_ago)
        date_str = check_date.strftime('%Y-%m-%d')
        prev_summary_path = os.path.join(web_dir, 'data', date_str, 'summary.json')
        
        if not os.path.exists(prev_summary_path):
            continue
        
        try:
            with open(prev_summary_path, 'r', encoding='utf-8') as f:
                prev_data = json.load(f)
            exec_summary = prev_data.get('executive_summary', '')
            if exec_summary:
                previous_summaries.append(f"=== {date_str} ===\n{exec_summary}")
                print(f"  Loaded previous summary from {date_str}")
        except Exception as e:
            print(f"  Warning: Failed to load {date_str}: {e}")
            continue
    
    previous_coverage = ""
    if previous_summaries:
        previous_coverage = "PREVIOUS DAYS' COVERAGE (do NOT repeat these as new/breaking news):\n\n" + "\n\n".join(previous_summaries)
    
    # Build context from categories
    context_parts = [f"Date: {target_date}", ""]
    
    # Add previous coverage first
    if previous_coverage:
        context_parts.append(previous_coverage)
        context_parts.append("")
    
    # Add top topics
    context_parts.append("TOP TOPICS:")
    top_topics = summary_data.get('top_topics', [])
    for i, topic in enumerate(top_topics[:6], 1):
        name = topic.get('name', 'Unknown')
        desc = topic.get('description', '')
        context_parts.append(f"{i}. {name}: {desc}")
    context_parts.append("")
    
    # Add category summaries
    categories = summary_data.get('categories', {})
    for category, cat_data in categories.items():
        context_parts.append(f"--- {category.upper()} ---")
        context_parts.append(f"Summary: {cat_data.get('category_summary', 'N/A')}")
        top_items = cat_data.get('top_items', [])
        if top_items:
            context_parts.append("Top story: " + top_items[0].get('title', 'Unknown'))
        context_parts.append("")
    
    context = "\n".join(context_parts)
    
    # Get prompt
    prompt = prompt_accessor.get_orchestration_prompt('executive_summary', {'context': context})
    
    print(f"  Context length: {len(context)} chars")
    print(f"  Previous coverage: {len(previous_coverage)} chars")
    print("  Calling LLM...")
    
    try:
        response = await async_client.call_with_thinking(
            messages=[{"role": "user", "content": prompt}],
            budget_tokens=ThinkingLevel.DEEP,
            caller="regenerate_summary"
        )
        
        new_summary = response.content
        print(f"  Generated new summary ({len(new_summary)} chars)")
        
        # Update summary.json
        old_summary = summary_data.get('executive_summary', '')
        summary_data['executive_summary'] = new_summary
        summary_data['executive_summary_regenerated'] = datetime.now().isoformat()
        
        # Backup old summary
        backup_path = summary_path.replace('.json', '.backup.json')
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump({'executive_summary': old_summary, 'backed_up_at': datetime.now().isoformat()}, f, indent=2)
        print(f"  Backed up old summary to {backup_path}")
        
        # Write updated summary
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)
        print(f"  Updated {summary_path}")
        
        print("\n=== OLD SUMMARY ===")
        print(old_summary[:500] + "..." if len(old_summary) > 500 else old_summary)
        print("\n=== NEW SUMMARY ===")
        print(new_summary[:500] + "..." if len(new_summary) > 500 else new_summary)
        
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to generate summary: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python regenerate_summary.py YYYY-MM-DD")
        sys.exit(1)
    
    target_date = sys.argv[1]
    
    # Validate date format
    try:
        datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        print(f"Invalid date format: {target_date}. Use YYYY-MM-DD")
        sys.exit(1)
    
    success = asyncio.run(regenerate_summary(target_date))
    sys.exit(0 if success else 1)
