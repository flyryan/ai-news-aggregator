# Security Remediation — July 2026

**Date landed:** 2026-07-07
**Branch:** `main` (flyryan/ai-news-aggregator) at `917800b`
**Source:** Trend ZDI AESIR/FENRIR scan, using Claude Mythos accessed via Anthropic's Project Glasswing (org issues #1–#12; board findings #1247, #1248)

This document records the security findings remediated on 2026-07-07 and how
each fix was verified.

## Finding provenance: Trend ZDI via Anthropic's Project Glasswing (Claude Mythos)

**Project Glasswing** is **Anthropic's** defensive-security initiative
(announced 2026-04-07, ~$100M in model-usage credits) that gives partner
organizations access to **Claude Mythos** — the approved-org, reduced-guardrail
tier of Anthropic's Mythos-class frontier model, capable of finding and
exploiting software vulnerabilities at a level beyond most human experts — so
they can find and fix flaws in their own critical systems before adversaries
weaponize comparable AI. Access is delivered through the usual model channels
(Claude API, Amazon Bedrock, Google Vertex AI, Microsoft Foundry), and
qualifying security teams also receive Anthropic-provided tooling: agent
**skills**, a **scanning harness**, and **threat-model builders**. (Mythos and
the generally-available **Claude Fable 5** share the same underlying model;
Mythos omits the GA dual-use guardrails.)

**Trend Micro's Zero Day Initiative (ZDI)** team is a Glasswing participant —
that participation is how we have Claude Mythos access. On top of it the ZDI
team runs its own discovery-and-validation pipeline (the
`trend-zdi-threat-hunting` org): **AESIR** is the Mythos-driven discovery stage
that scanned this repo and filed the raw findings, and **FENRIR** is the
adversarial validator (`fenrir-validator`, skills `vulnerability-triage` /
`reviewing-security` / `fenrir-adversarial-validate`) that reproduces each
candidate in a contained `docker-compose` harness, gathers evidence (PCAPs,
execution/access logs) and a `confidence-assessment.md`, and emits a ZDI-format
report (with a "Root Cause Analysis (ZDI Section 4)" section), filtering false
positives before a finding reaches the AATF maintainer.

So these twelve findings are **Claude-Mythos-discovered (via Glasswing),
FENRIR-validated ZDI findings**, filed here as org issues #1–#12 (the two HIGH
findings also went to the ZDI board as #1247 / #1248) and remediated by Claude
(Fable 5) working with the AATF maintainer. Per-finding harnesses and evidence
bundles live in each finding's `fenrir_package/`, in the
`trend-zdi-threat-hunting/zth-aesir-vulnerability-discovery` repo under
`bugs/Internal/trend-ai-acceleration-task-force/ai-news-aggregator/`.

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
taken** (typed exception, IPv4-mapped-IPv6 unwrap, hermetic test suite);
wave-3's SSRF commit (`7e2552d`) was **dropped as superseded**. The guard
routes both fetch sinks through `_safe_get()`, which:

- allows only `http`/`https` schemes;
- resolves every address and rejects private / loopback / link-local
  (cloud-metadata) / reserved / multicast / unspecified IPs, unwrapping
  IPv4-mapped IPv6 first, and additionally requires the target to be globally
  routable (`is_global`) — this blocks carrier-grade NAT (`100.64.0.0/10`) and
  other shared/special ranges that older Python does not flag as private;
- follows redirects manually with per-hop scheme+IP revalidation (5-hop cap),
  so a 302 from an allowlisted host cannot bounce the fetch to an internal
  address, and closes intermediate responses; and
- buffers each response body under a hard 5 MiB cap (rejecting an oversized or
  falsely-declared `Content-Length`), so a hostile host cannot exhaust memory.

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

## Wave 4 (2026-07-07): #10 CWE-345 deploy integrity, #11 CWE-693 weak CSP, #12 CWE-1427 prompt injection

| Issue | CWE | Fix | Commits |
|-------|-----|-----|---------|
| #10 | CWE-345 | Signed-commit verification gate in `deploy.sh` + SSH signing for all commit sources | `49d6edd` `cdb19be` `2eddeb9` (main) |
| #11 | CWE-693 | Hash-based CSP split + URL scheme allowlist | `e69c1a5` (main, deployed) |
| #12 | CWE-1427 | Stage 1: input normalization + bounded output schema | `9149e4a` (main) |
| #12 | CWE-1427 | Stage 2: system/user channel separation + nonce fencing | `bf969d7` (merged to main 2026-07-07 after passing the A/B quality gate) |

