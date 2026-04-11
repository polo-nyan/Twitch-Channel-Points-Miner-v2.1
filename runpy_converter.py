# -*- coding: utf-8 -*-
"""
runpy_converter.py — Safe AST-based converter from run.py to settings.json.

Parses a legacy run.py without executing it, extracts configuration data
using Python's AST module, and writes a settings.json file.

Usage:
    python runpy_converter.py                     # run.py → settings.json
    python runpy_converter.py -i my_run.py -o my_settings.json
"""

import ast
import json
import logging
import os
import sys

# Mapping from Python enum references to JSON string values
_ENUM_MAP = {
    # Priority
    "Priority.STREAK": "STREAK",
    "Priority.DROPS": "DROPS",
    "Priority.ORDER": "ORDER",
    "Priority.POINTS_ASCENDING": "POINTS_ASCENDING",
    "Priority.POINTS_DESCENDING": "POINTS_DESCENDING",
    # Strategy
    "Strategy.SMART": "SMART",
    "Strategy.MOST_VOTED": "MOST_VOTED",
    "Strategy.HIGH_ODDS": "HIGH_ODDS",
    "Strategy.PERCENTAGE": "PERCENTAGE",
    "Strategy.SMART_MONEY": "SMART_MONEY",
    "Strategy.NUMBER_1": "NUMBER_1",
    "Strategy.NUMBER_2": "NUMBER_2",
    "Strategy.NUMBER_3": "NUMBER_3",
    "Strategy.NUMBER_4": "NUMBER_4",
    "Strategy.NUMBER_5": "NUMBER_5",
    "Strategy.NUMBER_6": "NUMBER_6",
    "Strategy.NUMBER_7": "NUMBER_7",
    "Strategy.NUMBER_8": "NUMBER_8",
    "Strategy.HISTORICAL": "HISTORICAL",
    "Strategy.KELLY_CRITERION": "KELLY_CRITERION",
    "Strategy.CONTRARIAN": "CONTRARIAN",
    "Strategy.MOMENTUM": "MOMENTUM",
    "Strategy.VALUE_BET": "VALUE_BET",
    "Strategy.WEIGHTED_AVERAGE": "WEIGHTED_AVERAGE",
    "Strategy.UNDERDOG": "UNDERDOG",
    # Condition
    "Condition.GT": "GT",
    "Condition.LT": "LT",
    "Condition.GTE": "GTE",
    "Condition.LTE": "LTE",
    # OutcomeKeys
    "OutcomeKeys.PERCENTAGE_USERS": "PERCENTAGE_USERS",
    "OutcomeKeys.ODDS_PERCENTAGE": "ODDS_PERCENTAGE",
    "OutcomeKeys.ODDS": "ODDS",
    "OutcomeKeys.TOP_POINTS": "TOP_POINTS",
    "OutcomeKeys.TOTAL_USERS": "TOTAL_USERS",
    "OutcomeKeys.TOTAL_POINTS": "TOTAL_POINTS",
    "OutcomeKeys.DECISION_USERS": "DECISION_USERS",
    "OutcomeKeys.DECISION_POINTS": "DECISION_POINTS",
    # DelayMode
    "DelayMode.FROM_END": "FROM_END",
    "DelayMode.FROM_START": "FROM_START",
    # ChatPresence
    "ChatPresence.ALWAYS": "ALWAYS",
    "ChatPresence.NEVER": "NEVER",
    "ChatPresence.ONLINE": "ONLINE",
    "ChatPresence.OFFLINE": "OFFLINE",
    # Events
    "Events.BET_GENERAL": "BET_GENERAL",
    "Events.BET_FAILED": "BET_FAILED",
    "Events.BET_START": "BET_START",
    "Events.BET_WIN": "BET_WIN",
    "Events.BET_LOSE": "BET_LOSE",
    "Events.BET_REFUND": "BET_REFUND",
    "Events.BET_FILTERS": "BET_FILTERS",
    "Events.STREAMER_ONLINE": "STREAMER_ONLINE",
    "Events.STREAMER_OFFLINE": "STREAMER_OFFLINE",
    "Events.GAIN_FOR_RAID": "GAIN_FOR_RAID",
    "Events.GAIN_FOR_CLAIM": "GAIN_FOR_CLAIM",
    "Events.GAIN_FOR_WATCH": "GAIN_FOR_WATCH",
    "Events.CHAT_MENTION": "CHAT_MENTION",
    "Events.DROP_CLAIM": "DROP_CLAIM",
    "Events.DROP_STATUS": "DROP_STATUS",
    "Events.JOIN_RAID": "JOIN_RAID",
    "Events.COMMUNITY_GOAL_CONTRIBUTION": "COMMUNITY_GOAL_CONTRIBUTION",
    "Events.MOMENT_CLAIM": "MOMENT_CLAIM",
    # FollowersOrder
    "FollowersOrder.ASC": "ASC",
    "FollowersOrder.DESC": "DESC",
    # logging levels
    "logging.DEBUG": "DEBUG",
    "logging.INFO": "INFO",
    "logging.WARNING": "WARNING",
    "logging.ERROR": "ERROR",
    "logging.CRITICAL": "CRITICAL",
}

