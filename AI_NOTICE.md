# AI-Assisted Development Notice

## Overview

This repository (`polo-nyan/Twitch-Channel-Points-Miner-v2.1`) is a fork that includes commits generated with the assistance of **AI coding agents** (GitHub Copilot). This document provides transparency about how AI tools have been used in the development of this project.

## What This Means

- **AI-Generated Code**: Some commits in this repository were authored by AI coding agents. These commits are identifiable by the author `copilot-swe-agent[bot]`.
- **Human Review**: All AI-generated changes have been reviewed, but may not have been manually tested in every possible edge case or production scenario.
- **No Guarantee of Correctness**: As with all software, AI-generated code may contain bugs or unexpected behavior. The standard open-source disclaimers apply (see [LICENSE](LICENSE)).

## AI-Assisted Features

The following features were developed with AI assistance:

1. **Dry-Run Strategy Comparison** — Shadow-evaluates all prediction strategies and scores them retroactively
2. **HISTORICAL Prediction Strategy** — Weighs historical outcomes with current odds for prediction decisions
3. **Discord Rich Embeds** — Upgraded Discord notifications with event-specific colors, icons, and embed formatting
4. **Discord Mute System** — Per-channel and per-event notification muting
5. **Web Config Editor** — Browser-based config editing with syntax validation
6. **README Restructuring** — Updated documentation to reflect the fork

## Recommendations

- **Review Before Deploying**: Carefully review any recent changes before running in a production environment
- **Report Issues**: If you find bugs or unexpected behavior, please open an issue
- **Verify Configuration**: After using the web config editor, always validate your configuration works as expected

## Identifying AI Commits

AI-generated commits can be identified by:
- Author: `copilot-swe-agent[bot]`
- Commit messages often follow conventional commit format (e.g., `feat:`, `fix:`)
- Commits may include `Agent-Logs-Url` in the commit body

## Questions?

If you have concerns about the AI-assisted nature of this code, please open an issue for discussion.
