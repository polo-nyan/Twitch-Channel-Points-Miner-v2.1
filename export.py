#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export.py  –  Export settings.json into a standalone run.py compatible with
the upstream Twitch-Channel-Points-Miner-v2 project.

Fork-specific features (HISTORICAL strategy, KELLY_CRITERION, CONTRARIAN,
MOMENTUM, VALUE_BET, WEIGHTED_AVERAGE, UNDERDOG strategies, env-var
references, etc.) are gracefully downgraded:
  - Unknown strategies fall back to SMART
  - Extra fields are silently dropped

Usage:
    python export.py                         # reads settings.json → writes run.py
    python export.py -c my.json -o run.py    # custom paths
"""

import argparse
import json
import os
import sys
import textwrap

from settings_loader import load_settings

# Strategies available in the original upstream project
UPSTREAM_STRATEGIES = {
    "MOST_VOTED", "HIGH_ODDS", "PERCENTAGE", "SMART_MONEY", "SMART",
    "NUMBER_1", "NUMBER_2", "NUMBER_3", "NUMBER_4",
    "NUMBER_5", "NUMBER_6", "NUMBER_7", "NUMBER_8",
}

UPSTREAM_DELAY_MODES = {"FROM_START", "FROM_END", "PERCENTAGE"}
UPSTREAM_CONDITIONS = {"GT", "LT", "GTE", "LTE"}
UPSTREAM_OUTCOME_KEYS = {
    "PERCENTAGE_USERS", "ODDS_PERCENTAGE", "ODDS",
    "TOP_POINTS", "TOTAL_USERS", "TOTAL_POINTS",
}
UPSTREAM_CHAT = {"ALWAYS", "NEVER", "ONLINE", "OFFLINE"}
UPSTREAM_PRIORITIES = {"STREAK", "DROPS", "ORDER", "POINTS_ASCENDING", "POINTS_DESCENDING", "SUBSCRIBED"}


def _safe_strategy(s):
    s = (s or "SMART").upper()
    return s if s in UPSTREAM_STRATEGIES else "SMART"


def _safe_delay_mode(d):
    d = (d or "FROM_END").upper()
    return d if d in UPSTREAM_DELAY_MODES else "FROM_END"


def _resolve_env(val):
    if isinstance(val, str) and val.startswith("$"):
        return os.environ.get(val[1:], "")
    return val


def _q(val):
    """Quote a string for Python source."""
    if val is None:
        return "None"
    return repr(str(val))


def _gen_filter_condition(fc):
    if not fc:
        return "None"
    by = fc.get("by", "TOTAL_USERS").upper()
    if by not in UPSTREAM_OUTCOME_KEYS:
        by = "TOTAL_USERS"
    where = fc.get("where", "LTE").upper()
    if where not in UPSTREAM_CONDITIONS:
        where = "LTE"
    value = fc.get("value", 0)
    return f"FilterCondition(by=OutcomeKeys.{by}, where=Condition.{where}, value={value})"


def _gen_bet_settings(bet):
    if not bet:
        return "BetSettings()"
    parts = []
    parts.append(f"strategy=Strategy.{_safe_strategy(bet.get('strategy'))}")
    if bet.get("percentage") is not None:
        parts.append(f"percentage={bet['percentage']}")
    if bet.get("percentage_gap") is not None:
        parts.append(f"percentage_gap={bet['percentage_gap']}")
    if bet.get("max_points") is not None:
        parts.append(f"max_points={bet['max_points']}")
    if bet.get("minimum_points") is not None:
        parts.append(f"minimum_points={bet['minimum_points']}")
    if bet.get("stealth_mode") is not None:
        parts.append(f"stealth_mode={bet['stealth_mode']}")
    if bet.get("delay_mode") is not None:
        parts.append(f"delay_mode=DelayMode.{_safe_delay_mode(bet['delay_mode'])}")
    if bet.get("delay") is not None:
        parts.append(f"delay={bet['delay']}")
    fc = bet.get("filter_condition")
    if fc:
        parts.append(f"filter_condition={_gen_filter_condition(fc)}")
    return f"BetSettings({', '.join(parts)})"


def _gen_streamer_settings(ss):
    if not ss:
        return ""
    parts = []
    for key in ("make_predictions", "follow_raid", "claim_drops", "claim_moments",
                "watch_streak", "community_goals"):
        if ss.get(key) is not None:
            parts.append(f"{key}={ss[key]}")
    chat = (ss.get("chat") or "").upper()
    if chat and chat in UPSTREAM_CHAT:
        parts.append(f"chat=ChatPresence.{chat}")
    if ss.get("bet"):
        parts.append(f"bet={_gen_bet_settings(ss['bet'])}")
    return f"StreamerSettings({', '.join(parts)})"


def _gen_notification(kind, cfg):
    if not cfg:
        return "None"
    events_str = ", ".join(f"Events.{e}" for e in cfg.get("events", []))
    if kind == "telegram":
        token = _resolve_env(cfg.get("token", ""))
        if not token:
            return "None"
        return (
            f"Telegram(\n"
            f"            chat_id={cfg['chat_id']},\n"
            f"            token={_q(token)},\n"
            f"            events=[{events_str}],\n"
            f"            disable_notification={cfg.get('disable_notification', False)},\n"
            f"        )"
        )
    if kind == "discord":
        webhook = _resolve_env(cfg.get("webhook_api", ""))
        if not webhook:
            return "None"
        return (
            f"Discord(\n"
            f"            webhook_api={_q(webhook)},\n"
            f"            events=[{events_str}],\n"
            f"        )"
        )
    if kind == "webhook":
        if not cfg.get("endpoint"):
            return "None"
        return (
            f"Webhook(\n"
            f"            endpoint={_q(cfg['endpoint'])},\n"
            f"            method={_q(cfg.get('method', 'GET'))},\n"
            f"            events=[{events_str}],\n"
            f"        )"
        )
    if kind == "matrix":
        if not cfg.get("homeserver"):
            return "None"
        pw = _resolve_env(cfg.get("password", ""))
        return (
            f"Matrix(\n"
            f"            username={_q(cfg.get('username', ''))},\n"
            f"            password={_q(pw)},\n"
            f"            homeserver={_q(cfg['homeserver'])},\n"
            f"            room_id={_q(cfg.get('room_id', ''))},\n"
            f"            events=[{events_str}],\n"
            f"        )"
        )
    if kind == "pushover":
        if not cfg.get("userkey"):
            return "None"
        return (
            f"Pushover(\n"
            f"            userkey={_q(_resolve_env(cfg['userkey']))},\n"
            f"            token={_q(_resolve_env(cfg.get('token', '')))},\n"
            f"            priority={cfg.get('priority', 0)},\n"
            f"            sound={_q(cfg.get('sound', 'pushover'))},\n"
            f"            events=[{events_str}],\n"
            f"        )"
        )
    if kind == "gotify":
        if not cfg.get("endpoint"):
            return "None"
        return (
            f"Gotify(\n"
            f"            endpoint={_q(cfg['endpoint'])},\n"
            f"            priority={cfg.get('priority', 8)},\n"
            f"            events=[{events_str}],\n"
            f"        )"
        )
    return "None"


def export(cfg, output_path="run.py"):
    """Generate a standalone run.py from settings dict."""
    # Priorities
    priorities = cfg.get("priority", ["STREAK", "DROPS", "ORDER"])
    priorities = [p for p in priorities if p in UPSTREAM_PRIORITIES]
    priority_str = ", ".join(f"Priority.{p}" for p in priorities)

    # Logger / notifications
    log_cfg = cfg.get("logger", {})
    palette_cfg = log_cfg.get("color_palette", {})
    palette_kwargs = ", ".join(f'{k}="{v}"' for k, v in palette_cfg.items()) if palette_cfg else ""

    telegram_cfg = log_cfg.get("telegram") or cfg.get("telegram")
    discord_cfg = log_cfg.get("discord") or cfg.get("discord")
    webhook_cfg = log_cfg.get("webhook") or cfg.get("webhook")
    matrix_cfg = log_cfg.get("matrix") or cfg.get("matrix")
    pushover_cfg = log_cfg.get("pushover") or cfg.get("pushover")
    gotify_cfg = log_cfg.get("gotify") or cfg.get("gotify")

    console_level = str(log_cfg.get("console_level", "INFO")).upper()
    file_level = str(log_cfg.get("file_level", "DEBUG")).upper()

    # Global streamer settings
    ss_cfg = cfg.get("streamer_settings", {})
    global_ss = _gen_streamer_settings(ss_cfg)

    # Streamers
    streamer_lines = []
    for entry in cfg.get("streamers", []):
        if isinstance(entry, str):
            streamer_lines.append(f'        "{entry}",')
        elif isinstance(entry, dict):
            username = entry["username"]
            ss = entry.get("settings")
            if ss:
                streamer_lines.append(
                    f'        Streamer("{username}", settings={_gen_streamer_settings(ss)}),'
                )
            else:
                streamer_lines.append(f'        Streamer("{username}"),')

    streamers_block = "\n".join(streamer_lines) if streamer_lines else '        # Add your streamers here'

    followers = cfg.get("followers", False)
    followers_order = cfg.get("followers_order", "ASC").upper()
    if followers_order not in ("ASC", "DESC"):
        followers_order = "ASC"

    # Analytics
    acfg = cfg.get("analytics", {})
    analytics_enabled = acfg.get("enabled", False) or cfg.get("enable_analytics", False)
    analytics_line = ""
    if analytics_enabled:
        analytics_line = (
            f"twitch_miner.analytics("
            f"host=\"{acfg.get('host', '127.0.0.1')}\", "
            f"port={acfg.get('port', 5000)}, "
            f"refresh={acfg.get('refresh', 5)}, "
            f"days_ago={acfg.get('days_ago', 7)})"
        )

    source = textwrap.dedent(f"""\
        # -*- coding: utf-8 -*-
        # Auto-generated by export.py — compatible with upstream Twitch-Channel-Points-Miner-v2

        import logging
        from colorama import Fore
        from TwitchChannelPointsMiner import TwitchChannelPointsMiner
        from TwitchChannelPointsMiner.logger import LoggerSettings, ColorPalette
        from TwitchChannelPointsMiner.classes.Chat import ChatPresence
        from TwitchChannelPointsMiner.classes.Discord import Discord
        from TwitchChannelPointsMiner.classes.Webhook import Webhook
        from TwitchChannelPointsMiner.classes.Telegram import Telegram
        from TwitchChannelPointsMiner.classes.Matrix import Matrix
        from TwitchChannelPointsMiner.classes.Pushover import Pushover
        from TwitchChannelPointsMiner.classes.Gotify import Gotify
        from TwitchChannelPointsMiner.classes.Settings import Priority, Events, FollowersOrder
        from TwitchChannelPointsMiner.classes.entities.Bet import Strategy, BetSettings, Condition, OutcomeKeys, FilterCondition, DelayMode
        from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer, StreamerSettings

        twitch_miner = TwitchChannelPointsMiner(
            username={_q(_resolve_env(cfg.get('username', '')))},
            password={_q(_resolve_env(cfg.get('password', ''))) if cfg.get('password') else 'None'},
            claim_drops_startup={cfg.get('claim_drops_startup', False)},
            priority=[{priority_str}],
            enable_analytics={analytics_enabled},
            disable_ssl_cert_verification={cfg.get('disable_ssl_cert_verification', False)},
            disable_at_in_nickname={cfg.get('disable_at_in_nickname', False)},
            logger_settings=LoggerSettings(
                save={log_cfg.get('save', True)},
                console_level=logging.{console_level},
                console_username={log_cfg.get('console_username', False)},
                auto_clear={log_cfg.get('auto_clear', True)},
                time_zone={_q(log_cfg.get('time_zone', ''))},
                file_level=logging.{file_level},
                emoji={log_cfg.get('emoji', True)},
                less={log_cfg.get('less', False)},
                colored={log_cfg.get('colored', False)},
                color_palette=ColorPalette({palette_kwargs}),
                telegram={_gen_notification('telegram', telegram_cfg)},
                discord={_gen_notification('discord', discord_cfg)},
                webhook={_gen_notification('webhook', webhook_cfg)},
                matrix={_gen_notification('matrix', matrix_cfg)},
                pushover={_gen_notification('pushover', pushover_cfg)},
                gotify={_gen_notification('gotify', gotify_cfg)},
            ),
            streamer_settings={global_ss or 'StreamerSettings()'},
        )

        {"" if not analytics_line else analytics_line}

        twitch_miner.mine(
            [
        {streamers_block}
            ],
            followers={followers},
            followers_order=FollowersOrder.{followers_order},
        )
    """)

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(source)
    print(f"[export] Wrote {output_path}  (upstream-compatible)")


def main():
    parser = argparse.ArgumentParser(
        description="Export settings.json to a standalone run.py (upstream-compatible)"
    )
    parser.add_argument(
        "--config", "-c",
        default=os.environ.get("MINER_CONFIG", "settings.json"),
        help="Path to settings JSON file (default: settings.json)",
    )
    parser.add_argument(
        "--output", "-o",
        default="run.py",
        help="Output run.py path (default: run.py)",
    )
    args = parser.parse_args()
    cfg = load_settings(args.config)
    export(cfg, args.output)


if __name__ == "__main__":
    main()