# Colorama Fore.X → string
_FORE_MAP = {
    "Fore.BLACK": "BLACK",
    "Fore.RED": "RED",
    "Fore.GREEN": "GREEN",
    "Fore.YELLOW": "YELLOW",
    "Fore.BLUE": "BLUE",
    "Fore.MAGENTA": "MAGENTA",
    "Fore.CYAN": "CYAN",
    "Fore.WHITE": "WHITE",
    "Fore.RESET": "RESET",
}


def _eval_node(node):
    """Safely evaluate an AST node to a Python literal or enum string.

    Supports: strings, numbers, bools, None, lists, dicts, enum references,
    constructor calls (e.g. BetSettings(...), Discord(...)).
    Rejects anything else.
    """
    if node is None:
        return None

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.List):
        return [_eval_node(el) for el in node.elts]

    if isinstance(node, ast.Tuple):
        return [_eval_node(el) for el in node.elts]

    if isinstance(node, ast.Dict):
        result = {}
        for k, v in zip(node.keys, node.values):
            key = _eval_node(k)
            result[key] = _eval_node(v)
        return result

    if isinstance(node, ast.Set):
        return [_eval_node(el) for el in node.elts]

    # Handle NameConstant for older Python AST compat (< 3.8)
    if hasattr(ast, "NameConstant") and isinstance(node, ast.NameConstant):
        return node.value

    # Handle Num/Str for older AST (< 3.8)
    if hasattr(ast, "Num") and isinstance(node, ast.Num):
        return node.n
    if hasattr(ast, "Str") and isinstance(node, ast.Str):
        return node.s

    # Unary operator (e.g. -1)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _eval_node(node.operand)
        if isinstance(val, (int, float)):
            return -val

    # Name reference (True, False, None)
    if isinstance(node, ast.Name):
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id == "None":
            return None
        # Could be a variable reference - return as string marker
        return f"${node.id}"

    # Attribute access: e.g. Strategy.SMART, Events.BET_WIN, logging.INFO, Fore.MAGENTA
    if isinstance(node, ast.Attribute):
        attr_str = _attr_to_string(node)
        if attr_str in _ENUM_MAP:
            return _ENUM_MAP[attr_str]
        if attr_str in _FORE_MAP:
            return _FORE_MAP[attr_str]
        return attr_str

    # Function/constructor call: BetSettings(...), Discord(...), etc.
    if isinstance(node, ast.Call):
        return _eval_call(node)

    return f"<unparsed:{ast.dump(node)[:80]}>"


def _attr_to_string(node):
    """Convert an ast.Attribute chain to dotted string like 'Strategy.SMART'."""
    if isinstance(node, ast.Attribute):
        parent = _attr_to_string(node.value)
        return f"{parent}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return "?"


def _eval_call(node):
    """Evaluate a constructor call like BetSettings(...) or Discord(...)."""
    func_name = ""
    if isinstance(node.func, ast.Name):
        func_name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        func_name = _attr_to_string(node.func)

    kwargs = {}
    for kw in node.keywords:
        if kw.arg is not None:
            kwargs[kw.arg] = _eval_node(kw.value)

    # Also handle positional args for Streamer("name", ...) and FilterCondition
    args = [_eval_node(a) for a in node.args]

    return _build_json_block(func_name, args, kwargs)


