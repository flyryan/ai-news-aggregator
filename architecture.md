# AI News Aggregation System Architecture

## System Overview

The AI News Aggregation System is a Docker-based workflow that automatically collects, analyzes, and presents AI news from multiple sources on a daily basis. The system runs locally and generates a browsable website with comprehensive AI news coverage for the previous 24-hour period.

## Architecture Components

### 1. Data Collection Layer

The data collection layer fetches content from multiple sources in parallel:

**RSS Feed Collector**
- Fetches and parses 100+ RSS feeds from AI news sites, blogs, and research organizations
- Uses Python `feedparser` library
- Implements rate limiting and error handling
- Stores raw feed data with timestamps

**arXiv Paper Collector**
- Monitors key arXiv categories (cs.AI, cs.LG, cs.CL, cs.CV)
- Fetches daily paper submissions
- Extracts titles, abstracts, authors, and links
- Filters by publication date (last 24 hours)

**Social Media Collector**
- **Twitter/X**: Uses Manus Data API to fetch tweets from key accounts and search terms
- **Reddit**: Uses Manus Data API to fetch hot posts from AI-related subreddits
- **YouTube**: Fetches latest videos from AI channels via RSS feeds
- **GitHub**: Monitors trending AI repositories via GitHub API

**Hacker News Collector**
- Uses Algolia HN API to fetch AI-related stories
- Filters by score and relevance
- Tracks comments and engagement

### 2. Data Processing Layer

**Content Normalizer**
- Standardizes data format across all sources
- Extracts key fields: title, content, URL, timestamp, source, author
- Removes duplicates based on URL and content similarity
- Validates and cleans HTML/Markdown content

**Content Deduplicator**
- Uses content hashing to identify duplicates
- Implements fuzzy matching for similar articles
- Maintains a 7-day rolling window of seen content
- Preserves the earliest/most authoritative source

**Metadata Enricher**
- Extracts entities (companies, people, technologies)
- Identifies topics and categories
- Calculates engagement metrics (views, likes, comments)
- Assigns relevance scores

### 3. AI Analysis Layer

**LLM-Powered Analysis** (Using Claude Opus 4.5 via LiteLLM)

The system uses Claude Opus 4.5 to perform sophisticated analysis:

**Content Summarization**
- Generates concise summaries for each article/paper
- Extracts key findings and implications
- Identifies technical details and methodologies

**Topic Clustering**
- Groups related content into themes
- Identifies emerging trends
- Detects breaking news and major announcements

**Importance Ranking**
- Scores content by significance to AI field
- Considers source authority, engagement, and novelty
- Prioritizes breaking news and research breakthroughs

**Executive Summary Generation**
- Creates a daily overview of AI developments
- Highlights top stories and trends
- Provides context and analysis

**Trend Detection**
- Identifies recurring themes across sources
- Tracks sentiment and discussion volume
- Flags potential paradigm shifts or controversies

### 4. Content Generation Layer

**Daily Report Generator**
- Compiles analyzed content into structured report
- Organizes by categories: Research, Industry, Products, Policy, etc.
- Includes executive summary, top stories, and detailed sections
- Generates metadata for web presentation

**Web Page Generator**
- Creates static HTML pages from report data
- Implements responsive design for desktop and mobile
- Includes navigation, search, and filtering capabilities
- Generates RSS feed for the daily reports

### 5. Storage Layer

**Database** (SQLite for simplicity, can upgrade to PostgreSQL)
- Stores raw collected data
- Maintains processed and analyzed content
- Tracks historical data for trend analysis
- Implements efficient indexing for queries

**File Storage**
- Stores generated HTML pages
- Maintains asset files (CSS, JS, images)
- Archives daily reports
- Implements cleanup policies for old data

### 6. Web Serving Layer

**Web Server** (Nginx)
- Serves static HTML pages
- Handles routing and redirects
- Implements caching for performance
- Provides SSL/TLS support (optional)

**Web Interface**
- Homepage with latest daily report
- Archive page with historical reports
- Search and filter functionality
- Category and topic navigation
- Responsive design for all devices

## Workflow Orchestration

### Daily Execution Schedule

The system runs on a daily schedule (configurable, default: 6 AM local time):

**Phase 1: Data Collection** (30-60 minutes)
1. Fetch RSS feeds in parallel
2. Collect arXiv papers
3. Gather social media content
4. Fetch GitHub trending repos
5. Collect Hacker News stories

**Phase 2: Data Processing** (15-30 minutes)
1. Normalize all collected data
2. Deduplicate content
3. Enrich with metadata
4. Store in database

**Phase 3: AI Analysis** (30-60 minutes)
1. Summarize individual items
2. Cluster by topics
3. Rank by importance
4. Generate executive summary
5. Detect trends

**Phase 4: Content Generation** (10-15 minutes)
1. Generate daily report structure
2. Create HTML pages
3. Update navigation and indexes
4. Generate RSS feed

**Phase 5: Deployment** (5 minutes)
1. Copy files to web server directory
2. Update database
3. Clear caches
4. Send completion notification

