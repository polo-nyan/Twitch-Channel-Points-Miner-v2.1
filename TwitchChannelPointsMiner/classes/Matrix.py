from textwrap import dedent

import logging
import requests
from urllib.parse import quote

from TwitchChannelPointsMiner.classes.RateLimiter import RateLimiter
from TwitchChannelPointsMiner.classes.Settings import Events

logger = logging.getLogger(__name__)


class Matrix(object):
    __slots__ = ["access_token", "homeserver", "room_id", "events", "_rate_limiter"]

    def __init__(self, username: str, password: str, homeserver: str, room_id: str, events: list):
        self.homeserver = homeserver
        self.room_id = quote(room_id)
        self.events = [str(e) for e in events]
        self._rate_limiter = RateLimiter(min_interval=0.5, max_retries=3)

        body = requests.post(
            url=f"https://{self.homeserver}/_matrix/client/r0/login",
            json={
                "user": username,
                "password": password,
                "type": "m.login.password"
            },
            timeout=10,
        ).json()

        self.access_token = body.get("access_token")

        if not self.access_token:
            logger.info("Invalid Matrix password provided. Notifications will not be sent.")

    def send(self, message: str, event: Events) -> None:
        if str(event) in self.events:
            self._rate_limiter.acquire()
            try:
                resp = requests.post(
                    url=f"https://{self.homeserver}/_matrix/client/r0/rooms/{self.room_id}/send/m.room.message?access_token={self.access_token}",
                    json={
                        "body": dedent(message),
                        "msgtype": "m.text"
                    },
                    timeout=10,
                )
                if resp.status_code == 429:
                    self._rate_limiter.report_rate_limited()
                else:
                    self._rate_limiter.report_success()
            except requests.RequestException:
                logger.warning("Failed to send Matrix notification", exc_info=True)
