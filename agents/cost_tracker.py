"""
Cost Tracker for LLM API Usage

Tracks token usage and calculates costs based on Anthropic pricing.
Provides detailed statistics for pipeline runs.

Pricing source: https://platform.claude.com/docs/en/about-claude/pricing
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ModelPricing(Enum):
    """Pricing per million tokens (MTok) for different models."""

    # Claude Opus 4.5 pricing (USD per million tokens)
    OPUS_4_5_INPUT = 5.00
    OPUS_4_5_OUTPUT = 25.00
    OPUS_4_5_CACHE_WRITE_5MIN = 6.25
    OPUS_4_5_CACHE_WRITE_1HR = 10.00
    OPUS_4_5_CACHE_HIT = 0.50

    # Claude Sonnet 4.5 pricing
    SONNET_4_5_INPUT = 3.00
    SONNET_4_5_OUTPUT = 15.00

    # Claude Haiku 4.5 pricing
    HAIKU_4_5_INPUT = 1.00
    HAIKU_4_5_OUTPUT = 5.00


@dataclass
class APICallRecord:
    """Record of a single API call."""
    timestamp: str
    caller: str  # Which component made the call (e.g., "news_analyzer.filter")
    thinking_level: Optional[str]
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = "claude-4.5-opus-aws"
    duration_seconds: float = 0.0

    @property
    def total_input_tokens(self) -> int:
        """Total input tokens including cache operations."""
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens

    @property
    def total_tokens(self) -> int:
        """Total tokens (input + output)."""
        return self.total_input_tokens + self.output_tokens


@dataclass
class CostBreakdown:
    """Detailed cost breakdown."""
    input_cost: float = 0.0
    output_cost: float = 0.0
    cache_write_cost: float = 0.0
    cache_hit_cost: float = 0.0

    @property
    def total_cost(self) -> float:
        return self.input_cost + self.output_cost + self.cache_write_cost + self.cache_hit_cost


class CostTracker:
    """
    Tracks API usage and calculates costs for pipeline runs.

    Usage:
        tracker = CostTracker()
        tracker.record_call("news_analyzer.filter", usage_dict, "QUICK")
        tracker.record_call("news_analyzer.analyze", usage_dict, "DEEP")
        print(tracker.get_summary())
    """

    def __init__(self, model: str = "claude-4.5-opus-aws"):
        self.model = model
        self.calls: List[APICallRecord] = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

        # Determine pricing based on model
        if "opus" in model.lower():
            self.input_price = ModelPricing.OPUS_4_5_INPUT.value
            self.output_price = ModelPricing.OPUS_4_5_OUTPUT.value
            self.cache_write_price = ModelPricing.OPUS_4_5_CACHE_WRITE_5MIN.value
            self.cache_hit_price = ModelPricing.OPUS_4_5_CACHE_HIT.value
        elif "sonnet" in model.lower():
            self.input_price = ModelPricing.SONNET_4_5_INPUT.value
            self.output_price = ModelPricing.SONNET_4_5_OUTPUT.value
            self.cache_write_price = self.input_price * 1.25
            self.cache_hit_price = self.input_price * 0.1
        elif "haiku" in model.lower():
            self.input_price = ModelPricing.HAIKU_4_5_INPUT.value
            self.output_price = ModelPricing.HAIKU_4_5_OUTPUT.value
            self.cache_write_price = self.input_price * 1.25
            self.cache_hit_price = self.input_price * 0.1
        else:
            # Default to Opus pricing
            self.input_price = ModelPricing.OPUS_4_5_INPUT.value
            self.output_price = ModelPricing.OPUS_4_5_OUTPUT.value
            self.cache_write_price = ModelPricing.OPUS_4_5_CACHE_WRITE_5MIN.value
            self.cache_hit_price = ModelPricing.OPUS_4_5_CACHE_HIT.value

    def start(self):
        """Mark the start of a pipeline run."""
        self.start_time = datetime.now()
        self.calls = []
        logger.info("Cost tracking started")

    def stop(self):
        """Mark the end of a pipeline run."""
        self.end_time = datetime.now()
        logger.info(f"Cost tracking stopped. Total calls: {len(self.calls)}")

    def record_call(
        self,
        caller: str,
        usage: Dict[str, int],
        thinking_level: Optional[str] = None,
        duration_seconds: float = 0.0,
        model: Optional[str] = None
    ):
        """
        Record an API call.

        Args:
            caller: Identifier for the component making the call
            usage: Usage dict from API response with input_tokens, output_tokens, etc.
            thinking_level: ThinkingLevel used (QUICK, STANDARD, DEEP, ULTRATHINK)
            duration_seconds: How long the call took
            model: Model used (if different from default)
        """
        record = APICallRecord(
            timestamp=datetime.now().isoformat(),
            caller=caller,
            thinking_level=thinking_level,
            input_tokens=usage.get('input_tokens', 0),
            output_tokens=usage.get('output_tokens', 0),
            cache_creation_tokens=usage.get('cache_creation_input_tokens', 0),
            cache_read_tokens=usage.get('cache_read_input_tokens', 0),
            model=model or self.model,
            duration_seconds=duration_seconds
        )
        self.calls.append(record)

        logger.debug(
            f"Recorded call: {caller} - "
            f"in={record.input_tokens}, out={record.output_tokens}, "
            f"cache_write={record.cache_creation_tokens}, cache_read={record.cache_read_tokens}"
        )

    def calculate_cost(self, record: APICallRecord) -> CostBreakdown:
        """Calculate cost for a single API call."""
        # Costs are per million tokens
        mtok = 1_000_000

        return CostBreakdown(
            input_cost=(record.input_tokens / mtok) * self.input_price,
            output_cost=(record.output_tokens / mtok) * self.output_price,
            cache_write_cost=(record.cache_creation_tokens / mtok) * self.cache_write_price,
            cache_hit_cost=(record.cache_read_tokens / mtok) * self.cache_hit_price
        )

    def get_totals(self) -> Dict[str, int]:
        """Get total token counts."""
        totals = {
            'input_tokens': 0,
            'output_tokens': 0,
            'cache_creation_tokens': 0,
            'cache_read_tokens': 0,
            'total_tokens': 0
        }

        for call in self.calls:
            totals['input_tokens'] += call.input_tokens
            totals['output_tokens'] += call.output_tokens
            totals['cache_creation_tokens'] += call.cache_creation_tokens
            totals['cache_read_tokens'] += call.cache_read_tokens
            totals['total_tokens'] += call.total_tokens

        return totals

    def get_total_cost(self) -> CostBreakdown:
        """Get total cost breakdown."""
        total = CostBreakdown()

        for call in self.calls:
            cost = self.calculate_cost(call)
            total.input_cost += cost.input_cost
            total.output_cost += cost.output_cost
            total.cache_write_cost += cost.cache_write_cost
            total.cache_hit_cost += cost.cache_hit_cost

        return total

    def get_cost_by_caller(self) -> Dict[str, CostBreakdown]:
        """Get cost breakdown by caller/component."""
        by_caller: Dict[str, CostBreakdown] = {}

        for call in self.calls:
            if call.caller not in by_caller:
                by_caller[call.caller] = CostBreakdown()

            cost = self.calculate_cost(call)
            by_caller[call.caller].input_cost += cost.input_cost
            by_caller[call.caller].output_cost += cost.output_cost
            by_caller[call.caller].cache_write_cost += cost.cache_write_cost
            by_caller[call.caller].cache_hit_cost += cost.cache_hit_cost

        return by_caller

    def get_summary(self) -> str:
        """Get a formatted summary of usage and costs."""
        totals = self.get_totals()
        cost = self.get_total_cost()
        by_caller = self.get_cost_by_caller()

        duration = ""
        if self.start_time and self.end_time:
            elapsed = (self.end_time - self.start_time).total_seconds()
            duration = f"\nTotal Duration: {elapsed:.1f}s"

        lines = [
            "=" * 60,
            "📊 PIPELINE COST REPORT",
            "=" * 60,
            f"Model: {self.model}",
            f"API Calls: {len(self.calls)}{duration}",
            "",
            "TOKEN USAGE:",
            f"  Input tokens:        {totals['input_tokens']:>12,}",
            f"  Output tokens:       {totals['output_tokens']:>12,}",
            f"  Cache write tokens:  {totals['cache_creation_tokens']:>12,}",
            f"  Cache read tokens:   {totals['cache_read_tokens']:>12,}",
            f"  ─────────────────────────────────",
            f"  Total tokens:        {totals['total_tokens']:>12,}",
            "",
            "COST BREAKDOWN:",
            f"  Input cost:          ${cost.input_cost:>10.4f}",
            f"  Output cost:         ${cost.output_cost:>10.4f}",
            f"  Cache write cost:    ${cost.cache_write_cost:>10.4f}",
            f"  Cache hit savings:   ${cost.cache_hit_cost:>10.4f}",
            f"  ─────────────────────────────────",
            f"  TOTAL COST:          ${cost.total_cost:>10.4f}",
            "",
            "COST BY COMPONENT:",
        ]

        # Sort by cost descending
        sorted_callers = sorted(
            by_caller.items(),
            key=lambda x: x[1].total_cost,
            reverse=True
        )

        for caller, caller_cost in sorted_callers:
            lines.append(f"  {caller:30s} ${caller_cost.total_cost:.4f}")

        lines.extend([
            "",
            "PRICING (Claude Opus 4.5):",
            f"  Input:  ${self.input_price:.2f}/MTok",
            f"  Output: ${self.output_price:.2f}/MTok",
            "=" * 60
        ])

        return "\n".join(lines)

    def get_json_report(self) -> Dict:
        """Get a JSON-serializable report."""
        totals = self.get_totals()
        cost = self.get_total_cost()
        by_caller = self.get_cost_by_caller()

        return {
            "model": self.model,
            "api_calls": len(self.calls),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": (
                (self.end_time - self.start_time).total_seconds()
                if self.start_time and self.end_time else None
            ),
            "tokens": totals,
            "cost": {
                "input": round(cost.input_cost, 6),
                "output": round(cost.output_cost, 6),
                "cache_write": round(cost.cache_write_cost, 6),
                "cache_hit": round(cost.cache_hit_cost, 6),
                "total": round(cost.total_cost, 6)
            },
            "cost_by_component": {
                caller: round(c.total_cost, 6)
                for caller, c in by_caller.items()
            },
            "calls": [
                {
                    "timestamp": call.timestamp,
                    "caller": call.caller,
                    "thinking_level": call.thinking_level,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cache_creation_tokens": call.cache_creation_tokens,
                    "cache_read_tokens": call.cache_read_tokens,
                    "duration_seconds": call.duration_seconds
                }
                for call in self.calls
            ]
        }

    def save_report(self, filepath: str):
        """Save the JSON report to a file."""
        with open(filepath, 'w') as f:
            json.dump(self.get_json_report(), f, indent=2)
        logger.info(f"Cost report saved to {filepath}")


# Global tracker instance for easy access
_global_tracker: Optional[CostTracker] = None


def get_tracker() -> CostTracker:
    """Get the global cost tracker instance."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = CostTracker()
    return _global_tracker


def reset_tracker(model: str = "claude-4.5-opus-aws") -> CostTracker:
    """Reset and return a new global tracker."""
    global _global_tracker
    _global_tracker = CostTracker(model)
    return _global_tracker
