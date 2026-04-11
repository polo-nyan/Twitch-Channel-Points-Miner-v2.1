import logging
from textwrap import dedent

import requests

from TwitchChannelPointsMiner.classes.RateLimiter import RateLimiter
from TwitchChannelPointsMiner.classes.Settings import Events

logger = logging.getLogger(__name__)


class Pushover(object):
    __slots__ = ["userkey", "token", "priority", "sound", "events", "_rate_limiter"]

    def __init__(self, userkey: str, token: str, priority, sound, events: list):
        self.userkey = userkey
        self.token = token
        self.priority = priority
        self.sound = sound
        self.events = [str(e) for e in events]
        self._rate_limiter = RateLimiter(min_interval=1.0, max_retries=3)

    def send(self, message: str, event: Events) -> None:
        if str(event) in self.events:
            self._rate_limiter.acquire()
            try:
                resp = requests.post(
                    url="https://api.pushover.net/1/messages.json",
                    data={
                        "user": self.userkey,
                        "token": self.token,
                        "message": dedent(message),
                        "title": "Twitch Channel Points Miner",
                        "priority": self.priority,
                        "sound": self.sound,
                    },
                    timeout=10,
                )
                if resp.status_code == 429:
                    self._rate_limiter.report_rate_limited()
                else:
                    self._rate_limiter.report_success()
            except requests.RequestException:
                logger.warning("Failed to send Pushover notification", exc_info=True)
