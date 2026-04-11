import logging
from textwrap import dedent

import requests

from TwitchChannelPointsMiner.classes.RateLimiter import RateLimiter
from TwitchChannelPointsMiner.classes.Settings import Events

logger = logging.getLogger(__name__)


class Telegram(object):
    __slots__ = ["chat_id", "telegram_api", "events", "disable_notification", "_rate_limiter"]

    def __init__(
        self, chat_id: int, token: str, events: list, disable_notification: bool = False
    ):
        self.chat_id = chat_id
        self.telegram_api = f"https://api.telegram.org/bot{token}/sendMessage"
        self.events = [str(e) for e in events]
        self.disable_notification = disable_notification
        self._rate_limiter = RateLimiter(min_interval=1.0, max_retries=3)

    def send(self, message: str, event: Events) -> None:
        if str(event) in self.events:
            self._rate_limiter.acquire()
            try:
                resp = requests.post(
                    url=self.telegram_api,
                    data={
                        "chat_id": self.chat_id,
                        "text": dedent(message),
                        "disable_web_page_preview": True,
                        "disable_notification": self.disable_notification,
                    },
                    timeout=10,
                )
                if resp.status_code == 429:
                    self._rate_limiter.report_rate_limited()
                else:
                    self._rate_limiter.report_success()
            except requests.RequestException:
                logger.warning("Failed to send Telegram notification", exc_info=True)
