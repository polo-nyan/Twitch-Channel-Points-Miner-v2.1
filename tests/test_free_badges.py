# -*- coding: utf-8 -*-
"""Tests for classes/FreeBadges.py (free watch-time badges on not-followed
channels, with the operator's 2.5-5 day cooldown)."""

import copy
import os
import logging
import json
import random
from types import SimpleNamespace

from conftest import ROOT, load_module

fb = load_module(
    "TwitchChannelPointsMiner.classes.FreeBadges",
    "TwitchChannelPointsMiner/classes/FreeBadges.py",
)

NOW = 1_761_130_000.0  # inside the campaign window below
DAY = 86400.0

CAMPAIGN = {
    "id": "camp-witch",
    "name": "Witchspire Halloween",
    "status": "ACTIVE",
    "startAt": "2025-10-01T00:00:00Z",
    "endAt": "2025-11-30T00:00:00Z",
    "accountLinkURL": "",
    "self": {"isAccountConnected": False},
    "game": {"id": "1408630947", "name": "Witchspire", "displayName": "Witchspire", "slug": "witchspire"},
    "allow": {"isEnabled": False, "channels": None},
    "timeBasedDrops": [
        {
            "id": "drop-1", "name": "Witchspire Logo", "requiredMinutesWatched": 60,
            "startAt": "2025-10-01T00:00:00Z", "endAt": "2025-11-30T00:00:00Z",
            "preconditionDrops": None,
            "benefitEdges": [{"benefit": {"id": "b1", "name": "Witchspire Logo", "distributionType": "BADGE"}}],
            "self": {"currentMinutesWatched": 16, "isClaimed": False, "dropInstanceID": None},
        }
    ],
}


def campaign(**changes):
    c = copy.deepcopy(CAMPAIGN)
    for key, value in changes.items():
        c[key] = value
    return c


def directory(channels):
    calls = []

    def fn(slug):
        calls.append(slug)
        return list(channels)

    fn.calls = calls
    return fn


def planner(tmp_path, seed=1, clock=None, weekend=None):
    return fb.FreeBadgePlanner(
        str(tmp_path / "analytics" / fb.STATE_FILE), mode="on",
        rng=random.Random(seed), clock=clock or (lambda: NOW), weekend=weekend)


# ------------------------------- filters --------------------------------- #
def test_eligible_badge_campaign():
    assert fb.campaign_eligible(CAMPAIGN, NOW) is True


def test_filters_reject_non_badge_linked_account_ended_and_finished():
    drop = copy.deepcopy(CAMPAIGN["timeBasedDrops"][0])
    drop["benefitEdges"][0]["benefit"]["distributionType"] = "DIRECT_ENTITLEMENT"
    assert fb.campaign_eligible(campaign(timeBasedDrops=[drop]), NOW) is False
    assert fb.campaign_eligible(campaign(accountLinkURL="https://link.test"), NOW) is False
    assert fb.campaign_eligible(
        campaign(accountLinkURL="https://link.test", self={"isAccountConnected": True}), NOW) is True
    assert fb.campaign_eligible(campaign(endAt="2025-10-02T00:00:00Z"), NOW) is False
    assert fb.campaign_eligible(campaign(status="EXPIRED"), NOW) is False
    claimed = copy.deepcopy(CAMPAIGN["timeBasedDrops"][0])
    claimed["self"]["isClaimed"] = True
    assert fb.campaign_eligible(campaign(timeBasedDrops=[claimed]), NOW) is False


def test_allow_list_is_respected(tmp_path):
    restricted = campaign(allow={"isEnabled": True, "channels": [{"id": "42", "name": "okchannel"}]})
    p = planner(tmp_path)
    pick = p.update([restricted], [], directory([("1", "random1"), ("42", "okchannel")]))
    assert pick == "okchannel"


def test_followed_channels_are_never_picked(tmp_path):
    p = planner(tmp_path)
    assert p.update([CAMPAIGN], [], directory([("1", "configuredone")]),
                    followed=["ConfiguredOne"]) is None


