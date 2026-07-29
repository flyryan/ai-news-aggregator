# Admin Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working operations dashboard at `/admin` — source health with anomaly flags, run history, cost trend, vendor balance burn-down, replay links, and the action controls — developable and viewable through `npm run dev`.

**Architecture:** A new SvelteKit route in the existing frontend, so it inherits the production bundle, theme, and component vocabulary. Data comes from three places: committed JSON already on disk (source health, cost, replay), the admin service's API (actions, audit, run history, balances), and the anomaly detector shared with the pipeline. A Vite dev proxy forwards `/api` to a locally running admin service so the whole panel works under `npm run dev` with no host access.

**Tech Stack:** Svelte 5 (runes), SvelteKit 2, Vite 6, TypeScript, Tailwind, FastAPI, stdlib `unittest`.

## Global Constraints

- **Follow the `/replay` interface.** It is the existing surface that solves dense operational data in AATF branding. Reuse its vocabulary — `.eyebrow`, `.card`, the `.run-stats` KPI grid, the `.viewswitch` segmented tabs, `data-status` attribute styling — rather than inventing a parallel one. Tokens are catalogued in `../specs/.admin-design-tokens.md`.
- **Never render `collection_status.status` as health.** It reads `"success"` on all 215 published days because failed runs never publish. Render counts and the detector's verdict.
- **Colour collision:** `category-research` is the same green as `success` (`#10b981`) and `category-reddit` the same red as `failed` (`#ef4444`). Encode health with the status ramp; identify sources by label and position. Never both encodings on one mark.
- The admin route must **not** be prerendered (`export const prerender = false`) — it is authenticated and dynamic. Every other route stays as it is.
- Motion stays functional: 140 ms on tab/colour transitions, 200 ms on card shadow. No decorative animation.
- Quality floor: responsive to mobile, visible keyboard focus, `prefers-reduced-motion` respected.
- Commits must be SSH-signed.

---

## Prerequisites

Plan 1 (`agents/source_anomaly.py`) and plan 2 (the service, `require_principal`, `AdminStore`) must be complete. Plan 3 (actions) is needed for the Actions tab to do anything, but the rest of the dashboard works without it.

---

## File Structure

| File | Responsibility |
|---|---|
| `admin_service/dashboard.py` (create) | Server-side data assembly: health series, cost series, balances. |
| `admin_service/balances.py` (create) | Live vendor balance probes + trend from the store. |
| `admin_service/app.py` (modify) | Dashboard endpoints. |
| `frontend/vite.config.ts` (modify) | Proxy `/api` to the local admin service in dev. |
| `frontend/src/routes/admin/+page.svelte` (create) | The panel shell: header, KPI row, tabs. |
| `frontend/src/routes/admin/+page.ts` (create) | `prerender = false`. |
| `frontend/src/lib/services/adminApi.ts` (create) | Typed fetch wrappers. |
| `frontend/src/lib/types/admin.ts` (create) | Shared TypeScript types. |
| `frontend/src/lib/components/admin/HealthTimeline.svelte` (create) | Per-source volume heatmap with anomaly flags. |
| `frontend/src/lib/components/admin/RunHistory.svelte` (create) | Workflow run rows, no-op filtering. |
| `frontend/src/lib/components/admin/CostTrend.svelte` (create) | Cost per run over time. |
| `frontend/src/lib/components/admin/BalanceCard.svelte` (create) | Vendor balance + days-to-zero. |
| `frontend/src/lib/components/admin/ActionPanel.svelte` (create) | The four actions, with confirmation and live output. |
| `tests/admin_dashboard_test.py` (create) | Server-side assembly tests. |

---

### Task 1: Server-side dashboard data

**Files:**
- Create: `admin_service/dashboard.py`
- Test: `tests/admin_dashboard_test.py`

**Interfaces:**
- Consumes: `agents.source_anomaly.{load_history, detect, SourceReading, Anomaly}` (plan 1).
- Produces:
  - `health_series(web_dir, days=90) -> dict` → `{"sources": [...], "dates": [...], "series": {source: [counts]}, "anomalies": [...]}`
  - `cost_series(web_dir, days=90) -> list[dict]` → per-date `{date, cost_usd, llm_calls, input_tokens, output_tokens, items, status, timings_measured}`
  - `latest_report(web_dir) -> dict | None`

- [ ] **Step 1: Write the failing test**

Create `tests/admin_dashboard_test.py`:

```python
"""Tests for admin dashboard data assembly.

The dashboard reads data that is already committed, so these tests run against
the real web/data tree rather than fixtures -- the failure mode worth catching
is "assembly silently returns nothing because a path or key changed", which a
synthetic fixture would hide.

  python3 -m unittest tests.admin_dashboard_test -v
"""

import unittest
from pathlib import Path

from admin_service.dashboard import cost_series, health_series, latest_report

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"


class HealthSeriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = health_series(WEB_DIR, days=90)

    def test_returns_dates_and_series(self):
        self.assertGreater(len(self.data["dates"]), 30, "expected a real date range")
        self.assertTrue(self.data["sources"], "expected at least one source")

    def test_every_source_series_aligns_with_the_date_axis(self):
        span = len(self.data["dates"])
        for source in self.data["sources"]:
            self.assertEqual(
                span, len(self.data["series"][source]),
                f"{source} series length does not match the date axis; the heatmap "
                "would silently shift every cell",
            )

    def test_missing_days_are_null_not_zero(self):
        # A day with no published report is unknown, not "collected nothing".
        # Rendering it as 0 would look identical to an outage.
        for values in self.data["series"].values():
            for value in values:
                self.assertTrue(
                    value is None or isinstance(value, int),
                    "series values must be int or None",
                )

    def test_anomalies_are_included_and_shaped_for_the_ui(self):
        for anomaly in self.data["anomalies"]:
            self.assertIn("date", anomaly)
            self.assertIn("source", anomaly)
            self.assertIn("count", anomaly)
            self.assertIn("baseline", anomaly)
            self.assertIn("detail", anomaly)

    def test_known_outage_is_flagged(self):
        # The 90-day window may or may not still contain June; check only if it does.
        if "2026-06-22" in self.data["dates"]:
            flagged = {(a["date"], a["source"]) for a in self.data["anomalies"]}
            self.assertIn(
                ("2026-06-22", "research"), flagged,
                "the arXiv outage must surface in the dashboard, not just the CLI",
            )


class CostSeriesTest(unittest.TestCase):
    def test_reads_committed_replay_indexes(self):
        rows = cost_series(WEB_DIR, days=400)
        self.assertTrue(rows, "expected at least one replay-index.json in web/data")
        for row in rows:
            self.assertIn("date", row)
            self.assertIsInstance(row["cost_usd"], float)
            self.assertIsInstance(row["llm_calls"], int)

    def test_marks_reconstructed_runs_honestly(self):
        # Offline-regenerated days cannot recover queue/first-token timings. The
        # UI must be able to say so rather than presenting a reconstruction as a
        # measurement.
        rows = cost_series(WEB_DIR, days=400)
        self.assertTrue(
            all("timings_measured" in row for row in rows),
            "every cost row must carry timings_measured so the UI can label it",
        )


class LatestReportTest(unittest.TestCase):
    def test_returns_the_newest_published_day(self):
        latest = latest_report(WEB_DIR)
        self.assertIsNotNone(latest)
        self.assertIn("date", latest)
        self.assertIn("total_items", latest)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./venv/bin/python3 -m unittest tests.admin_dashboard_test -v`

Expected: FAIL at import — `No module named 'admin_service.dashboard'`.

- [ ] **Step 3: Write the module**

Create `admin_service/dashboard.py`:

