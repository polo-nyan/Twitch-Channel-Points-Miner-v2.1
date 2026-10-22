# -*- coding: utf-8 -*-
"""Tests for classes/BadgeGames.py: the streamdatabase badge-game list
(parser, daily fetch, cache and fallback) and the per-channel discovery with
its request budget."""

import json
import logging
import os
import random
from datetime import datetime, timezone

from conftest import ROOT, load_module

bg = load_module(
    "TwitchChannelPointsMiner.classes.BadgeGames",
    "TwitchChannelPointsMiner/classes/BadgeGames.py",
)

with open(os.path.join(ROOT, "tests", "fixtures", "streamdatabase_global_badges.html")) as f:
    PAGE = f.read()

NOW = datetime(2026, 10, 22, 13, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400.0


# --------------------------------- parser --------------------------------- #
def test_parser_on_the_saved_page_keeps_free_current_watch_badges():
    # Fixture: 11 real records. Current + free + "watch": Witchspire (2),
    # Alien Isolation, Dragon's Dogma 2. Ended (RuneScape, PAYDAY 3, D20,
    # Streamer Awards, Cyberpunk event), paid (Wolf Medallion) and removed
    # (lead moderator) badges are dropped.
    assert bg.parse_badge_games(PAGE, NOW) == [
        "Alien Isolation", "Dragon's Dogma 2: Dark Arisen", "Witchspire"]


def test_game_name_patterns():
    assert bg.game_name("earned by watching a streamer in the PAYDAY 3 category for 60 minutes") == "PAYDAY 3"
    assert bg.game_name("earned by watching 30 minutes of RuneScape: Dragonwilds category") == "RuneScape: Dragonwilds"
    assert bg.game_name("earned by watching Dragon's Dogma 2: Dark Arisen for 1 hour") == "Dragon's Dogma 2: Dark Arisen"
    assert bg.game_name("Watch 1 hour of Roblox content on Twitch") == "Roblox"
    assert bg.game_name("earned by subscribing to a streamer in The Witcher 3 category") is None
    assert bg.game_name("earned by watching and voting on the 2026 Streamer Awards!") is None


def test_parser_rejects_a_page_without_data():
    try:
        bg.parse_badge_games("<html>changed layout</html>", NOW)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ------------------------------ fetch + cache ------------------------------ #
class FakeResponse(object):
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class FakeSession(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def game_list(tmp_path, session, now):
    t = {"now": now}
    lst = bg.BadgeGameList(str(tmp_path / "analytics" / bg.CACHE_FILE), session=session,
                           clock=lambda: t["now"])
    return lst, t


def test_fetch_once_a_day_with_an_honest_user_agent(tmp_path):
    session = FakeSession([FakeResponse(200, PAGE), FakeResponse(200, PAGE)])
    lst, t = game_list(tmp_path, session, NOW)
    assert len(lst.games()) == 3
    t["now"] += 12 * 3600
    assert len(lst.games()) == 3  # cached, no second fetch
    assert len(session.calls) == 1
    url, headers = session.calls[0]
    assert url == bg.SOURCE_URL and "polo-nyan/Twitch-Channel-Points-Miner" in headers["User-Agent"]
    t["now"] += 12 * 3600 + 1
    lst.games()
    assert len(session.calls) == 2  # a day later


def test_failed_fetch_keeps_the_cache_and_warns_and_does_not_retry_at_once(tmp_path, caplog):
    session = FakeSession([FakeResponse(200, PAGE), ConnectionError("down"),
                           FakeResponse(200, "<html>new layout</html>")])
    lst, t = game_list(tmp_path, session, NOW)
    lst.games()
    t["now"] += DAY
    with caplog.at_level(logging.WARNING, logger=bg.__name__):
        assert len(lst.games()) == 3  # network error: cache
        assert len(lst.games()) == 3  # no retry the same day
        t["now"] += DAY
        assert len(lst.games()) == 3  # parse error: cache
    assert len(session.calls) == 3
    assert sum("fetch failed" in r.getMessage() for r in caplog.records) == 2


def test_cache_survives_a_restart(tmp_path):
    lst, _ = game_list(tmp_path, FakeSession([FakeResponse(200, PAGE)]), NOW)
    lst.games()
    again, _ = game_list(tmp_path, FakeSession([]), NOW + 3600)  # no fetch allowed
    assert again.games() == ["Alien Isolation", "Dragon's Dogma 2: Dark Arisen", "Witchspire"]
    raw = json.load(open(tmp_path / "analytics" / bg.CACHE_FILE))
    assert raw["fetched_at"] == NOW and raw["games"][2] == "Witchspire"


def test_no_cache_and_failed_fetch_gives_an_empty_list(tmp_path):
    lst, _ = game_list(tmp_path, FakeSession([FakeResponse(503, "")]), NOW)
    assert lst.games() == []
    assert lst.fetched_text() == "never"


# ----------------------------- discovery budget ---------------------------- #
class Probe(object):
    """Counts every request a discovery makes."""

    def __init__(self, channels=8, campaigns=("c1",)):
        self.calls = []
        self.channels, self.campaigns = channels, campaigns

    def resolve(self, name):
        self.calls.append(("game", name))
        return name.lower().replace(" ", "-")

    def directory(self, slug):
        self.calls.append(("dir", slug))
        return [(f"{slug}-{i}", f"{slug}login{i}") for i in range(self.channels)]

    def channel(self, channel_id):
        self.calls.append(("chan", channel_id))
        return list(self.campaigns)


def test_discovery_stays_within_the_request_caps(tmp_path):
    lst, _ = game_list(tmp_path, FakeSession([]), NOW)
    probe = Probe(channels=8)
    budget = bg.RequestBudget()
    sleeps = []
    games = [f"Game {i}" for i in range(10)]
    ids, probed, tried = bg.discover(games, lst, probe.resolve, probe.directory, probe.channel,
                                     budget, random.Random(2), sleeps.append)
    assert len(probe.calls) == budget.used <= bg.MAX_REQUESTS - bg.DETAIL_RESERVE
    assert len(tried) <= bg.GAMES_PER_CYCLE
    per_game = {}
    for kind, arg in probe.calls:
        if kind == "chan":
            per_game[arg.rsplit("-", 1)[0]] = per_game.get(arg.rsplit("-", 1)[0], 0) + 1
    assert all(n <= bg.CHANNELS_PER_GAME for n in per_game.values())
    assert probed == sum(per_game.values()) and ids == ["c1"]
    assert sleeps and all(bg.JITTER[0] <= s <= bg.JITTER[1] for s in sleeps)
    assert len(set(sleeps)) > 1


def test_slugs_are_cached_so_the_next_cycle_skips_the_lookup(tmp_path):
    lst, _ = game_list(tmp_path, FakeSession([]), NOW)
    probe = Probe()
    bg.discover(["Witchspire"], lst, probe.resolve, probe.directory, probe.channel,
                bg.RequestBudget(), random.Random(1), lambda s: None)
    probe.calls.clear()
    bg.discover(["Witchspire"], lst, probe.resolve, probe.directory, probe.channel,
                bg.RequestBudget(), random.Random(1), lambda s: None)
    assert ("game", "Witchspire") not in probe.calls


def test_known_ineligible_campaigns_are_skipped(tmp_path):
    lst, _ = game_list(tmp_path, FakeSession([]), NOW)
    probe = Probe(campaigns=("bad", "good"))
    ids, _, _ = bg.discover(["Witchspire"], lst, probe.resolve, probe.directory, probe.channel,
                            bg.RequestBudget(), random.Random(1), lambda s: None, skip_ids={"bad"})
    assert ids == ["good"]


def test_budget_reserve():
    b = bg.RequestBudget(15)
    assert all(b.take(1, reserve=3) for _ in range(12))
    assert b.take(1, reserve=3) is False
    assert b.take(3) is True and b.take(1) is False