def _build_json_block(func_name, args, kwargs):
    """Convert a constructor call to its JSON representation."""

    if func_name == "TwitchChannelPointsMiner":
        return {"__type__": "miner", **kwargs}

    if func_name == "LoggerSettings":
        return {"__type__": "logger", **kwargs}

    if func_name == "ColorPalette":
        # Normalize keys to uppercase
        return {k.upper(): v for k, v in kwargs.items()}

    if func_name == "StreamerSettings":
        return {"__type__": "streamer_settings", **kwargs}

    if func_name == "BetSettings":
        return kwargs

    if func_name == "FilterCondition":
        return kwargs

    if func_name == "Streamer":
        username = args[0] if args else kwargs.get("username", "")
        result = {"username": username}
        if "settings" in kwargs:
            result["settings"] = kwargs["settings"]
        return result

    # Notification providers
    if func_name == "Discord":
        return {"__type__": "discord", **kwargs}
    if func_name == "Telegram":
        return {"__type__": "telegram", **kwargs}
    if func_name == "Webhook":
        return {"__type__": "webhook", **kwargs}
    if func_name == "Matrix":
        return {"__type__": "matrix", **kwargs}
    if func_name == "Pushover":
        return {"__type__": "pushover", **kwargs}
    if func_name == "Gotify":
        return {"__type__": "gotify", **kwargs}

    # FollowersOrder is handled via enum map, but just in case
    return {"__type__": func_name, **kwargs}


def _strip_type(d):
    """Remove internal __type__ keys recursively."""
    if isinstance(d, dict):
        return {k: _strip_type(v) for k, v in d.items() if k != "__type__"}
    if isinstance(d, list):
        return [_strip_type(i) for i in d]
    return d


def _extract_miner_and_mine(source: str):
    """Parse run.py source and extract the miner constructor kwargs and mine() kwargs."""
    tree = ast.parse(source)

    miner_kwargs = {}
    mine_kwargs = {}
    analytics_kwargs = {}
    miner_var = None

    for node in ast.walk(tree):
        # Look for: twitch_miner = TwitchChannelPointsMiner(...)
        if isinstance(node, ast.Assign):
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
            ):
                call = node.value
                func_name = ""
                if isinstance(call.func, ast.Name):
                    func_name = call.func.id
                elif isinstance(call.func, ast.Attribute):
                    func_name = _attr_to_string(call.func)

                if func_name == "TwitchChannelPointsMiner":
                    miner_var = node.targets[0].id
                    miner_kwargs = _eval_call(call)

        # Look for: twitch_miner.analytics(...) and twitch_miner.mine(...)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute):
                if (
                    isinstance(call.func.value, ast.Name)
                    and call.func.value.id == miner_var
                ):
                    method = call.func.attr
                    if method == "mine":
                        for kw in call.keywords:
                            if kw.arg is not None:
                                mine_kwargs[kw.arg] = _eval_node(kw.value)
                        # Handle positional: mine([...], ...)
                        if call.args:
                            mine_kwargs["streamers"] = _eval_node(call.args[0])
                    elif method == "analytics":
                        for kw in call.keywords:
                            if kw.arg is not None:
                                analytics_kwargs[kw.arg] = _eval_node(kw.value)
                        # Positional args for analytics(host, port, refresh, days_ago)
                        positional_keys = ["host", "port", "refresh", "days_ago"]
                        for i, arg in enumerate(call.args):
                            if i < len(positional_keys):
                                analytics_kwargs[positional_keys[i]] = _eval_node(arg)

    return miner_kwargs, mine_kwargs, analytics_kwargs