# -------------------------- followed split -------------------------------- #
def test_campaign_a_configured_streamer_serves_is_not_picked_and_not_limited(tmp_path):
    p = planner(tmp_path)
    configured = [("777", "1408630947")]  # a configured streamer plays Witchspire
    d = directory([("1", "random1")])
    assert p.update([CAMPAIGN], configured, d) is None
    assert p.state["cooldown_until"] == 0.0 and p.active is None
    assert d.calls == []  # no directory query either


def test_configured_streamer_outside_the_allow_list_does_not_cover():
    restricted = campaign(allow={"isEnabled": True, "channels": [{"id": "42", "name": "x"}]})
    assert fb.covered_by_configured(restricted, [("777", "1408630947")]) is False
    assert fb.covered_by_configured(restricted, [("42", "1408630947")]) is True


# ------------------------------- cooldown --------------------------------- #
def test_cooldown_draw_is_within_2_5_to_5_days_and_random(tmp_path):
    p = planner(tmp_path)
    draws = [p.draw_cooldown() / DAY for _ in range(300)]
    assert all(2.5 <= d <= 5.0 for d in draws)
    assert len({round(d, 3) for d in draws}) > 250


def test_one_badge_at_a_time_then_cooldown_blocks_a_second_pick(tmp_path):
    t = {"now": NOW}
    p = planner(tmp_path, clock=lambda: t["now"])
    d = directory([("1", "random1"), ("2", "random2")])
    other = campaign(id="camp-other", game={"id": "9", "name": "Other", "displayName": "Other", "slug": "other"})
    first = p.update([CAMPAIGN, other], [], d)
    assert first in ("random1", "random2")
    picked_id = p.active["campaign_id"]

    # While one is active, no second campaign is picked.
    p.note_watched(first)
    current = [c for c in (CAMPAIGN, other) if c["id"] == picked_id][0]
    progressed = copy.deepcopy(current)
    progressed["timeBasedDrops"][0]["self"]["currentMinutesWatched"] = 40
    t["now"] += 1800
    assert p.update([progressed, other if picked_id != "camp-other" else CAMPAIGN], [], d) == first

    # Earned -> cooldown 2.5-5 d, no pick inside the window.
    earned = copy.deepcopy(progressed)
    earned["timeBasedDrops"][0]["self"].update(currentMinutesWatched=60, isClaimed=True)
    t["now"] += 1800
    assert p.update([earned], [], d) is None
    until = p.state["cooldown_until"]
    assert 2.5 * DAY <= until - t["now"] <= 5 * DAY
    for step in (1, DAY, 2 * DAY):
        assert p.update([CAMPAIGN, other], [], d, now=t["now"] + step) is None
    # After the window: a new pick.
    assert p.update([CAMPAIGN, other], [], d, now=until + 1) is not None


def test_cooldown_and_active_pick_survive_a_restart(tmp_path):
    p = planner(tmp_path)
    p.update([CAMPAIGN], [], directory([("1", "random1")]))
    again = planner(tmp_path)
    assert again.active["channel"] == "random1"
    again.state["cooldown_until"] = NOW + 3 * DAY
    again.state["active"] = None
    again.save()
    third = planner(tmp_path)
    assert third.state["cooldown_until"] == NOW + 3 * DAY
    assert third.update([CAMPAIGN], [], directory([("1", "random1")])) is None
    raw = json.load(open(tmp_path / "analytics" / fb.STATE_FILE))
    assert raw["cooldown_until"] == NOW + 3 * DAY


def test_no_progress_while_watched_abandons_and_starts_cooldown(tmp_path):
    p = planner(tmp_path)
    d = directory([("1", "random1")])
    p.update([CAMPAIGN], [], d)
    for i in range(fb.STALE_CHECKS):
        p.note_watched("random1")
        p.update([CAMPAIGN], [], d, now=NOW + (i + 1) * 1800)
    assert p.active is None
    assert p.state["cooldown_until"] > NOW + 2.5 * DAY