**#10 (fixed + deployed):** `scripts/deploy.sh` previously did a blind
`git reset --hard origin/main` and executed whatever the tip pointed at, so
anyone able to move `flyryan/main` (a compromised token, a malicious
force-push) gained code execution on the host — the deploy verified *who*
triggered it (the webhook HMAC fix, #9) but not *what* it was about to run. The
deploy now requires the tip of `origin/main` to be SSH-signed by a trusted key:
`deploy.sh` runs `git verify-commit` against a host-only allow-list
(`/home/ubuntu/deploy_allowed_signers`, template `deploy/allowed_signers.example`)
before resetting, captures that one verified hash and operates on it for both
resets (dropping the second fetch so nothing arriving mid-deploy slips in
unverified), and aborts the deploy on failure (`ALLOW_UNSIGNED_DEPLOY=1` is a
loudly-logged emergency override). All three commit sources sign: Ryan's local
flyryan and EMU identities (SSH signing config) and the daily-pipeline CI bot
(`PIPELINE_SIGNING_KEY` secret; the workflow signs the auto-update commit and
re-signs across the push-step rebase). Allow-list principals are wildcards
because the CI committer email contains `[bot]`, which the ssh matcher treats as
a glob character class. Verified 2026-07-07 end-to-end via the live webhook: a
signed tip logs `Signature OK … signed by a trusted signer` and deploys; an
unsigned tip is refused. Signing is config-driven (no per-commit step) and
documented in `CLAUDE.md` + `deploy/README.md` so a fresh clone configures it
and the gate fails safe (visible stall, never a silent bad deploy) if missed.

**#11 (fixed + deployed):** SvelteKit `kit.csp` (mode `hash`) now owns script
policy via a per-page `<meta>` CSP whose `script-src` carries the sha256 hash
of the inline hydration script; the nginx header carries **no**
`script-src`/`default-src` (a second script policy would be enforced in
addition to the meta and blank the site), drops `https:` from `img-src`
(beacon-exfil vector), and adds `worker-src`/`object-src`/`base-uri` plus
header-only `frame-ancestors`. `NewsCard` hrefs and `json_generator` output
both pass an http/https/mailto scheme allowlist, closing the `javascript:`-URI
sink for the SPA, feeds, and search corpus alike. Regression check:
`scripts/check_csp.sh`. Verified 2026-07-07 on localhost and
`https://news.aatf.ai` (header + meta hash + hydration + search worker, zero
console CSP violations).

**#12 stage 1 (on main):** all analyzer context builders normalize untrusted
text at the `_clip_context_text` chokepoint (NFKC fold, zero-width/bidi/control
stripping — `agents/prompt_security.py`), `item.url` is capped at 512 chars,
and `agents/analysis_schema.py` clamps every republished response field
(scores into 0–100, strings truncated, unknown keys dropped; clamp/repair,
never reject).

**#12 stage 2 (merged after gate):** operator instructions move verbatim to the
`system` prompt behind a SECURITY BOUNDARY preamble; untrusted data moves to
the `user` message inside a per-prompt `secrets.token_hex` nonce fence, across
the analyzer map/ranking path and the second-order sinks (news filter/combined,
topic detection, executive summary, link enricher, ecosystem enrichment; the
hero prompt gets normalization only since an image model has no channel
split).

**A/B quality gate (run 2026-07-07, PASS):** the 2026-07-07 report was
regenerated from the gathering checkpoint (`--resume-from 2`, identical
analysis inputs) and compared to the committed baseline with
`scripts/compare_outputs.py`. Research/social/reddit: 0% item loss, top-10
overlap 5–7/10, mean |score Δ| 4–6 (temperature-1.0 rank jitter). The script's
one flagged failure (news "lost" 4 items) was traced to input provenance, not
prompts: those 4 articles were never in the local gathering checkpoint (the
baseline CI run's link-follower had captured them); input-corrected news loss
was 0/33 with top-10 overlap 5/6. Executive summaries were qualitatively
equivalent (same top story and section structure). A 3×-vs-3× controlled
probe of the news filter showed the new structure is stable run-to-run and
marginally more inclusive on 3–4 gray-zone items (AI-attributed layoff
stories, scores 32–42 — far below the top-10 cutoff).

## Verification commands

```bash
# The two new hermetic unit-test suites (no network, no pipeline):
python3 -m unittest tests.staleness_checker_ssrf_test tests.webhook_hook_auth_test
```
