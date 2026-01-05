#!/usr/bin/env python3
"""
AI News Aggregation Pipeline
Main orchestration script that runs the complete workflow.
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# Add project directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'collectors'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'processors'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'generators'))

from rss_collector import RSSCollector, load_feed_list
from arxiv_collector import ArxivCollector
from social_collector import SocialMediaCollector, load_list_from_file
from data_processor import DataProcessor
from llm_analyzer import LLMAnalyzer
from html_generator import HTMLGenerator, create_default_templates

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Pipeline:
    """Main pipeline orchestrator."""

    def __init__(self, config_dir: str, data_dir: str, web_dir: str):
        """
        Initialize pipeline.

        Args:
            config_dir: Directory containing configuration files
            data_dir: Directory for data storage
            web_dir: Directory for generated website
        """
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.web_dir = web_dir
        self.lookback_hours = int(os.getenv('LOOKBACK_HOURS', '24'))
        self.target_date = os.getenv('TARGET_DATE', '')  # Format: YYYY-MM-DD
        
        # Create directories
        for dir_path in [config_dir, data_dir, web_dir]:
            os.makedirs(dir_path, exist_ok=True)
        
        # Create subdirectories
        self.raw_data_dir = os.path.join(data_dir, 'raw')
        self.processed_data_dir = os.path.join(data_dir, 'processed')
        os.makedirs(self.raw_data_dir, exist_ok=True)
        os.makedirs(self.processed_data_dir, exist_ok=True)
        
        # Template directory
        self.template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        
        logger.info("Pipeline initialized")
    
    def run_collection(self) -> dict:
        """Run data collection phase."""
        logger.info("=" * 60)
        logger.info("PHASE 1: DATA COLLECTION")
        logger.info("=" * 60)
        
        collection_stats = {}
        
        # Collect RSS feeds
        try:
            rss_feeds_file = os.path.join(self.config_dir, 'rss_feeds.txt')
            if os.path.exists(rss_feeds_file):
                logger.info("Collecting RSS feeds...")
                feeds = load_feed_list(rss_feeds_file)
                collector = RSSCollector(
                    feeds,
                    lookback_hours=self.lookback_hours,
                    target_date=self.target_date if self.target_date else None
                )
                articles = collector.collect()
                output_file = os.path.join(self.raw_data_dir, 'rss.json')
                collector.save_to_file(articles, output_file)
                collection_stats['rss'] = len(articles)
            else:
                logger.warning(f"RSS feeds file not found: {rss_feeds_file}")
                collection_stats['rss'] = 0
        except Exception as e:
            logger.error(f"RSS collection failed: {e}")
            collection_stats['rss'] = 0
        
        # Collect arXiv papers
        try:
            logger.info("Collecting arXiv papers...")
            collector = ArxivCollector(
                lookback_hours=self.lookback_hours,
                target_date=self.target_date if self.target_date else None
            )
            papers = collector.collect()
            output_file = os.path.join(self.raw_data_dir, 'arxiv.json')
            collector.save_to_file(papers, output_file)
            collection_stats['arxiv'] = len(papers)
        except Exception as e:
            logger.error(f"arXiv collection failed: {e}")
            collection_stats['arxiv'] = 0
        
        # Collect social media
        try:
            logger.info("Collecting social media...")
            collector = SocialMediaCollector(
                lookback_hours=self.lookback_hours,
                target_date=self.target_date if self.target_date else None
            )

            # Twitter (using TwitterAPI.io batch search - more efficient)
            twitter_file = os.path.join(self.config_dir, 'twitter_accounts.txt')
            if os.path.exists(twitter_file):
                usernames = load_list_from_file(twitter_file)
                if usernames:
                    # Use batch search for efficiency (fewer API calls)
                    tweets = collector.collect_twitter_search(usernames)
                    if tweets:
                        output_file = os.path.join(self.raw_data_dir, 'twitter.json')
                        collector.save_to_file(tweets, output_file, 'twitter')
                        collection_stats['twitter'] = len(tweets)
                    else:
                        logger.warning("No tweets collected (check TWITTERAPI_IO_KEY)")

            # Reddit (using free JSON endpoint)
            reddit_file = os.path.join(self.config_dir, 'reddit_subreddits.txt')
            if os.path.exists(reddit_file):
                subreddits = load_list_from_file(reddit_file)
                if subreddits:
                    posts = collector.collect_reddit_json(subreddits)
                    if posts:
                        output_file = os.path.join(self.raw_data_dir, 'reddit.json')
                        collector.save_to_file(posts, output_file, 'reddit')
                        collection_stats['reddit'] = len(posts)

            # Bluesky (public API)
            bluesky_file = os.path.join(self.config_dir, 'bluesky_accounts.txt')
            if os.path.exists(bluesky_file):
                handles = load_list_from_file(bluesky_file)
                if handles:
                    posts = collector.collect_bluesky(handles)
                    output_file = os.path.join(self.raw_data_dir, 'bluesky.json')
                    collector.save_to_file(posts, output_file, 'bluesky')
                    collection_stats['bluesky'] = len(posts)

            # Mastodon (public API)
            mastodon_file = os.path.join(self.config_dir, 'mastodon_accounts.txt')
            if os.path.exists(mastodon_file):
                accounts = load_list_from_file(mastodon_file)
                if accounts:
                    posts = collector.collect_mastodon(accounts)
                    output_file = os.path.join(self.raw_data_dir, 'mastodon.json')
                    collector.save_to_file(posts, output_file, 'mastodon')
                    collection_stats['mastodon'] = len(posts)
        except Exception as e:
            logger.error(f"Social media collection failed: {e}")
        
        logger.info(f"Collection complete. Stats: {collection_stats}")
        return collection_stats
    
    def run_processing(self) -> str:
        """Run data processing phase."""
        logger.info("=" * 60)
        logger.info("PHASE 2: DATA PROCESSING")
        logger.info("=" * 60)
        
        # Find all raw data files
        import glob
        data_files = glob.glob(os.path.join(self.raw_data_dir, '*.json'))
        logger.info(f"Found {len(data_files)} raw data files")
        
        # Process data
        processor = DataProcessor()
        items = processor.process(data_files)
        
        # Save processed data
        output_file = os.path.join(self.processed_data_dir, 'processed.json')
        processor.save_to_file(items, output_file)
        
        logger.info(f"Processing complete. {len(items)} unique items")
        return output_file
    
    def run_analysis(self, processed_file: str) -> str:
        """Run LLM analysis phase."""
        logger.info("=" * 60)
        logger.info("PHASE 3: LLM ANALYSIS")
        logger.info("=" * 60)
        
        # Load processed data
        with open(processed_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        items = data.get('items', [])
        logger.info(f"Analyzing {len(items)} items")
        
        # Run analysis
        analyzer = LLMAnalyzer()
        analysis = analyzer.analyze_all(items)
        
        # Save analysis
        output_file = os.path.join(self.processed_data_dir, 'analyzed.json')
        analyzer.save_analysis(analysis, output_file)
        
        logger.info("Analysis complete")
        return output_file
    
    def run_generation(self, analysis_file: str):
        """Run HTML generation phase."""
        logger.info("=" * 60)
        logger.info("PHASE 4: HTML GENERATION")
        logger.info("=" * 60)
        
        # Create templates if they don't exist
        if not os.path.exists(self.template_dir):
            logger.info("Creating default templates")
            create_default_templates(self.template_dir)
        
        # Load analysis
        with open(analysis_file, 'r', encoding='utf-8') as f:
            analysis = json.load(f)
        
        # Generate HTML
        generator = HTMLGenerator(self.template_dir, self.web_dir)
        generator.generate_all(analysis)
        
        logger.info(f"HTML generation complete. Website available at: {self.web_dir}")
    
    def run(self):
        """Run the complete pipeline."""
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("AI NEWS AGGREGATION PIPELINE STARTED")
        logger.info(f"Start time: {start_time}")
        logger.info("=" * 60)
        
        try:
            # Phase 1: Collection
            collection_stats = self.run_collection()
            
            # Phase 2: Processing
            processed_file = self.run_processing()
            
            # Phase 3: Analysis
            analysis_file = self.run_analysis(processed_file)
            
            # Phase 4: Generation
            self.run_generation(analysis_file)
            
            # Complete
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.info("=" * 60)
            logger.info("PIPELINE COMPLETED SUCCESSFULLY")
            logger.info(f"Duration: {duration:.2f} seconds")
            logger.info(f"Website: {self.web_dir}/index.html")
            logger.info("=" * 60)
            
            return True
            
        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            return False


def create_default_config_files(config_dir: str):
    """Create default configuration files."""
    
    # RSS feeds
    rss_feeds = """# AI News RSS Feeds (one per line)
