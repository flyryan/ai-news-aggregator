# Admin panel — design

**Date:** 2026-07-28
**Status:** design approved in conversation; implementation plan pending
**Surface:** `https://admin.aatf.ai` (new hostname on the existing cloudflared tunnel)

## Why

Two problems, one surface.

**Operational:** rebuilding the web container currently requires an SSH session with a
PEM key. That is the only path that ships a frontend change — `scripts/deploy.sh` has
never touched Docker in its entire history (verified: no `docker` string in the file or
in `git log -p -- scripts/deploy.sh`). Data-only updates already flow without it,
because `web/data` and `web/assets` are bind-mounted (`docker-compose.web.yml:19-20`),
so a host `git reset --hard` updates the live site with no container action. The gap is
narrow but real: every frontend change needs a laptop, a key, and a shell.

**Observability — the larger problem.** The pipeline runs in GitHub Actions and
publishes only on success, so git records an unbroken 215-day success streak while the
GitHub API reports **2 failures and 8 cancellations in the last 60 runs**. Worse, a run
can succeed and still publish a degraded report. arXiv returned zero papers on three
consecutive Monday catch-up runs (2026-06-22, 06-29, 07-06 — 8, 18, and 16 research
items against a 268–782 norm, the handful that landed being LessWrong only). Every one
of those days reported `collection_status.overall: "success"`. The regression ran three
weeks before `a281939` fixed it. Nothing was watching the shape of the data.

Separately, ScrapeCreators credits are at 11,668 and burning ~212/day — dry around
**2026-09-21**. When they run out Reddit collection stops, and by the above, nothing
would report red.

## Scope

In: authentication, four maintenance actions, preview-before-publish for the two that
spend money, a dashboard over run health and cost, and source-anomaly detection wired
to the existing alert channel.

Out: multi-user roles (single operator for now), analytics/traffic metrics, and any
change to what the public site serves.

---

## 1. Placement and process model

A **systemd service on the host**, running as a dedicated `aatfadmin` user, bound to
`127.0.0.1:8200`, exposed as `admin.aatf.ai` through the existing cloudflared tunnel.
Not a compose service.

The reasoning matters because it inverts the obvious choice. Containerizing would only
buy isolation if the service were isolated — but its job is to rebuild the sibling
container, so it would need `/var/run/docker.sock`, which is effective root on the host.
That is isolation in appearance only. And the host's `ubuntu` user is **already** in the
docker group (every documented invocation is bare `docker compose` with no `sudo` —
`CLAUDE.md:60`, `scripts/post_pipeline_verify.sh:117`, `webhook/README.md:37`), so a
broker process running as `ubuntu` would be a speed bump, not a boundary.

Running as `aatfadmin` — deliberately **not** in the docker group — with a narrow sudo
allowlist is the stronger position: one wrapper script, auditable in a single file.
There is precedent; the deploy webhook already runs as a host systemd service.

The cost is honest: a Python venv at `/opt/aatf-admin` (FastAPI, uvicorn, PyJWT,
httpx). It does not touch the nginx container, and the site keeps serving if the admin
service is stopped.

Two problems this solves for free, which the container would have created:

- **Self-restart.** A rebuild triggered from inside a compose-project container kills
  the container serving the request. As a systemd oneshot unit, the rebuild is not a
  child of the admin process and survives it.
- **Log streaming.** `journalctl -u <unit> --output=cat -f` gives live build output with
  no pipe to break.

## 2. Authentication

Two layers, because the JWT signature is the only real boundary.

**Layer 1 — tunnel.** `access: {required: true, teamName, audTag}` on the admin
hostname's `originRequest`, so cloudflared refuses to proxy anything without a valid
Access JWT before the request reaches Python.

**Layer 2 — application.** Independently verify the `Cf-Access-Jwt-Assertion` header:
`PyJWT[crypto]` + `jwt.PyJWKClient` against
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, pinning
`algorithms=["RS256"]`, `audience=<AUD tag>`, `issuer=https://<team>.cloudflareaccess.com`,
and requiring `exp`, `iat`, `nbf`, `aud`, `iss`. Then authorize the resulting principal
against an explicit allowlist (`email` for humans, `common_name` for service tokens).
The service **refuses to boot** if team domain or AUD is unset.

Implementation notes that are easy to get wrong:

- `aud` arrives as a JSON **array**; PyJWT handles this with a string `audience=`, but
  `strict_aud=True` rejects it. Do not set it.
- Keys rotate every 6 weeks with a 7-day overlap. Use `PyJWKClient`'s `kid` matching and
  `cache_jwk_set=True, lifespan=600`; leave `cache_keys=False` (Tier-2 LRU has no time
  expiry and would pin a revoked key).
