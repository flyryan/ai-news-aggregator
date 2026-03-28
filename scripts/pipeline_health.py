#!/usr/bin/env python3
"""Summarize the most recent AI news pipeline log in text or JSON."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

LOG_DIR = Path.home() / "ai-news-aggregator" / "logs"
ITEM_RE = re.compile(r"agents\.orchestrator - INFO -\s+([a-z]+) gatherer collected (\d+) items")
DATE_RE = re.compile(r"pipeline_(\d{4}-\d{2}-\d{2})\.log$")
WARNING_RE = re.compile(r" - (WARNING|ERROR|CRITICAL) - ")
LESSWRONG_RE = re.compile(r"lesswrong|vercel|429|security checkpoint|bot challenge", re.IGNORECASE)
TOTAL_RE = re.compile(r"Total items collected: (\d+)")
STATUS_RE = re.compile(r"\[(ok|fail)\] (.+)")


@dataclass
class HealthReport:
    date: str
    log_path: str
    counts: Dict[str, int]
    degraded_sources: List[str]
    errors: List[str]
    warnings: List[str]
    total_items_collected: Optional[int]
    pipeline_completed_successfully: bool

    def text_summary(self) -> str:
        lines = [f"Pipeline Health Report — {self.date}"]
        for category in ["news", "research", "social", "reddit"]:
            count = self.counts.get(category, 0)
            marker = "⚠️" if category == "research" and count == 0 else "✅"
            detail = ""
            if category == "research" and count == 0:
                detail = " (LessWrong GraphQL/Vercel challenge or upstream research source failure)"
            lines.append(f"{marker} {category.capitalize()}: {count} items{detail}")

        if self.degraded_sources:
            lines.append("Degraded sources:")
            lines.extend(f"- {item}" for item in self.degraded_sources)

        if self.errors:
            lines.append("Errors:")
            lines.extend(f"- {line}" for line in self.errors[:10])
        elif self.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {line}" for line in self.warnings[:10])

        if self.total_items_collected is not None:
            lines.append(f"Total items collected: {self.total_items_collected}")
        lines.append(
            "Pipeline status: completed successfully" if self.pipeline_completed_successfully else "Pipeline status: incomplete or failed"
        )
        return "\n".join(lines)


def find_latest_log(log_dir: Path) -> Path:
    logs = sorted(log_dir.glob("pipeline_*.log"))
    if not logs:
        raise FileNotFoundError(f"No pipeline logs found in {log_dir}")
    return logs[-1]


def parse_log(log_path: Path) -> HealthReport:
    text = log_path.read_text(errors="replace")
    counts: Dict[str, int] = {}
    degraded_sources: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []
    total_items_collected: Optional[int] = None

    for line in text.splitlines():
        item_match = ITEM_RE.search(line)
        if item_match:
            counts[item_match.group(1)] = int(item_match.group(2))

        if WARNING_RE.search(line):
            if " - ERROR - " in line or " - CRITICAL - " in line:
                errors.append(line.strip())
            else:
                warnings.append(line.strip())

        lowered = line.lower()
        if "research gatherer collected 0 items" in lowered:
            degraded_sources.append("Research gatherer returned 0 items")
        if LESSWRONG_RE.search(line):
            degraded_sources.append(line.strip())
        if "feed warning" in lowered:
            degraded_sources.append(line.strip())

        total_match = TOTAL_RE.search(line)
        if total_match:
            total_items_collected = int(total_match.group(1))

    m = DATE_RE.search(log_path.name)
    report_date = m.group(1) if m else "unknown"
    pipeline_completed_successfully = "PIPELINE COMPLETED SUCCESSFULLY" in text

    def dedupe_keep_order(items: List[str]) -> List[str]:
        seen = set()
        out = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return HealthReport(
        date=report_date,
        log_path=str(log_path),
        counts=counts,
        degraded_sources=dedupe_keep_order(degraded_sources),
        errors=dedupe_keep_order(errors),
        warnings=dedupe_keep_order(warnings),
        total_items_collected=total_items_collected,
        pipeline_completed_successfully=pipeline_completed_successfully,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize the most recent AI news pipeline log")
    parser.add_argument("--log", help="Specific log path to inspect")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    args = parser.parse_args()

    log_path = Path(args.log).expanduser() if args.log else find_latest_log(LOG_DIR)
    report = parse_log(log_path)

    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(report.text_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
