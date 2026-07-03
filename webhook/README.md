# Webhook Configuration

This directory contains the optional host-local configuration for GitHub deploy hooks. The daily pipeline now runs in GitHub Actions; a web host only needs a webhook if it should react to pushes by pulling `origin/main` and restarting or rebuilding the web-only container.

## Security model (read first)

The `deploy` hook runs `scripts/deploy.sh` on the host (it performs `git reset --hard`, `git clean -fd`, and a force-push to the mirror). The listener on port `9000` must be reachable by GitHub, which means it is reachable by anyone on that network path. **The only thing that stops an arbitrary caller from triggering a deploy is the HMAC signature check** defined in `trigger-rule`.

Therefore the example config ships with a `payload-hmac-sha256` trigger-rule that:

- requires a valid `X-Hub-Signature-256` HMAC computed with a shared secret, and
- only fires for pushes to `refs/heads/main`.

Do **not** remove the `trigger-rule` block, and do **not** run a hook with a blank/placeholder secret. A hook without a signed trigger-rule is exploitable by any unauthenticated client (CWE-306).

## Setup

1. Copy the example config:
   ```bash
   cp hooks.example.json hooks.json
   ```

2. Generate a strong shared secret and put it in **both** places (they must match exactly):
   ```bash
   openssl rand -hex 32
   ```
   - Replace `CHANGE_ME_SET_A_STRONG_SHARED_SECRET_MATCHING_GITHUB` in `hooks.json` with the generated value.
   - Set the **same** value as the webhook *Secret* in the GitHub repo settings (Settings → Webhooks → Secret). Once a secret is set, GitHub includes the `X-Hub-Signature-256` HMAC header on every delivery.
   - Also set the webhook *Content type* to `application/json`. The content type controls the exact body bytes GitHub signs; a raw JSON body is what this hook's `trigger-rule` expects — both for the HMAC check and for the `payload.ref` branch match.

   Never commit the real secret — `hooks.json` is git-ignored.

3. Edit paths in `hooks.json` to match your server setup. The example points at `scripts/deploy.sh`, but production hosts can use their own wrapper as long as it performs the same basic sync:
   ```bash
   git fetch origin
   git reset --hard origin/main
   docker compose -f docker-compose.web.yml up -d --build
   ```
   Keep the `trigger-rule` block intact when editing.

4. Symlink to `/etc` if using a systemd webhook service:
   ```bash
   sudo ln -sf "$(pwd)/hooks.json" /etc/webhook.conf
   ```

5. Run the listener so signature failures are rejected (not soft-passed). With `adnanh/webhook`:
   ```bash
   webhook -hooks /etc/webhook.conf -verbose
   ```
   `webhook` treats a hook with a `trigger-rule` that does not match as a rejected
   request (HTTP 200 with "Hook rules were not satisfied." and no command execution),
   so requests without a valid `X-Hub-Signature-256` never reach `deploy.sh`.

   Defense in depth: prefer binding the listener to `127.0.0.1` (`-ip 127.0.0.1`) behind a
   TLS-terminating reverse proxy, or restrict port `9000` to GitHub's published webhook
   source ranges at the firewall.

Keep `hooks.json` and any server-specific wrapper settings out of git. They can contain absolute paths, local service names, the webhook secret, or mirror-specific behavior that should not be part of the portable OSS configuration.
