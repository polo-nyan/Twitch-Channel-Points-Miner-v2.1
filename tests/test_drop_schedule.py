# -*- coding: utf-8 -*-
"""Tests for classes/DropSchedule.py and the lane_channels set in DropLanes."""

from types import SimpleNamespace

from conftest import load_module

schedule = load_module(
    "TwitchChannelPointsMiner.classes.DropSchedule",
    "TwitchChannelPointsMiner/classes/DropSchedule.py",
)


def test_refresh_is_every_30_minutes_not_15():
    start = 1_000_000.0
    assert schedule.campaigns_refresh_due(0, start) is True
    assert schedule.campaigns_refresh_due(start, start + 15 * 60 + 1) is False
    assert schedule.campaigns_refresh_due(start, start + 29 * 60) is False
    assert schedule.campaigns_refresh_due(start, start + 30 * 60 + 1) is True


def streamer(drops=False, claim=True, online=True, channel_id="56648155"):
    return SimpleNamespace(
        drops_condition=lambda: drops,
        settings=SimpleNamespace(claim_drops=claim),
        is_online=online,
        channel_id=channel_id,
    )


def test_game_drops_stream_is_relevant():
    assert schedule.is_drop_relevant(streamer(drops=True), set()) is True


def test_reward_choice_channel_is_relevant():
    assert schedule.is_drop_relevant(streamer(), {"56648155"}) is True


def test_other_channels_are_not_relevant():
    assert schedule.is_drop_relevant(streamer(), set()) is False
    assert schedule.is_drop_relevant(streamer(claim=False), {"56648155"}) is False
    assert schedule.is_drop_relevant(streamer(online=False), {"56648155"}) is False
    assert schedule.is_drop_relevant(streamer(channel_id=""), {""}) is False



def test_drops_order_puts_reward_choice_channels_first_when_enabled():
    streamers = [
        streamer(drops=True, channel_id="game1"),
        streamer(drops=True, channel_id="game2"),
        streamer(channel_id="tpp"),
        streamer(channel_id="nothing"),
    ]
    lanes = {"tpp"}
    assert schedule.drops_order(streamers, [0, 1, 2, 3], lanes, True) == [2, 0, 1]
    assert schedule.drops_order(streamers, [0, 1, 2, 3], lanes, False) == [0, 1, 2]
    # With 2 watch slots the reward-choice channel now gets one.
    assert 2 in schedule.drops_order(streamers, [0, 1, 2, 3], lanes, True)[:2]


def test_lane_priority_flag(monkeypatch):
    monkeypatch.delenv(schedule.LANE_PRIORITY_ENV, raising=False)
    assert schedule.lane_priority_enabled("on") is True
    assert schedule.lane_priority_enabled("dry-run") is False
    assert schedule.lane_priority_enabled("off") is False
    monkeypatch.setenv(schedule.LANE_PRIORITY_ENV, "off")
    assert schedule.lane_priority_enabled("on") is False
    monkeypatch.setenv(schedule.LANE_PRIORITY_ENV, "on")
    assert schedule.lane_priority_enabled("off") is True
