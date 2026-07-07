# Security Remediation — July 2026

**Date landed:** 2026-07-07
**Branch:** `main` (flyryan/ai-news-aggregator) at `917800b`
**Source:** AESIR/FENRIR repo scan (org issues #1–#12; board findings #1247, #1248)

This document records the security findings remediated on 2026-07-07, how they
were verified, and what hardening remains open.

## Summary

Eight findings were fixed and landed on `main`. Two HIGH-severity findings
(the deploy-webhook auth bypass and the StalenessChecker SSRF) were additionally
closed on the live production host. Three findings (deploy integrity, CSP,
prompt injection) remain open and are tracked below.

## How the fixes were landed: cherry-pick, never merge

All fix branches (`security/1247-*`, `security/1248-*`, `security/wave-1/2/3`)
were cut from the **github-emu mirror**, whose base commits swap `README.md` for
the internal version and **delete `.github/workflows/daily-pipeline.yml`** (the
497-line production publishing workflow). This deletion is performed by
`scripts/deploy.sh` on every deploy — it is a deliberate transform for the
public mirror, not part of any fix.

Consequence: **a `git merge` of any of these branches into `main` is textually
clean (no conflict warning) but silently deletes the production workflow.** Every
fix was therefore **cherry-picked** onto `main`, and each result was verified to
leave `daily-pipeline.yml` and `README.md` untouched (0 deletions in range).

The five org PRs (#13–#17) should be **closed, not merged**, for the same reason.

## Findings remediated

| Issue | CWE | Severity | Description | PR | Commits on `main` |
|-------|-----|----------|-------------|----|-------------------|
| #2 | CWE-91 | — | Atom feed XML attribute injection (unescaped `"`/`'` in `href`/`term`) | #13 | `e26e555` |
| #3 | CWE-770 | — | LinkFollower uncontrolled URL fetching + fail-open relevance gate | #13 | `e1bb1ae` |
| #4 | CWE-79 | — | `top_topics[].description_html` bypassed the nh3 sanitizer at the publish boundary | #14 | `a49bd8e` |
| #5 | CWE-116 | — | LLM-supplied `model_name`/`ga_date` spliced into `model_releases.yaml` unvalidated | #14 | `8ea391e` |
| #6 | CWE-78 | — | `pipeline-watchdog.yml` interpolated `target_date` input into a `run:` shell body | #14 | `9baa068` |
| #7 | CWE-295 | — | `post_pipeline_verify.sh` hardcoded `StrictHostKeyChecking=no` on production SSH | #15 | `6599349` |
| #8 / #1248 | CWE-918 | HIGH | SSRF via untrusted RSS `<link>` + second-order `<a href>` in StalenessChecker | #17 | `426d8f6` `e5f3b5d` `1604f54` `6804f7c` `db947c1` `1ea6a97` |
| #9 / #1247 | CWE-306 | HIGH (CVSS 8.6) | Unauthenticated deploy webhook → arbitrary `deploy.sh` execution (RCE-adjacent) | #16 | `6157fae` `fa068a5` |

Post-cherry-pick follow-ups (commit `917800b`): added `%` to the feed URL
fragment safe-set (stops a `%2520` anchor regression from the CWE-91 fix),
widened the CWE-116 model-name allowlist to accept `()`/`/`/`+` (so names like
`GPT-5.3-mini (preview)` still auto-add while `:`/`#`/quotes/newline stay
blocked), and documented `LINK_FOLLOWER_MAX_URLS` in `CLAUDE.md`.

### Notes on the SSRF fix (#8 / #1248)

Two competing implementations existed (PR #15 wave-3 and PR #17). **PR #17 was
taken** (typed exception, IPv4-mapped-IPv6 unwrap, 223-line hermetic test
suite); wave-3's SSRF commit (`7e2552d`) was **dropped as superseded**. The guard
routes both fetch sinks through `_safe_get()`: http/https allowlist, resolves
every address and rejects private/loopback/link-local/reserved/multicast/
unspecified IPs, follows redirects manually with per-hop revalidation (5-hop
cap), and closes intermediate responses.

## Deploy webhook (CWE-306) — code + production host

The code fix (`webhook/hooks.example.json` + `webhook/README.md`) is
example-config-only. The vulnerability was **confirmed live** on the host:
the running `deploy` hook had no trigger-rule (adnanh/webhook treats that as
match-all), so any request to the public `https://webhook.aatf.ai/hooks/deploy`
ran `scripts/deploy.sh` (`git reset --hard`, `git clean -fd`, force-push to the
mirror). GitHub delivered a push to it and the deploy fired 13 s later.

**Remediation applied to the live host (2026-07-07):**

1. Rotated a fresh HMAC shared secret on **both** the GitHub repo webhook
   (`hook id 590962545`) and the host `webhook/hooks.json`, GitHub-first so no
   deliveries failed during the rotation.
2. Added an `and` trigger-rule to the live hook: `payload-hmac-sha256` against
   the `X-Hub-Signature-256` header **and** `payload.ref == refs/heads/main`.
   Restarted the `webhook` service. (`hooks.json` is git-ignored, so it survives
   `git clean -fd` on every deploy.)

**Verified end-to-end:**

- Unsigned POST → `Hook rules were not satisfied.` (no deploy)
- Wrong signature → HMAC evaluation errors, command not run
- Valid signature + non-`main` ref → rejected (proves HMAC accepted, ref gate blocks)
- Real GitHub push redelivery → deploy fired (proves both sides share the secret)
- Public-edge unsigned POST to `https://webhook.aatf.ai/hooks/deploy` → rejected

## Production network architecture (verified 2026-07-07)

Relevant to the exposure model: instance `i-0d732832c32d3ccb7`, security group
`sg-0727ce88d29c38ec5`. The **only** inbound rule is SSH `:22` from `0.0.0.0/0`.
There is **no `:443`/`:9000` ingress rule** — the origin is fronted by a
`cloudflared` **tunnel** (outbound-only), so `news.aatf.ai` and `webhook.aatf.ai`
reach it through Cloudflare. Therefore `:9000` is **not** directly reachable at
the public IP; the deploy hook's only public path is the Cloudflare hostname,
now HMAC-gated.

## Remaining / open items

| Item | Status | Notes |
|------|--------|-------|
| #10 CWE-345 — `deploy.sh` blind `reset --hard`/force-push without integrity verification | **Open** | Flow-affecting; left for maintainer decision (harden vs. network lockdown). |
| #11 CWE-693 — Weak CSP (`script-src 'unsafe-inline'`) | **Open** | Wave 4. Undermines XSS defense-in-depth. |
| #12 CWE-1427 — Indirect prompt injection in analyzer pipeline | **Open** | Wave 4. |
| SSH `:22` open to `0.0.0.0/0` | **Open (infra)** | Only broad SG exposure; key-only auth mitigates. Restrict to admin IPs or move to SSM. |
| Cloudflare allowlist on `webhook.aatf.ai` | **Optional** | Defense-in-depth: scope to GitHub webhook IP ranges at the CF edge. |

### Non-blocking hardening noted during review (SSRF)

- DNS-rebinding TOCTOU: guard validates via `getaddrinfo`, then `requests`
  re-resolves at connect time. Impact tempered — fetched content is date-parsed,
  never echoed to users (blind-GET only). Full fix: pin the validated IP.
- No response body size cap (OOM on a multi-GB body). Port wave-3's 5 MB
  streamed cap.
- CGNAT `100.64.0.0/10` not blocked on Python < 3.13; add `or not ip.is_global`.

## Verification commands

```bash
# The two new hermetic unit-test suites (no network, no pipeline):
python3 -m unittest tests.staleness_checker_ssrf_test tests.webhook_hook_auth_test
```