- Verify the header, not the `CF_Authorization` cookie — the cookie is not guaranteed
  to be sent.

**Why this is spelled out:** the abandoned `admin-service` branch (commit `42cfad0`)
authenticated on *header presence* — `request.headers.get(...)` and any truthy value
passed. `curl -H 'cf-access-authenticated-user-email: x'` was a full admin session, and
the audit log would record the attacker's chosen identity. A correctly-signed JWT from
any other Access application also passed, and expiry was never checked.

**Guard test.** A CI test in the style of `tests/webhook_hook_auth_test.py`, asserting
the verifier rejects: forged signature, `alg=none`, HS256 confusion, wrong `aud`, wrong
`iss`, expired, missing `exp`, malformed, and empty. That file documents a near-identical
CWE-306 finding; this is the same class of bug and deserves the same lock-in.

## 3. Preview before publish

Both money-spending actions (pipeline dispatch, hero regeneration) render to a preview
the operator approves before anything becomes public.

**Where preview data lives.** Under `data/admin/previews/<job_id>/` — inside `/data/`,
which is gitignored (`.gitignore:38`). This is load-bearing: `scripts/deploy.sh` runs
`git reset --hard` and `git clean -fd` (lines 40-41, 75), so anything staged under the
tracked `web/data/` tree is destroyed by the next deploy. Preview content also never
touches the public origin, so unapproved drafts are not fetchable by URL.

**How it renders.** The admin service serves the real built SPA bundle from its own
origin with the preview directory mounted as that page's data source. Same origin for
UI and preview satisfies the CSP `frame-ancestors 'self'` (`nginx.conf:66`).

**The frontend change this requires.** The data base path is hardcoded in five places
(`dataLoader.ts:21,48,67`, `searchWorker.ts:43`, `replayLoader.ts:15,19`) with no
indirection, so the frontend must accept an override. It must be passed via a `data-*`
attribute on served-as-is markup, **not** an injected inline `<script>`: the build emits
a per-page CSP hash (`web/index.html` carries
`sha256-CXSEFFaI1aXvpFRAue+e+fnImVPL2oqjZkossFtyluU=`, `archive.html` a different one),
so an added inline script is blocked. The override must **fail loudly** when absent —
the abandoned branch's `preview.ts` fell back to `/data`, which would render **live**
data labeled as a draft, letting an operator approve a report they never saw. That
fallback is the single most dangerous line in the prior attempt.

**Promotion.** On approval, the admin service copies the approved files into `web/data/`,
commits them **SSH-signed**, and pushes — the same path CI uses
(`.github/workflows/daily-pipeline.yml:406-429`), satisfying the CWE-345 gate in
`deploy.sh:27-35`. If signing is not configured, fail at approval time with a clear
message; never reach for `ALLOW_UNSIGNED_DEPLOY=1`.

**Hero specifics.** Preview shows draft against current side by side. `hero.webp` is
git-tracked and its URL carries a cache-buster (`hero_image_url:
/data/<date>/hero.webp?v=<mtime>`), which must be regenerated on promote or browsers
serve the stale image — `.webp` is absent from the immutable-extension list
(`nginx.conf:20`), so caching is undefined-by-default.

**Garbage collection.** Previews are reaped on a fixed retention (default 7 days) and on
approval. This is not optional: the prior attempt left **2.4 GB** of orphaned git
worktrees under `data/admin/workspaces/` with no cleanup — the predicted disk-exhaustion
failure, already realized. Previews here are plain directories, not worktrees, which
avoids most of that weight.

## 4. Maintenance actions

Four actions, each a pre-declared systemd oneshot unit triggered through a single
allowlisted sudo wrapper (`/usr/local/sbin/aatf-admin-trigger`). The admin service never
runs `docker` or `git` directly.

| Action | What runs | Notes |
|---|---|---|
| Rebuild web container | `docker compose -f docker-compose.web.yml up -d --build ai-news-aggregator` | **Scoped to the service name.** An unscoped `up -d --build` would rebuild every service in the project. |
| Git sync host to origin/main | `git fetch` + `git verify-commit` + `git reset --hard <verified>` | Shares `deploy.sh`'s signature gate. |
| Dispatch pipeline run | GitHub API `workflow_dispatch` | Must check for an in-flight run first — `daily-pipeline.yml:42` sets `cancel-in-progress` for dispatch, so a careless trigger **cancels a running pipeline**. |
| Regenerate hero | `scripts/regenerate_hero.py` on the host, into preview | Commits immediately on approval, since `hero.webp` is tracked and would otherwise be reverted. |

