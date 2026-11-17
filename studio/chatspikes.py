"""Deciding when chat is suddenly busy. Used by the dashboard's live recording (chat spikes mode) and by ./watch.sh."""
from __future__ import annotations

import statistics
from collections import deque


class SpikeFinder:
    """Fed one message count per 10-second slice. A slice is a spike when it has at least `ratio` times the usual
    count (the middle value of the previous `window` slices: 18 slices = 3 minutes) and at least `minimum` messages.

    For the first `warmup` slices there is too little history to know what "usual" is, so nothing counts yet."""

    def __init__(self, ratio: float = 2.5, minimum: int = 10, window: int = 18, warmup: int = 6):
        self.ratio, self.minimum, self.warmup = ratio, minimum, warmup
        self.history: deque[int] = deque(maxlen=window)

    def usual(self) -> float:
        return statistics.median(self.history) if self.history else 0.0

    def add(self, count: int) -> bool:
        ready = len(self.history) >= self.warmup
        usual = self.usual()
        self.history.append(count)
        return ready and count >= self.minimum and count >= self.ratio * usual
