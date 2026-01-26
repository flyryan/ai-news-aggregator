# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

# Copy package files
COPY frontend/package*.json ./

# Install dependencies
RUN npm ci

# Copy frontend source
COPY frontend/ ./

# Build the frontend (outputs to ../web)
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    cron \
    nginx \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agents/ ./agents/
COPY generators/ ./generators/
COPY scripts/ ./scripts/
COPY assets/ ./assets/
COPY run_pipeline.py .
COPY entrypoint.sh .

# Create necessary directories
RUN mkdir -p /app/config /app/data /app/web /app/logs

# Copy built frontend from stage 1
COPY --from=frontend-builder /frontend/../web ./web/

# Make scripts executable
RUN chmod +x run_pipeline.py entrypoint.sh

# Configure nginx
COPY nginx.conf /etc/nginx/sites-available/default

# Expose web port
EXPOSE 80

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
