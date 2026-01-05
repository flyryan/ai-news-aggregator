#!/usr/bin/env python3
"""
LLM Analyzer
Uses Claude Opus 4.5 via LiteLLM to analyze and summarize AI news content.
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging
from collections import defaultdict

try:
    from litellm import completion
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    logging.warning("LiteLLM not available. Install with: pip install litellm")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LLMAnalyzer:
    """Analyzes content using LLM (Claude Opus 4.5 via LiteLLM)."""
    
    def __init__(
        self,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None
    ):
        """
        Initialize LLM analyzer.

        Args:
            api_base: LiteLLM API base URL (default: from env LITELLM_API_BASE)
            api_key: API key (default: from env LITELLM_API_KEY)
            model: Model name (default: from env LITELLM_MODEL)
        """
        if not LITELLM_AVAILABLE:
            raise ImportError("LiteLLM is required. Install with: pip install litellm")

        self.api_base = api_base or os.getenv('LITELLM_API_BASE')
        self.api_key = api_key or os.getenv('LITELLM_API_KEY', 'dummy-key')
        self.model = model or os.getenv('LITELLM_MODEL', 'openai/gpt-4')

        logger.info(f"Initialized LLM Analyzer with model: {self.model}")
    
    def _call_llm(self, messages: List[Dict[str, str]], max_tokens: int = 2000) -> str:
        """Call LLM with messages."""
        try:
            response = completion(
                model=self.model,
                messages=messages,
                api_base=self.api_base,
                api_key=self.api_key,
                max_tokens=max_tokens,
                temperature=0.3
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Error calling LLM: {e}")
            return ""
    
    def summarize_item(self, item: Dict[str, Any]) -> str:
        """
        Generate a concise summary for a single news item.

        Args:
            item: News item dictionary

        Returns:
            Summary string
        """
        title = item.get('title', '')
        content = item.get('content', '')[:2000]  # Limit content length
        source_type = item.get('source_type', '')

        # Classify item type for appropriate summarization
        is_discussion = source_type == 'reddit' and any(word in title.lower() for word in
            ['how do you', 'anyone else', 'what do you', 'opinion on', 'thoughts on', '?'])

        if is_discussion:
            prompt = f"""This is a community discussion thread, not news. Briefly describe what users are discussing.

Title: {title}
Content: {content}

Write 1-2 sentences describing the discussion topic. Do NOT treat this as a news announcement."""
        else:
            prompt = f"""Summarize this AI news item factually in 2-3 sentences.

RULES:
- Lead with the concrete fact: WHO did WHAT
- Include specific details: names, numbers, dates, versions
- Avoid vague phrases like "represents a real-world example of" or "demonstrates the growing trend"
- If it's a product/tool release, name it and what it does
- If it's research, state the finding

Source Type: {source_type}
Title: {title}
Content: {content}

