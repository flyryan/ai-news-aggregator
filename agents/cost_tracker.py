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
from typing import Any, Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ModelPricing(Enum):
    """Pricing per million tokens (MTok) for different models."""

    # Claude Opus pricing (USD per million tokens) — identical across 4.6, 4.8, and Opus 5
    OPUS_4_6_INPUT = 5.00
    OPUS_4_6_OUTPUT = 25.00
    OPUS_4_6_CACHE_WRITE_5MIN = 6.25
    OPUS_4_6_CACHE_WRITE_1HR = 10.00
    OPUS_4_6_CACHE_HIT = 0.50

    # Claude Sonnet 4.5 pricing
    SONNET_4_5_INPUT = 3.00
    SONNET_4_5_OUTPUT = 15.00

    # Claude Haiku 4.5 pricing
    HAIKU_4_5_INPUT = 1.00
    HAIKU_4_5_OUTPUT = 5.00

    # Gemini 3 Pro Image (Nano Banana Pro) — the hero illustrator.
    #
    # Three DIFFERENT rates apply to one response, which is why image cost cannot be
    # computed the way an LLM call is. `usage.completion_tokens` bundles the image
    # tokens with the model's own thinking tokens, and pricing the whole bundle at
    # the image rate overstates a real call by ~22%.
    #
    #   input            $2/M    (text prompt, and 560 tok per reference image)
    #   image output   $120/M    (1120 tok at 1K/2K => $0.134, 2000 tok at 4K => $0.24)
    #   text/thinking   $12/M    (reasoning_tokens + text_tokens)
    #
    # Verified 2026-07-28 against a live generation: 1120 image tokens priced to
    # $0.1344, matching Google's published $0.134 per 1K/2K image.
    GEMINI_3_PRO_IMAGE_INPUT = 2.00
    GEMINI_3_PRO_IMAGE_OUTPUT_IMAGE = 120.00
    GEMINI_3_PRO_IMAGE_OUTPUT_TEXT = 12.00


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
    model: str = "claude-5-opus-aws"
    provider_id: Optional[str] = None
    analysis_profile: Optional[str] = None
    adaptive_effort: Optional[str] = None
    duration_seconds: float = 0.0
    # True when the call failed mid-stream and these counts were read off the SSE
    # events rather than a final response. The provider still billed for them, so
    # they belong in the total -- but they are a floor, not an exact figure: any
    # tokens emitted after the last message_delta are unaccounted for.
    partial: bool = False

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


@dataclass
class ImageCost:
    """Cost of one image generation, split by the rate each token class is billed at."""

    input_tokens: int = 0
    image_tokens: int = 0
    text_tokens: int = 0
    input_cost: float = 0.0
    image_cost: float = 0.0
    text_cost: float = 0.0
    # Authoritative total reported by the provider itself (OpenRouter's image
    # endpoint includes ``usage.cost``). When present it wins over any local
    # token-class arithmetic -- the provider knows its own bill.
    provider_reported_cost: Optional[float] = None

    @property
    def total_cost(self) -> float:
        if self.provider_reported_cost is not None:
            return self.provider_reported_cost
        return self.input_cost + self.image_cost + self.text_cost

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.image_tokens + self.text_tokens


def price_image_usage(usage: Optional[Dict[str, Any]]) -> Optional[ImageCost]:
    """Price a Gemini image response from its ``usage`` block.

    Returns None when the provider reported nothing, which is the signal to render
    "n/a" rather than a zero -- a zero would claim we measured a free call.

    The split matters. ``completion_tokens`` bundles image tokens (billed at $120/M)
    with the model's thinking tokens (billed at $12/M), so charging the bundle at the
    image rate overstates a real call by roughly a fifth. When the provider gives us
    the breakdown we use it; when it only gives a total we fall back to treating the
    completion as image tokens, which is the conservative (higher) reading and is
    flagged by ``text_tokens == 0``.
    """
    if not usage:
        return None

    def _int(value: Any) -> int:
        return int(value) if isinstance(value, (int, float)) else 0

    completion_details = usage.get("completion_tokens_details") or {}
    input_tokens = _int(usage.get("prompt_tokens"))
    completion_total = _int(usage.get("completion_tokens"))

    image_tokens = _int(completion_details.get("image_tokens"))
    text_tokens = _int(completion_details.get("reasoning_tokens")) + _int(
        completion_details.get("text_tokens")
    )
    if not image_tokens and not text_tokens:
        image_tokens = completion_total

    # Provider-reported totals are authoritative (OpenRouter includes
    # usage.cost); token-class rates are only our own estimate of a bill the
    # provider already itemized for us.
    reported_cost = usage.get("cost")
    if isinstance(reported_cost, (int, float)):
        return ImageCost(
            input_tokens=input_tokens,
            image_tokens=image_tokens,
            text_tokens=text_tokens,
            provider_reported_cost=float(reported_cost),
        )

    mtok = 1_000_000
    return ImageCost(
        input_tokens=input_tokens,
        image_tokens=image_tokens,
        text_tokens=text_tokens,
        input_cost=(input_tokens / mtok) * ModelPricing.GEMINI_3_PRO_IMAGE_INPUT.value,
        image_cost=(image_tokens / mtok) * ModelPricing.GEMINI_3_PRO_IMAGE_OUTPUT_IMAGE.value,
        text_cost=(text_tokens / mtok) * ModelPricing.GEMINI_3_PRO_IMAGE_OUTPUT_TEXT.value,
    )


