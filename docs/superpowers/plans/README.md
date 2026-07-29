# Admin panel — plan set

Spec: [`../specs/2026-07-28-admin-panel-design.md`](../specs/2026-07-28-admin-panel-design.md)

The spec spans several independent subsystems. Rather than one monolithic plan, it is
split into five, each producing working, testable software on its own and each
independently useful if the ones after it are never built.

| # | Plan | Delivers | Depends on |
|---|---|---|---|
| 0 | [`2026-07-28-deploy-signature-fix.md`](2026-07-28-deploy-signature-fix.md) | Closes the `post_pipeline_verify.sh` CWE-345 bypass; migrates `webhook.service` off `User=ubuntu` | nothing |
| 1 | [`2026-07-28-admin-1-anomaly-detector.md`](2026-07-28-admin-1-anomaly-detector.md) | Weekday-aware source-anomaly detector + CI post-run check + degraded alerts | nothing |
| 2 | [`2026-07-28-admin-2-service-auth.md`](2026-07-28-admin-2-service-auth.md) | The service itself: systemd unit, `aatfadmin`, verified CF Access JWT auth | nothing (plan 1 optional) |
| 3 | [`2026-07-28-admin-3-actions.md`](2026-07-28-admin-3-actions.md) | Four maintenance actions via sudo-allowlisted oneshot units, with locking | plan 2 |
| 4 | [`2026-07-28-admin-4-dashboard.md`](2026-07-28-admin-4-dashboard.md) | Dashboard UI: health timeline, run health, cost, balances, replay, logs | plans 1, 2 |
| 5 | [`2026-07-28-admin-5-preview.md`](2026-07-28-admin-5-preview.md) | Preview/approve/promote for pipeline runs and heroes | plans 2, 3 |

**Recommended order:** 0 → 1 → 2 → 3 → 4 → 5.

Plan 0 first because it is a live security bug that exists whether or not this feature
ships. Plan 1 second because it is pure Python with no infrastructure, delivers the
single highest-value outcome (catching the next silent source outage), and needs neither
the service nor the host work.

**Do not write the Hermes handoff document until plan 1 is deployed and observed
working.** It describes alert payloads an agent must act on; writing it against
unverified behavior would document intentions rather than facts.