Factual summary:"""

        messages = [
            {"role": "system", "content": "You are a news reporter writing brief factual summaries. Be specific and direct. Never use corporate jargon or buzzwords like 'landscape', 'maturation', 'operationalizing', or 'leveraging'."},
            {"role": "user", "content": prompt}
        ]

        summary = self._call_llm(messages, max_tokens=200)
        return summary.strip()
    
    def categorize_items(self, items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Categorize items into topics using LLM.
        
        Args:
            items: List of news items
            
        Returns:
            Dictionary mapping categories to items
        """
        # Create a summary of all items for categorization
        items_summary = []
        for i, item in enumerate(items[:50]):  # Limit to first 50 for LLM context
            items_summary.append(f"{i}. {item.get('title', '')}")
        
        items_text = "\n".join(items_summary)
        
        prompt = f"""Analyze these AI news items and group them into major categories. Use these categories:
- Research & Papers (new AI research, papers, breakthroughs)
- Industry & Business (company news, funding, partnerships)
- Products & Tools (new AI products, tools, releases)
- Models & Benchmarks (new models, model evaluations, benchmarks)
- Policy & Ethics (AI regulation, ethics, safety)
- Applications (real-world AI applications, use cases)
- Infrastructure (AI infrastructure, hardware, platforms)
- Other (items that don't fit above categories)

Items:
{items_text}

For each item number, assign ONE primary category. Format as JSON:
{{"0": "Research & Papers", "1": "Industry & Business", ...}}"""
        
        messages = [
            {"role": "system", "content": "You are an AI news categorization expert. Analyze and categorize AI news accurately."},
            {"role": "user", "content": prompt}
        ]
        
        response = self._call_llm(messages, max_tokens=1000)
        
        # Parse LLM response
        try:
            # Extract JSON from response
            import re
            json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
            if json_match:
                categorization = json.loads(json_match.group())
            else:
                categorization = {}
        except Exception as e:
            logger.error(f"Error parsing categorization: {e}")
            categorization = {}
        
        # Group items by category
        categorized = defaultdict(list)
        for i, item in enumerate(items):
            if i < 50:
                category = categorization.get(str(i), "Other")
            else:
                # For items beyond the first 50, use simple keyword matching
                category = self._simple_categorize(item)
            
            categorized[category].append(item)
        
        return dict(categorized)
    
    def _simple_categorize(self, item: Dict[str, Any]) -> str:
        """Simple keyword-based categorization fallback."""
        text = f"{item.get('title', '')} {item.get('content', '')}".lower()
        
        if any(word in text for word in ['paper', 'research', 'arxiv', 'study', 'findings']):
            return "Research & Papers"
        elif any(word in text for word in ['company', 'funding', 'acquisition', 'partnership', 'business']):
            return "Industry & Business"
        elif any(word in text for word in ['release', 'launch', 'tool', 'product', 'app', 'platform']):
            return "Products & Tools"
        elif any(word in text for word in ['model', 'gpt', 'llm', 'benchmark', 'evaluation']):
            return "Models & Benchmarks"
        elif any(word in text for word in ['regulation', 'policy', 'ethics', 'safety', 'governance']):
            return "Policy & Ethics"
        elif any(word in text for word in ['application', 'use case', 'deployment', 'implementation']):
            return "Applications"
        elif any(word in text for word in ['infrastructure', 'hardware', 'gpu', 'compute', 'cloud']):
            return "Infrastructure"
        else:
            return "Other"
    
    def rank_items(self, items: List[Dict[str, Any]], top_n: int = 10) -> List[Dict[str, Any]]:
        """
        Rank items by importance using LLM.

        Args:
            items: List of news items
            top_n: Number of top items to return

        Returns:
            List of top N items with importance scores
        """
        # First, separate items by source type for diversity
        rss_items = [i for i, item in enumerate(items) if item.get('source_type') == 'rss']
        arxiv_items = [i for i, item in enumerate(items) if item.get('source_type') == 'arxiv']
        twitter_items = [i for i, item in enumerate(items) if item.get('source_type') == 'twitter']
        social_items = [i for i, item in enumerate(items) if item.get('source_type') in ['reddit', 'bluesky', 'mastodon']]

        # Build candidate pool with source diversity (up to 50 items)
        # Prioritize professional sources (RSS, arXiv) over social
        candidates = []
        candidates.extend(rss_items[:15])      # Up to 15 RSS articles
        candidates.extend(arxiv_items[:10])    # Up to 10 arXiv papers
        candidates.extend(twitter_items[:10])  # Up to 10 tweets
        candidates.extend(social_items[:15])   # Up to 15 social posts

        # Dedupe and limit
        candidates = list(dict.fromkeys(candidates))[:50]

        # If we have very few items, use what we have
        if len(candidates) < 10:
            candidates = list(range(min(50, len(items))))

        # Create summary for ranking
        items_summary = []
        for idx in candidates:
            if idx < len(items):
                item = items[idx]
                items_summary.append(f"{idx}. [{item.get('source_type', '')}] {item.get('title', '')}")

        items_text = "\n".join(items_summary)

        prompt = f"""Rank these AI news items by NEWS VALUE for a daily intelligence briefing.

PRIORITIZE (high news value):
1. ANNOUNCEMENTS: Product releases, model launches, company news, funding
2. RESEARCH: Papers with novel findings, technical breakthroughs
3. MILESTONES: Contributor counts, adoption metrics, significant events
4. POLICY: Regulatory developments, safety announcements

DEPRIORITIZE (low news value):
- Opinion threads, user complaints, polls
- "How do you use X?" discussion posts
- Model comparison opinion threads ("Is X better than Y?")
- Simple technical questions

Items:
{items_text}

Return the top {min(top_n, len(candidates))} item numbers by NEWS VALUE (not engagement/popularity).
Format as JSON array: [5, 12, 3, ...]"""

        messages = [
            {"role": "system", "content": "You are a news editor selecting the most newsworthy items for a daily briefing. Prioritize concrete events over community discussions."},
            {"role": "user", "content": prompt}
        ]

        response = self._call_llm(messages, max_tokens=500)

        # Parse LLM response
        try:
            import re
            json_match = re.search(r'\[[^\]]+\]', response)
            if json_match:
                rankings = json.loads(json_match.group())
            else:
                rankings = candidates[:top_n]
        except Exception as e:
            logger.error(f"Error parsing rankings: {e}")
            rankings = candidates[:top_n]

        # Return ranked items
        ranked_items = []
        for rank, idx in enumerate(rankings[:top_n]):
            if idx < len(items):
                item = items[idx].copy()
                item['importance_rank'] = rank + 1
                item['importance_score'] = 1.0 - (rank / top_n)
                ranked_items.append(item)

        return ranked_items
    
    def generate_executive_summary(self, items: List[Dict[str, Any]], date: str) -> str:
        """
        Generate an executive summary of the day's AI news.

        Args:
            items: List of news items
            date: Date string

        Returns:
            Executive summary text
        """
        # Separate actual news from discussion threads
        news_items = []
        discussion_items = []

        for item in items[:30]:
            title = item.get('title', '').lower()
            source_type = item.get('source_type', '')
            is_discussion = source_type == 'reddit' and any(word in title for word in
                ['how do you', 'anyone else', 'what do you', 'opinion on', 'thoughts on', '?',
                 'i keep saying', 'must be going crazy', 'hate when'])

            if is_discussion:
                discussion_items.append(item)
            else:
                news_items.append(item)

        # Build summary text prioritizing news
        items_summary = []
        for item in news_items[:15]:
            source = item.get('source', item.get('source_type', ''))
            items_summary.append(f"- [{source}] {item.get('title', '')}")

        # Add a few discussion topics for context
        if discussion_items:
            items_summary.append("\nCommunity Discussion Topics:")
            for item in discussion_items[:5]:
                items_summary.append(f"- {item.get('title', '')}")

        items_text = "\n".join(items_summary)

        prompt = f"""Write a daily AI news briefing for {date}.

NEWS ITEMS:
{items_text}

INSTRUCTIONS:
Write 2-3 paragraphs summarizing WHAT ACTUALLY HAPPENED on {date}. Be specific:
- Lead with concrete events: "[Company] released [product]", "[Researcher] published [paper]"
- Name specific companies, products, people, and numbers
- If there are few major announcements, say so directly: "A relatively quiet day with [X] being the notable development"
- At the end, briefly note any notable community discussions if relevant

DO NOT:
- Use vague phrases like "the AI landscape", "maturation of the field", "operationalizing workflows"
- Write generic industry analysis that could apply to any day
- Use corporate buzzwords: consolidation, landscape, maturation, operationalizing, leveraging

Daily Briefing for {date}:"""

        messages = [
            {"role": "system", "content": "You are a news reporter writing a factual daily briefing. Be specific about what happened. If it was a slow news day, say so. Never pad with generic industry analysis."},
            {"role": "user", "content": prompt}
        ]

        summary = self._call_llm(messages, max_tokens=800)
        return summary.strip()
    
    def detect_trends(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Detect emerging trends from news items.

        Args:
            items: List of news items

        Returns:
            List of trend dictionaries
        """
        # Extract titles with source type for context
        titles = []
        for i, item in enumerate(items[:50]):
            source_type = item.get('source_type', '')
            title = item.get('title', '')
            titles.append(f"{i}. [{source_type}] {title}")

        titles_text = "\n".join(titles)

        prompt = f"""Analyze these AI news items and identify 3-5 concrete topics being discussed.

GOOD topic names (specific):
- "Claude Code + Blender Integration" (specific tool/event)
- "vLLM 2000 Contributors Milestone" (specific milestone)
- "ChatGPT vs Gemini Performance Debates" (specific comparison)

BAD topic names (too generic):
- "AI-Assisted Coding and Productivity" (could apply to any day)
- "Open Source AI Development" (too broad)
- "The Future of AI" (meaningless)

Items:
{titles_text}

For each topic:
- Name it specifically (reference the actual product/event/announcement)
- One sentence explaining what specifically is being discussed
- Which items relate to this topic

Format as JSON array:
[
  {{"trend": "Specific Topic Name", "description": "One sentence about what's happening", "items": [1, 5, 12]}},
  ...
]"""

        messages = [
            {"role": "system", "content": "You are grouping today's news by topic. Use specific names that reference actual products, companies, or events - never generic industry themes."},
            {"role": "user", "content": prompt}
        ]

        response = self._call_llm(messages, max_tokens=1000)

        # Parse LLM response
        try:
            import re
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                trends = json.loads(json_match.group())
            else:
                trends = []
        except Exception as e:
            logger.error(f"Error parsing trends: {e}")
            trends = []

        return trends
    
    def analyze_all(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Perform complete analysis on all items.
        
        Args:
            items: List of news items
            
        Returns:
            Dictionary with all analysis results
        """
        logger.info(f"Starting LLM analysis of {len(items)} items")
        
        # Generate executive summary
        logger.info("Generating executive summary...")
        date_str = datetime.now().strftime('%Y-%m-%d')
        executive_summary = self.generate_executive_summary(items, date_str)
        
        # Rank top items
        logger.info("Ranking top items...")
        top_items = self.rank_items(items, top_n=15)
        
        # Categorize items
        logger.info("Categorizing items...")
        categorized = self.categorize_items(items)
        
        # Detect trends
        logger.info("Detecting trends...")
        trends = self.detect_trends(items)
        
        # Summarize top items (limit to avoid too many API calls)
        logger.info("Summarizing top items...")
        for item in top_items[:10]:
            item['llm_summary'] = self.summarize_item(item)
        
        analysis = {
            'analyzed_at': datetime.now().isoformat(),
            'date': date_str,
            'total_items': len(items),
            'executive_summary': executive_summary,
            'top_items': top_items,
            'trends': trends,
            'categories': {
                category: {
                    'count': len(items_list),
                    'items': items_list[:10]  # Limit items per category
                }
                for category, items_list in categorized.items()
            }
        }
        
        logger.info("LLM analysis complete")
        return analysis
    
    def save_analysis(self, analysis: Dict[str, Any], output_path: str):
        """Save analysis results to JSON file."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved analysis to {output_path}")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python llm_analyzer.py <input_file> <output_file>")
        print("Example: python llm_analyzer.py ./data/processed.json ./data/analyzed.json")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    # Load processed data
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    items = data.get('items', [])
    logger.info(f"Loaded {len(items)} items from {input_file}")
    
    # Analyze
    analyzer = LLMAnalyzer()
    analysis = analyzer.analyze_all(items)
    
    # Save results
    analyzer.save_analysis(analysis, output_file)
