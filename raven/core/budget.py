"""Run budget (plan §13): tokens and wall-clock time for one whole run —
every plan step, replan and sub-agent call — measured from the gateway's
real usage. At 80% the executor nudges the model to converge; at 100% it
stops, and the verifier judges whatever the run achieved."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

WARN_AT = 0.8


@dataclass
class Budget:
    gateway: object  # LLMGateway: anything with .stats.total_tokens
    total_tokens: int | None = None
    wall_clock_s: float | None = None
    _start_tokens: int = field(init=False, default=0)
    _start_time: float = field(init=False, default=0.0)
    _warned: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._start_tokens = self.gateway.stats.total_tokens
        self._start_time = time.monotonic()

    @property
    def tokens_used(self) -> int:
        return self.gateway.stats.total_tokens - self._start_tokens

    @property
    def seconds_used(self) -> float:
        return time.monotonic() - self._start_time

    def fraction_used(self) -> float:
        fractions = [0.0]
        if self.total_tokens:
            fractions.append(self.tokens_used / self.total_tokens)
        if self.wall_clock_s:
            fractions.append(self.seconds_used / self.wall_clock_s)
        return max(fractions)

    def exhausted(self) -> str | None:
        if self.total_tokens and self.tokens_used >= self.total_tokens:
            return f"token budget exhausted ({self.tokens_used:,}/{self.total_tokens:,} tokens)"
        if self.wall_clock_s and self.seconds_used >= self.wall_clock_s:
            return f"time budget exhausted ({self.seconds_used:.0f}s/{self.wall_clock_s:.0f}s)"
        return None

    def warning(self) -> str | None:
        """Once, at WARN_AT of either budget."""
        if self._warned or self.fraction_used() < WARN_AT:
            return None
        self._warned = True
        return (f"{self.fraction_used():.0%} of this run's budget is used. Converge now: finish the "
                "smallest correct fix, run the relevant tests, and call done.")
