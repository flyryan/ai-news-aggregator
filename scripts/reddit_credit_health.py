#!/usr/bin/env python3
"""Free preflight balance probe and consumption-based early credit warnings."""
import argparse
import json
import math
import os
import statistics
import urllib.request
from pathlib import Path
try:
    from .pipeline_alert import post_alert
except ImportError:
    from pipeline_alert import post_alert


def reserve(history, days=3, fallback=600):
    # A repeated full run is real consumption: aggregate by date before taking
    # a daily median. Never learn a low budget from an aborted collection.
    daily = {}
    for row in history:
        if row.get('complete') and isinstance(row.get('consumed'), (int, float)) and row['consumed'] > 0:
            daily[row['date']] = daily.get(row['date'], 0) + row['consumed']
    values = [daily[date] for date in sorted(daily)[-14:]]
    burn = statistics.median(values) if values else fallback
    return math.ceil(burn * days), burn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    root = Path(args.data_dir)
    state_dir = root / 'health'
    state_dir.mkdir(parents=True, exist_ok=True)
    history_path = state_dir / 'reddit-credit-history.json'
    try:
        history = json.loads(history_path.read_text())
    except (OSError, ValueError):
        history = []
    if args.record:
        cost_path = root / 'processed' / f'cost_report_{args.date}.json'
        if not cost_path.exists():
            return 0
        cost = json.loads(cost_path.read_text())
        usage = cost.get('external_apis', {}).get('ScrapeCreators (Reddit)', {})
        consumed = usage.get('credits_consumed')
        run = os.environ.get('GITHUB_RUN_ID', cost.get('start_time', ''))
        history = [row for row in history if row.get('run') != run]
        history.append(dict(date=args.date, run=run, consumed=consumed,
                            complete=bool(consumed is not None and not usage.get('note'))))
        history_path.write_text(json.dumps(history[-60:], indent=2))
        balance = usage.get('balance')
    else:
        key = os.environ.get('SCRAPECREATORS_API_KEY', '').strip()
        if not key:
            print('::error::Reddit preflight: SCRAPECREATORS_API_KEY is missing')
            return 1
        base = os.environ.get('SCRAPECREATORS_BASE', 'https://api.scrapecreators.com').rstrip('/')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(base + '/v1/account/credit-balance', headers={'x-api-key': key})
        try:
            with opener.open(req, timeout=30) as response:
                data = json.load(response)
            balance = data.get('creditCount', data.get('credits_remaining'))
        except Exception as exc:
            # Unknown is not zero. Collection still gets its bounded retries.
            print(f'::warning::Reddit balance probe unavailable: {type(exc).__name__}')
            balance = None
    threshold, burn = reserve(history)
    result = dict(date=args.date, balance=balance, daily_consumption=burn,
                  warning_threshold=threshold, observation='after' if args.record else 'before')
    (state_dir / f'reddit-credit-{args.date}-{result["observation"]}.json').write_text(json.dumps(result, indent=2))
    print('Reddit credit health:', json.dumps(result))
    if balance is not None and balance < threshold:
        reason = f'Reddit credits {balance}; replenish before collection stops. Three-day reserve is {threshold} credits (daily estimate {burn:g}).'
        print('::warning::' + reason)
        # Receipt is per date AND severity. An exhaustion alert must escalate
        # an earlier low-balance warning, and failed delivery records nothing.
        receipt = state_dir / f'reddit-credit-alert-{args.date}-{"empty" if balance <= 0 else "low"}.json'
        if not receipt.exists():
            delivered = post_alert(dict(status='degraded', report_date=args.date, reason=reason,
                                        run_url=os.environ.get('RUN_URL', '')))
            if delivered:
                receipt.write_text(json.dumps(result))
            else:
                print('::error::Reddit credit warning could not be delivered')
        if balance <= 0:
            print('::error::Reddit credits exhausted; refusing to start the paid pipeline')
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
