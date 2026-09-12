#!/usr/bin/env python3
"""Deliver existing pipeline alerts directly, without collection proxy settings."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_ALERT_URL = 'https://flybotwebhook.duffplex.com/alert/pipeline'


def post_alert(payload):
    token = os.environ.get('PIPELINE_ALERT_TOKEN', '').strip()
    if not token:
        print('Alert delivery failed: PIPELINE_ALERT_TOKEN is missing')
        return False
    # Mullvad is for gathering sources. Its shared egress can be rejected by
    # the alert ingress even when the credentials and receiver are healthy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        os.environ.get('PIPELINE_ALERT_URL') or DEFAULT_ALERT_URL,
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
                 'User-Agent': 'ai-news-pipeline-alert/1.0'}, method='POST')
    for attempt in range(3):
        try:
            with opener.open(request, timeout=20) as response:
                print(f'Alert POST -> HTTP {response.status}')
                result = json.load(response)
                return 200 <= response.status < 300 and result.get('ok') is True
        except urllib.error.HTTPError as exc:
            print(f'Alert POST -> HTTP {exc.code} (delivery failed)')
            if exc.code not in (429, 500, 502, 503, 504):
                return False
        except (OSError, urllib.error.URLError, ValueError) as exc:
            print(f'Alert delivery failed: {type(exc).__name__}')
        if attempt < 2:
            time.sleep(2 ** attempt)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', default='failure')
    parser.add_argument('--date', required=True)
    parser.add_argument('--reason', default='')
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    payload = dict(status=args.status, report_date=args.date, reason=args.reason,
                   run_url=os.environ.get('RUN_URL', ''))
    if args.probe:
        payload['probe'] = True
    return 0 if post_alert(payload) else 1


if __name__ == '__main__':
    raise SystemExit(main())