**Locking.** A single `flock` mutex at `/var/lib/aatf-admin/privileged.lock`, held inside
the unit's `ExecStart` rather than in the Python process, so the kernel releases it on
crash. `deploy.sh` must take the same lock — the HMAC-gated deploy webhook fires
independently, and a push mid-rebuild would `git reset --hard` the build context out
from under a running build.

**Completion detection.** Read the terminal `Result`/`ExecMainStatus` from systemd. Never
infer success from the log stream ending — a crashed or OOM-killed build looks identical
to a slow one.

**Rebuild safety.** Build first, then `up -d` only on build success, then verify
`/data/index.json` serves before reporting success. A broken image plus
`restart: unless-stopped` (`docker-compose.web.yml:24`) loops the site down.

**Audit log.** Every privileged action records principal (from the verified JWT), action,
arguments, timestamp, and outcome — to SQLite outside the checkout **and** to the journal,
so a compromise of the service cannot silently rewrite its own history.

## 5. Dashboard

Ranked by value over effort. Every panel names its source, because the sourcing is the
part that is easy to get wrong.

**1. Source health timeline — the headline.** Per-source item counts across 215 days from
committed `summary.json`, with anomaly flags. Do **not** render
`collection_status.status`: it reads `success` on all 215 days because failed runs never
publish (`daily-pipeline.yml:373-384` reverts generated data on failure). Rendering it
verbatim is misinformation.

The detector compares each source's count to the trailing median of the **same report
weekday** (last 6 samples; alert when median ≥ 25 and count < 35% of it). Weekday-keying
is essential and must match the gatherer's own logic: `research_gatherer.py:148-153`
skips arXiv on Saturday/Sunday reports and runs a 3-day Sat–Mon catch-up on Mondays, so
research medians by report weekday are Sat 19 / Sun 11 / Mon 374 / Tue 996. A naive
threshold fires **35 times in 215 days**, nearly all false. The weekday-aware version
fires **4 times** — the three arXiv Mondays and a genuine social collapse (2026-04-10,
172 against a 593 median). Validated against the full history.

**2. Run health.** One row per report date: conclusion, duration, and whether it did real
work — from the GitHub API, the only source for the 2 failures and 8 cancellations git
hides. Filter schedule-gate no-ops: **13 of 50 "successful" runs completed in under 120
seconds** because the 3 AM ET gate skipped them. Counting them halves the apparent
success rate and wrecks duration averages (real runs: 36 min median, 106 min max).

**3. Cost.** Per-run total and trend from committed `replay-index.json`. No ingest
needed — `cost_by_component` is fully derivable from `calls[].caller` (41 of 41 keys map)
and `cost_by_provider` from `calls[].provider_id`. Runs have ranged $1.30–$22.80,
median $6.78.

**4. External API balances.** Live, on demand, from the free probe endpoints already used
by the pipeline: ScrapeCreators `/v1/account/credit-balance`
(`reddit_gatherer.py:419-434`) and TwitterAPI.io `/oapi/my/info`
(`social_gatherer.py:184-189`). Neither consumes credit. A tiny SQLite append log
(`{date, vendor, balance}`) accumulates the trend that powers the days-to-zero
projection — a single live reading cannot produce one.

**5. Replay.** Link each run to its existing `/replay` view; no new work. Backfill 41
historical dates from `data/processed/` (~49 KB each, ~2 MB total). Backfill must run
locally because its inputs are gitignored, and those days are marked
`timings_measured: false` — queue and first-token timings are unrecoverable after the
fact, and the replay contract forbids presenting a reconstruction as a measurement.

**6. Logs.** Job logs via the GitHub API, which returns **403 unauthenticated** — this is
what makes the PAT mandatory rather than optional. Use the per-job endpoint (plain text,
~278 KB) rather than the run endpoint (zip). Do not forward the `Authorization` header
across the redirect: the `Location` points at a different host. Cache extracted text;
the redirect expires after 60 seconds.

## 6. Alerting

Anomalies POST to the **existing** ingress — same URL (`PIPELINE_ALERT_URL`, default
`https://flybotwebhook.duffplex.com/alert/pipeline`), same bearer
(`PIPELINE_ALERT_TOKEN`), with `status: "degraded"` instead of `"failure"`. The channel
is already built, bearer-authenticated, fails closed, and best-effort so it never breaks
its caller (`daily-pipeline.yml:501-520`). It fires today only on hard job failure —
which is precisely why the arXiv regression was silent.

Alerts are deduplicated per `(source, incident)` so a three-week outage notifies once,
not twenty-one times.

**One detector, two callers.** A single Python module is imported by both the panel (for
the timeline view) and the pipeline (for its post-run check), so "anomalous" has exactly
one definition and cannot drift. Agent-facing detail lives in
`docs/admin-alerts-hermes.md`.