def test_not_watched_is_not_counted_as_stale(tmp_path):
    p = planner(tmp_path)
    d = directory([("1", "random1")])
    p.update([CAMPAIGN], [], d)
    for i in range(10):  # never got a watch slot
        p.update([CAMPAIGN], [], d, now=NOW + (i + 1) * 1800)
    assert p.active is not None and p.active["stale"] == 0


def test_nothing_eligible(tmp_path):
    p = planner(tmp_path)
    assert p.update([], [], directory([])) is None
    assert p.update([CAMPAIGN], [], directory([])) is None  # no live channel
    assert p.active is None and p.state["cooldown_until"] == 0.0


def test_off_does_nothing(tmp_path):
    p = fb.FreeBadgePlanner(str(tmp_path / "x.json"), mode="off")
    d = directory([("1", "random1")])
    assert p.update([CAMPAIGN], [], d) is None and d.calls == []


# --------------------------------- slots ---------------------------------- #
def s(name, relevant=False, streak=False):
    return SimpleNamespace(name=name, relevant=relevant, streak=streak)


def slot(targets, free):
    return fb.pick_slot(targets, free, lambda x: x.relevant, lambda x: x.streak)


def test_free_badge_takes_an_empty_slot():
    free = s("free")
    a = s("a", relevant=True)
    assert slot([a], free) == [a, free]
    assert slot([], free) == [free]


def test_free_badge_never_displaces_drop_streams_or_streaks():
    free = s("free")
    lane, game = s("lane", relevant=True), s("game", relevant=True)
    assert slot([lane, game], free) == [lane, game]
    streak = s("streak", streak=True)
    assert slot([lane, streak], free) == [lane, streak]


def test_free_badge_replaces_a_points_only_stream():
    free = s("free")
    game, points = s("game", relevant=True), s("points")
    assert slot([game, points], free) == [game, free]


def test_missing_campaign_data_keeps_the_badge_for_a_while(tmp_path):
    p = planner(tmp_path)
    p.update([CAMPAIGN], [], directory([("1", "random1")]))
    # No answer: no watching this cycle, the badge and the timers stay.
    assert p.update([], [], directory([]), now=NOW + 1800) is None
    assert p.active is not None and p.state["cooldown_until"] == 0.0
    # Bounded: after MISSING_CHECKS cycles without an answer it is abandoned.
    for i in range(fb.MISSING_CHECKS):
        p.update([], [], directory([]), now=NOW + 3600 * (i + 2))
    assert p.active is None and p.state["cooldown_until"] > NOW



# ----------------------------- weekend timer ------------------------------ #
from datetime import datetime, timezone  # noqa: E402


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


WINDOW = fb.WeekendWindow("fri 18:00-sun 22:00", "Europe/Berlin")


def test_window_edges_across_the_october_dst_change():
    # Fri 2026-10-23 is CEST (UTC+2); Sun 2026-10-25 is CET (UTC+1) after 03:00.
    assert WINDOW.contains(utc("2026-10-23T15:59:00")) is False  # Fri 17:59 local
    assert WINDOW.contains(utc("2026-10-23T16:00:00")) is True   # Fri 18:00 local
    assert WINDOW.contains(utc("2026-10-25T00:30:00")) is True   # Sun 02:30 CEST
    assert WINDOW.contains(utc("2026-10-25T20:59:00")) is True   # Sun 21:59 CET
    assert WINDOW.contains(utc("2026-10-25T21:00:00")) is False  # Sun 22:00 CET


