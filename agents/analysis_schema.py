"""
Bounded output schema for analyzer LLM responses (CWE-1427).

Analyzer batch/ranking responses are generated from untrusted third-party
content, so a prompt-injected response could otherwise carry oversized prose,
out-of-range scores, or unexpected keys straight into published JSON. These
models bound every field the pipeline republishes.

Design rule: CLAMP/REPAIR, never reject. A malformed field falls back to a
safe default and an over-long string is truncated, so one hostile (or merely
sloppy) field never drops a whole batch of items from the day's report --
coverage is part of output quality. Only entries that are structurally
unusable (not a dict, missing/blank id) are dropped, with a warning.
"""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# Generous ceilings: legitimate output sits far below these, so clamping only
# ever bites on runaway/injected prose. Tightening them risks real content.
MAX_ID_LEN = 64
MAX_SUMMARY_LEN = 2000
MAX_REASONING_LEN = 1000
MAX_ITEM_THEMES = 12
MAX_THEME_LABEL_LEN = 80
MAX_THEME_NAME_LEN = 120
MAX_THEME_DESC_LEN = 600
MAX_SIGNALS = 20
MAX_SIGNAL_LEN = 300
MAX_TOP_IDS = 15
MAX_CATEGORY_SUMMARY_LEN = 6000


def _coerce_str(value: Any, max_len: int) -> str:
    """Best-effort string coercion with truncation."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value[:max_len]


def _coerce_score(value: Any, default: float = 50.0) -> float:
    """Coerce to a float clamped into [0, 100]."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    if score != score:  # NaN
        return default
    return min(100.0, max(0.0, score))


class AnalyzedItemModel(BaseModel):
    """One per-item analysis entry from a map/combined batch response."""
    model_config = ConfigDict(extra='ignore')

    id: str
    summary: str = ""
    importance_score: float = 50.0
    reasoning: str = ""
    themes: List[str] = Field(default_factory=list)

    @field_validator('id', mode='before')
    @classmethod
    def _require_id(cls, value: Any) -> str:
        # The one hard requirement: merge is keyed on id, so an entry
        # without one is unusable and gets dropped by the sanitizer.
        if not isinstance(value, str) or not value.strip():
            raise ValueError("missing or non-string item id")
        return value.strip()[:MAX_ID_LEN]

    @field_validator('summary', mode='before')
    @classmethod
    def _clamp_summary(cls, value: Any) -> str:
        return _coerce_str(value, MAX_SUMMARY_LEN)

    @field_validator('importance_score', mode='before')
    @classmethod
    def _clamp_score(cls, value: Any) -> float:
        return _coerce_score(value)

    @field_validator('reasoning', mode='before')
    @classmethod
    def _clamp_reasoning(cls, value: Any) -> str:
        return _coerce_str(value, MAX_REASONING_LEN)

    @field_validator('themes', mode='before')
    @classmethod
    def _clamp_themes(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        labels = [
            _coerce_str(entry, MAX_THEME_LABEL_LEN)
            for entry in value
            if isinstance(entry, (str, int, float))
        ]
        return [label for label in labels if label][:MAX_ITEM_THEMES]


class ThemeModel(BaseModel):
    """One aggregated theme entry from a batch response."""
    model_config = ConfigDict(extra='ignore')

    name: str = ""
    description: str = ""
    item_count: int = 0
    importance: float = 50.0

    @field_validator('name', mode='before')
    @classmethod
    def _clamp_name(cls, value: Any) -> str:
        return _coerce_str(value, MAX_THEME_NAME_LEN)

    @field_validator('description', mode='before')
    @classmethod
    def _clamp_description(cls, value: Any) -> str:
        return _coerce_str(value, MAX_THEME_DESC_LEN)

    @field_validator('item_count', mode='before')
    @classmethod
    def _clamp_item_count(cls, value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @field_validator('importance', mode='before')
    @classmethod
    def _clamp_importance(cls, value: Any) -> float:
        return _coerce_score(value)


def _sanitize_top_ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    ids = [
        entry.strip()[:MAX_ID_LEN]
        for entry in value
        if isinstance(entry, str) and entry.strip()
    ]
    return ids[:MAX_TOP_IDS]


def _sanitize_signals(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    signals = [
        _coerce_str(entry, MAX_SIGNAL_LEN)
        for entry in value
        if isinstance(entry, (str, int, float))
    ]
    return [signal for signal in signals if signal][:MAX_SIGNALS]


def sanitize_batch_result(result: Any, where: str = "batch") -> Dict[str, Any]:
    """Clamp a parsed map/combined batch response to the expected schema.

    Returns a dict containing only the keys downstream code reads (items,
    themes/category_themes, cross_signals, and the combined-shape extras
    top_10/category_summary), each bounded. Never raises: on unexpected
    structural failure the original result is returned unchanged so a
    sanitizer bug can never drop a batch.
    """
    if not isinstance(result, dict):
        if result:
            logger.warning(f"{where}: non-dict analysis response ({type(result).__name__}); discarding")
        return {}
    try:
        clean: Dict[str, Any] = {}

        raw_items = result.get('items')
        if isinstance(raw_items, list):
            items = []
            dropped = 0
            for entry in raw_items:
                try:
                    items.append(AnalyzedItemModel.model_validate(entry).model_dump())
                except Exception:
                    dropped += 1
            if dropped:
                logger.warning(f"{where}: dropped {dropped} unusable item entrie(s) (no id / not an object)")
            clean['items'] = items
        elif 'items' in result:
            logger.warning(f"{where}: 'items' is not a list; treating as empty")
            clean['items'] = []

        # Preserve whichever theme key the model used; callers check both.
        for key in ('themes', 'category_themes'):
            if key in result:
                raw_themes = result[key]
                if isinstance(raw_themes, list):
                    clean[key] = [
                        ThemeModel.model_validate(entry).model_dump()
                        for entry in raw_themes
                        if isinstance(entry, dict)
                    ]
                else:
                    clean[key] = []

        if 'cross_signals' in result:
            clean['cross_signals'] = _sanitize_signals(result['cross_signals'])

        if 'top_10' in result:
            clean['top_10'] = _sanitize_top_ids(result['top_10'])

        if 'category_summary' in result:
            clean['category_summary'] = _coerce_str(
                result['category_summary'], MAX_CATEGORY_SUMMARY_LEN
            )

        return clean
    except Exception as exc:  # pragma: no cover - safety net
        logger.error(f"{where}: analysis sanitizer failed ({exc}); using unsanitized result")
        return result


def sanitize_ranking_result(result: Any, where: str = "ranking") -> Dict[str, Any]:
    """Clamp a parsed reduce/ranking response (top_10 + category_summary)."""
    if not isinstance(result, dict):
        if result:
            logger.warning(f"{where}: non-dict ranking response ({type(result).__name__}); discarding")
        return {}
    try:
        clean: Dict[str, Any] = {}
        if 'top_10' in result:
            clean['top_10'] = _sanitize_top_ids(result['top_10'])
        if 'category_summary' in result:
            clean['category_summary'] = _coerce_str(
                result['category_summary'], MAX_CATEGORY_SUMMARY_LEN
            )
        return clean
    except Exception as exc:  # pragma: no cover - safety net
        logger.error(f"{where}: ranking sanitizer failed ({exc}); using unsanitized result")
        return result
