import logging
from urllib.parse import urlencode

import requests

from TwitchChannelPointsMiner.classes.Settings import Events

logger = logging.getLogger(__name__)


class Webhook(object):
    __slots__ = ["endpoint", "method", "events"]

    def __init__(self, endpoint: str, method: str, events: list):
        self.endpoint = endpoint
        self.method = method.upper()
        if self.method not in ("GET", "POST"):
            raise ValueError(
                f"Invalid webhook method '{method}', use 'GET' or 'POST'"
            )
        self.events = [str(e) for e in events]

    def send(self, message: str, event: Events) -> None:
        if str(event) not in self.events:
            return

        params = urlencode({"event_name": str(event), "message": message})
        url = f"{self.endpoint}?{params}"

        try:
            if self.method == "GET":
                requests.get(url=url, timeout=10)
            else:
                requests.post(url=url, timeout=10)
        except requests.RequestException as e:
            logger.warning(f"Webhook request failed: {e}")