```python
"""Assemble dashboard data from committed report files.

Everything here reads what the pipeline already publishes into web/data. No
GitHub API, no network: this is the tier that works offline, has full history,
and costs nothing.

The one judgement encoded here: a date with no published report yields None,
not 0. Zero means "collected nothing", which is an outage; None means "no
report", which is a gap. Conflating them turns every missed day into a false
alarm.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.source_anomaly import detect, load_history  # noqa: E402

__all__ = ["health_series", "cost_series", "latest_report"]


def _published_dates(web_dir: Path) -> list[str]:
    data_dir = Path(web_dir) / "data"
    if not data_dir.is_dir():
        return []
    return sorted(p.parent.name for p in data_dir.glob("*/summary.json"))


def health_series(web_dir: Path, days: int = 90) -> dict[str, Any]:
    """Per-source item counts over a trailing window, with anomaly flags."""
    web_dir = Path(web_dir)
    readings = load_history(web_dir)
    if not readings:
        return {"sources": [], "dates": [], "series": {}, "anomalies": []}

    # Detect over ALL history so baselines are well-formed, then window the view.
    anomalies = detect(readings)

    dates = sorted({r.date for r in readings})[-days:]
    date_set = set(dates)

    by_source: dict[str, dict[str, int]] = {}
    for reading in readings:
        by_source.setdefault(reading.source, {})[reading.date] = reading.count

    # Order sources by typical volume so the heaviest lanes read first.
    sources = sorted(
        by_source,
        key=lambda s: -max(by_source[s].values(), default=0),
    )

    series = {
        source: [by_source[source].get(date) for date in dates]
        for source in sources
    }

    return {
        "sources": sources,
        "dates": dates,
        "series": series,
        "anomalies": [
            {
                "date": a.date,
                "source": a.source,
                "count": a.count,
                "baseline": round(a.baseline),
                "weekday": a.weekday,
                "ratio": round(a.ratio, 3),
                "detail": a.describe(),
            }
            for a in anomalies
            if a.date in date_set
        ],
    }


def cost_series(web_dir: Path, days: int = 90) -> list[dict[str, Any]]:
    """Per-run cost and token totals from committed replay indexes.

    replay-index.json already carries everything the cost panel needs, so no
    artifact download or ingest is required. Days without a replay index are
    simply absent -- the feature is new and there is no backfill for most of
    the archive.
    """
    web_dir = Path(web_dir)
    rows: list[dict[str, Any]] = []

    for date in _published_dates(web_dir)[-days:]:
        index_path = web_dir / "data" / date / "replay-index.json"
        if not index_path.is_file():
            continue
        try:
            payload = json.loads(index_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        run = payload.get("run") or {}
        rows.append({
            "date": date,
            "cost_usd": float(run.get("total_cost_usd") or 0.0),
            "llm_calls": int(run.get("llm_calls") or 0),
            "input_tokens": int(run.get("total_input_tokens") or 0),
            "output_tokens": int(run.get("total_output_tokens") or 0),
            "items": int(run.get("total_items_analyzed") or 0),
            "status": str(run.get("status") or "unknown"),
            "duration_ms": int(payload.get("duration_ms") or 0),
            # False means the run was reconstructed offline; wait_ms and
            # first_token_ms are unrecoverable after the fact. The UI must not
            # present a reconstruction as a measurement.
            "timings_measured": bool(run.get("timings_measured", False)),
        })

    return rows


def latest_report(web_dir: Path) -> dict[str, Any] | None:
    """Headline numbers for the most recent published day."""
    web_dir = Path(web_dir)
    dates = _published_dates(web_dir)
    if not dates:
        return None

    date = dates[-1]
    try:
        index = json.loads((web_dir / "data" / "index.json").read_text())
    except (OSError, json.JSONDecodeError):
        index = {}

    entry = next(
        (d for d in index.get("dates", []) if d.get("date") == date),
        {},
    )

    try:
        summary = json.loads((web_dir / "data" / date / "summary.json").read_text())
    except (OSError, json.JSONDecodeError):
        summary = {}

    return {
        "date": date,
        "total_items": entry.get("total_items", 0),
        "categories": entry.get("categories", {}),
        "topics": len(summary.get("top_topics") or []),
        "generated_at": summary.get("generated_at"),
        "has_replay": (web_dir / "data" / date / "replay-index.json").is_file(),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/bin/python3 -m unittest tests.admin_dashboard_test -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add admin_service/dashboard.py tests/admin_dashboard_test.py
git commit -m "admin: assemble dashboard data from committed reports

Health, cost, and headline numbers all come from what the pipeline already
publishes -- no API, no ingest, full history, works offline.

Missing days are null rather than 0: zero means the source collected nothing,
which is an outage, while null means no report was published that day. Drawing
them the same way turns every gap into a false alarm.

Cost rows carry timings_measured so the UI can mark offline-reconstructed runs
instead of presenting them as measurements."
```

---

### Task 2: Live vendor balances

**Files:**
- Create: `admin_service/balances.py`

**Interfaces:**
- Consumes: `AdminStore.{record_balance, balance_history}` (plan 2).
- Produces: `fetch_balances(store, *, scrapecreators_key="", twitter_key="") -> list[dict]` returning per-vendor `{vendor, label, balance, balance_usd, unit, history, burn_per_day, days_remaining, error}`.

- [ ] **Step 1: Write the module**

Create `admin_service/balances.py`:

```python
"""Live vendor credit balances, and the burn-down they imply.

Both probes are free and documented as not consuming credit
(agents/gatherers/reddit_gatherer.py:419-434,
agents/gatherers/social_gatherer.py:184-189), so the panel can read them on
demand rather than ingesting artifacts.

A single reading cannot produce a trend, so each probe is appended to the
store. Days-to-zero appears once there are two readings far enough apart to
mean something.

This exists because ScrapeCreators credits are finite and their exhaustion is
silent: when they run out Reddit collection stops, and published
collection_status would still read success.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from .store import AdminStore

__all__ = ["fetch_balances"]

logger = logging.getLogger("admin_service.balances")

SCRAPECREATORS_URL = "https://api.scrapecreators.com/v1/account/credit-balance"
TWITTERAPI_URL = "https://api.twitterapi.io/oapi/my/info"


def _burn_rate(history: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Credits per day and days remaining, from the oldest and newest readings."""
    if len(history) < 2:
        return None, None

    newest, oldest = history[0], history[-1]
    try:
        t_new = datetime.fromisoformat(newest["ts"])
        t_old = datetime.fromisoformat(oldest["ts"])
    except (KeyError, ValueError):
        return None, None

    elapsed_days = (t_new - t_old).total_seconds() / 86400
    if elapsed_days < 0.5:
        return None, None  # too short a span to extrapolate honestly

    consumed = oldest["balance"] - newest["balance"]
    if consumed <= 0:
        return None, None  # topped up, or flat

    per_day = consumed / elapsed_days
    return round(per_day, 1), round(newest["balance"] / per_day, 1) if per_day else None


def _probe_scrapecreators(key: str) -> tuple[int | None, str]:
    if not key:
        return None, "SCRAPECREATORS_API_KEY not configured"
    try:
        response = httpx.get(
            SCRAPECREATORS_URL, headers={"x-api-key": key}, timeout=15
        )
        if response.status_code != 200:
            return None, f"probe returned HTTP {response.status_code}"
        payload = response.json()
        value = payload.get("creditCount", payload.get("credits_remaining"))
        return (int(value), "") if value is not None else (None, "no balance in response")
    except Exception as exc:  # noqa: BLE001 - a probe failure must not break the page
        return None, f"{type(exc).__name__}"


def _probe_twitter(key: str) -> tuple[int | None, str]:
    if not key:
        return None, "TWITTERAPI_IO_KEY not configured"
    try:
        response = httpx.get(TWITTERAPI_URL, headers={"x-api-key": key}, timeout=15)
        if response.status_code != 200:
            return None, f"probe returned HTTP {response.status_code}"
        payload = response.json()
        data = payload.get("data") or payload
        value = data.get("recharge_credits")
        return (int(value), "") if value is not None else (None, "no balance in response")
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}"


def fetch_balances(
    store: AdminStore,
    *,
    scrapecreators_key: str = "",
    twitter_key: str = "",
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for vendor, label, unit, balance, error, usd in (
        ("scrapecreators", "ScrapeCreators (Reddit)", "credits",
         *_probe_scrapecreators(scrapecreators_key), None),
        ("twitterapi", "TwitterAPI.io (Twitter)", "credits",
         *_probe_twitter(twitter_key), None),
    ):
        if balance is not None:
            # TwitterAPI bills $1 per 100,000 recharge credits.
            usd = round(balance / 100000, 2) if vendor == "twitterapi" else None
            store.record_balance(vendor, balance, usd)

        history = store.balance_history(vendor, limit=90)
        per_day, days_left = _burn_rate(history)

        results.append({
            "vendor": vendor,
            "label": label,
            "unit": unit,
            "balance": balance,
            "balance_usd": usd,
            "error": error,
            "history": list(reversed(history)),  # oldest first, for charting
            "burn_per_day": per_day,
            "days_remaining": days_left,
        })

    return results
```