class CostTracker:
    """
    Tracks API usage and calculates costs for pipeline runs.

    Usage:
        tracker = CostTracker()
        tracker.record_call("news_analyzer.filter", usage_dict, "QUICK")
        tracker.record_call("news_analyzer.analyze", usage_dict, "DEEP")
        print(tracker.get_summary())
    """

    def __init__(self, model: str = "claude-5-opus-aws"):
        self.model = model
        self.calls: List[APICallRecord] = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        # Non-LLM/third-party API usage (e.g. ScrapeCreators, TwitterAPI.io), keyed by
        # provider name. Each value is a flat dict of metrics (calls, credits_consumed,
        # balance, balance_usd, est_cost_usd, items, note).
        self.external_apis: Dict[str, Dict] = {}

        # Whether the rates below are a real schedule (False) or an
        # unknown-model fallback standing in for one (True). Surfaced in the
        # run summary so an estimate can never masquerade as a measurement.
        self.pricing_is_estimate = False

        # Determine pricing based on model
        if "muse-spark" in model.lower() and "contributor" in model.lower():
            # meta/muse-spark-1.3-contributor -- Meta's data-sharing
            # ("contributor") tier of Muse Spark, listed on OpenRouter
            # 2026-09-02. Verified live against
            # /api/v1/models/meta/muse-spark-1.3-contributor-20260902/endpoints
            # on 2026-09-03: $0.10/MTok prompt, $0.20/MTok completion,
            # $0.002/MTok cache read. One Meta-served endpoint, so this single
            # row IS the whole schedule (unlike GLM's 13 endpoints at two
            # prices). No cache-write premium is published, so writes bill at
            # the prompt rate. Reasoning is mandatory and billed as completion
            # tokens.
            self.input_price = 0.10
            self.output_price = 0.20
            self.cache_write_price = 0.10
            self.cache_hit_price = 0.002
        elif "muse-spark" in model.lower():
            # meta/muse-spark-1.x standard tier, same endpoint shape:
            # $1.25/MTok prompt, $4.25/MTok completion, $0.15/MTok cache read
            # (verified 2026-09-03 for 1.3; 1.1 and 1.2 list identical rates).
            self.input_price = 1.25
            self.output_price = 4.25
            self.cache_write_price = 1.25
            self.cache_hit_price = 0.15
        elif "glm-5.3-flash" in model.lower():
            # z-ai/GLM-5.3-Flash -- ox-alpha's real identity after the reveal.
            # Verified live against /api/v1/models/z-ai/glm-5.3-flash/endpoints
            # on 2026-08-27: $0.075/MTok prompt, $0.25/MTok completion,
            # $0.015/MTok cache read. No cache-write premium is published, so
            # writes bill at the prompt rate.
            self.input_price = 0.075
            self.output_price = 0.25
            self.cache_write_price = 0.075
            self.cache_hit_price = 0.015
        elif "ox-alpha" in model.lower():
            # stealth/ox-alpha was $0/$0 on OpenRouter while listed (verified
            # live on 2026-08-22). Delisted 2026-08-27 when revealed as
            # GLM-5.3-Flash; kept at zero so offline regeneration of runs from
            # the stealth period reports what they actually cost.
            self.input_price = 0.0
            self.output_price = 0.0
            self.cache_write_price = 0.0
            self.cache_hit_price = 0.0
        elif "opus" in model.lower():
            self.input_price = ModelPricing.OPUS_4_6_INPUT.value
            self.output_price = ModelPricing.OPUS_4_6_OUTPUT.value
            self.cache_write_price = ModelPricing.OPUS_4_6_CACHE_WRITE_5MIN.value
            self.cache_hit_price = ModelPricing.OPUS_4_6_CACHE_HIT.value
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
            # Unknown model: fall back to Opus rates as a worst-case estimate,
            # but say so loudly -- the 2026-08-22 run silently priced free
            # ox-alpha traffic at $11.63 this way.
            self.pricing_is_estimate = True
            logger.warning(
                "No pricing entry for model '%s' - cost report uses Opus 5 "
                "rates as a worst-case ESTIMATE, not a measurement", model,
            )
            self.input_price = ModelPricing.OPUS_4_6_INPUT.value
            self.output_price = ModelPricing.OPUS_4_6_OUTPUT.value
            self.cache_write_price = ModelPricing.OPUS_4_6_CACHE_WRITE_5MIN.value
            self.cache_hit_price = ModelPricing.OPUS_4_6_CACHE_HIT.value

    def start(self):
        """Mark the start of a pipeline run."""
        self.start_time = datetime.now()
        self.calls = []
        self.external_apis = {}
        logger.info("Cost tracking started")

    def record_external_api(self, name: str, **metrics):
        """
        Record (or merge) usage for a non-LLM third-party API so it shows up in the
        run summary. Pass any of: calls, items, credits_consumed, balance, balance_usd,
        est_cost_usd, note. Repeated calls for the same name merge their keys.
        """
        existing = self.external_apis.get(name, {})
        existing.update({k: v for k, v in metrics.items() if v is not None})
        self.external_apis[name] = existing
        logger.debug(f"Recorded external API usage: {name} -> {existing}")

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
        model: Optional[str] = None,
        provider_id: Optional[str] = None,
        analysis_profile: Optional[str] = None,
        adaptive_effort: Optional[str] = None,
        partial: bool = False
    ):
        """
        Record an API call.

        Args:
            caller: Identifier for the component making the call
            usage: Usage dict from API response with input_tokens, output_tokens, etc.
            thinking_level: ThinkingLevel used (QUICK, STANDARD, DEEP, ULTRATHINK)
            duration_seconds: How long the call took
            model: Model used (if different from default)
            partial: True when the call failed mid-stream and these counts came
                from SSE events rather than a final response. Still billed, so
                still counted -- but a floor rather than an exact figure.
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
            provider_id=provider_id,
            analysis_profile=analysis_profile,
            adaptive_effort=adaptive_effort,
            duration_seconds=duration_seconds,
            partial=partial
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

    def get_cost_by_provider(self) -> Dict[str, CostBreakdown]:
        """Get cost breakdown by routed provider id."""
        by_provider: Dict[str, CostBreakdown] = {}

        for call in self.calls:
            provider = call.provider_id or call.model
            if provider not in by_provider:
                by_provider[provider] = CostBreakdown()

            cost = self.calculate_cost(call)
            by_provider[provider].input_cost += cost.input_cost
            by_provider[provider].output_cost += cost.output_cost
            by_provider[provider].cache_write_cost += cost.cache_write_cost
            by_provider[provider].cache_hit_cost += cost.cache_hit_cost

        return by_provider

    def get_summary(self) -> str:
        """Get a formatted summary of usage and costs."""
        totals = self.get_totals()
        cost = self.get_total_cost()
        by_caller = self.get_cost_by_caller()
        by_provider = self.get_cost_by_provider()

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

        if len(by_provider) > 1:
            lines.extend([
                "",
                "COST BY PROVIDER:",
            ])
            sorted_providers = sorted(
                by_provider.items(),
                key=lambda x: x[1].total_cost,
                reverse=True
            )
            for provider, provider_cost in sorted_providers:
                lines.append(f"  {provider:30s} ${provider_cost.total_cost:.4f}")

        if self.external_apis:
            lines.extend(["", "EXTERNAL API USAGE (non-LLM):"])
            for name, info in self.external_apis.items():
                parts: List[str] = []
                if info.get('calls') is not None:
                    parts.append(f"calls={info['calls']:,}")
                if info.get('items') is not None:
                    parts.append(f"items={info['items']:,}")
                if info.get('credits_consumed') is not None:
                    parts.append(f"credits used={info['credits_consumed']:,}")
                if info.get('balance') is not None:
                    bal = f"balance={info['balance']:,}"
                    if info.get('balance_usd') is not None:
                        bal += f" (${info['balance_usd']:.2f})"
                    parts.append(bal)
                if info.get('est_cost_usd') is not None:
                    parts.append(f"est cost=${info['est_cost_usd']:.4f}")
                if info.get('note'):
                    parts.append(str(info['note']))
                lines.append(f"  {name}: " + "  |  ".join(parts) if parts else f"  {name}")

        lines.extend([
            "",
            f"PRICING ({self.model}):",
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
        by_provider = self.get_cost_by_provider()

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
            "cost_by_provider": {
                provider: round(c.total_cost, 6)
                for provider, c in by_provider.items()
            },
            "external_apis": self.external_apis,
            "calls": [
                {
                    "timestamp": call.timestamp,
                    "caller": call.caller,
                    "thinking_level": call.thinking_level,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cache_creation_tokens": call.cache_creation_tokens,
                    "cache_read_tokens": call.cache_read_tokens,
                    "model": call.model,
                    "provider_id": call.provider_id,
                    "analysis_profile": call.analysis_profile,
                    "adaptive_effort": call.adaptive_effort,
                    "duration_seconds": call.duration_seconds,
                    "partial": call.partial
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


def reset_tracker(model: str = "claude-5-opus-aws") -> CostTracker:
    """Reset and return a new global tracker."""
    global _global_tracker
    _global_tracker = CostTracker(model)
    return _global_tracker
