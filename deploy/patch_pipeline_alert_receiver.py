#!/usr/bin/env python3
"""Apply the September 12 pipeline alert repair to the existing FlyBot receiver.

Run on the receiver with server.ts as the only argument, then restart its existing
launch daemon. No tokens, recipient changes or grants are introduced.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
start = text.index('  const wakeText = buildPipelineAlertText({ detailBits, selftest: isSelftest });')
end = text.index('\n});', start)
replacement = '''  // PIPELINE_DIRECT_SIGNAL_V1: notification must not require a working LLM.
  // Keep the existing private Signal recipient, never accept one from the body.
  const args = buildPipelineAlertArgs('');
  const recipient = args[args.indexOf('--reply-to') + 1];
  const rpcUrl = 'http://127.0.0.1:8080/api/v1/rpc';
  try {
    // Authenticated, non-notifying readiness probe for scheduled preflight.
    if (body.probe === true) {
      const check = await fetch('http://127.0.0.1:8080/api/v1/check', {
        signal: AbortSignal.timeout(5000),
      });
      return c.json({ ok: check.ok, delivery: 'direct-signal' }, check.ok ? 200 : 503);
    }
    const message = `${isSelftest ? 'DRILL — ' : ''}AI news pipeline alert\\n${detailBits}`;
    const response = await fetch(rpcUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: crypto.randomUUID(), method: 'send',
        params: { recipient: [recipient], message } }),
      signal: AbortSignal.timeout(20000),
    });
    const sent: any = await response.json();
    const failures = sent.result?.results?.some((r: any) => r.error || (r.type && r.type !== 'SUCCESS'));
    if (!response.ok || sent.error || !sent.result?.timestamp || failures) {
      console.error('[ALERT] Direct Signal delivery failed', response.status, sent.error?.code);
      return c.json({ ok: false, delivery: 'failed' }, 502);
    }
    console.log(`[ALERT] Pipeline alert delivered to Signal: ${detailBits}`);
    return c.json({ ok: true, delivered: true, timestamp: sent.result.timestamp });
  } catch (error) {
    console.error('[ALERT] Direct Signal delivery unavailable', error instanceof Error ? error.name : 'unknown');
    return c.json({ ok: false, delivery: 'unavailable' }, 502);
  }'''
if 'PIPELINE_DIRECT_SIGNAL_V1' in text:
    raise SystemExit('Receiver already patched; no changes made')
backup = path.with_name(path.name + '.before-pipeline-alert-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.write_text(text)
path.write_text(text[:start] + replacement + text[end:])
print('Patched pipeline-only alert handler; backup:', backup)