def test_window_edges_across_the_march_dst_change():
    # Fri 2026-03-27 is CET (UTC+1); Sun 2026-03-29 is CEST (UTC+2).
    assert WINDOW.contains(utc("2026-03-27T16:59:00")) is False  # Fri 17:59 local
    assert WINDOW.contains(utc("2026-03-27T17:00:00")) is True   # Fri 18:00 local
    assert WINDOW.contains(utc("2026-03-29T19:59:00")) is True   # Sun 21:59 CEST
    assert WINDOW.contains(utc("2026-03-29T20:00:00")) is False  # Sun 22:00 CEST
    assert WINDOW.contains(utc("2026-03-25T12:00:00")) is False  # a Wednesday


def test_window_parsing_and_wrap_over_monday():
    assert fb.parse_window("fri 18:00-sun 22:00") == (4 * 1440 + 1080, 6 * 1440 + 1320)
    wrap = fb.WeekendWindow("sun 20:00-mon 02:00", "UTC")
    assert wrap.contains(utc("2026-10-25T23:00:00")) is True   # Sunday
    assert wrap.contains(utc("2026-10-26T01:59:00")) is True   # Monday
    assert wrap.contains(utc("2026-10-26T02:00:00")) is False


def test_weekend_draw_is_within_10_to_25_hours(tmp_path):
    p = planner(tmp_path, weekend=WINDOW)
    draws = [p.draw_weekend_cooldown() / 3600 for _ in range(300)]
    assert all(10 <= d <= 25 for d in draws) and len({round(d, 3) for d in draws}) > 250


def weekend_campaign():
    return campaign(startAt="2026-10-01T00:00:00Z", endAt="2026-12-31T00:00:00Z",
                    timeBasedDrops=[dict(CAMPAIGN["timeBasedDrops"][0],
                                         startAt="2026-10-01T00:00:00Z",
                                         endAt="2026-12-31T00:00:00Z")])


def earn(c):
    c = copy.deepcopy(c)
    c["timeBasedDrops"][0]["self"].update(currentMinutesWatched=60, isClaimed=True)
    return c


FRI_EVENING = utc("2026-10-23T17:00:00")  # Fri 19:00 Berlin, inside the window
WEDNESDAY = utc("2026-10-21T12:00:00")


def test_weekend_and_weekday_timers_are_independent(tmp_path):
    p = planner(tmp_path, weekend=WINDOW)
    d = directory([("1", "random1")])
    c = weekend_campaign()
    # Weekday timer busy (3 days left) does not block a weekend pick.
    p.state["cooldown_until"] = FRI_EVENING + 3 * DAY
    assert p.update([c], [], d, now=FRI_EVENING) == "random1"
    assert p.active["timer"] == "weekend"
    # Earning it sets ONLY the weekend timer (10-25 h).
    assert p.update([earn(c)], [], d, now=FRI_EVENING + 3600) is None
    assert p.state["cooldown_until"] == FRI_EVENING + 3 * DAY
    assert 10 * 3600 <= p.state["weekend_cooldown_until"] - (FRI_EVENING + 3600) <= 25 * 3600
    # Weekend timer busy and weekday timer busy: no pick.
    assert p.update([c], [], d, now=FRI_EVENING + 2 * 3600) is None
    # A later weekend pick after the weekend cooldown, still in the window.
    sunday = p.state["weekend_cooldown_until"] + 60
    assert WINDOW.contains(sunday)
    assert p.update([c], [], d, now=sunday) == "random1"
    assert p.active["timer"] == "weekend"


def test_weekday_pick_does_not_touch_the_weekend_timer(tmp_path):
    p = planner(tmp_path, weekend=WINDOW)
    d = directory([("1", "random1")])
    c = weekend_campaign()
    p.state["weekend_cooldown_until"] = 123.0
    assert p.update([c], [], d, now=WEDNESDAY) == "random1"
    assert p.active["timer"] == "weekday"
    p.update([earn(c)], [], d, now=WEDNESDAY + 3600)
    assert p.state["weekend_cooldown_until"] == 123.0
    assert 2.5 * DAY <= p.state["cooldown_until"] - (WEDNESDAY + 3600) <= 5 * DAY


