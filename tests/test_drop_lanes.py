# -*- coding: utf-8 -*-
"""Tests for the reward choice ("lanes") logic in classes/DropLanes.py.

The GQL responses are mocked. tests/fixtures/drop_lanes_campaign.json is a
SYNTHETIC campaign shaped like the web client's DropChannelCampaignsProgress
response, with the three lane states seen on the live account: done for
today, selected and in progress, not started.
"""

import copy
import json
import logging
import os
from datetime import datetime, timezone

from conftest import ROOT, load_module

lanes_module = load_module(
    "TwitchChannelPointsMiner.classes.DropLanes",
    "TwitchChannelPointsMiner/classes/DropLanes.py",
)
DropLaneSwitcher = lanes_module.DropLaneSwitcher
decide = lanes_module.decide
tracks_from_campaigns = lanes_module.tracks_from_campaigns
lane_state = lanes_module.lane_state

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

with open(os.path.join(ROOT, "tests", "fixtures", "drop_lanes_campaign.json")) as f:
    CAMPAIGN = json.load(f)


def campaign(**changes):
    """The fixture, with `changes` = {group_id: new self dict}."""
    data = copy.deepcopy(CAMPAIGN)
    for group in data["data"]["channelDropCampaignsProgress"][0]["rewardGroups"]:
        if group["id"] in changes:
            group["self"] = changes[group["id"]]
    return data


def selection(lane):
    return {"data": {"trackSelections": [{"id": "track-1", "track": "track-1", "lane": lane}]}}


class FakeGQL(object):
    """Answers by operation name and records every request."""

    def __init__(self, campaign_response, selection_response, mutation_error=None):
        self.responses = {
            "DropChannelCampaignsProgress": campaign_response,
            "ProgressionTrackSelections": selection_response,
            "SetProgressionTrackSelection": {
                "data": {"setTrackSelection": {"error": mutation_error}}
            },
        }
        self.requests = []

    def __call__(self, json_data):
        self.requests.append(json_data)
        return self.responses[json_data["operationName"]]

    def names(self):
        return [r["operationName"] for r in self.requests]

    def mutations(self):
        return [
            r["variables"]["input"]
            for r in self.requests
            if r["operationName"] == "SetProgressionTrackSelection"
        ]


def finished(minutes=20):
    return {"status": "CLAIMED", "currentMinutesWatched": minutes, "grantCount": 4,
            "nextEligibleAt": None, "currentWindow": None}


def test_fixture_lane_states():
    lanes = tracks_from_campaigns(CAMPAIGN["data"]["channelDropCampaignsProgress"])
    assert list(lanes) == ["track-1"]  # the plain drop has no track
    states = {lane: lane_state(groups, NOW) for lane, groups in lanes["track-1"].items()}
    assert states == {"lane-a": "done_today", "lane-b": "available", "lane-c": "available"}


def test_selected_lane_done_switches_to_next_unearned():
    gql = FakeGQL(CAMPAIGN, selection("lane-a"))
    changes = DropLaneSwitcher(mode="on", preference=[]).run("1", "tpp", gql, now=NOW)
    assert changes == [("track-1", "lane-b")]
    assert gql.mutations() == [{"track": "track-1", "lane": "lane-b"}]


def test_selected_lane_unearned_is_a_no_op():
    gql = FakeGQL(CAMPAIGN, selection("lane-b"))
    assert DropLaneSwitcher(mode="on", preference=[]).run("1", "tpp", gql, now=NOW) == []
    assert gql.names() == ["DropChannelCampaignsProgress", "ProgressionTrackSelections"]


def test_not_started_lane_selected_is_a_no_op():
    gql = FakeGQL(CAMPAIGN, selection("lane-c"))
    assert DropLaneSwitcher(mode="on", preference=[]).run("1", "tpp", gql, now=NOW) == []
    assert gql.mutations() == []


def test_all_earned_stops_and_makes_no_further_calls(caplog):
    done = campaign(**{g: finished() for g in ("group-a", "group-b", "group-c")})
    gql = FakeGQL(done, selection("lane-a"))
    switcher = DropLaneSwitcher(mode="on", preference=[])
    with caplog.at_level(logging.INFO, logger=lanes_module.__name__):
        assert switcher.run("1", "tpp", gql, now=NOW) == []
        calls = len(gql.requests)
        assert switcher.run("1", "tpp", gql, now=NOW) == []
    assert gql.mutations() == []
    assert len(gql.requests) == calls == 2
    assert sum("every reward is earned" in r.getMessage() for r in caplog.records) == 1


