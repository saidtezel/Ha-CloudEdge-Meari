"""Exponential retry delays for the cloud login / polling loops."""

from __future__ import annotations


class RetryBackoff:
    """Double the delay after each failure, up to ``max_s``.

    Passing ``ran_for_s`` to :meth:`next_delay` resets the delay when the
    attempt that just failed had been healthy for at least ``max_s``, so an
    occasional drop after hours of uptime is retried promptly while a
    persistent failure (bad credentials, cloud outage) settles at ``max_s``
    instead of hammering the Meari login endpoint.
    """

    def __init__(self, initial_s: float, max_s: float) -> None:
        self._initial_s = initial_s
        self._max_s = max_s
        self._delay_s = initial_s

    def reset(self) -> None:
        self._delay_s = self._initial_s

    def next_delay(self, ran_for_s: float = 0.0) -> float:
        if ran_for_s >= self._max_s:
            self.reset()
        delay = self._delay_s
        self._delay_s = min(self._max_s, self._delay_s * 2)
        return delay
