#!/usr/bin/env python3
"""
Regenerate just the executive summary for an existing day's data.
Uses the updated prompt with previous days' context to avoid repetition.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.llm_client import AsyncAnthropicClient, ThinkingLevel
from agents.config.prompts import PromptAccessor, load_prompts
from agents.config import load_config
from agents.prompt_security import (
    DATA_POINTER,
    build_fenced_user_message,
    build_hardened_system,
    new_fence_nonce,
    normalize_untrusted_text,
)
from agents.summary_context import (
    build_executive_context,
    format_previous_coverage,
    load_previous_summaries,
)


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
    
    # Load previous days' summaries (3 days lookback), same scaffolding as the
    # live pipeline (agents/orchestrator.py) via agents/summary_context.py
    pairs = load_previous_summaries(web_dir, target_date, lookback_days=3)
    for date_str, _ in pairs:
        print(f"  Loaded previous summary from {date_str}")
    previous_coverage = format_previous_coverage(pairs)

    # Build context from the published summary.json
    topics = [
        (topic.get('name', 'Unknown'), topic.get('description', ''))
        for topic in summary_data.get('top_topics', [])[:6]
    ]
    categories = []
    for category, cat_data in summary_data.get('categories', {}).items():
        top_items = cat_data.get('top_items', [])
        top_story = (
            normalize_untrusted_text(top_items[0].get('title', 'Unknown'))[:300]
            if top_items else None
        )
        categories.append((category, cat_data.get('category_summary', 'N/A'), top_story))

    context = build_executive_context(target_date, previous_coverage, topics, categories)

    # CWE-1427: instructions travel in the system prompt; the aggregated
    # context travels in the user message inside a nonce fence, matching the
    # live pipeline path.
    nonce = new_fence_nonce()
    instructions = prompt_accessor.get_orchestration_prompt(
        'executive_summary', {'context': DATA_POINTER}
    )
    system_prompt = build_hardened_system(instructions, nonce)
    user_message = build_fenced_user_message(
        context, nonce,
        task_line="Write the executive summary from the fenced context below according to your system instructions.",
    )

    print(f"  Context length: {len(context)} chars")
    print(f"  Previous coverage: {len(previous_coverage)} chars")
    print("  Calling LLM...")

    try:
        response = await async_client.call_with_thinking(
            messages=[{"role": "user", "content": user_message}],
            system=system_prompt,
            profile=ThinkingLevel.DEEP,
            caller="regenerate_summary",
            full_output_budget=True,
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