- [ ] **Step 2: Verify the burn-rate maths against the real numbers**

Run:
```bash
./venv/bin/python3 -c "
import tempfile, pathlib
from admin_service.store import AdminStore
from admin_service.balances import _burn_rate
s = AdminStore(pathlib.Path(tempfile.mkdtemp())/'t.sqlite3')
# Real observed readings: 24198 on 2026-05-29, 11668 on 2026-07-27 (59 days).
hist = [
    {'ts':'2026-07-27T00:00:00+00:00','balance':11668},
    {'ts':'2026-05-29T00:00:00+00:00','balance':24198},
]
per_day, days = _burn_rate(hist)
print(f'burn {per_day}/day, {days} days remaining')
print('matches the ~212/day and ~55-day figure measured during design:', 200 < per_day < 225 and 45 < days < 65)
print('single reading ->', _burn_rate(hist[:1]))
print('topped up     ->', _burn_rate([{'ts':'2026-07-27T00:00:00+00:00','balance':30000},{'ts':'2026-05-29T00:00:00+00:00','balance':10000}]))
"
```

Expected: roughly `burn 212.4/day, 54.9 days remaining`, `True`, then `(None, None)` for both the single-reading and topped-up cases — neither can honestly produce a trend.

- [ ] **Step 3: Commit**

```bash
git add admin_service/balances.py
git commit -m "admin: live vendor balance probes with burn-down

Both endpoints are free and do not consume credit, so the panel reads them on
demand instead of ingesting artifacts. Each reading is appended to the store,
which is what makes a trend possible -- one reading is a number, two are a
forecast.

Refuses to extrapolate from a single reading, a span under half a day, or a
balance that went up. A confident days-to-zero built on noise is worse than
no estimate."
```

---

### Task 3: Dashboard endpoints and the dev proxy

**Files:**
- Modify: `admin_service/app.py`, `frontend/vite.config.ts`

**Interfaces:**
- Consumes: `dashboard.{health_series, cost_series, latest_report}`, `balances.fetch_balances`, `GitHubClient.list_runs`.
- Produces: `GET /api/dashboard/health`, `/api/dashboard/cost`, `/api/dashboard/latest`, `/api/dashboard/balances`, `/api/dashboard/runs`.

- [ ] **Step 1: Add the endpoints**

In `admin_service/app.py`, add imports:

```python
import os

from .balances import fetch_balances
from .dashboard import cost_series, health_series, latest_report
```

Add these routes inside `create_app`, before `return app`:

```python
    @app.get("/api/dashboard/latest")
    def dashboard_latest(principal: Principal = Depends(require_principal)) -> dict:
        return {"latest": latest_report(settings.repo_dir / "web")}

    @app.get("/api/dashboard/health")
    def dashboard_health(
        days: int = 90, principal: Principal = Depends(require_principal)
    ) -> dict:
        return health_series(settings.repo_dir / "web", days=max(7, min(days, 365)))

    @app.get("/api/dashboard/cost")
    def dashboard_cost(
        days: int = 90, principal: Principal = Depends(require_principal)
    ) -> dict:
        return {"runs": cost_series(settings.repo_dir / "web", days=max(7, min(days, 400)))}

    @app.get("/api/dashboard/balances")
    def dashboard_balances(principal: Principal = Depends(require_principal)) -> dict:
        return {
            "balances": fetch_balances(
                store,
                scrapecreators_key=os.environ.get("SCRAPECREATORS_API_KEY", ""),
                twitter_key=os.environ.get("TWITTERAPI_IO_KEY", ""),
            )
        }

    @app.get("/api/dashboard/runs")
    def dashboard_runs(
        limit: int = 30, principal: Principal = Depends(require_principal)
    ) -> dict:
        try:
            runs = github.list_runs(limit=max(1, min(limit, 100)))
        except GitHubError as exc:
            # A GitHub outage or missing token must degrade this panel, not the page.
            return {"runs": [], "error": str(exc)}

        for run in runs:
            # 13 of 50 "successful" runs are 15-second schedule-gate no-ops. Counting
            # them halves the apparent success rate and wrecks duration averages, so
            # mark them rather than filtering silently -- a hidden filter is its own
            # kind of lie.
            duration = 0
            if run.get("created_at") and run.get("updated_at"):
                from datetime import datetime
                try:
                    started = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                    ended = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
                    duration = int((ended - started).total_seconds())
                except ValueError:
                    duration = 0
            run["duration_seconds"] = duration
            run["did_real_work"] = duration >= 120

        return {"runs": runs}
```

- [ ] **Step 2: Add the dev proxy**

In `frontend/vite.config.ts`, add a `proxy` block inside the existing `server` object so it reads:

```ts
	server: {
		fs: {
			allow: ['..']
		},
		proxy: {
			// The admin panel talks to the admin service. In production the two are
			// the same origin behind the tunnel; in dev the service runs separately,
			// so forward /api to it. Start it with:
			//   ADMIN_DEV=1 ./venv/bin/uvicorn --factory admin_service.app:create_app --port 8200
			'/api': {
				target: 'http://127.0.0.1:8200',
				changeOrigin: false
			}
		}
	}
```

- [ ] **Step 3: Add a dev auth bypass — explicitly, loudly, and only for dev**

The panel cannot be developed locally if every request needs a real Cloudflare JWT. Add to `admin_service/app.py`, inside `require_principal`, as the **first** statement:

```python
        # Local development only. ADMIN_DEV is never set on the host: the
        # provisioned env file does not define it, and the systemd unit does not
        # pass it. Kept explicit and noisy rather than clever, because an auth
        # bypass that is easy to enable by accident is the bug this whole
        # service was designed to avoid.
        if os.environ.get("ADMIN_DEV") == "1":
            logger.warning("ADMIN_DEV=1: bypassing Access verification (dev only)")
            return Principal(email="dev@localhost", subject="dev", kind="user")
```

Then add a guard test to `tests/admin_auth_test.py`:

```python
class DevBypassTest(unittest.TestCase):
    """The dev bypass must be opt-in, and must never be on by default."""

    def test_bypass_is_off_unless_explicitly_enabled(self):
        import os
        self.assertNotEqual(
            "1", os.environ.get("ADMIN_DEV"),
            "ADMIN_DEV=1 is set in this environment; tests would pass against a "
            "bypassed verifier and prove nothing",
        )

    def test_production_env_file_does_not_define_it(self):
        from pathlib import Path
        template = Path(__file__).resolve().parents[1] / "deploy" / "setup_admin_service.sh"
        if template.is_file():
            self.assertNotIn(
                "ADMIN_DEV", template.read_text(),
                "the provisioning script must never write ADMIN_DEV into the host "
                "env file",
            )
```

- [ ] **Step 4: Verify the endpoints work in dev mode**

Run:
```bash
cd /Users/ryand/Code/AATF/ai-news-aggregator
ADMIN_DEV=1 ADMIN_CF_TEAM_DOMAIN=dev.cloudflareaccess.com ADMIN_CF_AUD=dev \
ADMIN_ALLOWED_EMAILS=dev@localhost ADMIN_REPO_DIR="$PWD" \
ADMIN_STATE_DB=/tmp/admin-dev.sqlite3 \
./venv/bin/python3 -c "
from fastapi.testclient import TestClient
from admin_service.app import create_app
c = TestClient(create_app())
for path in ['/api/health','/api/me','/api/dashboard/latest','/api/dashboard/health?days=30','/api/dashboard/cost']:
    r = c.get(path)
    body = r.json()
    key = next(iter(body)) if isinstance(body, dict) else '?'
    print(f'{path:36s} {r.status_code} ({key})')
r = c.get('/api/dashboard/health?days=30')
d = r.json()
print('sources:', d['sources'][:5])
print('dates:', len(d['dates']), 'anomalies:', len(d['anomalies']))
"
```