def test_every_lane_done_for_today_waits_without_mutation():
    today = {"status": "IN_PROGRESS", "currentMinutesWatched": 20, "grantCount": 1,
             "nextEligibleAt": None,
             "currentWindow": {"startedAt": "2026-10-22T00:00:00Z", "expiresAt": "2026-10-23T00:00:00Z"}}
    gql = FakeGQL(campaign(**{"group-b": today, "group-c": today}), selection("lane-a"))
    switcher = DropLaneSwitcher(mode="on", preference=[])
    assert switcher.run("1", "tpp", gql, now=NOW) == []
    assert gql.mutations() == []
    assert "1" not in switcher.finished  # tomorrow brings new windows


def test_expired_window_makes_a_lane_available_again():
    lanes = tracks_from_campaigns(CAMPAIGN["data"]["channelDropCampaignsProgress"])["track-1"]
    tomorrow = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)
    assert lane_state(lanes["lane-a"], tomorrow) == "available"
    assert decide(lanes, "lane-a", now=tomorrow) == ("keep", "lane-a")


def test_no_flip_flop_after_switch():
    gql = FakeGQL(CAMPAIGN, selection("lane-a"))
    switcher = DropLaneSwitcher(mode="on", preference=[])
    switcher.run("1", "tpp", gql, now=NOW)
    # Twitch now reports the new selection: nothing more to do.
    gql.responses["ProgressionTrackSelections"] = selection("lane-b")
    for _ in range(3):
        switcher.run("1", "tpp", gql, now=NOW)
    assert gql.mutations() == [{"track": "track-1", "lane": "lane-b"}]


def test_rejected_selection_is_not_retried_forever():
    gql = FakeGQL(CAMPAIGN, selection("lane-a"), mutation_error="FORBIDDEN")
    switcher = DropLaneSwitcher(mode="on", preference=[])
    for _ in range(5):
        assert switcher.run("1", "tpp", gql, now=NOW) == []
    assert len(gql.mutations()) == lanes_module.MAX_SELECT_ATTEMPTS


def test_preference_list_wins_over_response_order():
    gql = FakeGQL(CAMPAIGN, selection("lane-a"))
    DropLaneSwitcher(mode="on", preference=["Squirtle"]).run("1", "tpp", gql, now=NOW)
    assert gql.mutations() == [{"track": "track-1", "lane": "lane-c"}]


def test_dry_run_reads_but_never_mutates(caplog):
    gql = FakeGQL(CAMPAIGN, selection("lane-a"))
    with caplog.at_level(logging.INFO, logger=lanes_module.__name__):
        changes = DropLaneSwitcher(mode="dry-run", preference=[]).run("1", "tpp", gql, now=NOW)
    assert changes == [("track-1", "lane-b")]
    assert gql.mutations() == []
    assert any("would select Charmeleon" in r.getMessage() for r in caplog.records)


def test_off_makes_no_requests():
    gql = FakeGQL(CAMPAIGN, selection("lane-a"))
    assert DropLaneSwitcher(mode="off", preference=[]).run("1", "tpp", gql, now=NOW) == []
    assert gql.requests == []


def test_channel_without_lanes_costs_one_query():
    plain = {"data": {"channelDropCampaignsProgress": []}}
    gql = FakeGQL(plain, selection("lane-a"))
    assert DropLaneSwitcher(mode="on", preference=[]).run("1", "tpp", gql, now=NOW) == []
    assert gql.names() == ["DropChannelCampaignsProgress"]


def test_mode_from_env(monkeypatch):
    monkeypatch.delenv(lanes_module.MODE_ENV, raising=False)
    assert lanes_module.mode_from_env() == "off"
    monkeypatch.setenv(lanes_module.MODE_ENV, "DRY-RUN")
    assert lanes_module.mode_from_env() == "dry-run"
    monkeypatch.setenv(lanes_module.MODE_ENV, "yes please")
    assert lanes_module.mode_from_env() == "off"


