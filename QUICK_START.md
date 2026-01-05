# Quick Start Guide

Get your AI News Aggregator running in 5 minutes!

## Prerequisites

- Docker and Docker Compose installed
- LiteLLM endpoint with Claude Opus 4.5 access
- Your LiteLLM API key

## Installation

### 1. Extract Files

```bash
# Extract the archive
tar -xzf ai-news-aggregator.tar.gz
cd ai-news-aggregator
```

### 2. Configure

```bash
# Copy environment template
cp .env.example .env

# Edit with your LiteLLM settings
nano .env
```

**Required settings in .env:**
```
LITELLM_API_BASE=http://your-litellm-server:4000
LITELLM_API_KEY=your-api-key-here
LITELLM_MODEL=claude-opus-4.5
```

### 3. Launch

```bash
# Build and start
docker compose up -d

# Watch logs
docker logs -f ai-news-aggregator
```

### 4. Access

Open your browser to:
```
http://localhost:8080
```

## First Run

The system will:
1. Create default configuration files (takes ~1 minute)
2. Collect data from 100+ sources (takes ~5-10 minutes)
3. Analyze with Claude Opus 4.5 (takes ~3-5 minutes)
4. Generate the website (takes ~1 minute)

**Total first run time: ~10-20 minutes**

## Customization

### Add Your Favorite Sources

```bash
# Edit RSS feeds
nano config/rss_feeds.txt

# Edit Twitter accounts
nano config/twitter_accounts.txt

# Edit Reddit subreddits
nano config/reddit_subreddits.txt

# Restart to apply
docker compose restart
```

### Change Schedule

Edit `.env` and change `COLLECTION_SCHEDULE`:
```
# Daily at 6 AM (default)
COLLECTION_SCHEDULE=0 6 * * *

# Every 6 hours
COLLECTION_SCHEDULE=0 */6 * * *

# Twice daily (9 AM and 6 PM)
COLLECTION_SCHEDULE=0 9,18 * * *
```

Then restart:
```bash
docker compose restart
```

## Manual Update

To run the pipeline manually anytime:

```bash
docker exec ai-news-aggregator python3 /app/run_pipeline.py
```

## Common Commands

```bash
# View logs
docker logs ai-news-aggregator

# Follow logs in real-time
docker logs -f ai-news-aggregator

# Restart
docker compose restart

# Stop
docker compose down

# Start
docker compose up -d

# Rebuild after changes
docker compose build
docker compose up -d
```

## Troubleshooting

### Container won't start
```bash
# Check logs
docker logs ai-news-aggregator

# Verify environment
docker exec ai-news-aggregator env | grep LITELLM
```

### Website not accessible
```bash
# Check if nginx is running
docker exec ai-news-aggregator ps aux | grep nginx

# Check port
docker ps
```

### No data collected
```bash
# Check internet connectivity
docker exec ai-news-aggregator ping -c 3 google.com

# Check config files exist
docker exec ai-news-aggregator ls -l /app/config/
```

### LLM errors
```bash
# Test LiteLLM endpoint
curl -v http://your-litellm-server:4000

# Verify API key in container
docker exec ai-news-aggregator env | grep LITELLM_API_KEY
```

## What's Collected?

- **100+ RSS Feeds**: AI news sites, tech blogs, research blogs
- **arXiv Papers**: Latest AI research (cs.AI, cs.LG, cs.CL, cs.CV)
- **Twitter**: Key AI researchers and companies (requires Manus API)
- **Reddit**: AI-related subreddits (requires Manus API)

## What's Generated?

- **Executive Summary**: Daily overview of AI developments
- **Top Stories**: Most important news ranked by Claude Opus 4.5
- **Trends**: Emerging themes and patterns
- **Categories**: Research, Industry, Products, Models, Policy, etc.
- **Full Articles**: Links to original sources

## Next Steps

1. ✅ Wait for first collection to complete
2. ✅ Browse the generated website
3. ✅ Customize RSS feeds and sources
4. ✅ Adjust schedule to your preference
5. ✅ Set up monitoring and backups (see DEPLOYMENT_GUIDE.md)

## Need Help?

- Check logs: `docker logs ai-news-aggregator`
- Review DEPLOYMENT_GUIDE.md for detailed instructions
- Verify LiteLLM endpoint is accessible
- Ensure API key is correct

## Tips

- **First run takes longer**: Initial setup and data collection
- **Subsequent runs are faster**: Only new data is collected
- **Customize sources**: Focus on your areas of interest
- **Adjust schedule**: Match your reading habits
- **Monitor costs**: LLM analysis uses API credits

---

**You're all set!** Your AI News Aggregator will now keep you informed with daily AI news digests.
