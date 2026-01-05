# AI News Aggregator

A comprehensive Docker-based workflow that automatically collects, analyzes, and presents AI news from multiple sources daily. Powered by Claude Opus 4.5 via LiteLLM.

## Features

- **Multi-Source Collection**: Aggregates from 100+ RSS feeds, arXiv papers, Twitter, Reddit, and more
- **AI-Powered Analysis**: Uses Claude Opus 4.5 to summarize, categorize, and rank news items
- **Daily Website Generation**: Creates a beautiful, browsable website with the day's AI news
- **Automated Workflow**: Runs on a schedule (default: daily at 6 AM)
- **Docker-Based**: Easy deployment with Docker Compose
- **Trend Detection**: Identifies emerging themes and important developments
- **Executive Summaries**: Provides high-level overviews for busy professionals

## Architecture

The system consists of four main phases:

1. **Data Collection**: Fetches content from RSS feeds, arXiv, Twitter, Reddit, and other sources
2. **Data Processing**: Normalizes, deduplicates, and enriches collected data
3. **LLM Analysis**: Uses Claude Opus 4.5 to analyze, summarize, and categorize content
4. **HTML Generation**: Creates a static website with the analyzed news

## Prerequisites

- Docker and Docker Compose installed
- LiteLLM endpoint with access to Claude Opus 4.5 (or compatible model)
- (Optional) Manus Data API access for Twitter/Reddit collection

## Quick Start

### 1. Clone or Download

```bash
# If you have the files, navigate to the directory
cd ai-news-aggregator
```

### 2. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your settings
nano .env
```

Required environment variables:
- `LITELLM_API_BASE`: Your LiteLLM endpoint URL
- `LITELLM_API_KEY`: Your API key
- `LITELLM_MODEL`: Model name (default: claude-opus-4.5)

### 3. Build and Run

```bash
# Build the Docker image
docker-compose build

# Start the container
docker-compose up -d
```

The system will:
1. Create default configuration files in `./config/`
2. Run an initial collection and analysis
3. Start the web server on port 8080
4. Schedule daily updates

### 4. Access the Website

Open your browser and navigate to:
```
http://localhost:8080
```

## Configuration

### RSS Feeds

Edit `config/rss_feeds.txt` to add or remove RSS feeds:

```
# One feed URL per line
https://feeds.arstechnica.com/arstechnica/index
https://www.wired.com/feed/tag/ai/latest/rss
https://venturebeat.com/category/ai/feed/
```

### Twitter Accounts

Edit `config/twitter_accounts.txt` to monitor specific Twitter accounts:

```
# One username per line (without @)
sama
karpathy
OpenAI
AnthropicAI
```

**Note**: Twitter collection requires Manus Data API access. The system will skip Twitter if the API is not available.

### Reddit Subreddits

Edit `config/reddit_subreddits.txt` to monitor subreddits:

```
# One subreddit per line (without r/)
MachineLearning
artificial
LocalLLaMA
```

**Note**: Reddit collection requires Manus Data API access. The system will skip Reddit if the API is not available.

### Schedule

The collection schedule is set via the `COLLECTION_SCHEDULE` environment variable in `.env`:

```
# Cron format: minute hour day month weekday
COLLECTION_SCHEDULE=0 6 * * *  # Daily at 6 AM
```

Examples:
- `0 6 * * *` - Daily at 6 AM
- `0 */6 * * *` - Every 6 hours
- `0 9,18 * * *` - Twice daily at 9 AM and 6 PM

## Manual Execution

To run the pipeline manually:

```bash
# Enter the container
docker exec -it ai-news-aggregator bash

# Run the pipeline
python3 /app/run_pipeline.py
```

Or from outside the container:

```bash
docker exec ai-news-aggregator python3 /app/run_pipeline.py
```

## Directory Structure

```
ai-news-aggregator/
├── collectors/           # Data collection modules
│   ├── rss_collector.py
│   ├── arxiv_collector.py
│   └── social_collector.py
├── processors/          # Data processing modules
│   ├── data_processor.py
│   └── llm_analyzer.py
├── generators/          # HTML generation
│   └── html_generator.py
├── config/             # Configuration files (created on first run)
│   ├── rss_feeds.txt
│   ├── twitter_accounts.txt
│   └── reddit_subreddits.txt
├── data/               # Data storage
│   ├── raw/           # Raw collected data
│   └── processed/     # Processed and analyzed data
├── web/               # Generated website
├── logs/              # Application logs
├── templates/         # HTML templates (created on first run)
├── run_pipeline.py    # Main orchestration script
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── nginx.conf
├── entrypoint.sh
└── README.md
```

## Data Sources

### RSS Feeds (100+ sources)

The system monitors a curated list of AI news sources including:

- **Major Tech News**: Ars Technica, WIRED, VentureBeat, The Guardian
- **AI-Specific Sites**: AI Business, Analytics India Magazine, MarkTechPost
- **Research Blogs**: DeepMind, Hugging Face, LangChain, Cohere
- **Industry Analysis**: Chain of Thought, Last Week in AI, Latent Space
- **Academic**: ScienceDaily AI, Nature ML, MIT News ML

### arXiv Papers

Monitors key categories:
- cs.AI (Artificial Intelligence)
- cs.LG (Machine Learning)
- cs.CL (Computation and Language)
- cs.CV (Computer Vision)
- cs.NE (Neural and Evolutionary Computing)

### Social Media

- **Twitter**: Monitors key AI researchers, companies, and thought leaders
- **Reddit**: Tracks discussions in AI-related subreddits
- **YouTube**: (Future enhancement) AI channels and videos

## LLM Analysis Features

Claude Opus 4.5 performs the following analysis:

1. **Content Summarization**: Generates concise summaries of articles and papers
2. **Categorization**: Groups content into topics (Research, Industry, Products, etc.)
3. **Importance Ranking**: Identifies the most significant developments
4. **Executive Summary**: Creates a daily overview of AI developments
5. **Trend Detection**: Identifies emerging themes and patterns

## Customization

### Adding New Data Sources

1. Create a new collector module in `collectors/`
2. Implement the collection logic
3. Update `run_pipeline.py` to include the new collector
4. Rebuild the Docker image

### Modifying the Website

1. Edit templates in `templates/` directory
2. Customize CSS in `templates/base.html`
3. Modify `generators/html_generator.py` for structural changes
4. Rebuild and restart the container

### Using Different LLM Models

The system supports any model available through LiteLLM:

```bash
# In .env file
LITELLM_MODEL=gpt-4
# or
LITELLM_MODEL=claude-3-opus-20240229
# or any other supported model
```

## Monitoring

### View Logs

```bash
# Application logs
docker logs ai-news-aggregator