def test_weekend_pick_starts_only_inside_the_window(tmp_path):
    p = planner(tmp_path, weekend=WINDOW)
    p.state["cooldown_until"] = WEDNESDAY + 10 * DAY  # weekday timer busy
    d = directory([("1", "random1")])
    assert p.update([weekend_campaign()], [], d, now=WEDNESDAY) is None
    assert p.update([weekend_campaign()], [], d, now=utc("2026-10-25T21:00:00")) is None  # Sun 22:00


def test_weekend_badge_in_progress_at_window_close_is_left_to_finish(tmp_path):
    p = planner(tmp_path, weekend=WINDOW)
    p.state["cooldown_until"] = WEDNESDAY + 10 * DAY
    d = directory([("1", "random1")])
    c = weekend_campaign()
    assert p.update([c], [], d, now=utc("2026-10-25T20:30:00")) == "random1"  # Sun 21:30
    after_close = utc("2026-10-25T21:30:00")  # Sun 22:30, outside
    progressed = copy.deepcopy(c)
    progressed["timeBasedDrops"][0]["self"]["currentMinutesWatched"] = 40
    p.note_watched("random1")
    assert p.update([progressed], [], d, now=after_close) == "random1"
    assert p.active["timer"] == "weekend"


def test_both_timers_survive_a_restart(tmp_path):
    p = planner(tmp_path, weekend=WINDOW)
    p.update([weekend_campaign()], [], directory([("1", "random1")]), now=FRI_EVENING)
    p.update([earn(weekend_campaign())], [], directory([]), now=FRI_EVENING + 3600)
    p.state["cooldown_until"] = 999.0
    p.save()
    again = planner(tmp_path, weekend=WINDOW)
    assert again.state["cooldown_until"] == 999.0
    assert again.state["weekend_cooldown_until"] == p.state["weekend_cooldown_until"] > FRI_EVENING


def test_weekend_env_switch(monkeypatch):
    monkeypatch.delenv(fb.WEEKEND_ENV, raising=False)
    assert fb.weekend_from_env() is None
    monkeypatch.setenv(fb.WEEKEND_ENV, "on")
    w = fb.weekend_from_env()
    assert w.text == "fri 18:00-sun 22:00" and str(w.tz) == "Europe/Berlin"
    monkeypatch.setenv(fb.WINDOW_ENV, "sat 10:00-sat 12:00")
    assert fb.weekend_from_env().start == 5 * 1440 + 600
    monkeypatch.setenv(fb.WINDOW_ENV, "nonsense")
    assert fb.weekend_from_env() is None


# ------------------------- cycle with streamdatabase ----------------------- #
bgl = load_module(
    "TwitchChannelPointsMiner.classes.BadgeGames",
    "TwitchChannelPointsMiner/classes/BadgeGames.py",
)


class CountingOps(object):
    """Fake GQL side of a free-badge cycle; counts requests (details: per id)."""

    def __init__(self, campaigns):
        self.campaigns = {c["id"]: c for c in campaigns}
        self.requests = 0

    def directory(self, slug):
        self.requests += 1
        return [(f"{slug}{i}", f"{slug}login{i}") for i in range(10)]

    def game(self, name):
        self.requests += 1
        return name.lower()

    def channel_campaigns(self, channel_id):
        self.requests += 1
        return list(self.campaigns.values())


class StaticList(object):
    def __init__(self, games):
        self._games, self.calls = games, 0
        self.state = {"slugs": {}}

    def games(self):
        self.calls += 1
        return list(self._games)

    def fetched_text(self):
        return "2026-10-22 12:43 UTC"

    def slug(self, name, resolve, budget):
        return bgl.BadgeGameList.slug(self, name, resolve, budget)

    def save(self):
        pass

    clock = staticmethod(lambda: NOW)


def test_cycle_picks_a_channel_within_15_requests(tmp_path):
    p = planner(tmp_path)
    ops = CountingOps([CAMPAIGN])
    games = StaticList(["Witchspire", "Alien Isolation", "Roblox", "PAYDAY 3"])
    login, summary, used = fb.free_badge_cycle(p, games, ops, [], [], NOW, set(), sleep=lambda s: None)
    assert login is not None and p.active["timer"] == "weekday"
    assert used == ops.requests <= bgl.MAX_REQUESTS
    assert "channels probed" in summary