Expected: all `200`, a real source list, ~30 dates, and an anomaly count (0 if the window excludes the known incidents).

- [ ] **Step 5: Commit**

```bash
git add admin_service/app.py frontend/vite.config.ts tests/admin_auth_test.py
git commit -m "admin: dashboard endpoints and dev proxy

Vite forwards /api to a locally running service so the panel is developable
without host access or a real Access token.

The dev bypass is opt-in via ADMIN_DEV=1, logs a warning every request, is
never written into the host env file, and is pinned by tests that fail if it
is enabled while the suite runs. An auth bypass that can be switched on by
accident is exactly the failure this service exists to avoid.

Run rows are marked did_real_work rather than filtered: 13 of 50 successful
runs are 15-second schedule-gate no-ops, and silently dropping them is its own
distortion."
```

---

### Task 4: The panel shell

**Files:**
- Create: `frontend/src/routes/admin/+page.ts`, `frontend/src/routes/admin/+page.svelte`, `frontend/src/lib/types/admin.ts`, `frontend/src/lib/services/adminApi.ts`

**Interfaces:**
- Consumes: the endpoints from Task 3.
- Produces: `/admin` route; `adminApi.{getLatest, getHealth, getCost, getBalances, getRuns, getActions, runAction, getActionStatus, getActionLogs, getAudit}`; types `HealthSeries`, `CostRun`, `Balance`, `WorkflowRun`, `ActionSpec`, `ActionStatus`.

- [ ] **Step 1: Disable prerendering for the route**

Create `frontend/src/routes/admin/+page.ts`:

```ts
// The admin panel is authenticated and entirely dynamic. Prerendering it would
// bake an empty shell into the public build and ship it to news.aatf.ai.
export const prerender = false;
export const ssr = false;
```

- [ ] **Step 2: Write the types**

Create `frontend/src/lib/types/admin.ts`:

```ts
export interface HealthAnomaly {
	date: string;
	source: string;
	count: number;
	baseline: number;
	weekday: string;
	ratio: number;
	detail: string;
}

export interface HealthSeries {
	sources: string[];
	dates: string[];
	/** null means no report published that day — not "collected nothing". */
	series: Record<string, (number | null)[]>;
	anomalies: HealthAnomaly[];
}

export interface CostRun {
	date: string;
	cost_usd: number;
	llm_calls: number;
	input_tokens: number;
	output_tokens: number;
	items: number;
	status: string;
	duration_ms: number;
	/** false = reconstructed offline; timings are not measurements. */
	timings_measured: boolean;
}

export interface BalanceHistoryPoint {
	ts: string;
	balance: number;
	balance_usd: number | null;
}

export interface Balance {
	vendor: string;
	label: string;
	unit: string;
	balance: number | null;
	balance_usd: number | null;
	error: string;
	history: BalanceHistoryPoint[];
	burn_per_day: number | null;
	days_remaining: number | null;
}

export interface WorkflowRun {
	id: number;
	status: string;
	conclusion: string | null;
	event: string;
	created_at: string;
	updated_at: string;
	html_url: string;
	run_attempt: number;
	duration_seconds: number;
	/** false = a schedule-gate no-op, not a real pipeline run. */
	did_real_work: boolean;
}

export interface LatestReport {
	date: string;
	total_items: number;
	categories: Record<string, { count: number; file_size: number }>;
	topics: number;
	generated_at: string | null;
	has_replay: boolean;
}

export interface ActionSpec {
	name: string;
	description: string;
	needs_arg: boolean;
	danger: 'low' | 'medium' | 'high';
}

export interface ActionStatus {
	unit: string;
	active_state: string;
	result: string;
	exit_code: number | null;
	finished: boolean;
	succeeded: boolean;
}

export interface AuditEntry {
	id: number;
	ts: string;
	principal: string;
	action: string;
	target: string | null;
	outcome: string;
	detail: string;
}
```

- [ ] **Step 3: Write the API client**

Create `frontend/src/lib/services/adminApi.ts`:

```ts
import type {
	ActionSpec,
	ActionStatus,
	AuditEntry,
	Balance,
	CostRun,
	HealthSeries,
	LatestReport,
	WorkflowRun
} from '$lib/types/admin';

/** Thrown for any non-OK response so callers can show the real reason. */
export class AdminApiError extends Error {
	constructor(
		message: string,
		readonly status: number
	) {
		super(message);
		this.name = 'AdminApiError';
	}
}

async function get<T>(path: string): Promise<T> {
	const response = await fetch(path, { credentials: 'same-origin' });
	if (response.status === 401 || response.status === 403) {
		throw new AdminApiError(
			'Your Cloudflare Access session has expired. Reload to sign in again.',
			response.status
		);
	}
	if (!response.ok) {
		throw new AdminApiError(`Request failed (${response.status})`, response.status);
	}
	return (await response.json()) as T;
}

async function post<T>(path: string): Promise<T> {
	const response = await fetch(path, { method: 'POST', credentials: 'same-origin' });
	const body = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new AdminApiError(body?.detail ?? `Request failed (${response.status})`, response.status);
	}
	return body as T;
}

export const getLatest = () => get<{ latest: LatestReport | null }>('/api/dashboard/latest');
export const getHealth = (days = 90) => get<HealthSeries>(`/api/dashboard/health?days=${days}`);
export const getCost = (days = 90) => get<{ runs: CostRun[] }>(`/api/dashboard/cost?days=${days}`);
export const getBalances = () => get<{ balances: Balance[] }>('/api/dashboard/balances');
export const getRuns = (limit = 30) =>
	get<{ runs: WorkflowRun[]; error?: string }>(`/api/dashboard/runs?limit=${limit}`);
export const getActions = () => get<{ actions: ActionSpec[] }>('/api/actions');
export const getAudit = (limit = 50) => get<{ actions: AuditEntry[] }>(`/api/audit?limit=${limit}`);

export const runAction = (action: string, arg?: string) =>
	post<{ unit: string; started: boolean }>(
		`/api/actions/${action}${arg ? `?arg=${encodeURIComponent(arg)}` : ''}`
	);

export const getActionStatus = (unit: string) =>
	get<ActionStatus>(`/api/actions/status/${encodeURIComponent(unit)}`);

export const getActionLogs = (unit: string, lines = 200) =>
	get<{ unit: string; lines: string[] }>(
		`/api/actions/logs/${encodeURIComponent(unit)}?lines=${lines}`
	);

export const dispatchPipeline = (targetDate?: string, commitOutputs = false) => {
	const params = new URLSearchParams({ commit_outputs: String(commitOutputs) });
	if (targetDate) params.set('target_date', targetDate);
	return post<{ dispatched: boolean }>(`/api/pipeline/dispatch?${params}`);
};
```

- [ ] **Step 4: Write the page shell**

