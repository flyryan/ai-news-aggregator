#!/bin/bash
set -e

echo "Starting AI News Aggregator..."

# Create default config files if they don't exist
if [ ! -f /app/config/rss_feeds.txt ]; then
    echo "Creating default configuration files..."
    python3 /app/run_pipeline.py --create-config --config-dir /app/config
fi

# Run pipeline once on startup
echo "Running initial pipeline execution..."
python3 /app/run_pipeline.py --config-dir /app/config --data-dir /app/data --web-dir /app/web || echo "Initial run failed, will retry on schedule"

# Set up cron job for scheduled execution
CRON_SCHEDULE="${COLLECTION_SCHEDULE:-0 6 * * *}"
echo "$CRON_SCHEDULE cd /app && python3 /app/run_pipeline.py --config-dir /app/config --data-dir /app/data --web-dir /app/web >> /app/logs/cron.log 2>&1" > /etc/cron.d/ai-news-cron
chmod 0644 /etc/cron.d/ai-news-cron
crontab /etc/cron.d/ai-news-cron

echo "Cron job scheduled: $CRON_SCHEDULE"

# Start cron in background
cron

# Start nginx in foreground
echo "Starting web server on port 80..."
nginx -g 'daemon off;'
