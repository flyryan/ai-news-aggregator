#!/usr/bin/env python3
"""
AI News Aggregation Pipeline - Multi-Agent Architecture

Main entry point that orchestrates the multi-agent pipeline:
1. Parallel Gathering (4 gatherers: news, papers, social, reddit)
2. Parallel Analysis (4 analyzers with adaptive/manual thinking profiles)
3. Cross-Category Topic Detection (ULTRATHINK)
4. Executive Summary Generation
5. Deduplication & QC
6. JSON Data Generation (for SPA frontend)
7. Search Index Update (Lunr.js compatible)
"""

import asyncio
import os
import sys
import logging
import re
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from agents import MainOrchestrator
from agents.config import load_config, ProviderConfig
from agents.config.prompts import load_prompts, PromptAccessor
from agents.cost_tracker import get_tracker
from agents.replay_recorder import get_recorder
from generators.json_generator import JSONGenerator
from generators.replay_generator import generate_replay
from generators.search_indexer import SearchIndexer
from generators.feed_generator import FeedGenerator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> str:
    """
    Parse date string in YYYY-MM-DD or MM-DD-YYYY format.

    Returns date in YYYY-MM-DD format.
    Raises ValueError if format is invalid.
    """
    # Try YYYY-MM-DD format
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        datetime.strptime(date_str, '%Y-%m-%d')  # Validate
        return date_str

    # Try MM-DD-YYYY format
    if re.match(r'^\d{2}-\d{2}-\d{4}$', date_str):
        dt = datetime.strptime(date_str, '%m-%d-%Y')
        return dt.strftime('%Y-%m-%d')

    raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD or MM-DD-YYYY")


async def run_pipeline(config_dir: str, data_dir: str, web_dir: str, target_date: str = None, resume_from=None) -> bool:
    """
    Run the complete multi-agent pipeline.

    Args:
        config_dir: Directory containing configuration files
        data_dir: Directory for data storage
        web_dir: Directory for generated website
        target_date: Report date (YYYY-MM-DD). Coverage is day before.
        resume_from: Phase number (float) to resume from, or 'auto' for auto-detection.

    Returns:
        True if successful, False otherwise.
    """
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("AI NEWS AGGREGATION PIPELINE - MULTI-AGENT ARCHITECTURE")
    logger.info(f"Start time: {start_time}")
    logger.info("=" * 60)

    # Load and validate configuration FIRST
    # This will auto-migrate from env vars if needed, or exit with clear error
    logger.info("Loading provider configuration...")
    provider_config = load_config(config_dir)

    # Load prompt configuration
    logger.info("Loading prompt configuration...")
    prompt_config = load_prompts(config_dir)
    prompt_accessor = PromptAccessor(prompt_config)
    logger.info(f"Loaded prompts from {config_dir}/prompts.yaml")

    # Get additional configuration from environment (CLI args override env vars)
    lookback_hours = int(os.getenv('LOOKBACK_HOURS', '24'))
    if not target_date:
        target_date = os.getenv('TARGET_DATE', '')

    orchestrator = None

    try:
        # Initialize orchestrator with provider config and prompt accessor
        orchestrator = MainOrchestrator(
            config_dir=config_dir,
            data_dir=data_dir,
            web_dir=web_dir,
            lookback_hours=lookback_hours,
            target_date=target_date if target_date else None,
            provider_config=provider_config,
            prompt_accessor=prompt_accessor
        )

        # Handle resume modes
        actual_resume_from = None
        if resume_from == 'auto':
            actual_resume_from = orchestrator._detect_resume_point()
            if actual_resume_from is None:
                logger.info("No checkpoints found - running full pipeline")
            else:
                logger.info(f"Auto-resume: will resume from phase {actual_resume_from}")
        elif resume_from is not None:
            actual_resume_from = float(resume_from)

        # Run the multi-agent pipeline
        result = await orchestrator.run(resume_from=actual_resume_from)

        # Generate JSON data for SPA frontend
        logger.info("=" * 60)
        logger.info("PHASE 6: JSON DATA GENERATION")
        logger.info("=" * 60)

        llm_cfg = provider_config.llm if provider_config else None
        json_generator = JSONGenerator(
            web_dir,
            llm_model=llm_cfg.model if llm_cfg else None,
            llm_model_display=(llm_cfg.display_name or llm_cfg.model) if llm_cfg else None,
        )
        json_generator.generate_from_orchestrator_result(result.to_dict())

        # Generate the LLM replay artifacts. Best-effort by design: generate_replay
        # swallows its own failures, so a broken replay never costs us a report.
        logger.info("=" * 60)
        logger.info("PHASE 6.2: LLM REPLAY GENERATION")
        logger.info("=" * 60)

        replay_path = generate_replay(
            date=result.date,
            web_dir=web_dir,
            cost_report=get_tracker().get_json_report(),
            orchestrator_result=result.to_dict(),
            recorder_snapshot=get_recorder().snapshot(),
        )
        if replay_path is None:
            # generate_replay already logged the cause at WARNING with a traceback.
            # Say it again at ERROR, in one greppable line: this phase runs after
            # the orchestrator has printed its phase summary, so a silent failure
            # here leaves no trace in the end-of-run status block. On 2026-07-31 the
            # replay was lost this way and the run still reported success.
            logger.error(
                f"PHASE 6.2 FAILED: no replay artifacts written for {result.date} "
                f"(the report itself is unaffected)"
            )

        # Generate RSS/Atom feeds
        logger.info("=" * 60)
        logger.info("PHASE 6.5: RSS FEED GENERATION")
        logger.info("=" * 60)

        pipeline_config = provider_config.get_pipeline_config()
        feed_generator = FeedGenerator(
            web_dir,
            rolling_window_days=7,
            base_url=pipeline_config.base_url
        )
        feed_generator.generate_feeds()

        # Update search index
        logger.info("=" * 60)
        logger.info("PHASE 7: SEARCH INDEX UPDATE")
        logger.info("=" * 60)

        search_indexer = SearchIndexer(web_dir, rolling_window_days=30)
        search_indexer.update_index(result.to_dict())

        # Complete
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETED SUCCESSFULLY")
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info(f"Total items collected: {result.total_items_collected}")
        logger.info(f"Total items analyzed: {result.total_items_analyzed}")
        logger.info(f"Top topics: {len(result.top_topics)}")
        logger.info(f"Data output: {web_dir}/data/")
        logger.info("=" * 60)

        return True

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        return False

    finally:
        if orchestrator:
            await orchestrator.close()