## 7. Visual design

The panel reuses the production Svelte bundle and follows the `/replay` interface, which
already solves dense operational data in AATF branding. Inherited tokens are catalogued
in `.admin-design-tokens.md`; the vocabulary is `.eyebrow`, `.card`, the `.run-stats`
KPI grid, the `.viewswitch` segmented tabs, and `data-status` attribute styling.

**A color collision to resolve deliberately:** `category-research` is the same green as
`success` (`#10b981`) and `category-reddit` the same red as `failed` (`#ef4444`). On a
health chart a red Reddit lane reads as broken when it is fine. Health is encoded with
the status ramp; sources are identified by label and position. Never both encodings on
one mark.

**Preview must be unmistakable.** The `/replay` demo banner
(`replay/+page.svelte:301-305`) is the precedent; preview needs a louder equivalent,
present on every preview view, because the failure mode is approving something believing
it is a draft when it is live — or the reverse.

Motion stays functional: 140 ms on tab color, 200 ms on card shadow, matching the
existing surfaces.

## 8. Pre-existing issue found during design

Unrelated to this feature, on the same checkout: `scripts/post_pipeline_verify.sh:117`
runs `git reset --hard origin/main` with **no `git verify-commit`**, then builds and runs
the result when `REBUILD_WEB=true`. That bypasses the CWE-345 signed-commit gate
`deploy.sh:27-35` enforces on the same repository. Anyone able to move `flyryan/main`
gets code execution through the verification path. Worth fixing regardless of whether
this panel is built; the admin git-sync must not copy the pattern.

## What could go wrong

- **Preview renders live data silently.** Mitigated by failing loudly when the base-path
  override is missing, rather than defaulting.
- **A rebuild takes the site down.** Mitigated by build-then-deploy, a post-rebuild
  content check, and reading systemd's terminal status rather than trusting the stream.
- **Auth bypass spends money or owns the box.** Mitigated by verifying the JWT signature,
  `aud`, `iss`, and expiry; binding to loopback; running as a non-docker user; and the
  CI guard test.
- **Concurrent deploy and rebuild corrupt the build context.** Mitigated by a shared
  `flock` that `deploy.sh` also takes.
- **Disk fills with previews.** Mitigated by retention-based reaping; the prior attempt's
  2.4 GB of orphaned worktrees is the cautionary case.
- **Dispatch cancels a running pipeline.** Mitigated by an in-flight check before
  dispatch.

## Verified host state (2026-07-28, read-only inspection)

Checked directly on the EC2 origin rather than inferred from repo files:

| Fact | Value | Consequence |
|---|---|---|
| `ubuntu` groups | `sudo(27)`, `docker(988)` | Already full host root. Confirms a broker running as `ubuntu` would be no boundary; `aatfadmin` is a real improvement. |
| `webhook.service` | active, `User=ubuntu`, `/usr/lib/systemd/system/webhook.service` | Host-systemd precedent exists — and currently runs as the over-privileged user. |
| `cloudflared.service` | active, **token-based**, no local config file | Ingress rules live in the Cloudflare dashboard, not on the host. Adding `admin.aatf.ai` is a dashboard change. |
| Disk | 29 GB total, 18 GB free (39% used) | Preview retention is comfortable; reaping still required. |
| Containers | `ai-news-aggregator` up, healthy, `0.0.0.0:7100->80` | Only the web-only compose runs here, as documented. |
| Python | 3.12.3 present | No interpreter install needed; venv only. |
| `aatfadmin` | does not exist | Provisioning step. |

**Port allocation:** the admin service binds `127.0.0.1:8200`; tunnel ingress maps
`admin.aatf.ai` → `http://127.0.0.1:8200`. Port 7100 is taken by the web container.

## Open items for implementation

- **Cloudflare (dashboard, owner action):** add tunnel ingress `admin.aatf.ai` →
  `http://127.0.0.1:8200`, and create a Zero Trust Access application over that hostname
  with a policy for the operator's email. Supply the **team domain** and the application
  **AUD tag** to the service config — it refuses to boot without them. The app can be
  built and tested locally with verification stubbed before the hostname exists.
- **Host provisioning:** `aatfadmin` user, sudoers entry, and the four oneshot units live
  outside the checkout (like `deploy_allowed_signers`) and cannot be committed. They need
  a documented, idempotent setup script.
- **Host-side commit signing** must be configured for preview promotion to work; fail
  loudly at approval time if it is not.
- **Consider migrating `webhook.service` off `User=ubuntu`** while the sudo/unit pattern
  is being built. Out of scope for this feature, but the same reasoning applies.