Create `frontend/src/routes/admin/+page.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { getLatest } from '$lib/services/adminApi';
	import { AdminApiError } from '$lib/services/adminApi';
	import type { LatestReport } from '$lib/types/admin';
	import HealthTimeline from '$lib/components/admin/HealthTimeline.svelte';
	import RunHistory from '$lib/components/admin/RunHistory.svelte';
	import CostTrend from '$lib/components/admin/CostTrend.svelte';
	import BalanceCard from '$lib/components/admin/BalanceCard.svelte';
	import ActionPanel from '$lib/components/admin/ActionPanel.svelte';

	type View = 'health' | 'runs' | 'cost' | 'actions';

	let view = $state<View>('health');
	let latest = $state<LatestReport | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);

	onMount(async () => {
		try {
			latest = (await getLatest()).latest;
		} catch (e) {
			error = e instanceof AdminApiError ? e.message : 'Could not reach the admin service.';
		} finally {
			loading = false;
		}
	});

	const views: { id: View; label: string }[] = [
		{ id: 'health', label: 'Health' },
		{ id: 'runs', label: 'Runs' },
		{ id: 'cost', label: 'Cost' },
		{ id: 'actions', label: 'Actions' }
	];
</script>

<svelte:head>
	<title>Operations · AATF</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
	<header class="mb-6">
		<p class="eyebrow">Operations</p>
		<h1 class="text-2xl sm:text-3xl font-bold text-trend-gray-800 dark:text-trend-gray-100">
			Pipeline control
		</h1>
		<p class="mt-1 text-sm text-trend-gray-600 dark:text-trend-gray-400 max-w-2xl">
			{#if latest}
				Last published <strong class="text-trend-gray-800 dark:text-trend-gray-200"
					>{latest.date}</strong
				>
				— {latest.total_items.toLocaleString()} items, {latest.topics} topics.
			{:else if loading}
				Loading the current state…
			{:else}
				No published report found.
			{/if}
		</p>
	</header>

	{#if error}
		<div class="card border-l-4 border-l-trend-red" role="alert">
			<h2 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				The admin service is not responding
			</h2>
			<p class="mt-1 text-sm text-trend-gray-600 dark:text-trend-gray-400">{error}</p>
			<p class="mt-3 text-sm text-trend-gray-600 dark:text-trend-gray-400">
				In development, start it with:
				<code class="block mt-1 text-xs bg-black/5 dark:bg-white/5 rounded px-2 py-1"
					>ADMIN_DEV=1 ./venv/bin/uvicorn --factory admin_service.app:create_app --port 8200</code
				>
			</p>
		</div>
	{:else}
		{#if latest}
			<div class="run-stats mb-5">
				<div><span class="rs-label">Items</span><span class="rs-value">{latest.total_items.toLocaleString()}</span></div>
				<div><span class="rs-label">Topics</span><span class="rs-value">{latest.topics}</span></div>
				{#each Object.entries(latest.categories) as [name, info] (name)}
					<div>
						<span class="rs-label">{name}</span><span class="rs-value">{info.count}</span>
					</div>
				{/each}
				<div>
					<span class="rs-label">Replay</span>
					<span class="rs-value">{latest.has_replay ? 'yes' : 'none'}</span>
				</div>
			</div>
		{/if}

		<div class="viewswitch mb-4" role="tablist" aria-label="Dashboard view">
			{#each views as v (v.id)}
				<button
					role="tab"
					aria-selected={view === v.id}
					class:on={view === v.id}
					onclick={() => (view = v.id)}
				>
					{v.label}
				</button>
			{/each}
		</div>

		{#if view === 'health'}
			<HealthTimeline />
		{:else if view === 'runs'}
			<RunHistory />
		{:else if view === 'cost'}
			<CostTrend />
			<div class="grid gap-4 sm:grid-cols-2 mt-4">
				<BalanceCard />
			</div>
		{:else if view === 'actions'}
			<ActionPanel />
		{/if}
	{/if}
</div>

<style>
	.eyebrow {
		font-size: 0.62rem;
		font-weight: 700;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: #e63946;
		margin-bottom: 2px;
	}

	.run-stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(5.5rem, 1fr));
		gap: 0.4rem;
	}
	.run-stats > div {
		display: flex;
		flex-direction: column;
		padding: 0.4rem 0.55rem;
		border-radius: 0.5rem;
		background: rgb(0 0 0 / 0.035);
	}
	:global(.dark) .run-stats > div {
		background: rgb(255 255 255 / 0.045);
	}
	.run-stats :global(.rs-label) {
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: #737373;
	}
	.run-stats :global(.rs-value) {
		font-size: 0.95rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
	}

	.viewswitch {
		display: inline-flex;
		border-radius: 8px;
		overflow: hidden;
		border: 1px solid rgb(0 0 0 / 0.12);
	}
	:global(.dark) .viewswitch {
		border-color: rgb(255 255 255 / 0.13);
	}
	.viewswitch button {
		font-size: 0.75rem;
		font-weight: 650;
		padding: 5px 14px;
		color: #525252;
		transition: background 140ms ease, color 140ms ease;
	}
	:global(.dark) .viewswitch button {
		color: #a3a3a3;
	}
	.viewswitch button.on {
		background: #e63946;
		color: #fff;
	}
	.viewswitch button:focus-visible {
		outline: 2px solid #e63946;
		outline-offset: -2px;
	}

	@media (prefers-reduced-motion: reduce) {
		.viewswitch button {
			transition: none;
		}
	}
</style>
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/admin frontend/src/lib/types/admin.ts frontend/src/lib/services/adminApi.ts
git commit -m "admin: panel shell following the replay interface

Reuses the replay vocabulary directly -- eyebrow, run-stats KPI grid,
viewswitch tabs -- so the panel reads as part of the same system rather than a
second design.

prerender and ssr are both off: baking an authenticated shell into the static
build would ship it to the public site.

The error state names the actual dev command instead of apologising, since the
most common cause is simply that the service is not running yet."
```

---

### Task 5: Health timeline

**Files:**
- Create: `frontend/src/lib/components/admin/HealthTimeline.svelte`

**Interfaces:**
- Consumes: `adminApi.getHealth`, types `HealthSeries`, `HealthAnomaly`.
- Produces: the health view.

- [ ] **Step 1: Write the component**

