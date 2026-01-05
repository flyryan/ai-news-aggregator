FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    cron \
    nginx \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY collectors/ ./collectors/
COPY processors/ ./processors/
COPY generators/ ./generators/
COPY run_pipeline.py .
COPY entrypoint.sh .

# Create necessary directories
RUN mkdir -p /app/config /app/data /app/web /app/logs /app/templates

# Make scripts executable
RUN chmod +x run_pipeline.py entrypoint.sh

# Configure nginx
COPY nginx.conf /etc/nginx/sites-available/default

# Expose web port
EXPOSE 80

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