def _normalise_streamer(entry):
    """Normalise a streamer entry: string stays string, dict gets cleaned."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        result = {"username": entry.get("username", "")}
        settings = entry.get("settings")
        if settings and isinstance(settings, dict):
            s = _strip_type(settings)
            # Clean bet sub-object
            if "bet" in s and isinstance(s["bet"], dict):
                s["bet"] = _strip_type(s["bet"])
                if "filter_condition" in s["bet"]:
                    s["bet"]["filter_condition"] = _strip_type(s["bet"]["filter_condition"])
            result["settings"] = s
        return result
    return str(entry)


def _build_notification_block(logger_data, kind):
    """Extract a notification provider block from the logger data."""
    block = logger_data.get(kind)
    if not block or not isinstance(block, dict):
        return None
    return _strip_type(block)


def convert(source: str) -> dict:
    """Convert run.py source code to a settings.json dict."""
    miner_raw, mine_raw, analytics_raw = _extract_miner_and_mine(source)

    cfg = {}

    # Top-level miner settings
    cfg["username"] = miner_raw.get("username", "")
    # Don't store password — cookies handle auth now
    cfg["claim_drops_startup"] = miner_raw.get("claim_drops_startup", False)
    cfg["enable_analytics"] = miner_raw.get("enable_analytics", False)
    cfg["disable_ssl_cert_verification"] = miner_raw.get("disable_ssl_cert_verification", False)
    cfg["disable_at_in_nickname"] = miner_raw.get("disable_at_in_nickname", False)

    # Priority
    priority = miner_raw.get("priority", ["STREAK", "DROPS", "ORDER"])
    if isinstance(priority, list):
        cfg["priority"] = priority
    else:
        cfg["priority"] = ["STREAK", "DROPS", "ORDER"]

    # Logger settings
    logger_data = miner_raw.get("logger_settings", {})
    if isinstance(logger_data, dict):
        logger_cfg = {}
        for key in ["save", "less", "console_level", "console_username",
                     "time_zone", "file_level", "emoji", "colored", "auto_clear"]:
            if key in logger_data:
                logger_cfg[key] = logger_data[key]

        if "color_palette" in logger_data:
            logger_cfg["color_palette"] = _strip_type(logger_data["color_palette"])

        cfg["logger"] = logger_cfg

        # Notification providers — extract from logger block
        for kind in ["telegram", "discord", "webhook", "matrix", "pushover", "gotify"]:
            block = _build_notification_block(logger_data, kind)
            if block:
                cfg[kind] = block
    else:
        cfg["logger"] = {}

    # Streamer settings
    streamer_settings = miner_raw.get("streamer_settings", {})
    if isinstance(streamer_settings, dict):
        ss = _strip_type(streamer_settings)
        if "bet" in ss and isinstance(ss["bet"], dict):
            ss["bet"] = _strip_type(ss["bet"])
            if "filter_condition" in ss["bet"]:
                ss["bet"]["filter_condition"] = _strip_type(ss["bet"]["filter_condition"])
        cfg["streamer_settings"] = ss
    else:
        cfg["streamer_settings"] = {}

    # Analytics
    if analytics_raw or cfg.get("enable_analytics"):
        acfg = {"enabled": True}
        for key in ["host", "port", "refresh", "days_ago"]:
            if key in analytics_raw:
                acfg[key] = analytics_raw[key]
        cfg["analytics"] = acfg

    # Streamers from mine()
    raw_streamers = mine_raw.get("streamers", [])
    if isinstance(raw_streamers, list):
        cfg["streamers"] = [_normalise_streamer(s) for s in raw_streamers]
    else:
        cfg["streamers"] = []

    # Followers
    cfg["followers"] = mine_raw.get("followers", False)
    cfg["followers_order"] = mine_raw.get("followers_order", "ASC")

    return cfg


def convert_file(input_path: str, output_path: str):
    """Read a run.py, convert to settings.json, and write it."""
    with open(input_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    cfg = convert(source)

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=4, ensure_ascii=False)

    return cfg


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Convert a legacy run.py to settings.json (no code execution)"
    )
    parser.add_argument("-i", "--input", default="run.py", help="Input run.py path")
    parser.add_argument("-o", "--output", default="settings.json", help="Output settings.json path")
    parser.add_argument("--stdout", action="store_true", help="Print JSON to stdout instead of writing file")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as fh:
        source = fh.read()

    cfg = convert(source)

    if args.stdout:
        print(json.dumps(cfg, indent=4, ensure_ascii=False))
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=4, ensure_ascii=False)
        print(f"Converted {args.input} → {args.output}")
        print(f"  Username: {cfg.get('username', '?')}")
        print(f"  Streamers: {len(cfg.get('streamers', []))}")
        print(f"  Followers: {cfg.get('followers', False)}")
        print(f"  Analytics: {bool(cfg.get('analytics'))}")
        print(f"  Note: Password removed — auth uses cookies from cookies/ directory")


if __name__ == "__main__":
    main()
