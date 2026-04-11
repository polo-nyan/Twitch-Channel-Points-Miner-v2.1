import json
import logging
import os
import re
from datetime import datetime, timezone
from textwrap import dedent

import requests

from TwitchChannelPointsMiner.classes.RateLimiter import RateLimiter
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

# Regex patterns for parsing old plain-text Discord messages into structured data
_PREDICTION_RESULT_RE = re.compile(
    r"EventPrediction\(event_id=([a-f0-9-]+).*?streamer=Streamer\(username=(\w+).*?channel_points=([\d.]+k?).*?title=(.+?)\)"
    r"\s*-\s*Decision:\s*(\d+):\s*(.+?)\s*\((\w+)\)\s*-\s*Result:\s*(\w+),\s*Gained:\s*([+-][\d.]+k?)",
    re.IGNORECASE,
)
_PLACE_BET_RE = re.compile(
    r"Place\s+([\d.]+k?)\s+channel points on:\s+(.+?)\s*,\s*Points:\s*([\d.]+[kKMB]?),\s*Users:\s*(\d+)\s*\(([\d.]+)%\),\s*Odds:\s*([\d.]+)\s*\(([\d.]+)%\)",
    re.IGNORECASE,
)
_RAID_RE = re.compile(
    r"Joining raid from Streamer\(username=(\w+).*?\)\s+to\s+(\w+)",
    re.IGNORECASE,
)
_POINTS_GAIN_RE = re.compile(
    r"\+(\d+)\s*→\s*Streamer\(username=(\w+).*?\)\s*-\s*Reason:\s*(\w+)",
    re.IGNORECASE,
)


def parse_legacy_message(text: str) -> dict | None:
    """Parse an old plain-text Discord message into structured data.
    Returns a dict with keys: type, data, or None if not recognized."""
    m = _PREDICTION_RESULT_RE.search(text)
    if m:
        return {
            "type": "prediction_result",
            "event_id": m.group(1),
            "streamer": m.group(2),
            "channel_points": m.group(3),
            "title": m.group(4),
            "choice_index": int(m.group(5)),
            "choice_title": m.group(6),
            "choice_color": m.group(7),
            "result": m.group(8),
            "gained": m.group(9),
        }
    m = _PLACE_BET_RE.search(text)
    if m:
        return {
            "type": "bet_placed",
            "amount": m.group(1),
            "outcome": m.group(2),
            "total_points": m.group(3),
            "users": int(m.group(4)),
            "user_pct": float(m.group(5)),
            "odds": float(m.group(6)),
            "odds_pct": float(m.group(7)),
        }
    m = _RAID_RE.search(text)
    if m:
        return {
            "type": "raid",
            "from_streamer": m.group(1),
            "to_streamer": m.group(2),
        }
    m = _POINTS_GAIN_RE.search(text)
    if m:
        return {
            "type": "points_gain",
            "amount": int(m.group(1)),
            "streamer": m.group(2),
            "reason": m.group(3),
        }
    return None


