---
title: Twitch-Channel-Points-Miner-v2.1 code map
scope: Twitch-Channel-Points-Miner-v2.1
tags:
- code-map
- mermaid
- structure
- auto
summary: 'Code map: Twitch-Channel-Points-Miner-v2.1 — 31 components, 40 call-dependencies
  (top); core: utils.py, RateLimiter.py, Bet.py, Chat.py'
related: []
web: []
---

Code map: Twitch-Channel-Points-Miner-v2.1 — 31 components, 40 call-dependencies (top); core: utils.py, RateLimiter.py, Bet.py, Chat.py

_Auto-generated (deterministic, no AI) from the symbol index._

## Core components (PageRank — most depended-upon)
- `utils.py` — 0.052
- `RateLimiter.py` — 0.050
- `Bet.py` — 0.029
- `Chat.py` — 0.028
- `Stream.py` — 0.025
- `Exceptions.py` — 0.024
- `script.js` — 0.023
- `Streamer.py` — 0.022
- `CommunityGoal.py` — 0.022
- `Drop.py` — 0.022
- `TwitchChannelPointsMiner` — 0.022
- `RateLimiter` — 0.021

## Call dependencies (who calls whom)
```mermaid
graph LR
  n_AnalyticsServer["AnalyticsServer"]
  n_AnalyticsServer_py["AnalyticsServer.py"]
  n_Bet["Bet"]
  n_Bet_py["Bet.py"]
  n_Campaign["Campaign"]
  n_ClientIRC["ClientIRC"]
  n_Discord["Discord"]
  n_Discord_py["Discord.py"]
  n_DryRunResult["DryRunResult"]
  n_EventPrediction["EventPrediction"]
  n_GlobalFormatter["GlobalFormatter"]
  n_PubsubTopic_py["PubsubTopic.py"]
  n_RateLimiter["RateLimiter"]
  n_Stream["Stream"]
  n_Streamer["Streamer"]
  n_Streamer_py["Streamer.py"]
  n_Telemetry["Telemetry"]
  n_Telemetry_py["Telemetry.py"]
  n_Twitch["Twitch"]
  n_TwitchChannelPointsMiner["TwitchChannelPointsMiner"]
  n_TwitchLogin["TwitchLogin"]
  n_TwitchWebSocket["TwitchWebSocket"]
  n_WebSocketsPool["WebSocketsPool"]
  n_example_py["example.py"]
  n_export_py["export.py"]
  n_logger_py["logger.py"]
  n_main_py["main.py"]
  n_runpy_converter_py["runpy_converter.py"]
  n_script_js["script.js"]
  n_settings_loader_py["settings_loader.py"]
  n_utils_py["utils.py"]
  n_AnalyticsServer -->|58| n_AnalyticsServer_py
  n_AnalyticsServer -->|31| n_Telemetry
  n_Discord -->|28| n_RateLimiter
  n_WebSocketsPool -->|14| n_Streamer
  n_example_py -->|13| n_Streamer_py
  n_AnalyticsServer -->|12| n_Telemetry_py
  n_AnalyticsServer -->|11| n_Discord
  n_TwitchChannelPointsMiner -->|10| n_utils_py
  n_example_py -->|10| n_Bet_py
  n_Twitch -->|9| n_TwitchLogin
  n_TwitchChannelPointsMiner -->|8| n_Twitch
  n_TwitchChannelPointsMiner -->|8| n_WebSocketsPool
  n_Twitch -->|8| n_Stream
  n_TwitchChannelPointsMiner -->|7| n_PubsubTopic_py
  n_AnalyticsServer -->|7| n_RateLimiter
  n_Twitch -->|7| n_Streamer
  n_runpy_converter_py -->|6| n_TwitchChannelPointsMiner
  n_WebSocketsPool -->|5| n_Twitch
  n_Twitch -->|5| n_utils_py
  n_WebSocketsPool -->|5| n_TwitchWebSocket
  n_Bet -->|5| n_Bet_py
  n_Twitch -->|4| n_Bet
  n_Twitch -->|4| n_Campaign
  n_main_py -->|4| n_settings_loader_py
  n_TwitchChannelPointsMiner --> n_ClientIRC
  n_WebSocketsPool --> n_ClientIRC
  n_Streamer --> n_Streamer_py
  n_EventPrediction --> n_utils_py
  n_Streamer --> n_utils_py
  n_AnalyticsServer --> n_script_js
  n_AnalyticsServer --> n_DryRunResult
  n_runpy_converter_py --> n_Discord_py
  n_WebSocketsPool --> n_EventPrediction
  n_export_py --> n_Bet_py
  n_Bet --> n_utils_py
  n_GlobalFormatter --> n_logger_py
  n_TwitchChannelPointsMiner --> n_logger_py
  n_example_py --> n_logger_py
  n_export_py --> n_logger_py
  n_settings_loader_py --> n_logger_py
```

## Largest components (by member count)
- `Twitch` — 32 members
- `Telemetry` — 28 members
- `Bet` — 24 members
- `Streamer` — 21 members
- `Discord` — 18 members
- `WebSocketsPool` — 13 members
- `Stream` — 13 members
- `TwitchLogin` — 12 members
- `GlobalFormatter` — 9 members
- `TwitchChannelPointsMiner` — 8 members
- `EventPrediction` — 7 members
- `TwitchWebSocket` — 6 members
- `Campaign` — 6 members
- `CommunityGoal` — 6 members
- `Drop` — 6 members
