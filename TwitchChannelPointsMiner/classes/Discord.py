import json
import logging
from datetime import datetime, timezone
from textwrap import dedent

import requests

from TwitchChannelPointsMiner.classes.Settings import Events

logger = logging.getLogger(__name__)

AVATAR_URL = "https://i.imgur.com/X9fEkhT.png"

# Event-specific colors for embeds (decimal color values)
EVENT_COLORS = {
    "STREAMER_ONLINE": 0x00FF00,   # Green
    "STREAMER_OFFLINE": 0x808080,  # Gray
    "GAIN_FOR_RAID": 0xFFD700,     # Gold
    "GAIN_FOR_CLAIM": 0x1E90FF,    # DodgerBlue
    "GAIN_FOR_WATCH": 0x87CEEB,    # SkyBlue
    "GAIN_FOR_WATCH_STREAK": 0xFFA500,  # Orange
    "BET_WIN": 0x00FF00,           # Green
    "BET_LOSE": 0xFF0000,          # Red
    "BET_REFUND": 0xFFFF00,        # Yellow
    "BET_FILTERS": 0xAAAAAA,       # LightGray
    "BET_GENERAL": 0x9B59B6,       # Purple
    "BET_FAILED": 0xFF4500,        # OrangeRed
    "BET_START": 0x3498DB,         # Blue
    "BET_DRY_RUN": 0x2ECC71,       # Emerald
    "BONUS_CLAIM": 0x1ABC9C,       # Teal
    "MOMENT_CLAIM": 0xE91E63,      # Pink
    "JOIN_RAID": 0xF39C12,         # Amber
    "DROP_CLAIM": 0x8E44AD,        # DarkPurple
    "DROP_STATUS": 0x2980B9,       # StrongBlue
    "CHAT_MENTION": 0xE74C3C,      # Alizarin
}

# Event-specific emoji icons for embed titles
EVENT_ICONS = {
    "STREAMER_ONLINE": "🟢",
    "STREAMER_OFFLINE": "🔴",
    "GAIN_FOR_RAID": "⚔️",
    "GAIN_FOR_CLAIM": "🎁",
    "GAIN_FOR_WATCH": "👀",
    "GAIN_FOR_WATCH_STREAK": "🔥",
    "BET_WIN": "🏆",
    "BET_LOSE": "💸",
    "BET_REFUND": "🔄",
    "BET_FILTERS": "🔧",
    "BET_GENERAL": "🎰",
    "BET_FAILED": "❌",
    "BET_START": "🎲",
    "BET_DRY_RUN": "🔮",
    "BONUS_CLAIM": "💰",
    "MOMENT_CLAIM": "⭐",
    "JOIN_RAID": "🚀",
    "DROP_CLAIM": "🎮",
    "DROP_STATUS": "📦",
    "CHAT_MENTION": "💬",
}

# Event category groupings
EVENT_CATEGORIES = {
    "STREAMER_ONLINE": "Stream Status",
    "STREAMER_OFFLINE": "Stream Status",
    "GAIN_FOR_RAID": "Points Gained",
    "GAIN_FOR_CLAIM": "Points Gained",
    "GAIN_FOR_WATCH": "Points Gained",
    "GAIN_FOR_WATCH_STREAK": "Points Gained",
    "BET_WIN": "Predictions",
    "BET_LOSE": "Predictions",
    "BET_REFUND": "Predictions",
    "BET_FILTERS": "Predictions",
    "BET_GENERAL": "Predictions",
    "BET_FAILED": "Predictions",
    "BET_START": "Predictions",
    "BET_DRY_RUN": "Predictions",
    "BONUS_CLAIM": "Bonus",
    "MOMENT_CLAIM": "Moments",
    "JOIN_RAID": "Raids",
    "DROP_CLAIM": "Drops",
    "DROP_STATUS": "Drops",
    "CHAT_MENTION": "Chat",
}


class Discord(object):
    __slots__ = [
        "webhook_api",
        "events",
        "muted_channels",
        "muted_events_per_channel",
        "global_muted_events",
    ]

    def __init__(
        self,
        webhook_api: str,
        events: list,
        muted_channels: list = None,
        muted_events_per_channel: dict = None,
        global_muted_events: list = None,
    ):
        self.webhook_api = webhook_api
        self.events = [str(e) for e in events]
        self.muted_channels = [
            c.lower() for c in (muted_channels or [])
        ]
        self.muted_events_per_channel = {
            k.lower(): [str(e) for e in v]
            for k, v in (muted_events_per_channel or {}).items()
        }
        self.global_muted_events = [
            str(e) for e in (global_muted_events or [])
        ]

    def is_muted(self, event: Events, channel: str = None) -> bool:
        event_str = str(event)
        if event_str in self.global_muted_events:
            return True
        if channel:
            ch = channel.lower()
            if ch in self.muted_channels:
                return True
            if ch in self.muted_events_per_channel:
                if event_str in self.muted_events_per_channel[ch]:
                    return True
        return False

    def send(self, message: str, event: Events, channel: str = None) -> None:
        if str(event) not in self.events:
            return
        if self.is_muted(event, channel):
            return

        event_str = str(event)
        color = EVENT_COLORS.get(event_str, 0x7289DA)
        icon = EVENT_ICONS.get(event_str, "📋")
        category = EVENT_CATEGORIES.get(event_str, "General")

        # Build embed
        embed = {
            "title": f"{icon} {event_str.replace('_', ' ').title()}",
            "description": dedent(message),
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {
                "text": f"📂 {category}"
                + (f" • 📺 {channel}" if channel else ""),
                "icon_url": AVATAR_URL,
            },
        }

        if channel:
            embed["author"] = {
                "name": f"📺 {channel}",
                "url": f"https://twitch.tv/{channel}",
            }

        payload = {
            "username": "Twitch Channel Points Miner",
            "avatar_url": AVATAR_URL,
            "embeds": [embed],
        }

        try:
            resp = requests.post(
                url=self.webhook_api,
                json=payload,
            )
            if resp.status_code == 429:
                logger.warning("Discord webhook rate limited, notification dropped")
        except requests.RequestException:
            logger.warning("Failed to send Discord embed", exc_info=True)
