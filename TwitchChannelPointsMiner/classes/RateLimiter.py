import logging
import random
import time
from threading import Lock

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe token-bucket rate limiter with exponential backoff on 429s."""

    __slots__ = [
        "_min_interval",
        "_max_retries",
        "_backoff_base",
        "_backoff_max",
        "_lock",
        "_last_call",
        "_consecutive_429s",
    ]

    def __init__(
        self,
        min_interval: float = 1.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
    ):
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._lock = Lock()
        self._last_call = 0.0
        self._consecutive_429s = 0

    def acquire(self) -> None:
        """Block until it's safe to make the next request."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            wait = self._min_interval - elapsed
            if self._consecutive_429s > 0:
                backoff = min(
                    self._backoff_base ** self._consecutive_429s,
                    self._backoff_max,
                )
                jitter = random.uniform(0, backoff * 0.25)
                wait = max(wait, backoff + jitter)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def report_success(self) -> None:
        with self._lock:
            self._consecutive_429s = 0

    def report_rate_limited(self) -> None:
        with self._lock:
            self._consecutive_429s = min(
                self._consecutive_429s + 1, self._max_retries + 2
            )
            logger.warning(
                f"Rate limited (consecutive: {self._consecutive_429s}), "
                f"backing off"
            )

    @property
    def should_retry(self) -> bool:
        with self._lock:
            return self._consecutive_429s <= self._max_retries
