# Webhook Configuration

This directory contains the webhook configuration for GitHub deploy hooks.

## Setup

1. Copy the example config:
   ```bash
   cp hooks.example.json hooks.json
   ```

2. Edit paths in `hooks.json` to match your server setup

3. Symlink to /etc (if using systemd webhook service):
   ```bash
   sudo ln -sf $(pwd)/hooks.json /etc/webhook.conf
   ```

The `hooks.json` file is gitignored to prevent accidental commits of server-specific paths.
