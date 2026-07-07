#!/usr/bin/env python3
"""
A/B output-quality gate for prompt-structure changes (CWE-1427 stage 2).

Compares a regenerated day's outputs (working tree) against the committed
baseline for the same date (a git ref, default `main`). Used to verify that
the channel-separation/nonce-fencing prompt changes do not regress output
quality before they land.

Intended workflow (same inputs, only the analysis prompts differ):

    # on the security/wave-4 branch, with checkpoints for <date> present
    export TARGET_DATE=<date>
    python3 run_pipeline.py --resume-from 2 --config-dir ./config \
        --data-dir ./data --web-dir ./web
    python3 scripts/compare_outputs.py <date>            # baseline = main

Thresholds are deliberately loose: analysis runs at temperature 1.0, so two
runs of the SAME code do not produce identical rankings. The gate exists to
catch structural regressions (empty summaries, collapsed rankings, mass score
shifts), while summary equivalence is judged by reading the printed
side-by-side text.

CAVEAT (learned 2026-07-07): the item-loss metric assumes the local gathering
checkpoint matches the gathering that produced the baseline outputs. If the
baseline came from a different run (e.g. CI at 3 AM), its link-follower may
have captured articles the local checkpoint lacks — a "lost items" failure
must be input-verified (is the lost id present in
data/checkpoints/<date>/gathering.json at all?) before being attributed to a
prompt change.
"""

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

CATEGORIES = ["news", "research", "social", "reddit"]

# Gate thresholds (see module docstring for why they are loose)
MIN_TOP10_OVERLAP = 5        # of 10, per category
MAX_MEAN_SCORE_DELTA = 12.0  # mean |importance_score delta| per category
MIN_EXEC_SECTIONS = 2        # of the templated #### sections
MAX_ITEM_LOSS_PCT = 5.0      # analyzed items missing vs baseline


def load_baseline(ref: str, date: str, filename: str):
    """Load a baseline JSON file from a git ref."""
    path = f"web/data/{date}/{filename}"
    try:
        raw = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True, check=True,
        ).stdout
        return json.loads(raw)
    except subprocess.CalledProcessError:
        return None


def load_candidate(web_dir: str, date: str, filename: str):
    path = Path(web_dir) / "data" / date / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def bold_entities(text: str):
    """Entities the summary bolds -- a cheap proxy for 'who is covered'."""
    return {m.strip().lower() for m in re.findall(r"\*\*([^*]+)\*\*", text or "") if m.strip()}