def create_default_config_files(config_dir: str):
    """Create default configuration files."""

    # RSS feeds
    rss_feeds = """# AI News RSS/Atom Feeds (one per line)
# Optional per-feed routing directive (default = proxied when a proxy is set):
#   <url>  proxy=off -> fetch direct, bypass the Mullvad/pipeline proxy
#   <url>  proxy=on  -> force routing through the proxy
# Major news sites
https://feeds.arstechnica.com/arstechnica/index
https://www.wired.com/feed/tag/ai/latest/rss
https://venturebeat.com/category/ai/feed/
https://www.theguardian.com/technology/artificialintelligenceai/rss
https://www.artificialintelligence-news.com/feed/rss/
https://techcrunch.com/category/artificial-intelligence/feed/
https://www.theverge.com/rss/ai-artificial-intelligence/index.xml
https://www.technologyreview.com/topic/artificial-intelligence/feed/
https://spectrum.ieee.org/rss/artificial-intelligence/fulltext
https://the-decoder.com/feed/
https://www.theregister.com/software/ai_ml/headlines.atom
https://www.newscientist.com/subject/artificial-intelligence/feed/

# AI-specific sites
https://aibusiness.com/rss.xml
https://www.marktechpost.com/feed
https://openai.com/news/rss.xml
https://blog.google/technology/ai/rss/
https://blogs.microsoft.com/ai/feed/
https://aws.amazon.com/blogs/machine-learning/feed/
https://blogs.nvidia.com/blog/tag/generative-ai/feed/
https://github.blog/tag/github-copilot/feed/

# Research blogs
https://deepmind.com/blog/feed/basic/
https://huggingface.co/blog/feed.xml

# Industry analysis
https://every.to/chain-of-thought/feed.xml
https://lastweekin.ai/feed
https://www.latent.space/feed
https://semianalysis.com/feed/
"""

    research_feeds = """# AI research RSS/Atom feeds and technical blogs (one per line)
# Optional per-feed routing directive (default = proxied when a proxy is set):
#   <url>  proxy=off -> fetch direct, bypass the Mullvad/pipeline proxy
#   <url>  proxy=on  -> force routing through the proxy
https://www.lesswrong.com/feed.xml?view=frontpage-rss&karmaThreshold=2
https://research.google/blog/rss/
https://www.microsoft.com/en-us/research/blog/category/artificial-intelligence/feed/
# Meta blocks datacenter/VPN exits (400 via Mullvad), so fetch this one direct.
https://research.facebook.com/feed/  proxy=off
https://www.amazon.science/index.rss
https://allenai.org/rss.xml
https://bair.berkeley.edu/blog/feed.xml
https://metr.org/feed.xml
https://www.alignmentforum.org/feed.xml?view=frontpage-rss&karmaThreshold=2
https://news.mit.edu/rss/topic/artificial-intelligence2
https://blog.ml.cmu.edu/feed/
https://thegradient.pub/rss/
https://importai.substack.com/feed
https://www.interconnects.ai/feed
https://lilianweng.github.io/index.xml
https://huyenchip.com/feed.xml
https://www.nature.com/subjects/machine-learning.rss
https://www.nature.com/natmachintell.rss
http://feeds.trendmicro.com/TrendMicroSimplySecurity
"""

    # Twitter accounts
    twitter_accounts = """# Twitter accounts to monitor (one per line, without @)
# AI Lab Leaders
sama
demishassabis
ylecun
karpathy

# AI Companies
OpenAI
AnthropicAI
GoogleDeepMind
StabilityAI

# Researchers
emollick
hardmaru
"""

    # Reddit subreddits
    reddit_subs = """# Reddit subreddits to monitor (one per line, without r/)
MachineLearning
artificial
LocalLLaMA
OpenAI
singularity
"""

    # Bluesky accounts
    bluesky_accounts = """# Bluesky accounts to monitor (one per line)
# Format: handle or handle.bsky.social
# AI researchers and leaders
karpathy.bsky.social
ylecun.bsky.social
emollick.bsky.social

# AI companies and labs
anthropic.bsky.social
openai.bsky.social

# AI news and commentary
simonwillison.net
"""

    # Mastodon accounts
    mastodon_accounts = """# Mastodon accounts to monitor (one per line)
# Format: username@instance.social
# Note: Must be a real Mastodon instance (mastodon.social, fosstodon.org, etc.)

# AI/ML researchers
Geoffreylitt@mas.to
hardmaru@mas.to

# Tech community
Gargron@mastodon.social
"""

    os.makedirs(config_dir, exist_ok=True)

    with open(os.path.join(config_dir, 'rss_feeds.txt'), 'w') as f:
        f.write(rss_feeds)

    with open(os.path.join(config_dir, 'research_feeds.txt'), 'w') as f:
        f.write(research_feeds)

    with open(os.path.join(config_dir, 'twitter_accounts.txt'), 'w') as f:
        f.write(twitter_accounts)

    with open(os.path.join(config_dir, 'reddit_subreddits.txt'), 'w') as f:
        f.write(reddit_subs)

    with open(os.path.join(config_dir, 'bluesky_accounts.txt'), 'w') as f:
        f.write(bluesky_accounts)

    with open(os.path.join(config_dir, 'mastodon_accounts.txt'), 'w') as f:
        f.write(mastodon_accounts)

    logger.info(f"Created default configuration files in {config_dir}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='AI News Aggregation Pipeline - Multi-Agent Architecture'
    )
    parser.add_argument(
        '--config-dir', default='./config',
        help='Configuration directory'
    )
    parser.add_argument(
        '--data-dir', default='./data',
        help='Data directory'
    )
    parser.add_argument(
        '--web-dir', default='./web',
        help='Web output directory'
    )
    parser.add_argument(
        '--create-config', action='store_true',
        help='Create default config files'
    )
    parser.add_argument(
        '--date', '-d',
        help='Report date (YYYY-MM-DD or MM-DD-YYYY). Coverage is day before.'
    )
    parser.add_argument(
        '--resume', action='store_true',
        help='Auto-resume from latest checkpoint (for crash recovery)'
    )
    parser.add_argument(
        '--resume-from', type=float, metavar='PHASE',
        help='Resume from phase N (e.g., 3, 4.5, 4.7). Loads earlier phases from checkpoint.'
    )

    args = parser.parse_args()

    # Create default config if requested
    if args.create_config:
        create_default_config_files(args.config_dir)
        logger.info("Default configuration files created. Edit them and run again.")
        sys.exit(0)

    # Parse date if provided
    target_date = None
    if args.date:
        try:
            target_date = parse_date(args.date)
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)

    # Determine resume mode
    resume_from = None
    if args.resume and args.resume_from is not None:
        logger.error("Cannot use both --resume and --resume-from. Use one or the other.")
        sys.exit(1)
    elif args.resume:
        resume_from = 'auto'  # Auto-detect inside run_pipeline
    elif args.resume_from is not None:
        resume_from = args.resume_from

    # Run async pipeline
    success = asyncio.run(run_pipeline(
        args.config_dir,
        args.data_dir,
        args.web_dir,
        target_date,
        resume_from=resume_from
    ))

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
