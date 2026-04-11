# Improvement Plan

Status of the previous 25-point roadmap and the next set of priorities.

## Completed

| # | Feature | Notes |
|---|---------|-------|
| 2 | Input validation for config editor | `_DANGEROUS_PATTERNS` regex in AnalyticsServer blocks `eval`, `exec`, `os.system`, etc. |
| 3 | Secrets management | `settings.json` supports `$ENV_VAR` references; `python-dotenv` loads `.env` at startup |
| 4 | Rate limiting for all notification providers | Shared `RateLimiter` class (token-bucket + exponential backoff with jitter). Applied to Discord, Telegram, Matrix, Pushover, Gotify. 429 detection auto-backs off. |
| 5 | WebSocket exponential backoff | `_BACKOFF_SCHEDULE = [5, 10, 30, 60, 60, 60]` with jitter. Counter carried across reconnects, reset on PONG. |
| 7 | Cross-streamer aggregate dashboard | `/api/global_stats` endpoint — total points, overall win rate, most profitable streamer. Rendered as stat cards above chart. |
| 8 | CSV/JSON analytics export | `/api/export/csv?streamer=X` and `/api/export/json?streamer=X` endpoints. Export buttons in dashboard toolbar. |
| 10 | Prediction confidence score | HISTORICAL strategy emits `confidence: 0.0–1.0` (sample size + margin + consistency). Logged on bet placement. |
| 11 | Mobile-responsive dashboard | Full CSS rewrite: CSS Grid layout, responsive breakpoints, sidebar collapses on mobile. Bulma removed. |
| 12 | Hot-reload settings.json | Polls every 60s in main loop. On change: re-parses JSON, rebuilds `StreamerSettings`/`BetSettings`, logs diff. No restart needed. |
| 13 | Dashboard overhaul | Modern card-based CSS, dark theme, stat cards, sidebar streamer list, sort dropdown, export buttons, global stats panel. |
| 14 | Streamer status indicators | ACTIVE / BEST badges rendered in the dashboard sidebar |
| 15 | Dark/light theme | Toggle with localStorage persistence in dashboard + config editor |
| 16 | Type hints | Annotations present on all public loader / builder functions |
| 25 | Health check endpoint | `/health` returns status, uptime, streamer count |

## Completed — Fork-Specific Features

| Feature | Description |
|---------|-------------|
| `settings.json` config system | Declarative JSON replaces manual `run.py` editing; auto-detected at startup |
| `main.py` entrypoint | Auto-detects `settings.json` → JSON mode, `run.py` → legacy exec, or `--config` flag |
| `export.py` | Generates upstream-compatible `run.py` from `settings.json`; gracefully downgrades fork-only strategies |
| Web config editor (dual-mode) | Reads/writes both `settings.json` (JSON) and `run.py` (Python); export button downloads run.py |
| `/api/config/export` | REST endpoint to generate and download an upstream-compatible `run.py` |
| 6 new prediction strategies | KELLY_CRITERION, CONTRARIAN, MOMENTUM, VALUE_BET, WEIGHTED_AVERAGE, UNDERDOG |
| Discord back-import | `cleanup_and_repost()`: fetches old webhook messages, parses predictions/raids/bets via regex, deletes plain-text, re-posts as rich embeds |
| Dockerfile rewrite | `python:3.12-slim-bookworm`, consolidated layers, HEALTHCHECK, COPY instead of ADD, removed unnecessary build deps |
| `.env` / `python-dotenv` | Secrets loaded from `.env` file before settings are parsed |
| Self-hosted CI | All GitHub Actions workflows changed from `ubuntu-latest` to `self-hosted` |

---

## Next Priorities

### 🔴 Critical

1. **Add unit tests** — `pytest` suite for `Bet.calculate` (all 20 strategies), `settings_loader` round-trip, `export` downgrade logic, filter conditions. Target: 80% coverage of `Bet.py` and `settings_loader.py`.

### 🟡 Medium — Features

2. **Discord slash commands** — `/status`, `/mute <channel>`, `/unmute <channel>`, `/strategy <channel> <strategy>`, `/export`. Requires a Discord Bot token (separate from webhook).

3. **Multi-account support** — `settings.json` accepts an `accounts[]` array; each account runs in its own thread with separate auth/WebSocket/analytics but shared process.

4. **Per-strategy time-series charts** — Extend dry-run data to include timestamps; render a line chart showing cumulative points per strategy over time.

5. **Notification digest mode** — Batch notifications into 30-minute digests. Configurable per provider via `"digest_interval_minutes": 30`.

### 🟢 Medium — Quality

6. **Structured JSON logging** — Optional `"log_format": "json"` in settings. Each log line becomes `{"ts":..., "level":..., "msg":..., "event":..., "streamer":...}`.

7. **Pin dependency versions** — Generate `requirements.lock` with exact versions. Keep `requirements.txt` as minimums for compatibility.

8. **Pre-commit: mypy + bandit** — Add `mypy --strict` and `bandit -r TwitchChannelPointsMiner/` to `.pre-commit-config.yaml`.

### 🔵 Lower Priority

9. **Replace `__slots__` with dataclasses** — Modernise `Bet`, `BetSettings`, `DryRunResult`, `EventPrediction`, `Streamer`, `StreamerSettings`.

10. **Plugin system for strategies** — `strategies/` folder; each `.py` file exports a `def choose(outcomes, settings) -> int`. Auto-discovered at startup.

11. **Twitch EventSub migration** — PubSub deprecated. Build EventSub WebSocket transport alongside PubSub; feature-flag to switch.

12. **Localization / i18n** — String keys for UI labels and Discord embed text. Language files in `locales/`.

13. **Import run.py → settings.json** — AST-based parser that extracts username, password, streamers, bet settings from a `run.py` and writes `settings.json`. Enables one-click migration in web UI.
