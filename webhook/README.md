# Webhook Configuration

This directory contains the optional host-local configuration for GitHub deploy hooks. The daily pipeline now runs in GitHub Actions; a web host only needs a webhook if it should react to pushes by pulling `origin/main` and restarting or rebuilding the web-only container.

## Setup

1. Copy the example config:
   ```bash
   cp hooks.example.json hooks.json
   ```

2. Edit paths in `hooks.json` to match your server setup. The example points at `scripts/deploy.sh`, but production hosts can use their own wrapper as long as it performs the same basic sync:
   ```bash
   git fetch origin
   git reset --hard origin/main
   docker compose -f docker-compose.web.yml up -d --build
   ```

3. Symlink to `/etc` if using a systemd webhook service:
   ```bash
   sudo ln -sf "$(pwd)/hooks.json" /etc/webhook.conf
   ```

Keep `hooks.json` and any server-specific wrapper settings out of git. They can contain absolute paths, local service names, or mirror-specific behavior that should not be part of the portable OSS configuration.
