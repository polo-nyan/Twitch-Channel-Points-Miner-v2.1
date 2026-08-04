---
title: Twitch-Channel-Points-Miner-v2.1 doc drift report
scope: Twitch-Channel-Points-Miner-v2.1
tags:
- drift
- harvest
- auto
summary: 5 stale doc claim(s) found by docharvest 2026-07-23
related: []
web: []
---

Doc claims whose referenced paths no longer exist — the doc is likely stale (facts NOT saved to memory):

- `IMPROVEMENTS.md`: Settings config: settings.json with $ENV_VAR substitution, .env loaded via python-dotenv before parse, 60s hot-reload polling rebuilds Strea — missing: settings.json
- `IMPROVEMENTS.md`: Prediction strategies: 6 fork-specific (KELLY_CRITERION, CONTRARIAN, MOMENTUM, VALUE_BET, WEIGHTED_AVERAGE, UNDERDOG) with confidence 0.0–1. — missing: Bet.py
- `README.md`: SQLite telemetry database at analytics/telemetry.db persists all predictions, points events, and session data with full historical recall fo — missing: analytics/telemetry.db
- `README.md`: Config hot-reload: miner watches settings.json for changes and applies them without restarting the application. — missing: settings.json
- `README.md`: Web config editor allows editing settings.json directly in browser with visual form fields, syntax validation, save with backup, and revert  — missing: settings.json
