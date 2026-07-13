"""Cooperative deadline and cancellation boundaries for the daily pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


MonotonicClock = Callable[[], float]
CancellationProbe = Callable[[], bool]


class PipelineExecutionBoundary(RuntimeError):
    """A cooperative pipeline boundary stopped further work."""

    failure_class = "internal_error"

    def __init__(self, *, checkpoint: str, reason: str) -> None:
        super().__init__(reason)
        self.checkpoint = checkpoint
        self.reason = reason


class PipelineExecutionCancelled(PipelineExecutionBoundary):
    failure_class = "cancelled"


class PipelineDeadlineExceeded(PipelineExecutionBoundary):
    failure_class = "timeout"


@dataclass(frozen=True)
class PipelineExecutionContext:
    """One monotonic budget shared by every synchronous pipeline stage."""

    started_monotonic: float
    deadline_monotonic: float
    total_timeout_seconds: float
    monotonic_clock: MonotonicClock
    cancellation_requested: CancellationProbe

    @classmethod
    def start(
        cls,
        total_timeout_seconds: float,
        *,
        monotonic_clock: MonotonicClock,
        cancellation_requested: CancellationProbe | None = None,
    ) -> "PipelineExecutionContext":
        total = max(0.001, float(total_timeout_seconds))
        started = float(monotonic_clock())
        return cls(
            started_monotonic=started,
            deadline_monotonic=started + total,
            total_timeout_seconds=total,
            monotonic_clock=monotonic_clock,
            cancellation_requested=cancellation_requested or _never_cancelled,
        )

    def elapsed_seconds(self) -> float:
        return max(0.0, float(self.monotonic_clock()) - self.started_monotonic)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - float(self.monotonic_clock()))

    def checkpoint(self, checkpoint: str) -> None:
        self.remaining_at_checkpoint(checkpoint)

    def remaining_at_checkpoint(self, checkpoint: str) -> float:
        # A cancellation already requested at the same boundary takes priority
        # over timeout classification. Synchronous work is never claimed to be
        # interrupted; callers invoke this only before or after it returns.
        if bool(self.cancellation_requested()):
            raise PipelineExecutionCancelled(
                checkpoint=checkpoint,
                reason=f"cancelled at {checkpoint}",
            )
        now = float(self.monotonic_clock())
        if now >= self.deadline_monotonic:
            raise PipelineDeadlineExceeded(
                checkpoint=checkpoint,
                reason=f"timeout after {_format_seconds(self.total_timeout_seconds)}s",
            )
        return self.deadline_monotonic - now

    def bounded_timeout(self, maximum_seconds: float, *, checkpoint: str) -> float:
        remaining = self.remaining_at_checkpoint(checkpoint)
        return max(0.000001, min(float(maximum_seconds), remaining))


def _never_cancelled() -> bool:
    return False


def _format_seconds(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):.3f}".rstrip("0").rstrip(".")