def test_cooldown_skip_makes_zero_requests(tmp_path):
    p = planner(tmp_path)
    p.state["cooldown_until"] = NOW + 3 * DAY
    ops = CountingOps([CAMPAIGN])
    games = StaticList(["Witchspire"])
    login, summary, used = fb.free_badge_cycle(p, games, ops, [], [], NOW, set(), sleep=lambda s: None)
    assert (login, used, ops.requests, games.calls) == (None, 0, 0, 0)
    assert summary == "both timers cooling down"


def test_no_list_means_nothing_eligible_without_requests(tmp_path, caplog):
    p = planner(tmp_path)
    ops = CountingOps([CAMPAIGN])
    with caplog.at_level(logging.INFO, logger=fb.__name__):
        login, summary, used = fb.free_badge_cycle(p, StaticList([]), ops, [], [], NOW, set())
    assert login is None and used == 0 and ops.requests == 0
    assert any("nothing eligible (no badge-game list)" in r.getMessage() for r in caplog.records)


def test_active_badge_costs_one_channel_request(tmp_path):
    p = planner(tmp_path)
    ops = CountingOps([CAMPAIGN])
    fb.free_badge_cycle(p, StaticList(["Witchspire"]), ops, [], [], NOW, set(), sleep=lambda s: None)
    ops.requests = 0
    login, summary, used = fb.free_badge_cycle(p, StaticList(["Witchspire"]), ops, [], [], NOW + 1800, set())
    assert login is not None and used == ops.requests == 1 and summary == "tracking the active badge"


def test_ineligible_campaigns_are_remembered_and_skipped(tmp_path):
    p = planner(tmp_path)
    paid = campaign(id="not-badge")
    paid["timeBasedDrops"] = [dict(paid["timeBasedDrops"][0], benefitEdges=[
        {"benefit": {"id": "x", "name": "Skin", "distributionType": "DIRECT_ENTITLEMENT"}}])]
    ops = CountingOps([paid])
    skip = set()
    login, _, _ = fb.free_badge_cycle(p, StaticList(["Witchspire"]), ops, [], [], NOW, skip, sleep=lambda s: None)
    assert login is None and skip == {"not-badge"}


# --------------------- option D: per-channel eligibility ------------------- #
with open(os.path.join(ROOT, "tests", "fixtures", "channel_drops_apple_tv.json")) as f:
    APPLE = json.load(f)["data"]["channel"]["viewerDropCampaigns"][0]

LIVE_NOW = datetime(2026, 10, 22, 14, 35, tzinfo=timezone.utc).timestamp()


def as_badge(campaign, link=None, connected=False):
    """The live Apple TV answer turned into a badge campaign (derived)."""
    c = copy.deepcopy(campaign)
    c["accountLinkURL"] = link
    c["self"] = {"isAccountConnected": connected}
    for drop in c["timeBasedDrops"]:
        drop["requiredMinutesWatched"] = 60
        for edge in drop["benefitEdges"]:
            edge["benefit"]["distributionType"] = "BADGE"
    return c


def test_live_apple_tv_answer_is_not_eligible_code_reward_and_link():
    ok, reason = fb.eligibility(APPLE, LIVE_NOW)
    assert ok is False and reason == "needs a linked game account"
    linked = copy.deepcopy(APPLE)
    linked["self"]["isAccountConnected"] = True
    assert fb.eligibility(linked, LIVE_NOW) == (False, "not a watch-time drop (0 minutes)")
    timed = copy.deepcopy(linked)
    timed["timeBasedDrops"][0]["requiredMinutesWatched"] = 30
    assert fb.eligibility(timed, LIVE_NOW) == (False, "reward type CODE")