def summarize_text(label: str, text: str):
    print(f"\n----- {label} " + "-" * max(0, 60 - len(label)))
    print((text or "(empty)").strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="Report date YYYY-MM-DD")
    parser.add_argument("--baseline-ref", default="main", help="Git ref holding the baseline outputs")
    parser.add_argument("--web-dir", default="web", help="Directory holding the candidate outputs")
    parser.add_argument("--show-summaries", action="store_true", default=True)
    args = parser.parse_args()

    failures, warnings = [], []

    base_summary = load_baseline(args.baseline_ref, args.date, "summary.json")
    cand_summary = load_candidate(args.web_dir, args.date, "summary.json")
    if not base_summary:
        print(f"ERROR: no baseline summary.json at {args.baseline_ref}:web/data/{args.date}/")
        return 2
    if not cand_summary:
        print(f"ERROR: no candidate summary.json in {args.web_dir}/data/{args.date}/ (run the pipeline first)")
        return 2

    print(f"A/B gate: {args.date}  baseline={args.baseline_ref}  candidate={args.web_dir}/data/")
    print("=" * 74)

    # --- Per-category comparison --------------------------------------------
    for cat in CATEGORIES:
        base_cat = load_baseline(args.baseline_ref, args.date, f"{cat}.json") or {}
        cand_cat = load_candidate(args.web_dir, args.date, f"{cat}.json") or {}
        base_items = {i["id"]: i for i in base_cat.get("items", [])}
        cand_items = {i["id"]: i for i in cand_cat.get("items", [])}

        base_top = [i["id"] for i in (base_summary.get("categories", {}).get(cat, {}) or {}).get("top_items", [])]
        cand_top = [i["id"] for i in (cand_summary.get("categories", {}).get(cat, {}) or {}).get("top_items", [])]
        overlap = len(set(base_top) & set(cand_top))

        shared = set(base_items) & set(cand_items)
        deltas = [
            abs(float(cand_items[i].get("importance_score", 50)) - float(base_items[i].get("importance_score", 50)))
            for i in shared
        ]
        mean_delta = statistics.mean(deltas) if deltas else 0.0
        big_moves = sum(1 for d in deltas if d > 20)

        lost = len(set(base_items) - set(cand_items))
        loss_pct = 100.0 * lost / len(base_items) if base_items else 0.0

        cand_cat_summary = cand_cat.get("category_summary", "")
        base_ents = bold_entities(base_cat.get("category_summary", ""))
        cand_ents = bold_entities(cand_cat_summary)
        ent_overlap = len(base_ents & cand_ents)

        print(f"\n[{cat}] items: base={len(base_items)} cand={len(cand_items)} lost={lost} ({loss_pct:.1f}%)")
        print(f"  top-10 overlap: {overlap}/{min(len(base_top), 10) or 10}"
              f"   score |delta|: mean={mean_delta:.1f} n>{20}: {big_moves}/{len(deltas)}")
        print(f"  summary bold-entity overlap: {ent_overlap}/{len(base_ents) or 1} "
              f"(base={len(base_ents)}, cand={len(cand_ents)})")

        if base_top and overlap < MIN_TOP10_OVERLAP:
            failures.append(f"{cat}: top-10 overlap {overlap} < {MIN_TOP10_OVERLAP}")
        if mean_delta > MAX_MEAN_SCORE_DELTA:
            failures.append(f"{cat}: mean score delta {mean_delta:.1f} > {MAX_MEAN_SCORE_DELTA}")
        if loss_pct > MAX_ITEM_LOSS_PCT:
            failures.append(f"{cat}: {loss_pct:.1f}% of baseline items missing")
        if base_cat.get("category_summary") and not cand_cat_summary.strip():
            failures.append(f"{cat}: candidate category_summary is empty")
        elif cand_cat_summary and len(cand_cat_summary) < 0.4 * len(base_cat.get("category_summary", "") or "x"):
            warnings.append(f"{cat}: candidate summary much shorter than baseline")

    # --- Executive summary ----------------------------------------------------
    base_exec = base_summary.get("executive_summary", "")
    cand_exec = cand_summary.get("executive_summary", "")
    cand_sections = len(re.findall(r"^####\s+", cand_exec, flags=re.M))
    ents_b, ents_c = bold_entities(base_exec), bold_entities(cand_exec)
    print(f"\n[executive] sections: base={len(re.findall(r'^####', base_exec, flags=re.M))} cand={cand_sections}"
          f"   bold-entity overlap: {len(ents_b & ents_c)}/{len(ents_b) or 1}"
          f"   length: base={len(base_exec)} cand={len(cand_exec)}")
    if not cand_exec.strip():
        failures.append("executive summary empty")
    elif cand_sections < MIN_EXEC_SECTIONS:
        failures.append(f"executive summary has {cand_sections} sections (< {MIN_EXEC_SECTIONS})")

    # --- Topics ----------------------------------------------------------------
    base_topics = [t.get("name", "") for t in base_summary.get("top_topics", [])]
    cand_topics = [t.get("name", "") for t in cand_summary.get("top_topics", [])]
    print(f"\n[topics] base ({len(base_topics)}): {base_topics}")
    print(f"[topics] cand ({len(cand_topics)}): {cand_topics}")
    if base_topics and not cand_topics:
        failures.append("candidate has no top_topics")

    # --- Human-readable side-by-side ------------------------------------------
    if args.show_summaries:
        summarize_text("EXECUTIVE (baseline)", base_exec)
        summarize_text("EXECUTIVE (candidate)", cand_exec)

    print("\n" + "=" * 74)
    for w in warnings:
        print(f"WARN: {w}")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        print("\nVERDICT: FAIL — do not land the prompt-structure change as-is.")
        return 1
    print("VERDICT: PASS (metrics within tolerance — also read the summaries above; "
          "qualitative equivalence is part of the gate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