Create `frontend/src/lib/components/admin/HealthTimeline.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { getHealth } from '$lib/services/adminApi';
	import type { HealthSeries } from '$lib/types/admin';

	let data = $state<HealthSeries | null>(null);
	let error = $state<string | null>(null);
	let days = $state(90);

	async function load() {
		error = null;
		try {
			data = await getHealth(days);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load source health.';
		}
	}

	onMount(load);

	// Per-source scaling: research runs ~500/day and news ~45, so one shared
	// scale would flatten news into a single tone and hide exactly the drops
	// this view exists to show.
	function intensity(source: string, value: number | null): number {
		if (value === null || !data) return 0;
		const series = data.series[source].filter((v): v is number => v !== null);
		const peak = Math.max(...series, 1);
		return Math.min(1, value / peak);
	}

	function isAnomalous(date: string, source: string): boolean {
		return !!data?.anomalies.some((a) => a.date === date && a.source === source);
	}

	const recentAnomalies = $derived(
		[...(data?.anomalies ?? [])].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 8)
	);
</script>

<div class="card">
	<div class="flex items-baseline justify-between gap-3 flex-wrap mb-3">
		<div>
			<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				Source health
			</h2>
			<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
				Items collected per source. Compared against the same weekday, because arXiv skips
				weekends and Monday runs a three-day catch-up.
			</p>
		</div>
		<label class="text-xs text-trend-gray-600 dark:text-trend-gray-400">
			Window
			<select
				bind:value={days}
				onchange={load}
				class="ml-1 rounded border border-trend-gray-300 dark:border-trend-gray-600 bg-transparent px-1 py-0.5"
			>
				<option value={30}>30 days</option>
				<option value={90}>90 days</option>
				<option value={180}>180 days</option>
			</select>
		</label>
	</div>

	{#if error}
		<p class="text-sm text-trend-red">{error}</p>
	{:else if !data}
		<p class="text-sm text-trend-gray-500">Loading…</p>
	{:else if data.dates.length === 0}
		<p class="text-sm text-trend-gray-500">No published reports in this window.</p>
	{:else}
		<div class="overflow-x-auto">
			<table class="heatmap">
				<caption class="sr-only">
					Items collected per source per day, with anomalies marked
				</caption>
				<tbody>
					{#each data.sources as source (source)}
						<tr>
							<th scope="row">{source}</th>
							<td class="cells">
								{#each data.dates as date, i (date)}
									{@const value = data.series[source][i]}
									{@const flagged = isAnomalous(date, source)}
									<span
										class="cell"
										class:missing={value === null}
										class:flagged
										style="--i: {intensity(source, value)}"
										title="{source} · {date} · {value === null
											? 'no report published'
											: `${value} items`}{flagged ? ' · below baseline' : ''}"
									></span>
								{/each}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<p class="legend">
			<span class="cell" style="--i: 0.15"></span>
			<span class="cell" style="--i: 0.5"></span>
			<span class="cell" style="--i: 1"></span>
			<span>volume</span>
			<span class="cell flagged" style="--i: 0.2"></span>
			<span>below baseline</span>
			<span class="cell missing"></span>
			<span>no report</span>
		</p>

		{#if recentAnomalies.length}
			<div class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700">
				<h3 class="text-sm font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-2">
					Flagged
				</h3>
				<ul class="space-y-1">
					{#each recentAnomalies as a (a.date + a.source)}
						<li class="text-sm text-trend-gray-700 dark:text-trend-gray-300">
							<span class="font-mono text-xs text-trend-gray-500">{a.date}</span>
							{a.detail}
						</li>
					{/each}
				</ul>
			</div>
		{:else}
			<p class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700 text-sm text-trend-gray-600 dark:text-trend-gray-400">
				No sources fell below their baseline in this window.
			</p>
		{/if}
	{/if}
</div>

<style>
	.heatmap {
		border-collapse: collapse;
		width: 100%;
	}
	.heatmap th {
		text-align: right;
		padding-right: 0.6rem;
		font-size: 0.7rem;
		font-weight: 600;
		color: #525252;
		white-space: nowrap;
		vertical-align: middle;
	}
	:global(.dark) .heatmap th {
		color: #a3a3a3;
	}
	.cells {
		display: flex;
		gap: 1px;
		padding: 2px 0;
	}
	.cell {
		flex: 1 1 auto;
		min-width: 3px;
		height: 16px;
		border-radius: 1px;
		/* Volume is a neutral blue ramp, deliberately NOT the category colours:
		   category-research is the same green as `success` and category-reddit the
		   same red as `failed`, so a red Reddit lane would read as broken. */
		background: color-mix(in srgb, #3b82f6 calc(var(--i) * 100%), transparent);
	}
	.cell.missing {
		background: repeating-linear-gradient(
			45deg,
			rgb(0 0 0 / 0.06),
			rgb(0 0 0 / 0.06) 2px,
			transparent 2px,
			transparent 4px
		);
	}
	:global(.dark) .cell.missing {
		background: repeating-linear-gradient(
			45deg,
			rgb(255 255 255 / 0.08),
			rgb(255 255 255 / 0.08) 2px,
			transparent 2px,
			transparent 4px
		);
	}
	.cell.flagged {
		background: #ef4444;
		box-shadow: 0 0 0 1px #ef4444;
	}
	.legend {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-top: 0.6rem;
		font-size: 0.7rem;
		color: #737373;
	}
	.legend .cell {
		flex: 0 0 12px;
		width: 12px;
		height: 12px;
	}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/components/admin/HealthTimeline.svelte
git commit -m "admin: source health timeline

One lane per source, scaled per-source: research runs ~500/day and news ~45,
so a shared scale would flatten news into one tone and hide the drops this view
exists to surface.

Volume uses a neutral blue ramp rather than the category palette, because
category-research is the same green as success and category-reddit the same red
as failed -- a red Reddit lane would read as broken when it is fine.

Days with no report are hatched, not empty: null and zero mean different things
and only one of them is an outage."
```

---

### Task 6: Runs, cost, balances, and actions

**Files:**
- Create: `frontend/src/lib/components/admin/{RunHistory,CostTrend,BalanceCard,ActionPanel}.svelte`

- [ ] **Step 1: Write RunHistory**

Create `frontend/src/lib/components/admin/RunHistory.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { getRuns } from '$lib/services/adminApi';
	import type { WorkflowRun } from '$lib/types/admin';

	let runs = $state<WorkflowRun[]>([]);
	let error = $state<string | null>(null);
	let apiNote = $state<string | null>(null);
	let hideNoops = $state(true);

	onMount(async () => {
		try {
			const payload = await getRuns(50);
			runs = payload.runs;
			apiNote = payload.error ?? null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load run history.';
		}
	});

	const shown = $derived(hideNoops ? runs.filter((r) => r.did_real_work) : runs);
	const noopCount = $derived(runs.filter((r) => !r.did_real_work).length);

	function statusColor(run: WorkflowRun): string {
		if (run.status !== 'completed') return '#3b82f6';
		if (run.conclusion === 'success') return '#10b981';
		if (run.conclusion === 'cancelled') return '#a3a3a3';
		return '#ef4444';
	}

	function duration(seconds: number): string {
		if (seconds < 60) return `${seconds}s`;
		return `${Math.round(seconds / 60)}m`;
	}
</script>

<div class="card">
	<div class="flex items-baseline justify-between gap-3 flex-wrap mb-3">
		<div>
			<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				Pipeline runs
			</h2>
			<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
				From the Actions API. Published data only ever contains successful runs, so failures
				and cancellations appear nowhere else.
			</p>
		</div>
		{#if noopCount}
			<label class="text-xs text-trend-gray-600 dark:text-trend-gray-400">
				<input type="checkbox" bind:checked={hideNoops} />
				Hide {noopCount} schedule-gate no-op{noopCount === 1 ? '' : 's'}
			</label>
		{/if}
	</div>

	{#if error}
		<p class="text-sm text-trend-red">{error}</p>
	{:else if apiNote}
		<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">{apiNote}</p>
	{:else if !runs.length}
		<p class="text-sm text-trend-gray-500">Loading…</p>
	{:else}
		<ul class="divide-y divide-trend-gray-200 dark:divide-trend-gray-700">
			{#each shown as run (run.id)}
				<li class="py-2 flex items-center gap-3 text-sm">
					<span class="dot" style="--c: {statusColor(run)}" aria-hidden="true"></span>
					<a
						href={run.html_url}
						target="_blank"
						rel="noopener noreferrer"
						class="font-mono text-xs text-trend-red hover:text-guardian-red"
					>
						{run.created_at.slice(0, 10)}
					</a>
					<span class="text-trend-gray-700 dark:text-trend-gray-300">
						{run.status === 'completed' ? (run.conclusion ?? 'unknown') : run.status}
					</span>
					<span class="text-trend-gray-500 text-xs">{run.event}</span>
					<span class="ml-auto text-trend-gray-500 text-xs tabular-nums">
						{duration(run.duration_seconds)}
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--c);
		flex: 0 0 8px;
	}
</style>
```

- [ ] **Step 2: Write CostTrend**

Create `frontend/src/lib/components/admin/CostTrend.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { getCost } from '$lib/services/adminApi';
	import type { CostRun } from '$lib/types/admin';

	let runs = $state<CostRun[]>([]);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			runs = (await getCost(400)).runs;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load cost history.';
		}
	});

	const peak = $derived(Math.max(...runs.map((r) => r.cost_usd), 0.01));
	const total = $derived(runs.reduce((sum, r) => sum + r.cost_usd, 0));
	const mean = $derived(runs.length ? total / runs.length : 0);
</script>

<div class="card">
	<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">Cost per run</h2>
	<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
		From each day's published replay index. No ingest required.
	</p>

	{#if error}
		<p class="mt-3 text-sm text-trend-red">{error}</p>
	{:else if !runs.length}
		<p class="mt-3 text-sm text-trend-gray-500">
			No replay indexes published yet. This fills in as runs complete.
		</p>
	{:else}
		<div class="run-stats mt-3 mb-3">
			<div><span class="rs-label">Runs</span><span class="rs-value">{runs.length}</span></div>
			<div><span class="rs-label">Mean</span><span class="rs-value">${mean.toFixed(2)}</span></div>
			<div><span class="rs-label">Peak</span><span class="rs-value">${peak.toFixed(2)}</span></div>
			<div><span class="rs-label">Total</span><span class="rs-value">${total.toFixed(2)}</span></div>
		</div>

		<div class="bars">
			{#each runs as run (run.date)}
				<span
					class="bar"
					class:reconstructed={!run.timings_measured}
					style="--h: {(run.cost_usd / peak) * 100}%"
					title="{run.date} · ${run.cost_usd.toFixed(2)} · {run.llm_calls} calls{run.timings_measured
						? ''
						: ' · reconstructed offline'}"
				></span>
			{/each}
		</div>

		{#if runs.some((r) => !r.timings_measured)}
			<p class="mt-2 text-xs text-trend-gray-500">
				Hatched bars are days rebuilt from stored data. Their cost is real; their timings are
				not measurements.
			</p>
		{/if}
	{/if}
</div>

<style>
	.bars {
		display: flex;
		align-items: flex-end;
		gap: 2px;
		height: 120px;
	}
	.bar {
		flex: 1 1 auto;
		min-width: 4px;
		height: var(--h);
		background: #e63946;
		border-radius: 1px 1px 0 0;
	}
	.bar.reconstructed {
		background: repeating-linear-gradient(
			45deg,
			#e63946,
			#e63946 3px,
			rgb(230 57 70 / 0.35) 3px,
			rgb(230 57 70 / 0.35) 6px
		);
	}
	.run-stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(5.5rem, 1fr));
		gap: 0.4rem;
	}
	.run-stats > div {
		display: flex;
		flex-direction: column;
		padding: 0.4rem 0.55rem;
		border-radius: 0.5rem;
		background: rgb(0 0 0 / 0.035);
	}
	:global(.dark) .run-stats > div {
		background: rgb(255 255 255 / 0.045);
	}
	.run-stats :global(.rs-label) {
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: #737373;
	}
	.run-stats :global(.rs-value) {
		font-size: 0.95rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
	}
</style>
```