### Error Handling

- Each collection source has independent error handling
- Failed sources don't block the entire pipeline
- Retry logic with exponential backoff
- Logging and alerting for persistent failures
- Graceful degradation when sources are unavailable

## Technology Stack

### Core Technologies
- **Python 3.11+**: Main programming language
- **Docker & Docker Compose**: Containerization and orchestration
- **SQLite/PostgreSQL**: Data storage
- **Nginx**: Web server
- **LiteLLM**: LLM API gateway for Claude Opus 4.5

### Python Libraries
- **feedparser**: RSS feed parsing
- **requests**: HTTP client
- **beautifulsoup4**: HTML parsing
- **python-dateutil**: Date handling
- **schedule**: Job scheduling
- **jinja2**: Template engine for HTML generation
- **sqlite3/psycopg2**: Database connectivity
- **litellm**: LLM API client

### Frontend Technologies
- **HTML5/CSS3**: Web page structure and styling
- **Vanilla JavaScript**: Interactivity (no heavy frameworks)
- **TailwindCSS**: Utility-first CSS framework
- **Alpine.js**: Lightweight JavaScript framework (optional)

## Docker Architecture

### Container Structure

**1. Collector Container**
- Runs data collection scripts
- Scheduled via cron or Python schedule
- Mounts volume for data storage
- Environment variables for API keys

**2. Processor Container**
- Runs data processing and AI analysis
- Accesses LiteLLM endpoint
- Mounts shared volume with collector
- CPU and memory optimized

**3. Web Server Container**
- Runs Nginx
- Serves static files
- Mounts volume with generated content
- Exposes port 80/443

**4. Database Container** (optional, if using PostgreSQL)
- Runs PostgreSQL
- Persistent volume for data
- Backup and restore capabilities

### Docker Compose Configuration

All containers are orchestrated via Docker Compose with:
- Shared networks for inter-container communication
- Named volumes for data persistence
- Environment variable configuration
- Health checks and restart policies
- Resource limits and constraints

## Configuration

### Environment Variables

```
# LiteLLM Configuration
LITELLM_API_BASE=http://your-litellm-endpoint:4000
LITELLM_API_KEY=your-api-key
LITELLM_MODEL=claude-opus-4.5

# Data Collection
COLLECTION_SCHEDULE=0 6 * * *  # Daily at 6 AM
LOOKBACK_HOURS=24
MAX_ITEMS_PER_SOURCE=100

# Manus Data API (for Twitter/Reddit)
MANUS_API_KEY=your-manus-api-key

# GitHub API
GITHUB_TOKEN=your-github-token

# Database
DATABASE_TYPE=sqlite  # or postgresql
DATABASE_PATH=/data/ai_news.db

# Web Server
WEB_PORT=8080
WEB_HOST=0.0.0.0

# Logging
LOG_LEVEL=INFO
LOG_PATH=/logs
```

### Configuration Files

- `config.yaml`: Main configuration file
- `sources.yaml`: List of RSS feeds and sources
- `twitter_accounts.yaml`: Twitter accounts to monitor
- `reddit_subreddits.yaml`: Reddit communities to track
- `keywords.yaml`: Search keywords and filters

## Data Flow

```
Sources → Collectors → Raw Data Storage
                           ↓
                    Data Processor
                           ↓
                    Normalized Data
                           ↓
                    Deduplicator
                           ↓
                    Unique Content
                           ↓
                    AI Analyzer (Claude Opus 4.5)
                           ↓
                    Analyzed Content
                           ↓
                    Report Generator
                           ↓
                    HTML Pages
                           ↓
                    Web Server (Nginx)
                           ↓
                    User Browser
```

## Scalability Considerations

### Current Design (Local Server)
- Single Docker host
- SQLite database
- File-based storage
- Suitable for personal/team use

### Future Scaling Options
- PostgreSQL for better concurrency
- Redis for caching and job queues
- Celery for distributed task processing
- Object storage (S3) for files
- Load balancer for multiple web servers
- Kubernetes for container orchestration

## Security Considerations

- API keys stored in environment variables
- No sensitive data in source code
- Rate limiting on all external API calls
- Input validation and sanitization
- Regular security updates for dependencies
- Optional: SSL/TLS for web access
- Optional: Authentication for web interface

## Monitoring and Maintenance

### Logging
- Structured logging for all components
- Log rotation and archival
- Error tracking and alerting

### Metrics
- Collection success rates
- Processing times
- API usage and costs
- Storage utilization
- Web traffic statistics

### Maintenance Tasks
- Daily: Automated collection and generation
- Weekly: Review logs and errors
- Monthly: Database cleanup and optimization
- Quarterly: Dependency updates
- As needed: Source list updates

## Extensibility

The system is designed to be easily extensible:

- **New Sources**: Add to configuration files
- **New Analysis**: Extend AI analyzer with new prompts
- **New Visualizations**: Modify HTML templates
- **New Export Formats**: Add export modules (PDF, Email, etc.)
- **Integration**: Webhooks for external systems
