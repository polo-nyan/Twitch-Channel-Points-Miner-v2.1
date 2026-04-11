# -*- coding: utf-8 -*-
"""
Settings loader: reads settings.json and builds the miner configuration.

This module is the bridge between a declarative JSON config and the
TwitchChannelPointsMiner Python API.  It is used by main.py (auto-eval)
and by export.py (generate a standalone run.py for the upstream project).
"""

import json
import logging
import os
import sys

from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Discord import Discord
from TwitchChannelPointsMiner.classes.Gotify import Gotify
from TwitchChannelPointsMiner.classes.Matrix import Matrix
from TwitchChannelPointsMiner.classes.Pushover import Pushover
from TwitchChannelPointsMiner.classes.Settings import Events, FollowersOrder, Priority
from TwitchChannelPointsMiner.classes.Telegram import Telegram
from TwitchChannelPointsMiner.classes.Webhook import Webhook
from TwitchChannelPointsMiner.classes.entities.Bet import (
    BetSettings,
    Condition,
    DelayMode,
    FilterCondition,
    OutcomeKeys,
    Strategy,
)
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer, StreamerSettings
from TwitchChannelPointsMiner.logger import ColorPalette, LoggerSettings

# ---------------------------------------------------------------------------
# Look-up tables for string → enum conversion
# ---------------------------------------------------------------------------
PRIORITY_MAP = {p.name: p for p in Priority}
STRATEGY_MAP = {s.name: s for s in Strategy}
CONDITION_MAP = {c.name: c for c in Condition}
DELAY_MODE_MAP = {d.name: d for d in DelayMode}
EVENTS_MAP = {e.name: e for e in Events}
CHAT_MAP = {c.name: c for c in ChatPresence}
FOLLOWERS_ORDER_MAP = {f.name: f for f in FollowersOrder}
OUTCOME_KEYS_MAP = {
    "PERCENTAGE_USERS": OutcomeKeys.PERCENTAGE_USERS,
    "ODDS_PERCENTAGE": OutcomeKeys.ODDS_PERCENTAGE,
    "ODDS": OutcomeKeys.ODDS,
    "TOP_POINTS": OutcomeKeys.TOP_POINTS,
    "TOTAL_USERS": OutcomeKeys.TOTAL_USERS,
    "TOTAL_POINTS": OutcomeKeys.TOTAL_POINTS,
    "DECISION_USERS": OutcomeKeys.DECISION_USERS,
    "DECISION_POINTS": OutcomeKeys.DECISION_POINTS,
}

LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_env(value):
    """If a string value starts with $, treat it as an env-var reference."""
    if isinstance(value, str) and value.startswith("$"):
        return os.environ.get(value[1:], "")
    return value