- [ ] **Step 3: Write BalanceCard**

Create `frontend/src/lib/components/admin/BalanceCard.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { getBalances } from '$lib/services/adminApi';
	import type { Balance } from '$lib/types/admin';

	let balances = $state<Balance[]>([]);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			balances = (await getBalances()).balances;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not read vendor balances.';
		}
	});

	// Below this, a source is close enough to dying that it needs attention now.
	const URGENT_DAYS = 30;
</script>

{#if error}
	<div class="card"><p class="text-sm text-trend-red">{error}</p></div>
{:else}
	{#each balances as b (b.vendor)}
		<div class="card" data-urgent={b.days_remaining !== null && b.days_remaining < URGENT_DAYS}>
			<h3 class="text-sm font-semibold text-trend-gray-800 dark:text-trend-gray-100">{b.label}</h3>

			{#if b.balance === null}
				<p class="mt-2 text-sm text-trend-gray-600 dark:text-trend-gray-400">
					{b.error || 'No balance available.'}
				</p>
			{:else}
				<p class="balance">
					{b.balance.toLocaleString()}<span class="unit">{b.unit}</span>
				</p>
				{#if b.balance_usd !== null}
					<p class="text-xs text-trend-gray-500">${b.balance_usd.toFixed(2)}</p>
				{/if}

				{#if b.days_remaining !== null && b.burn_per_day !== null}
					<p
						class="mt-2 text-sm"
						class:urgent={b.days_remaining < URGENT_DAYS}
					>
						~{Math.round(b.days_remaining)} days left at {b.burn_per_day}/day
					</p>
					{#if b.days_remaining < URGENT_DAYS}
						<p class="mt-1 text-xs text-trend-gray-600 dark:text-trend-gray-400">
							When this reaches zero the source stops collecting, and the published report
							will still say success.
						</p>
					{/if}
				{:else}
					<p class="mt-2 text-xs text-trend-gray-500">
						Trend needs a second reading before it can project.
					</p>
				{/if}
			{/if}
		</div>
	{/each}
{/if}

<style>
	.balance {
		margin-top: 0.35rem;
		font-size: 1.6rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		line-height: 1.1;
	}
	.unit {
		font-size: 0.7rem;
		font-weight: 500;
		color: #737373;
		margin-left: 0.3rem;
	}
	.urgent {
		color: #ef4444;
		font-weight: 600;
	}
	.card[data-urgent='true'] {
		border-left: 4px solid #ef4444;
	}
</style>
```

- [ ] **Step 4: Write ActionPanel**

Create `frontend/src/lib/components/admin/ActionPanel.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getActionLogs,
		getActionStatus,
		getActions,
		getAudit,
		runAction
	} from '$lib/services/adminApi';
	import type { ActionSpec, ActionStatus, AuditEntry } from '$lib/types/admin';

	let actions = $state<ActionSpec[]>([]);
	let audit = $state<AuditEntry[]>([]);
	let error = $state<string | null>(null);

	let running = $state<string | null>(null);
	let status = $state<ActionStatus | null>(null);
	let logs = $state<string[]>([]);
	let confirming = $state<ActionSpec | null>(null);
	let dateArg = $state('');

	let poller: ReturnType<typeof setInterval> | null = null;

	onMount(async () => {
		try {
			actions = (await getActions()).actions;
			audit = (await getAudit(20)).actions;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load actions.';
		}
		return () => poller && clearInterval(poller);
	});

	async function start(spec: ActionSpec) {
		confirming = null;
		error = null;
		logs = [];
		status = null;
		try {
			const result = await runAction(spec.name, spec.needs_arg ? dateArg : undefined);
			running = result.unit;
			poll();
		} catch (e) {
			error = e instanceof Error ? e.message : `Could not start ${spec.name}.`;
		}
	}

	function poll() {
		if (poller) clearInterval(poller);
		poller = setInterval(async () => {
			if (!running) return;
			try {
				status = await getActionStatus(running);
				logs = (await getActionLogs(running, 120)).lines;
				// Stop on the unit's own terminal state, never on log silence:
				// a killed build and a slow build look identical in a log tail.
				if (status.finished && poller) {
					clearInterval(poller);
					poller = null;
					audit = (await getAudit(20)).actions;
				}
			} catch {
				// Transient poll failures are expected while a unit restarts.
			}
		}, 2000);
	}
</script>

<div class="card">
	<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">Actions</h2>
	<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
		Each action runs as a systemd unit on the host. Output is live.
	</p>

	{#if error}
		<p class="mt-3 text-sm text-trend-red" role="alert">{error}</p>
	{/if}

	<div class="mt-3 grid gap-2 sm:grid-cols-2">
		{#each actions as spec (spec.name)}
			<button
				class="action"
				data-danger={spec.danger}
				disabled={!!running && !status?.finished}
				onclick={() => (confirming = spec)}
			>
				<span class="name">{spec.name}</span>
				<span class="desc">{spec.description}</span>
			</button>
		{/each}
	</div>

	{#if confirming}
		<div class="confirm" role="dialog" aria-label="Confirm action">
			<p class="text-sm text-trend-gray-800 dark:text-trend-gray-100">
				Run <strong>{confirming.name}</strong>?
			</p>
			<p class="text-xs text-trend-gray-600 dark:text-trend-gray-400 mt-1">
				{confirming.description}.
				{#if confirming.danger === 'high'}
					This replaces the container serving news.aatf.ai.
				{/if}
			</p>
			{#if confirming.needs_arg}
				<label class="block mt-2 text-xs">
					Report date
					<input
						type="date"
						bind:value={dateArg}
						class="ml-1 rounded border border-trend-gray-300 dark:border-trend-gray-600 bg-transparent px-1 py-0.5"
					/>
				</label>
			{/if}
			<div class="mt-3 flex gap-2">
				<button
					class="btn-primary text-sm"
					disabled={confirming.needs_arg && !dateArg}
					onclick={() => confirming && start(confirming)}
				>
					Run {confirming.name}
				</button>
				<button class="btn-secondary text-sm" onclick={() => (confirming = null)}>Cancel</button>
			</div>
		</div>
	{/if}

	{#if running}
		<div class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700">
			<p class="text-sm">
				<span class="font-mono text-xs">{running}</span>
				{#if status}
					<span class="ml-2" data-state={status.finished ? (status.succeeded ? 'ok' : 'bad') : 'run'}>
						{status.finished ? (status.succeeded ? 'succeeded' : `failed (${status.result})`) : 'running…'}
					</span>
				{/if}
			</p>
			{#if logs.length}
				<pre class="logs">{logs.join('\n')}</pre>
			{/if}
		</div>
	{/if}

	{#if audit.length}
		<div class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700">
			<h3 class="text-sm font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-2">
				Recent activity
			</h3>
			<ul class="space-y-1 text-xs text-trend-gray-600 dark:text-trend-gray-400">
				{#each audit as entry (entry.id)}
					<li>
						<span class="font-mono">{entry.ts.slice(0, 16).replace('T', ' ')}</span>
						· {entry.principal} · <strong>{entry.action}</strong> · {entry.outcome}
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>

<style>
	.action {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 2px;
		padding: 0.6rem 0.75rem;
		border-radius: 0.5rem;
		border: 1px solid rgb(0 0 0 / 0.12);
		text-align: left;
		transition: border-color 140ms ease, background 140ms ease;
	}
	:global(.dark) .action {
		border-color: rgb(255 255 255 / 0.13);
	}
	.action:hover:not(:disabled) {
		border-color: #e63946;
	}
	.action:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.action[data-danger='high'] {
		border-left: 3px solid #e63946;
	}
	.action .name {
		font-weight: 650;
		font-size: 0.85rem;
	}
	.action .desc {
		font-size: 0.72rem;
		color: #737373;
	}
	.confirm {
		margin-top: 0.75rem;
		padding: 0.75rem;
		border-radius: 0.5rem;
		background: rgb(230 57 70 / 0.06);
		border: 1px solid rgb(230 57 70 / 0.25);
	}
	.logs {
		margin-top: 0.5rem;
		max-height: 18rem;
		overflow: auto;
		font-size: 0.7rem;
		line-height: 1.45;
		padding: 0.6rem;
		border-radius: 0.4rem;
		background: rgb(0 0 0 / 0.04);
		white-space: pre-wrap;
		word-break: break-word;
	}
	:global(.dark) .logs {
		background: rgb(255 255 255 / 0.05);
	}
	[data-state='ok'] {
		color: #10b981;
		font-weight: 600;
	}
	[data-state='bad'] {
		color: #ef4444;
		font-weight: 600;
	}
	[data-state='run'] {
		color: #3b82f6;
	}
	@media (prefers-reduced-motion: reduce) {
		.action {
			transition: none;
		}
	}
</style>
```

