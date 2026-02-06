"""
Phase Tracker - Tracks pipeline phase status, timing, and provides end-of-run summary.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PhaseRecord:
    """Record of a single pipeline phase execution."""
    name: str
    status: str  # 'success', 'partial', 'failed', 'skipped'
    start_time: float = 0.0
    end_time: float = 0.0
    error: Optional[str] = None
    details: Optional[str] = None

    @property
    def duration(self) -> float:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return 0.0


class PhaseTracker:
    """Tracks phase-level status and timing for the pipeline."""

    def __init__(self):
        self.phases: List[PhaseRecord] = []
        self._current: Optional[PhaseRecord] = None
        self._pipeline_start: float = time.time()

    def start_phase(self, name: str):
        """Mark a phase as started."""
        self._current = PhaseRecord(name=name, status='running', start_time=time.time())

    def end_phase(self, status: str = 'success', error: Optional[str] = None, details: Optional[str] = None):
        """Mark the current phase as complete."""
        if self._current:
            self._current.status = status
            self._current.end_time = time.time()
            self._current.error = error
            self._current.details = details
            self.phases.append(self._current)
            self._current = None

    def skip_phase(self, name: str, reason: str):
        """Record a phase as skipped."""
        self.phases.append(PhaseRecord(
            name=name,
            status='skipped',
            details=reason
        ))

    def get_summary(self) -> str:
        """Format a terminal-friendly phase summary table."""
        lines = []
        lines.append("=" * 60)
        lines.append("PHASE STATUS SUMMARY")
        lines.append("=" * 60)

        total_duration = 0.0
        has_failures = False
        has_partial = False

        for phase in self.phases:
            duration = phase.duration
            total_duration += duration

            # Status icon
            if phase.status == 'success':
                icon = '[ok]'
            elif phase.status == 'partial':
                icon = '[!!]'
                has_partial = True
            elif phase.status == 'failed':
                icon = '[XX]'
                has_failures = True
            elif phase.status == 'skipped':
                icon = '[--]'
            else:
                icon = '[??]'

            # Format line
            name_part = f"  {icon} {phase.name}"

            if duration > 0:
                time_part = f"{duration:.1f}s"
            else:
                time_part = ""

            # Details/error suffix
            suffix = ""
            if phase.status == 'failed' and phase.error:
                suffix = f" FAILED: {phase.error}"
            elif phase.status == 'partial' and phase.details:
                suffix = f" ({phase.details})"
            elif phase.status == 'skipped' and phase.details:
                suffix = f" ({phase.details})"
            elif phase.details:
                suffix = f" ({phase.details})"

            # Align timing and suffix
            if time_part:
                padding = max(1, 50 - len(name_part))
                lines.append(f"{name_part}{' ' * padding}{time_part}{suffix}")
            else:
                lines.append(f"{name_part}{suffix}")

        # Total
        lines.append(f"{'':50s}--------")
        lines.append(f"  {'Total':<48s}{total_duration:.1f}s")
        lines.append("=" * 60)

        # Warning footer
        if has_failures:
            lines.append("WARNING: Some phases FAILED - check errors above")
        elif has_partial:
            lines.append("NOTE: Some phases had partial results - check details above")

        return "\n".join(lines)

    def to_dict(self) -> List[Dict[str, Any]]:
        """Serialize phase records for JSON output."""
        return [
            {
                'name': p.name,
                'status': p.status,
                'duration': round(p.duration, 2),
                'error': p.error,
                'details': p.details,
            }
            for p in self.phases
        ]

    @property
    def has_failures(self) -> bool:
        return any(p.status == 'failed' for p in self.phases)

    @property
    def has_partial(self) -> bool:
        return any(p.status == 'partial' for p in self.phases)
