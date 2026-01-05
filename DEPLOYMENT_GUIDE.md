# AI News Aggregator - Deployment Guide

This guide provides step-by-step instructions for deploying the AI News Aggregator on your local server.

## Prerequisites Checklist

Before you begin, ensure you have:

- [ ] Docker installed (version 20.10 or higher)
- [ ] Docker Compose installed (version 2.0 or higher)
- [ ] LiteLLM endpoint running with Claude Opus 4.5 access
- [ ] LiteLLM API key
- [ ] At least 10GB free disk space
- [ ] Stable internet connection
- [ ] (Optional) Manus Data API key for Twitter/Reddit collection

## Step 1: Prepare the Server

### Install Docker (if not already installed)

**Ubuntu/Debian:**
```bash
# Update package index
sudo apt-get update

# Install dependencies
sudo apt-get install -y apt-transport-https ca-certificates curl software-properties-common

# Add Docker's official GPG key
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# Set up the stable repository
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Verify installation
docker --version
docker compose version
```

**CentOS/RHEL:**
```bash
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl start docker
sudo systemctl enable docker
```

### Add User to Docker Group (optional, for non-root access)

```bash
sudo usermod -aG docker $USER
newgrp docker
```

## Step 2: Deploy the Application

### Option A: From Source Files

1. **Copy files to server:**
   ```bash
   # Create project directory
   mkdir -p ~/ai-news-aggregator
   cd ~/ai-news-aggregator
   
   # Copy all project files here
   # (use scp, rsync, or git clone if using version control)
   ```

2. **Configure environment:**
   ```bash
   # Copy example environment file
   cp .env.example .env
   
   # Edit with your settings
   nano .env
   ```

3. **Set your LiteLLM configuration:**
   ```
   LITELLM_API_BASE=http://your-litellm-server:4000
   LITELLM_API_KEY=your-api-key-here
   LITELLM_MODEL=claude-opus-4.5
   ```

4. **Build and start:**
   ```bash
   # Build the Docker image
   docker compose build
   
   # Start the container
   docker compose up -d
   ```

### Option B: Quick Start with Pre-built Image (if available)

```bash
# Pull the image
docker pull your-registry/ai-news-aggregator:latest

# Run with docker-compose
docker compose up -d
```

## Step 3: Initial Configuration

### 1. Wait for Initial Setup

The container will automatically:
- Create default configuration files
- Run an initial data collection
- Generate the first website

Monitor progress:
```bash
docker logs -f ai-news-aggregator
```

### 2. Customize Data Sources

Edit the configuration files:

```bash
# RSS feeds
nano config/rss_feeds.txt

# Twitter accounts
nano config/twitter_accounts.txt

# Reddit subreddits
nano config/reddit_subreddits.txt
```

### 3. Restart to Apply Changes

```bash
docker compose restart
```

## Step 4: Verify Deployment

### Check Container Status

```bash
# View running containers
docker ps

# Should show ai-news-aggregator with status "Up"
```

### Check Logs

```bash
# View application logs
docker logs ai-news-aggregator

# Follow logs in real-time
docker logs -f ai-news-aggregator
```

### Access the Website

Open your browser and navigate to:
```
http://localhost:8080
```

Or if accessing from another machine:
```
http://your-server-ip:8080
```

### Verify Data Collection

```bash
# Check if data was collected
docker exec ai-news-aggregator ls -lh /app/data/raw/

# Check if website was generated
docker exec ai-news-aggregator ls -lh /app/web/
```

## Step 5: Configure Firewall (if needed)

### UFW (Ubuntu)

```bash
# Allow port 8080
sudo ufw allow 8080/tcp

# Reload firewall
sudo ufw reload
```

### firewalld (CentOS/RHEL)

```bash
# Allow port 8080
sudo firewall-cmd --permanent --add-port=8080/tcp

# Reload firewall
sudo firewall-cmd --reload
```

## Step 6: Set Up Reverse Proxy (Optional)

For production use, set up nginx or Apache as a reverse proxy with SSL.

### Example nginx Configuration

```nginx
server {
    listen 80;
    server_name ai-news.yourdomain.com;
    
    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Add SSL with Let's Encrypt

```bash
# Install certbot
sudo apt-get install -y certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d ai-news.yourdomain.com

# Auto-renewal is configured automatically
```

## Step 7: Configure Scheduled Updates

The system automatically runs daily at 6 AM by default. To change:

1. Edit `.env`:
   ```bash
   nano .env
   ```

2. Modify `COLLECTION_SCHEDULE`:
   ```
   # Examples:
   COLLECTION_SCHEDULE=0 6 * * *    # Daily at 6 AM
   COLLECTION_SCHEDULE=0 */6 * * *  # Every 6 hours
   COLLECTION_SCHEDULE=0 9,18 * * * # Twice daily at 9 AM and 6 PM
   ```

3. Restart container:
   ```bash
   docker compose restart
   ```

## Step 8: Set Up Monitoring (Optional)

### Basic Health Check Script

Create `health_check.sh`:
```bash
#!/bin/bash
if curl -f http://localhost:8080 > /dev/null 2>&1; then
    echo "$(date): Service is healthy"
