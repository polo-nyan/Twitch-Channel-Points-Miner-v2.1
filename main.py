#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py  –  Auto-eval entrypoint for the Twitch Channel Points Miner fork.

Resolution order:
  1. If --config / MINER_CONFIG points to a valid JSON → use settings_loader
  2. If settings.json exists in cwd → use settings_loader
  3. If run.py exists in cwd → convert it to settings.json via AST parser (no exec)
  4. Otherwise → error with instructions

Usage:
    python main.py                       # auto-detect settings.json or run.py
    python main.py --config my.json      # explicit JSON path
    MINER_CONFIG=my.json python main.py  # env-var override
"""

import argparse
import os
import sys

# Load .env file if present (for secrets like $TWITCH_PASSWORD)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _run_from_json(config_path):
    from settings_loader import (
        build_analytics_kwargs,
        build_mine_kwargs,
        build_miner_kwargs,
        load_settings,
    )
    from TwitchChannelPointsMiner import TwitchChannelPointsMiner

    cfg = load_settings(config_path)
    twitch_miner = TwitchChannelPointsMiner(**build_miner_kwargs(cfg))

    analytics_kwargs = build_analytics_kwargs(cfg)
    if analytics_kwargs:
        twitch_miner.analytics(**analytics_kwargs)

    twitch_miner.mine(**build_mine_kwargs(cfg))


def _run_from_runpy(run_path):
    """Convert a legacy run.py to settings.json via AST (no exec) and run from that."""
    print(f"[main] No settings.json found. Converting {run_path} → settings.json via AST parser (safe, no code execution).")
    from runpy_converter import convert_file
    try:
        cfg = convert_file(run_path, "settings.json")
    except SyntaxError as exc:
        print(f"[main] Failed to parse {run_path}: {exc}")
        sys.exit(1)
    print(f"[main] Generated settings.json for user '{cfg.get('username', '?')}' "
          f"(followers={cfg.get('followers', False)}, streamers={len(cfg.get('streamers', []))})")
    print("[main] Note: password removed from config — auth uses cookies from cookies/ directory.")
    _run_from_json("settings.json")


def main():
    parser = argparse.ArgumentParser(description="Twitch Channel Points Miner v2.1")
    parser.add_argument(
        "--config", "-c",
        default=os.environ.get("MINER_CONFIG", ""),
        help="Path to settings JSON file (default: auto-detect)",
    )
    args = parser.parse_args()

    # 1. Explicit config path provided
    if args.config:
        if os.path.isfile(args.config):
            _run_from_json(args.config)
            return
        else:
            print(f"[main] Config file not found: {args.config}")
            sys.exit(1)

    # 2. settings.json in cwd
    if os.path.isfile("settings.json"):
        _run_from_json("settings.json")
        return

    # 3. Fallback to run.py (mounted from original project)
    if os.path.isfile("run.py"):
        _run_from_runpy("run.py")
        return

    # 4. Nothing found — start the web setup wizard instead of hard-exiting.
    #    This handles Dokploy / Docker environments where single-file mounts fail.
    from setup_wizard import run_setup_wizard
    run_setup_wizard()
    # run_setup_wizard() only returns if Flask exits unexpectedly (e.g. port in use)
    sys.exit(1)


if __name__ == "__main__":
    main()
