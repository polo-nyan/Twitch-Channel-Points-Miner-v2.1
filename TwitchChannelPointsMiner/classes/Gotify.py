import logging
from textwrap import dedent

import requests

from TwitchChannelPointsMiner.classes.RateLimiter import RateLimiter
from TwitchChannelPointsMiner.classes.Settings import Events

logger = logging.getLogger(__name__)


class Gotify(object):
    __slots__ = ["endpoint", "priority", "events", "_rate_limiter"]

    def __init__(self, endpoint: str, priority: int, events: list):
        self.endpoint = endpoint
        self.priority = priority
        self.events = [str(e) for e in events]
        self._rate_limiter = RateLimiter(min_interval=0.5, max_retries=3)

    def send(self, message: str, event: Events) -> None:
        if str(event) in self.events:
            self._rate_limiter.acquire()
            try:
                resp = requests.post(
                    url=self.endpoint,
                    data={
                        "message": dedent(message),
                        "priority": self.priority
                    },
                    timeout=10,
                )
                if resp.status_code == 429:
                    self._rate_limiter.report_rate_limited()
                else:
                    self._rate_limiter.report_success()
            except requests.RequestException:
                logger.warning("Failed to send Gotify notification", exc_info=True)