else
    echo "$(date): Service is down! Restarting..."
    cd /path/to/ai-news-aggregator
    docker compose restart
fi
```

Add to crontab:
```bash
# Check every 15 minutes
*/15 * * * * /path/to/health_check.sh >> /var/log/ai-news-health.log 2>&1
```

### Log Rotation

Create `/etc/logrotate.d/ai-news-aggregator`:
```
/path/to/ai-news-aggregator/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
```

## Troubleshooting

### Container Won't Start

1. Check logs:
   ```bash
   docker logs ai-news-aggregator
   ```

2. Verify environment variables:
   ```bash
   docker exec ai-news-aggregator env | grep LITELLM
   ```

3. Check port availability:
   ```bash
   sudo netstat -tulpn | grep 8080
   ```

### LiteLLM Connection Issues

1. Test connectivity from container:
   ```bash
   docker exec ai-news-aggregator curl -v $LITELLM_API_BASE
   ```

2. Verify API key:
   ```bash
   docker exec ai-news-aggregator env | grep LITELLM_API_KEY
   ```

3. Check LiteLLM server logs

### No Data Collected

1. Check internet connectivity:
   ```bash
   docker exec ai-news-aggregator ping -c 3 google.com
   ```

2. Verify RSS feeds are accessible:
   ```bash
   docker exec ai-news-aggregator curl -I https://feeds.arstechnica.com/arstechnica/index
   ```

3. Review collection logs:
   ```bash
   docker exec ai-news-aggregator cat /app/logs/cron.log
   ```

### Website Not Updating

1. Check cron is running:
   ```bash
   docker exec ai-news-aggregator ps aux | grep cron
   ```

2. Verify cron schedule:
   ```bash
   docker exec ai-news-aggregator crontab -l
   ```

3. Manually trigger update:
   ```bash
   docker exec ai-news-aggregator python3 /app/run_pipeline.py
   ```

## Maintenance Tasks

### Daily

- Monitor logs for errors
- Verify website is accessible
- Check disk space usage

### Weekly

- Review collected data quality
- Update RSS feed list if needed
- Check for application updates

### Monthly

- Backup data and configuration
- Review and optimize performance
- Update dependencies for security

## Backup Procedures

### Backup Data

```bash
# Create backup directory
mkdir -p ~/backups

# Backup data and config
docker exec ai-news-aggregator tar -czf /tmp/backup.tar.gz /app/data /app/config
docker cp ai-news-aggregator:/tmp/backup.tar.gz ~/backups/ai-news-$(date +%Y%m%d).tar.gz
```

### Restore from Backup

```bash
# Stop container
docker compose down

# Extract backup
tar -xzf ~/backups/ai-news-YYYYMMDD.tar.gz -C ./

# Start container
docker compose up -d
```

## Updating the Application

### Update to New Version

```bash
# Pull latest changes (if using git)
git pull

# Rebuild image
docker compose build

# Restart with new image
docker compose down
docker compose up -d
```

### Update Dependencies

```bash
# Edit requirements.txt
nano requirements.txt

# Rebuild
docker compose build

# Restart
docker compose restart
```

## Performance Tuning

### Optimize for Large Datasets

Edit `docker-compose.yml` to increase resources:

```yaml
services:
  ai-news-aggregator:
    # ... existing config ...
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 2G
```

### Reduce Collection Time

Edit configuration files to:
- Reduce number of RSS feeds
- Limit lookback period
- Adjust parallel workers

### Reduce LLM Costs

In `.env`:
```
# Use a smaller model
LITELLM_MODEL=claude-3-sonnet-20240229

# Or reduce lookback period
LOOKBACK_HOURS=12
```

## Security Best Practices

1. **Protect API Keys**
   - Never commit `.env` to version control
   - Use Docker secrets for production
   - Rotate keys regularly

2. **Network Security**
   - Use firewall to restrict access
   - Implement SSL/TLS for external access
   - Consider VPN for remote access

3. **Container Security**
   - Run as non-root user (future enhancement)
   - Keep Docker and images updated
   - Scan images for vulnerabilities

4. **Data Privacy**
   - Review collected data regularly
   - Implement data retention policies
   - Secure backup storage

## Getting Help

If you encounter issues:

1. Check logs for error messages
2. Review this deployment guide
3. Verify all prerequisites are met
4. Test LiteLLM endpoint separately
5. Check Docker and system resources

## Next Steps

After successful deployment:

1. Customize the RSS feed list for your interests
2. Adjust the collection schedule
3. Explore the generated website
4. Set up monitoring and alerts
5. Configure backups
6. Consider adding authentication for external access

## Appendix: System Requirements

### Minimum Requirements

- CPU: 2 cores
- RAM: 4GB
- Disk: 10GB free space
- Network: Stable internet connection

### Recommended Requirements

- CPU: 4 cores
- RAM: 8GB
- Disk: 50GB free space (for historical data)
- Network: High-speed internet

### Estimated Resource Usage

- Docker image: ~500MB
- Data per day: ~50-100MB
- Peak memory: ~2-3GB during analysis
- CPU: Moderate during collection, high during analysis

---

**Congratulations!** Your AI News Aggregator is now deployed and running. Check back daily for fresh AI news insights!