def test_badge_only_without_link_is_eligible():
    assert fb.eligibility(as_badge(APPLE, link=None), LIVE_NOW) == (True, "eligible")
    assert fb.eligibility(as_badge(APPLE, link=""), LIVE_NOW) == (True, "eligible")


def test_link_set_and_not_connected_is_not_eligible():
    c = as_badge(APPLE, link="https://link.test", connected=False)
    assert fb.eligibility(c, LIVE_NOW) == (False, "needs a linked game account")


def test_link_set_and_connected_is_eligible():
    c = as_badge(APPLE, link="https://link.test", connected=True)
    assert fb.eligibility(c, LIVE_NOW) == (True, "eligible")


def test_allow_list_disabled_means_no_restriction_enabled_needs_the_list():
    c = as_badge(APPLE)
    assert c["allow"] == {"channels": None, "isEnabled": False}  # as Twitch sends it
    assert fb.eligibility(c, LIVE_NOW)[0] is True
    c["allow"]["isEnabled"] = True  # restricted, but no list: unknown
    assert fb.eligibility(c, LIVE_NOW) == (False, "allow.channels missing")


def drop_path(c, path):
    node = c
    for key in path[:-1]:
        node = node[key]
    return node, path[-1]


def test_any_missing_or_null_field_means_no_pick():
    paths = [
        ("status",), ("startAt",), ("endAt",), ("accountLinkURL",), ("self",),
        ("self", "isAccountConnected"), ("allow",), ("allow", "isEnabled"),
        ("game", "slug"), ("timeBasedDrops",),
        ("timeBasedDrops", 0, "requiredMinutesWatched"),
        ("timeBasedDrops", 0, "benefitEdges"),
        ("timeBasedDrops", 0, "benefitEdges", 0, "benefit", "distributionType"),
        ("timeBasedDrops", 0, "self"), ("timeBasedDrops", 0, "self", "isClaimed"),
    ]
    for path in paths:
        for how in ("missing", "null"):
            c = as_badge(APPLE)
            node, key = drop_path(c, path)
            if how == "missing":
                node.pop(key)
            else:
                node[key] = None
            ok, reason = fb.eligibility(c, LIVE_NOW)
            if path == ("accountLinkURL",) and how == "null":
                assert ok is True  # null link URL = no link needed
                continue
            assert ok is False, (path, how, reason)


def test_skip_reason_is_logged_once(tmp_path, caplog):
    p = planner(tmp_path, clock=lambda: LIVE_NOW)
    d = directory([("56648155", "twitchplayspokemon")])
    with caplog.at_level(logging.INFO, logger=fb.__name__):
        for _ in range(3):
            assert p.update([APPLE], [], d, now=LIVE_NOW) is None
    skips = [r.getMessage() for r in caplog.records if "skip 1 Month of Apple TV" in r.getMessage()]
    assert skips == ["Free badges: skip 1 Month of Apple TV (Special Events): needs a linked game account"]


def test_cycle_with_live_answer_uses_at_most_15_requests_and_picks_nothing(tmp_path):
    p = planner(tmp_path, clock=lambda: LIVE_NOW)
    ops = CountingOps([APPLE])
    games = StaticList(["Witchspire", "Alien Isolation", "Enshrouded", "PAYDAY 3", "Roblox"])
    login, summary, used = fb.free_badge_cycle(p, games, ops, [], [], LIVE_NOW, set(), sleep=lambda s: None)
    assert login is None and used == ops.requests <= 15 and "0 eligible" in summary


def test_cycle_picks_a_probed_channel_for_an_eligible_badge(tmp_path):
    p = planner(tmp_path, clock=lambda: LIVE_NOW)
    ops = CountingOps([as_badge(APPLE)])
    login, summary, used = fb.free_badge_cycle(
        p, StaticList(["Special Events"]), ops, [], [], LIVE_NOW, set(), sleep=lambda s: None)
    assert login is not None and used <= 15 and p.active["channel_id"]
