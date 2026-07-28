#!/usr/bin/env python3
"""Re-attribute an existing replay index against the current taxonomy.

The cast was split so that each agent performs exactly one role (and therefore runs at
exactly one effort tier): the category analysts became a Reader plus an Editor, the
news pre-filter became Triage, and storyline curation left the Continuity desk. Days
already published carry the old ``agent_id`` on every call.

A full ``replay_generator`` run would fix this, but it needs ``data/processed/`` inputs
that only exist for locally-run days -- CI days are published straight from the runner.
Every index does carry each call's original ``caller`` tag, which is the taxonomy's
only input, so the mapping can be replayed in place.

This only ever rewrites attribution: it re-runs ``resolve_call`` over the recorded
callers and rebuilds the agent rollup from the result. Timings, tokens, costs, and
stream offsets are untouched -- nothing here can invent a measurement.

    python3 scripts/reattribute_replay.py 2026-07-28 [--web-dir web] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

import replay_taxonomy as tax  # noqa: E402


def rebuild(index: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    """Return the re-attributed index plus a human-readable list of changes."""
    notes: List[str] = []

    old_by_id = {a["id"]: a for a in index.get("agents", [])}
    rollup: Dict[str, Dict[str, Any]] = {}

    for call in index.get("calls", []):
        caller = call.get("caller") or ""
        # The hero call is synthesised by the generator and has no caller tag to
        # resolve; leave its attribution exactly as published.
        if call.get("role") == "image" or not caller:
            agent_id = call.get("agent_id")
            role = call.get("role")
        else:
            resolved = tax.resolve_call(caller)
            agent_id = resolved.agent_id
            role = resolved.role
            if agent_id != call.get("agent_id"):
                notes.append(f"  {caller} : {call.get('agent_id')} -> {agent_id}")
            call["agent_id"] = agent_id
            call["role"] = role

        entry = rollup.setdefault(
            agent_id,
            {"call_count": 0, "cost_usd": 0.0, "output_tokens": 0, "phase_ids": [], "failed": 0},
        )
        entry["call_count"] += 1
        entry["cost_usd"] += call.get("cost_usd", 0.0)
        entry["output_tokens"] += call.get("output_tokens", 0)
        pid = call.get("phase_id")
        if pid and pid not in entry["phase_ids"]:
            entry["phase_ids"].append(pid)
        if call.get("outcome") in ("failed", "refused"):
            entry["failed"] += 1

    # Sources belong to gatherers, which make no LLM calls; carry them across so the
    # scouts keep their lanes.
    collected: Dict[str, int] = {}
    for src in index.get("sources", []):
        collected[src["agent_id"]] = collected.get(src["agent_id"], 0) + src.get("items", 0)

    ordered = [a for a in tax.agent_ids() if a in rollup or a in collected]
    ordered += [a for a in rollup if a not in ordered]

    agents: List[Dict[str, Any]] = []
    for agent_id in ordered:
        identity = tax.agent_for(agent_id)
        entry = rollup.get(agent_id, {})
        previous = old_by_id.get(agent_id, {})
        items_in = collected.get(agent_id)
        failed = entry.get("failed", 0)
        count = entry.get("call_count", 0)
        agents.append(
            {
                "id": identity.id,
                "label": identity.label,
                "kind": identity.kind,
                "category": identity.category,
                "role": identity.role,
                "effort": identity.effort,
                "phase_ids": entry.get("phase_ids", []),
                "call_count": count,
                "cost_usd": round(entry.get("cost_usd", 0.0), 6),
                "output_tokens": entry.get("output_tokens", 0),
                "items_in": items_in,
                # Only the gatherers report an item count, and it is unchanged by
                # re-attribution, so preserve whatever the original run measured.
                "items_out": previous.get("items_out"),
                "status": (
                    "success" if count and not failed else "partial" if count else "idle"
                ),
                "blurb": identity.blurb,
            }
        )

    index["agents"] = agents
    return index, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date")
    ap.add_argument("--web-dir", default="web")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.web_dir) / "data" / args.date / "replay-index.json"
    if not path.exists():
        print(f"missing {path}", file=sys.stderr)
        return 1

    index = json.loads(path.read_text())
    before = {a["id"]: a["call_count"] for a in index.get("agents", [])}
    index, notes = rebuild(index)
    after = {a["id"]: a["call_count"] for a in index["agents"]}

    total_before = sum(before.values())
    total_after = sum(after.values())
    if total_before != total_after:
        print(f"REFUSING: call count changed {total_before} -> {total_after}", file=sys.stderr)
        return 2

    print(f"{args.date}: {len(before)} agents -> {len(after)} agents, {total_after} calls intact")
    for agent in index["agents"]:
        if agent["call_count"]:
            print(
                f"  {agent['label']:22s} {agent['call_count']:3d} calls  "
                f"role={agent['role']}  effort={agent['effort']}"
            )

    if args.dry_run:
        print("\n(dry run, nothing written)")
        return 0

    path.write_text(json.dumps(index, separators=(",", ":")))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