# --------------------------------------------------------------------------- #
# Live capture (twitchplayspokemon, 2026-10-22 ~06:35Z). Lanes are evolution
# lines with several stages; the selected lane is Ivysaur -> Venusaur.
# --------------------------------------------------------------------------- #
with open(os.path.join(ROOT, "tests", "fixtures", "drop_lanes_live_20261022.json")) as f:
    LIVE = json.load(f)

LIVE_NOW = datetime(2026, 10, 22, 6, 35, tzinfo=timezone.utc)
TRACK = "pokemon#aebee668-75ca-4626-99f5-168d369d217b"
IVYSAUR = "53c2558e-9661-11f1-8d10-0a58a9feac02"  # selected; window expired
WARTORTLE = "6fb2b2bf-9661-11f1-b7ec-0a58a9feac02"  # 20/20 in today's window
GENGAR = "5130dfac-b7a5-11f1-93ac-0a58a9feac02"  # Haunter claimed, Gengar 2/20
CHARIZARD = "60456d93-9661-11f1-9755-0a58a9feac02"  # Charmeleon claimed, 0 min
PIKACHU = "7e978af0-9661-11f1-b03f-0a58a9feac02"  # claimed, single stage


def live_lanes():
    tracks = tracks_from_campaigns(
        LIVE["DropChannelCampaignsProgress"]["data"]["channelDropCampaignsProgress"]
    )
    return tracks


def live_selection(lane):
    data = copy.deepcopy(LIVE["ProgressionTrackSelections"])
    data["data"]["trackSelections"][0]["lane"] = lane
    return data


def test_live_only_the_evolution_track_has_lanes():
    # The balls# track groups have no lane and are not a choice.
    assert list(live_lanes()) == [TRACK]
    assert live_selection(IVYSAUR)["data"]["trackSelections"][0]["lane"] == IVYSAUR
    assert LIVE["ProgressionTrackSelections"]["data"]["trackSelections"][0]["lane"] == IVYSAUR


def test_live_lane_states():
    lanes = live_lanes()[TRACK]
    states = {lane: lane_state(groups, LIVE_NOW) for lane, groups in lanes.items()}
    assert states == {
        GENGAR: "available",
        CHARIZARD: "available",
        IVYSAUR: "available",  # last window expired, so today's minutes are 0
        PIKACHU: "finished",
        WARTORTLE: "done_today",  # not "available" because Blastoise is unstarted
    }
    assert lanes_module.lane_names(lanes[IVYSAUR])[0] == "Ivysaur"
    assert lanes_module.lane_names(lanes[WARTORTLE])[0] == "Wartortle"


def test_live_selected_lane_with_progress_left_is_kept(caplog):
    gql = FakeGQL(LIVE["DropChannelCampaignsProgress"], LIVE["ProgressionTrackSelections"])
    with caplog.at_level(logging.INFO, logger=lanes_module.__name__):
        changes = DropLaneSwitcher(mode="dry-run", preference=[]).run(
            "56648155", "twitchplayspokemon", gql, now=LIVE_NOW
        )
    assert changes == []
    assert gql.mutations() == []
    assert any("selected Ivysaur can still make progress" in r.getMessage() for r in caplog.records)


def test_live_done_today_lane_switches_to_first_available():
    gql = FakeGQL(LIVE["DropChannelCampaignsProgress"], live_selection(WARTORTLE))
    changes = DropLaneSwitcher(mode="on", preference=[]).run(
        "56648155", "twitchplayspokemon", gql, now=LIVE_NOW
    )
    assert changes == [(TRACK, GENGAR)]
    assert gql.mutations() == [{"track": TRACK, "lane": GENGAR}]


def test_live_finished_lane_switches_with_preference():
    gql = FakeGQL(LIVE["DropChannelCampaignsProgress"], live_selection(PIKACHU))
    DropLaneSwitcher(mode="on", preference=["Charizard"]).run(
        "56648155", "twitchplayspokemon", gql, now=LIVE_NOW
    )
    assert gql.mutations() == [{"track": TRACK, "lane": CHARIZARD}]


def test_live_done_today_lane_is_available_after_its_window():
    lanes = live_lanes()[TRACK]
    after = datetime(2026, 10, 22, 19, 15, tzinfo=timezone.utc)
    assert lane_state(lanes[WARTORTLE], after) == "available"