class Discord(object):
    __slots__ = [
        "webhook_api",
        "events",
        "muted_channels",
        "muted_events_per_channel",
        "global_muted_events",
        "_rate_limiter",
        "bot_token",
        "channel_id",
    ]

    def __init__(
        self,
        webhook_api: str,
        events: list,
        muted_channels: list = None,
        muted_events_per_channel: dict = None,
        global_muted_events: list = None,
        bot_token: str = None,
        channel_id: str = None,
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
        self._rate_limiter = RateLimiter(min_interval=0.5, max_retries=5, backoff_max=30.0)
        self.bot_token = bot_token
        self.channel_id = channel_id

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

    def _build_embed(self, message: str, event_str: str, channel: str = None) -> dict:
        """Build a rich embed for the given event."""
        color = EVENT_COLORS.get(event_str, 0x7289DA)
        icon = EVENT_ICONS.get(event_str, "📋")
        category = EVENT_CATEGORIES.get(event_str, "General")

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

        return embed

    def send(self, message: str, event: Events, channel: str = None) -> None:
        if str(event) not in self.events:
            return
        if self.is_muted(event, channel):
            return

        event_str = str(event)
        embed = self._build_embed(message, event_str, channel)

        payload = {
            "username": "Twitch Channel Points Miner",
            "avatar_url": AVATAR_URL,
            "embeds": [embed],
        }

        self._rate_limiter.acquire()
        try:
            resp = requests.post(
                url=self.webhook_api,
                json=payload,
                timeout=10,
            )
            if resp.status_code == 429:
                self._rate_limiter.report_rate_limited()
            else:
                self._rate_limiter.report_success()
        except requests.RequestException:
            logger.warning("Failed to send Discord embed", exc_info=True)

    def fetch_old_messages(self, limit: int = 1000) -> list[dict]:
        """Fetch recent messages from the Discord channel.
        Uses bot token (full channel history) if available, otherwise falls back to webhook GET."""
        results = []
        messages = []

        if self.bot_token and self.channel_id:
            # Bot mode: full channel history access
            headers = {"Authorization": f"Bot {self.bot_token}"}
            messages_url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
            fetched = 0
            before = None
            try:
                while fetched < limit:
                    params = {"limit": min(100, limit - fetched)}
                    if before:
                        params["before"] = before
                    self._rate_limiter.acquire()
                    resp = requests.get(messages_url, headers=headers, params=params, timeout=15)
                    if resp.status_code == 429:
                        self._rate_limiter.report_rate_limited()
                        continue
                    self._rate_limiter.report_success()
                    if resp.status_code != 200:
                        logger.info(f"Discord bot fetch: HTTP {resp.status_code} — {resp.text[:300]}")
                        break
                    batch = resp.json()
                    if not isinstance(batch, list) or not batch:
                        break
                    messages.extend(batch)
                    fetched += len(batch)
                    before = batch[-1]["id"]
                    if len(batch) < 100:
                        break
            except requests.RequestException:
                logger.warning("Discord bot back-import: failed to fetch messages", exc_info=True)
        else:
            # Webhook mode: limited access
            parts = self.webhook_api.rstrip("/").split("/")
            if len(parts) < 2:
                logger.warning("Cannot parse webhook URL for back-import")
                return []

            webhook_id = parts[-2]
            webhook_token = parts[-1]
            messages_url = f"https://discord.com/api/v10/webhooks/{webhook_id}/{webhook_token}/messages"

            try:
                resp = requests.get(
                    messages_url,
                    params={"limit": min(limit, 1000)},
                    timeout=15,
                )
                if resp.status_code != 200:
                    logger.info(
                        f"Discord back-import: cannot fetch messages (HTTP {resp.status_code}). "
                        "This is normal for webhooks without message history access."
                    )
                    return []
                messages = resp.json()
                if not isinstance(messages, list):
                    return []
            except requests.RequestException:
                logger.warning("Discord back-import: failed to fetch messages", exc_info=True)

        for msg in messages:
            msg_id = msg.get("id")
            content = msg.get("content", "")
            for emb in msg.get("embeds", []):
                content += " " + emb.get("description", "")

            parsed = parse_legacy_message(content)
            if parsed:
                parsed["message_id"] = msg_id
                parsed["timestamp"] = msg.get("timestamp")
                results.append(parsed)

        logger.info(f"Discord back-import: parsed {len(results)} events from {len(messages)} messages")
        return results

    def edit_message(self, message_id: str, embed: dict) -> bool:
        """Edit a message. Requires bot_token and channel_id."""
        if not self.bot_token or not self.channel_id:
            logger.warning("Cannot edit messages without bot_token and channel_id")
            return False

        headers = {"Authorization": f"Bot {self.bot_token}", "Content-Type": "application/json"}
        url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages/{message_id}"

        self._rate_limiter.acquire()
        try:
            resp = requests.patch(
                url,
                headers=headers,
                json={"embeds": [embed]},
                timeout=10,
            )
            if resp.status_code == 429:
                self._rate_limiter.report_rate_limited()
                return False
            self._rate_limiter.report_success()
            return resp.status_code == 200
        except requests.RequestException:
            logger.warning(f"Failed to edit Discord message {message_id}", exc_info=True)
            return False

    def delete_message(self, message_id: str) -> bool:
        """Delete a single webhook message by ID (for cleanup after back-import)."""
        parts = self.webhook_api.rstrip("/").split("/")
        if len(parts) < 2:
            return False
        webhook_id = parts[-2]
        webhook_token = parts[-1]
        url = f"https://discord.com/api/v10/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}"

        self._rate_limiter.acquire()
        try:
            resp = requests.delete(url, timeout=10)
            if resp.status_code == 429:
                self._rate_limiter.report_rate_limited()
                return False
            self._rate_limiter.report_success()
            return resp.status_code == 204
        except requests.RequestException:
            logger.warning(f"Failed to delete Discord message {message_id}", exc_info=True)
            return False

    def upsert_logbook_embed(
        self, streamer: str, payload: dict, state_path: str
    ) -> str | None:
        """Send or update a single logbook embed for a streamer.

        Tries to PATCH/edit an existing message first (stored in *state_path*).
        Falls back to POST with ``?wait=true`` to get the message ID so future
        calls can update the same message instead of creating a new one.

        *state_path* is a JSON file mapping ``streamer -> message_id``.
        Returns the message ID on success, None on failure.
        """
        # Load persisted message-ID state
        state: dict = {}
        if os.path.isfile(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as fh:
                    state = json.load(fh)
            except Exception:
                state = {}

        msg_id = state.get(streamer)

        def _save_id(new_id: str):
            state[streamer] = new_id
            try:
                with open(state_path, "w", encoding="utf-8") as fh:
                    json.dump(state, fh, indent=2)
            except Exception:
                logger.warning("Could not persist logbook state to %s", state_path)

        # --- Try to EDIT the existing message ---
        if msg_id:
            edited = False

            # Bot-token mode: edit channel message directly
            if self.bot_token and self.channel_id:
                edited = self.edit_message(msg_id, payload.get("embeds", [payload])[0]
                                           if "embeds" in payload else payload)

            # Webhook mode: PATCH webhook message
            if not edited and self.webhook_api:
                parts = self.webhook_api.rstrip("/").split("/")
                if len(parts) >= 2:
                    edit_url = (
                        f"https://discord.com/api/v10/webhooks/"
                        f"{parts[-2]}/{parts[-1]}/messages/{msg_id}"
                    )
                    self._rate_limiter.acquire()
                    try:
                        resp = requests.patch(edit_url, json=payload, timeout=10)
                        if resp.status_code == 200:
                            self._rate_limiter.report_success()
                            edited = True
                        elif resp.status_code == 429:
                            self._rate_limiter.report_rate_limited()
                        elif resp.status_code in (404, 403):
                            # Message gone — fall through to create a new one
                            state.pop(streamer, None)
                            msg_id = None
                    except requests.RequestException:
                        logger.warning("Failed to PATCH logbook embed", exc_info=True)

            if edited:
                return msg_id

        # --- Create a new message (POST with ?wait=true to get ID) ---
        if not self.webhook_api:
            return None

        self._rate_limiter.acquire()
        try:
            resp = requests.post(
                f"{self.webhook_api}?wait=true", json=payload, timeout=10
            )
            if resp.status_code == 200:
                self._rate_limiter.report_success()
                new_id = str(resp.json().get("id", ""))
                if new_id:
                    _save_id(new_id)
                return new_id or None
            elif resp.status_code == 429:
                self._rate_limiter.report_rate_limited()
        except requests.RequestException:
            logger.warning("Failed to POST logbook embed", exc_info=True)
        return None

    def purge_all_messages(self, limit: int = 500) -> int:
        """Delete ALL messages from the Discord channel (bot-token mode) or from
        this webhook (webhook mode, using stored logbook IDs only since Discord
        does not expose a list endpoint for webhooks).

        Returns the count of successfully deleted messages.
        """
        deleted = 0

        if self.bot_token and self.channel_id:
            headers = {"Authorization": f"Bot {self.bot_token}", "Content-Type": "application/json"}
            channel_url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"

            # 1. Collect all message IDs
            all_ids: list[str] = []
            before = None
            while len(all_ids) < limit:
                params: dict = {"limit": min(100, limit - len(all_ids))}
                if before:
                    params["before"] = before
                self._rate_limiter.acquire()
                try:
                    resp = requests.get(channel_url, headers=headers, params=params, timeout=15)
                    if resp.status_code == 429:
                        self._rate_limiter.report_rate_limited()
                        continue
                    self._rate_limiter.report_success()
                    if resp.status_code != 200:
                        break
                    batch = resp.json()
                    if not isinstance(batch, list) or not batch:
                        break
                    all_ids.extend(str(m["id"]) for m in batch if "id" in m)
                    before = batch[-1]["id"]
                    if len(batch) < 100:
                        break
                except requests.RequestException:
                    logger.warning("purge_all_messages: failed to fetch page", exc_info=True)
                    break

            # 2. Split by age: messages < 14 days old → bulk-delete; older → individual
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(days=13, hours=23)

            bulk_ids: list[str] = []
            old_ids: list[str] = []
            for mid in all_ids:
                try:
                    ts_ms = (int(mid) >> 22) + 1420070400000  # Discord snowflake epoch
                    msg_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                    if msg_dt > cutoff:
                        bulk_ids.append(mid)
                    else:
                        old_ids.append(mid)
                except (ValueError, OverflowError):
                    old_ids.append(mid)

            # Bulk-delete in chunks of up to 100
            bulk_url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages/bulk-delete"
            for i in range(0, len(bulk_ids), 100):
                chunk = bulk_ids[i:i + 100]
                if len(chunk) < 2:
                    old_ids.extend(chunk)
                    continue
                self._rate_limiter.acquire()
                try:
                    resp = requests.post(bulk_url, headers=headers, json={"messages": chunk}, timeout=15)
                    if resp.status_code == 204:
                        deleted += len(chunk)
                        self._rate_limiter.report_success()
                    elif resp.status_code == 429:
                        self._rate_limiter.report_rate_limited()
                    else:
                        old_ids.extend(chunk)  # Fall back to individual delete
                except requests.RequestException:
                    old_ids.extend(chunk)

            # Individual delete for old messages
            for mid in old_ids:
                self._rate_limiter.acquire()
                try:
                    resp = requests.delete(f"{channel_url}/{mid}", headers=headers, timeout=10)
                    if resp.status_code == 204:
                        deleted += 1
                        self._rate_limiter.report_success()
                    elif resp.status_code == 429:
                        self._rate_limiter.report_rate_limited()
                except requests.RequestException:
                    pass

        elif self.webhook_api:
            # Webhook-only mode: can only delete messages sent by this webhook
            # that we have stored IDs for (e.g. from logbook_state.json)
            logger.warning(
                "purge_all_messages: webhook-only mode — cannot list channel history. "
                "Configure bot_token + channel_id for full purge support."
            )

        return deleted

    def cleanup_and_repost(self, old_messages: list[dict]) -> int:
        """Delete old plain-text messages and re-post them as embeds.
        Returns count of successfully migrated messages."""
        migrated = 0
        for msg in old_messages:
            msg_id = msg.get("message_id")
            if not msg_id:
                continue

            # Determine event type for the embed
            msg_type = msg.get("type", "")
            if msg_type == "prediction_result":
                result = msg.get("result", "").upper()
                event_str = f"BET_{result}" if result in ("WIN", "LOSE", "REFUND") else "BET_GENERAL"
                streamer = msg.get("streamer", "")
                title = msg.get("title", "")
                gained = msg.get("gained", "")
                choice = msg.get("choice_title", "")
                color = msg.get("choice_color", "")
                desc = f"**{title}**\nDecision: {choice} ({color})\nResult: **{result}** {gained}"
            elif msg_type == "bet_placed":
                event_str = "BET_START"
                streamer = ""
                desc = (
                    f"Placed **{msg.get('amount', '')}** on: {msg.get('outcome', '')}\n"
                    f"Users: {msg.get('users', '')} ({msg.get('user_pct', '')}%) • "
                    f"Odds: {msg.get('odds', '')} ({msg.get('odds_pct', '')}%)"
                )
            elif msg_type == "raid":
                event_str = "JOIN_RAID"
                streamer = msg.get("from_streamer", "")
                desc = f"Joining raid from **{streamer}** to **{msg.get('to_streamer', '')}**"
            elif msg_type == "points_gain":
                event_str = f"GAIN_FOR_{msg.get('reason', 'WATCH').upper()}"
                if event_str not in EVENT_COLORS:
                    event_str = "GAIN_FOR_WATCH"
                streamer = msg.get("streamer", "")
                desc = f"+{msg.get('amount', '')} points — Reason: {msg.get('reason', '')}"
            else:
                continue

            embed = self._build_embed(desc, event_str, streamer or None)
            # Preserve original timestamp if available
            if msg.get("timestamp"):
                embed["timestamp"] = msg["timestamp"]
            embed["footer"]["text"] += " • 📥 Imported"

            # Delete old message
            if self.delete_message(msg_id):
                # Re-post as embed
                payload = {
                    "username": "Twitch Channel Points Miner",
                    "avatar_url": AVATAR_URL,
                    "embeds": [embed],
                }
                self._rate_limiter.acquire()
                try:
                    resp = requests.post(self.webhook_api, json=payload, timeout=10)
                    if resp.status_code in (200, 204):
                        self._rate_limiter.report_success()
                        migrated += 1
                    elif resp.status_code == 429:
                        self._rate_limiter.report_rate_limited()
                except requests.RequestException:
                    logger.warning("Failed to re-post migrated embed", exc_info=True)

        logger.info(f"Discord migration: {migrated}/{len(old_messages)} messages converted to embeds")
        return migrated