- [ ] **Step 5: Verify the panel builds and type-checks**

Run:
```bash
cd frontend
npm run check 2>&1 | tail -20
```

Expected: no errors in `src/routes/admin/` or `src/lib/components/admin/`.

**Known clean baseline:** `svelte-check` reports **4 pre-existing errors in `vite.config.ts`** from missing `@types/node` locally. Those are the baseline, not a regression. Run from `frontend/`, never the repo root — the root contains a stray untracked `data/admin/workspaces/` tree with old frontend copies that produces ~470 phantom errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/components/admin/
git commit -m "admin: runs, cost, balances, and action controls

Runs marks schedule-gate no-ops and offers to hide them, with the count shown
either way -- 13 of 50 successful runs are 15-second gate skips, and a silent
filter is its own distortion.

Cost hatches offline-reconstructed days: the spend is real, the timings are
not, and the replay contract forbids drawing a reconstruction as a
measurement.

Balances say what running out actually means, because the failure is silent --
the report keeps publishing and keeps saying success.

Action completion polls the unit's terminal state rather than watching output
stop, since a killed build and a slow build produce identical silence."
```

---

### Task 7: Make it runnable and document it

**Files:**
- Create: `scripts/admin_dev.sh`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write the dev launcher**

Create `scripts/admin_dev.sh`:

```bash
#!/bin/bash
# Run the admin service locally for frontend development.
#
# Auth is bypassed (ADMIN_DEV=1) because there is no Cloudflare Access in front
# of localhost. The bypass logs a warning on every request and is never present
# on the host.
#
#   ./scripts/admin_dev.sh          # then: cd frontend && npm run dev
set -euo pipefail

cd "$(dirname "$0")/.."

export ADMIN_DEV=1
export ADMIN_CF_TEAM_DOMAIN="${ADMIN_CF_TEAM_DOMAIN:-dev.cloudflareaccess.com}"
export ADMIN_CF_AUD="${ADMIN_CF_AUD:-dev-aud}"
export ADMIN_ALLOWED_EMAILS="${ADMIN_ALLOWED_EMAILS:-dev@localhost}"
export ADMIN_REPO_DIR="${ADMIN_REPO_DIR:-$PWD}"
export ADMIN_STATE_DB="${ADMIN_STATE_DB:-$PWD/data/admin-dev.sqlite3}"

echo "Admin service -> http://127.0.0.1:8200  (auth bypassed: dev only)"
echo "Frontend      -> cd frontend && npm run dev   (proxies /api here)"
echo

exec ./venv/bin/uvicorn --factory admin_service.app:create_app \
    --host 127.0.0.1 --port 8200 --reload
```

Then: `chmod +x scripts/admin_dev.sh`

- [ ] **Step 2: Verify the full dev loop**

In one terminal:
```bash
./scripts/admin_dev.sh
```

In another:
```bash
cd frontend && npm run dev
```

Then open `http://localhost:5173/admin`.

Expected: the panel loads, the KPI row shows the latest published date and item counts, and all four tabs render. Health shows the heatmap with flagged days; Cost shows bars for days that have a replay index; Runs shows recent workflow runs (or a clear note if no token is configured); Actions lists four actions and fails clearly on click, since the systemd units do not exist locally.

Confirm the service is reachable independently:
```bash
curl -sS localhost:8200/api/health
curl -sS localhost:5173/api/dashboard/latest | head -c 200
```

Expected: `{"status":"ok"}` and JSON through the Vite proxy.

- [ ] **Step 3: Document it**

Add to `CLAUDE.md` after the "Frontend Development" section:

```markdown
### Admin Panel Development

```bash
./scripts/admin_dev.sh            # admin service on :8200 (auth bypassed, dev only)
cd frontend && npm run dev        # panel at http://localhost:5173/admin
```

The Vite dev server proxies `/api` to the local service. `ADMIN_DEV=1` bypasses
Cloudflare Access verification and is set only by `scripts/admin_dev.sh` — it is
never written into the host env file, and a guard test fails if it is set while
the suite runs.

Actions will not work locally: they start systemd units that exist only on the
host. Everything else — health, cost, balances, runs — works from committed data
plus the GitHub API.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/admin_dev.sh CLAUDE.md
git commit -m "admin: dev launcher and docs

One script to run the service locally with the auth bypass, so the panel is
developable without host access or a Cloudflare session. Documents plainly that
actions do not work locally rather than letting them fail mysteriously."
```

---

## Self-Review

**Spec coverage.** Implements spec §5 panels 1-6 and §7 (visual design). Panel 5 (replay) is covered by `latest_report.has_replay` and the per-date link rather than a component, since `/replay` already exists and duplicating it would be the opposite of reuse.

**Placeholders.** None. Every component is complete and every step has a runnable command with expected output.

**Type consistency.** `admin.ts` interfaces mirror the endpoint payloads exactly: `HealthSeries` matches `health_series()`'s four keys; `CostRun` matches every key in `cost_series()`'s row dict; `Balance` matches `fetch_balances()`'s output including `burn_per_day`/`days_remaining`; `WorkflowRun` includes the `duration_seconds` and `did_real_work` the endpoint adds. `ActionStatus` fields match `/api/actions/status/{unit}`'s response keys, which in turn match the `ActionStatus` dataclass in plan 3.

**Two gaps carried forward.**

1. **The dev bypass is a real risk** and is treated as one: opt-in only, warns per request, absent from provisioning, and pinned by two tests. It is still the single most dangerous line in this plan, and any change near it deserves scrutiny.
2. **`deploy.sh` still does not take the shared `flock`** (carried from plan 3). Plan 5 must include it.

**One judgment call.** The health heatmap scales each source independently. A shared scale is more honest about relative volume but useless in practice — research at ~500/day would render news at ~45 as a uniform pale row, hiding exactly the drops the view exists to catch. The per-cell tooltip carries the absolute number, so the detail is one hover away.
