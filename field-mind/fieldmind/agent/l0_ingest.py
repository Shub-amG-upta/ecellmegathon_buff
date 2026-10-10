"""
=============================================================================
 L0  --  INGEST  (Plan §2, stage L0)
=============================================================================
Read the newest sensor samples, check they are fresh, in range and not frozen,
append to the rolling window.

The rolling window is the ONLY thing the check layer sees. It is a fixed-size
ring buffer: 60 minutes of 5-second samples = 720 samples per tag. Older data
is summarised, never kept raw (Plan §3.2).

Cost: ~0. This is a deque append.
=============================================================================
"""

from __future__ import annotations

from collections import deque

from ..schemas import TAGS


class SensorWindow:
    """Rolling 60-minute buffer over the six tags.

    Fixed capacity on purpose: on a device that must run all shift, an
    unbounded history is a slow memory leak that only shows up in hour three.
    """

    def __init__(self, window_min: float = 60.0, sample_period_s: float = 5.0,
                 tags: list[str] | None = None):
        self.tags = tags or list(TAGS)
        self.dt_s = sample_period_s
        self.capacity = int(window_min * 60 / sample_period_s)
        self._buf: dict[str, deque] = {t: deque(maxlen=self.capacity) for t in self.tags}
        self._t: deque = deque(maxlen=self.capacity)   # sample timestamps (s)
        self.n_ingested = 0

    # ---- writing ------------------------------------------------------
    def append(self, t_s: float, sample: dict[str, float]) -> None:
        self._t.append(t_s)
        for tag in self.tags:
            self._buf[tag].append(float(sample[tag]))
        self.n_ingested += 1

    # ---- reading ------------------------------------------------------
    def series(self, tag: str) -> list[float]:
        return list(self._buf[tag])

    def latest(self, tag: str):
        b = self._buf[tag]
        return b[-1] if b else None

    def mean(self, tag: str, n: int) -> float:
        s = list(self._buf[tag])[-n:]
        return sum(s) / len(s) if s else 0.0

    def window_ticks(self, tick: int) -> tuple[int, int]:
        """Tick range currently held, for the Fact.window field."""
        span = int(len(self._t) * self.dt_s / 30.0)     # 30 s per tick
        return (max(0, tick - span), tick)

    def ready(self, min_minutes: float = 5.0) -> bool:
        """Have we buffered enough history for the slope checks to mean
        anything? Before this, the agent reports NORMAL and says it is
        still filling its window rather than guessing."""
        return len(self._t) >= int(min_minutes * 60 / self.dt_s)


def ingest(window: SensorWindow, rows: list[dict]) -> int:
    """Append every raw sample belonging to this tick. Returns the count.

    Freshness/range/frozen checking is NOT done here -- it is the VALIDITY
    family in L1, so that a bad instrument produces a citable Fact rather
    than a silent drop. L0 only moves bytes.
    """
    for r in rows:
        window.append(float(r["t"]), r)
    return len(rows)