def load_settings(path: str = "settings.json") -> dict:
    """Load and return the raw settings dict from *path*."""
    if not os.path.isfile(path):
        print(f"[settings_loader] {path} not found.")
        print("[settings_loader] Copy settings.example.json → settings.json and edit it.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_events(lst):
    if not lst:
        return []
    return [EVENTS_MAP[e] for e in lst if e in EVENTS_MAP]


def _build_filter_condition(cfg):
    if not cfg:
        return None
    return FilterCondition(
        by=OUTCOME_KEYS_MAP.get(cfg.get("by", "").upper()),
        where=CONDITION_MAP.get(cfg.get("where", "").upper()),
        value=cfg.get("value"),
    )


def _build_bet_settings(cfg):
    if not cfg:
        return BetSettings()
    return BetSettings(
        strategy=STRATEGY_MAP.get(cfg.get("strategy", "").upper()),
        percentage=cfg.get("percentage"),
        percentage_gap=cfg.get("percentage_gap"),
        max_points=cfg.get("max_points"),
        minimum_points=cfg.get("minimum_points"),
        stealth_mode=cfg.get("stealth_mode"),
        delay=cfg.get("delay"),
        delay_mode=DELAY_MODE_MAP.get(cfg.get("delay_mode", "").upper()) if cfg.get("delay_mode") else None,
        filter_condition=_build_filter_condition(cfg.get("filter_condition")),
    )


def _build_streamer_settings(cfg):
    if not cfg:
        return None
    return StreamerSettings(
        make_predictions=cfg.get("make_predictions"),
        follow_raid=cfg.get("follow_raid"),
        claim_drops=cfg.get("claim_drops"),
        claim_moments=cfg.get("claim_moments"),
        watch_streak=cfg.get("watch_streak"),
        community_goals=cfg.get("community_goals"),
        chat=CHAT_MAP.get(cfg.get("chat", "").upper()) if cfg.get("chat") else None,
        bet=_build_bet_settings(cfg.get("bet")),
    )


def _build_notification(kind, cfg):
    """Build a notification object from its JSON block.  Returns None when
    the block is null / missing / has no required fields."""
    if not cfg:
        return None
    if kind == "telegram":
        if not cfg.get("token"):
            return None
        return Telegram(
            chat_id=cfg["chat_id"],
            token=_resolve_env(cfg["token"]),
            events=_parse_events(cfg.get("events")),
            disable_notification=cfg.get("disable_notification", False),
        )
    if kind == "discord":
        if not cfg.get("webhook_api"):
            return None
        return Discord(
            webhook_api=_resolve_env(cfg["webhook_api"]),
            events=_parse_events(cfg.get("events")),
            muted_channels=cfg.get("muted_channels", []),
            muted_events_per_channel={
                k: _parse_events(v) for k, v in cfg.get("muted_events_per_channel", {}).items()
            },
            global_muted_events=_parse_events(cfg.get("global_muted_events", [])),
            bot_token=_resolve_env(cfg.get("bot_token")) if cfg.get("bot_token") else None,
            channel_id=cfg.get("channel_id"),
        )
    if kind == "webhook":
        if not cfg.get("endpoint"):
            return None
        return Webhook(
            endpoint=cfg["endpoint"],
            method=cfg.get("method", "GET"),
            events=_parse_events(cfg.get("events")),
        )
    if kind == "matrix":
        if not cfg.get("homeserver"):
            return None
        return Matrix(
            username=cfg["username"],
            password=_resolve_env(cfg["password"]),
            homeserver=cfg["homeserver"],
            room_id=cfg["room_id"],
            events=_parse_events(cfg.get("events")),
        )
    if kind == "pushover":
        if not cfg.get("userkey"):
            return None
        return Pushover(
            userkey=_resolve_env(cfg["userkey"]),
            token=_resolve_env(cfg["token"]),
            priority=cfg.get("priority", 0),
            sound=cfg.get("sound", "pushover"),
            events=_parse_events(cfg.get("events")),
        )
    if kind == "gotify":
        if not cfg.get("endpoint"):
            return None
        return Gotify(
            endpoint=cfg["endpoint"],
            priority=cfg.get("priority", 8),
            events=_parse_events(cfg.get("events")),
        )
    return None


def build_logger_settings(cfg):
    log_cfg = cfg.get("logger", {})
    palette_cfg = log_cfg.get("color_palette", {})

    # Notification objects – first check logger sub-keys, then top-level
    telegram = _build_notification("telegram", log_cfg.get("telegram") or cfg.get("telegram"))
    discord = _build_notification("discord", log_cfg.get("discord") or cfg.get("discord"))
    webhook = _build_notification("webhook", log_cfg.get("webhook") or cfg.get("webhook"))
    matrix = _build_notification("matrix", log_cfg.get("matrix") or cfg.get("matrix"))
    pushover = _build_notification("pushover", log_cfg.get("pushover") or cfg.get("pushover"))
    gotify = _build_notification("gotify", log_cfg.get("gotify") or cfg.get("gotify"))

    return LoggerSettings(
        save=log_cfg.get("save", True),
        less=log_cfg.get("less", False),
        console_level=LOG_LEVEL_MAP.get(str(log_cfg.get("console_level", "INFO")).upper(), logging.INFO),
        console_username=log_cfg.get("console_username", False),
        time_zone=log_cfg.get("time_zone", ""),
        file_level=LOG_LEVEL_MAP.get(str(log_cfg.get("file_level", "DEBUG")).upper(), logging.DEBUG),
        emoji=log_cfg.get("emoji", True),
        colored=log_cfg.get("colored", False),
        color_palette=ColorPalette(**palette_cfg),
        auto_clear=log_cfg.get("auto_clear", True),
        telegram=telegram,
        discord=discord,
        webhook=webhook,
        matrix=matrix,
        pushover=pushover,
        gotify=gotify,
    )


def build_streamers(cfg):
    """Return a list of Streamer / str objects from the config."""
    result = []
    for entry in cfg.get("streamers", []):
        if isinstance(entry, str):
            result.append(entry)
        elif isinstance(entry, dict):
            username = entry["username"]
            settings = _build_streamer_settings(entry.get("settings"))
            result.append(Streamer(username, settings=settings))
    return result


def build_miner_kwargs(cfg):
    """Return the dict of kwargs for TwitchChannelPointsMiner(...)."""
    return dict(
        username=_resolve_env(cfg.get("username", "")),
        password=_resolve_env(cfg.get("password", "")) or None,
        claim_drops_startup=cfg.get("claim_drops_startup", False),
        enable_analytics=cfg.get("enable_analytics", False)
            or cfg.get("analytics", {}).get("enabled", False),
        disable_ssl_cert_verification=cfg.get("disable_ssl_cert_verification", False),
        disable_at_in_nickname=cfg.get("disable_at_in_nickname", False),
        priority=[PRIORITY_MAP[p] for p in cfg.get("priority", ["STREAK", "DROPS", "ORDER"]) if p in PRIORITY_MAP],
        logger_settings=build_logger_settings(cfg),
        streamer_settings=_build_streamer_settings(cfg.get("streamer_settings")) or StreamerSettings(),
    )


def build_mine_kwargs(cfg):
    """Return the dict of kwargs for twitch_miner.mine(...)."""
    return dict(
        streamers=build_streamers(cfg),
        followers=cfg.get("followers", False),
        followers_order=FOLLOWERS_ORDER_MAP.get(cfg.get("followers_order", "ASC").upper(), FollowersOrder.ASC),
    )


def build_analytics_kwargs(cfg):
    """Return analytics kwargs if analytics is enabled, else None."""
    acfg = cfg.get("analytics", {})
    if not acfg.get("enabled", False) and not cfg.get("enable_analytics", False):
        return None
    return dict(
        host=acfg.get("host", "127.0.0.1"),
        port=acfg.get("port", 5000),
        refresh=acfg.get("refresh", 5),
        days_ago=acfg.get("days_ago", 7),
    )
