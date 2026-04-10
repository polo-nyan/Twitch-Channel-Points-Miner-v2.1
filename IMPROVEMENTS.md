# 25-Point Improvement Plan

A roadmap of 25 actionable improvements for `Twitch-Channel-Points-Miner-v2.1`.

## 🔴 Critical / High Priority

1. **Add unit tests** — Create a test suite covering core logic (`Bet.calculate`, `Strategy` selection, `DryRunResult` scoring, filter conditions). Use `pytest` with fixtures.

2. **Input validation for config editor** — The web config editor currently accepts any Python code; add sandboxed validation that checks for dangerous imports (`os.system`, `subprocess`, `eval`) before saving.

3. **Secrets management** — Move sensitive values (webhook URLs, Twitch credentials) out of `run.py` into environment variables or a `.env` file with `python-dotenv`.

4. **Rate limiting for Discord/Telegram** — Add proper rate-limit handling with exponential backoff for all notification providers, not just Discord.

5. **Error recovery for WebSocket disconnects** — Improve reconnection logic to handle edge cases (partial subscriptions, stale topics after long disconnects).

## 🟡 Medium Priority — Features

6. **File watcher for config hot-reload** — Use `watchdog` library to detect `run.py` changes on disk and trigger a validated reload without restarting the miner.

7. **Discord slash commands** — Add a Discord bot that responds to commands like `/status`, `/mute <channel>`, `/unmute <channel>`, `/stats` for interactive control.

8. **Prediction confidence scoring** — Extend the HISTORICAL strategy to output a confidence score (0–100%) based on sample size and consistency of historical data.

9. **Multi-account support** — Allow running multiple Twitch accounts from a single process with separate config sections and shared analytics.

10. **Export analytics to CSV/JSON** — Add an API endpoint and UI button to export streamer analytics data for external analysis.

## 🟢 Medium Priority — UX / Quality

11. **Mobile-responsive web UI** — The current dashboard is optimized for desktop; add responsive breakpoints and touch-friendly controls.

12. **Notification grouping / digest mode** — Instead of sending individual Discord messages, batch notifications into periodic digest summaries (e.g., every 30 minutes).

13. **Dashboard global stats panel** — Show aggregate stats across all streamers: total points earned, overall win rate, most profitable streamer, time mining.

14. **Streamer status indicators** — Add online/offline badges next to streamer names in the dashboard sidebar.

15. **Dark/light theme for config editor** — The config editor is dark-only; add a light theme toggle matching the main dashboard.

## 🔵 Lower Priority — Technical Debt

16. **Type hints throughout codebase** — Add Python type annotations to all public methods for better IDE support and static analysis.

17. **Replace `__slots__` with dataclasses** — Modernize entity classes (`Bet`, `BetSettings`, `DryRunResult`, `EventPrediction`, `Streamer`) using `@dataclass`.

18. **Logging structured data** — Switch from string concatenation in log messages to structured logging (JSON format option) for better log aggregation.

19. **Pin dependency versions** — `requirements.txt` has unpinned dependencies; pin exact versions and add `requirements-dev.txt` for development tools.

20. **Pre-commit hooks** — Extend `.pre-commit-config.yaml` to include `mypy` type checking and security scanning (`bandit`).

## ⚪ Nice to Have

21. **Twitch EventSub migration** — Twitch PubSub is deprecated; begin migration to EventSub WebSocket for future-proofing.

22. **Plugin system for strategies** — Allow users to define custom strategies as Python modules in a `strategies/` folder without modifying core code.

23. **Historical data visualization** — Add time-series charts showing strategy performance over time (not just current totals).

24. **Localization / i18n** — Support multiple languages for the web UI and Discord notifications.

25. **Health check endpoint** — Add a `/health` API endpoint that returns miner status, uptime, connected streamers, and WebSocket health for monitoring tools (e.g., Uptime Kuma).
