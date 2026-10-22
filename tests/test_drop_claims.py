# -*- coding: utf-8 -*-
"""Tests for the drop claim attempt limit in classes/DropClaims.py.

The GQL responses are mocked. The ELIGIBLE_FOR_ALL case is the live one from
upstream #844/#845: Twitch reports the status, but the drop stays unclaimed
because it needs a linked game account.
"""

import logging

import pytest

from conftest import load_module

load_module(
    "TwitchChannelPointsMiner.classes.Settings",
    "TwitchChannelPointsMiner/classes/Settings.py",
)
drop_claims = load_module(
    "TwitchChannelPointsMiner.classes.DropClaims",
    "TwitchChannelPointsMiner/classes/DropClaims.py",
)

DropClaimTracker = drop_claims.DropClaimTracker
claim_result = drop_claims.claim_result

DROP_ID = "afe1c617-bcbc-11f1-a37e-0a58a9feac02"


class FakeDrop(object):
    def __init__(self, drop_id=DROP_ID, name="100 Accolades + a chat badge"):
        self.id = drop_id
        self.name = name
        self.drop_instance_id = f"69044579#19800c70#{drop_id}"

    def __str__(self):
        return self.name


def claim_response(status, connected=None):
    payload = {"status": status, "dropInstanceID": "x"}
    if connected is not None:
        payload["isUserAccountConnected"] = connected
    return {"data": {"claimDropRewards": payload}}


class FakeGQL(object):
    """Stands in for Twitch.post_gql_request and records each request."""

    def __init__(self, response):
        self.response = response
        self.requests = []

    def __call__(self, json_data):
        self.requests.append(json_data)
        return self.response


@pytest.mark.parametrize(
    "response, expected",
    [
        (claim_response("ELIGIBLE_FOR_ALL"), "claimed"),
        (claim_response("ELIGIBLE_FOR_ALL", connected=True), "claimed"),
        (claim_response("ELIGIBLE_FOR_ALL", connected=False), "needs_account"),
        (claim_response("DROP_INSTANCE_ALREADY_CLAIMED"), "claimed"),
        (claim_response("NOT_ELIGIBLE"), "failed"),
        (claim_response("ELIGIBLE_FOR_SOME"), "failed"),
        ({"data": {"claimDropRewards": None}}, "failed"),
        ({"data": {"errors": [{"message": "nope"}]}}, "failed"),
        ({}, "no_answer"),
        ({"data": None, "errors": [{"message": "service timeout"}]}, "no_answer"),
        ([], "no_answer"),
    ],
)
def test_claim_result(response, expected):
    assert claim_result(response)[0] == expected


def test_claim_request_carries_drop_instance_id():
    gql = FakeGQL(claim_response("ELIGIBLE_FOR_ALL"))
    drop = FakeDrop()
    assert DropClaimTracker().claim(drop, gql) is True
    assert gql.requests[0]["operationName"] == "DropsPage_ClaimDropRewards"
    assert gql.requests[0]["variables"] == {
        "input": {"dropInstanceID": drop.drop_instance_id}
    }


def test_needs_account_stops_after_three_attempts(caplog):
    gql = FakeGQL(claim_response("ELIGIBLE_FOR_ALL", connected=False))
    tracker = DropClaimTracker()
    drop = FakeDrop()

    with caplog.at_level(logging.INFO, logger=drop_claims.__name__):
        results = [tracker.claim(drop, gql) for _ in range(10)]

    assert results == [False] * 10
    assert len(gql.requests) == 3
    assert tracker.is_skipped(drop)
    give_up = [r for r in caplog.records if "Stop claiming drop" in r.getMessage()]
    assert len(give_up) == 1
    assert give_up[0].levelno == logging.INFO
    assert "linked game account" in give_up[0].getMessage()


def test_reported_success_that_does_not_stick_is_limited(caplog):
    # Twitch says ELIGIBLE_FOR_ALL each time, but the inventory sync keeps
    # handing the same drop back as claimable: the #844 loop.
    gql = FakeGQL(claim_response("ELIGIBLE_FOR_ALL"))
    tracker = DropClaimTracker()
    drop = FakeDrop()

    with caplog.at_level(logging.INFO, logger=drop_claims.__name__):
        for _ in range(10):
            tracker.claim(drop, gql)

    assert len(gql.requests) == 3
    assert tracker.is_skipped(drop)
    assert sum("Stop claiming drop" in r.getMessage() for r in caplog.records) == 1


def test_no_answer_does_not_count_as_attempt():
    gql = FakeGQL({})
    tracker = DropClaimTracker()
    drop = FakeDrop()
    for _ in range(5):
        assert tracker.claim(drop, gql) is False
    assert len(gql.requests) == 5
    assert not tracker.is_skipped(drop)


def test_limit_is_per_drop():
    gql = FakeGQL(claim_response("NOT_ELIGIBLE"))
    tracker = DropClaimTracker()
    bad, good = FakeDrop("bad"), FakeDrop("good")
    for _ in range(3):
        tracker.claim(bad, gql)
    assert tracker.is_skipped(bad)

    gql.response = claim_response("ELIGIBLE_FOR_ALL")
    assert tracker.claim(good, gql) is True
    assert not tracker.is_skipped(good)