# Major news sites
https://feeds.arstechnica.com/arstechnica/index
https://www.wired.com/feed/tag/ai/latest/rss
https://venturebeat.com/category/ai/feed/
https://www.theguardian.com/technology/artificialintelligenceai/rss
https://www.artificialintelligence-news.com/feed/rss/

# AI-specific sites
https://aibusiness.com/rss.xml
https://analyticsindiamag.com/feed/
https://www.marktechpost.com/feed

# Research blogs
https://deepmind.com/blog/feed/basic/
https://huggingface.co/blog/feed.xml
https://blog.langchain.dev/rss/

# Industry analysis
https://every.to/chain-of-thought/feed.xml
https://lastweekin.ai/feed
https://www.latent.space/feed
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

    with open(os.path.join(config_dir, 'twitter_accounts.txt'), 'w') as f:
        f.write(twitter_accounts)

    with open(os.path.join(config_dir, 'reddit_subreddits.txt'), 'w') as f:
        f.write(reddit_subs)

    with open(os.path.join(config_dir, 'bluesky_accounts.txt'), 'w') as f:
        f.write(bluesky_accounts)

    with open(os.path.join(config_dir, 'mastodon_accounts.txt'), 'w') as f:
        f.write(mastodon_accounts)

    logger.info(f"Created default configuration files in {config_dir}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='AI News Aggregation Pipeline')
    parser.add_argument('--config-dir', default='./config', help='Configuration directory')
    parser.add_argument('--data-dir', default='./data', help='Data directory')
    parser.add_argument('--web-dir', default='./web', help='Web output directory')
    parser.add_argument('--create-config', action='store_true', help='Create default config files')
    
    args = parser.parse_args()
    
    # Create default config if requested
    if args.create_config:
        create_default_config_files(args.config_dir)
        logger.info("Default configuration files created. Edit them and run again.")
        sys.exit(0)
    
    # Run pipeline
    pipeline = Pipeline(args.config_dir, args.data_dir, args.web_dir)
    success = pipeline.run()
    
    sys.exit(0 if success else 1)