# Cron logs
docker exec ai-news-aggregator cat /app/logs/cron.log

# Nginx logs
docker exec ai-news-aggregator cat /app/logs/nginx-access.log
```

### Check Status

```bash
# Container status
docker ps

# Health check
curl http://localhost:8080
```

## Troubleshooting

### Pipeline Fails to Run

1. Check logs: `docker logs ai-news-aggregator`
2. Verify LiteLLM endpoint is accessible
3. Ensure API key is correct in `.env`
4. Check if config files exist in `config/`

### No Data Collected

1. Verify RSS feeds are accessible
2. Check internet connectivity from container
3. Review collection logs in `logs/`
4. Some sources may be temporarily unavailable (system continues with available sources)

### Website Not Accessible

1. Check if nginx is running: `docker exec ai-news-aggregator ps aux | grep nginx`
2. Verify port mapping: `docker ps`
3. Check nginx logs: `docker exec ai-news-aggregator cat /app/logs/nginx-error.log`

### LLM Analysis Fails

1. Verify LiteLLM endpoint is accessible from container
2. Check API key and model name
3. Ensure model supports required context length
4. Review analysis logs for specific errors

## Performance Optimization

### Reduce Collection Time

- Limit number of RSS feeds
- Reduce `LOOKBACK_HOURS` to collect less data
- Adjust parallel workers in collector modules

### Reduce LLM Costs

- Limit number of items analyzed
- Use a smaller/cheaper model for less critical analysis
- Adjust summarization depth in `llm_analyzer.py`

### Improve Website Performance

- Enable caching in nginx (already configured)
- Reduce number of items per category page
- Optimize images and assets

## Backup and Archival

### Backup Data

```bash
# Backup all data
docker exec ai-news-aggregator tar -czf /app/backup.tar.gz /app/data /app/config

# Copy to host
docker cp ai-news-aggregator:/app/backup.tar.gz ./backup.tar.gz
```

### Archive Old Reports

The system stores all generated reports in `web/`. To archive:

```bash
# Create archive directory
mkdir -p archives

# Move old reports
mv web/archive/YYYY-MM-DD archives/
```

## Advanced Usage

### Running Without Docker

```bash
# Install dependencies
pip install -r requirements.txt

# Create config
python3 run_pipeline.py --create-config

# Run pipeline
python3 run_pipeline.py
```

### Integrating with External Systems

The pipeline generates JSON files that can be consumed by other systems:

- `data/processed/processed.json`: All collected and normalized items
- `data/processed/analyzed.json`: Complete analysis results

### Custom Analysis Prompts

Edit `processors/llm_analyzer.py` to customize prompts for:
- Summarization style
- Categorization criteria
- Ranking factors
- Trend detection sensitivity

## Security Considerations

- Store API keys securely in `.env` file (never commit to version control)
- Use Docker secrets for production deployments
- Implement authentication if exposing website publicly
- Regularly update dependencies for security patches
- Use HTTPS if accessing over network (configure reverse proxy)

## Contributing

To contribute improvements:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is provided as-is for personal and commercial use.

## Support

For issues and questions:
- Check logs for error messages
- Review configuration files
- Ensure all prerequisites are met
- Verify API endpoints are accessible

## Roadmap

Future enhancements:
- [ ] Email digest delivery
- [ ] Slack/Discord integration
- [ ] PDF report generation
- [ ] Search functionality
- [ ] Historical trend analysis
- [ ] Multi-language support
- [ ] Mobile app
- [ ] RSS feed output
- [ ] API for external access
- [ ] Machine learning for personalization

## Acknowledgments

- Built for AI professionals who need to stay informed
- Powered by Claude Opus 4.5 for intelligent analysis
- Inspired by the need for comprehensive AI news aggregation
- Uses open-source tools and libraries

---

**Note**: This system is designed for local deployment on a server you control. For production use at scale, consider additional infrastructure for reliability, monitoring, and performance.
