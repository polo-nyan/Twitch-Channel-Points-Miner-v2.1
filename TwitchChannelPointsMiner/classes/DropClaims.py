# -*- coding: utf-8 -*-
"""Drop claiming with a per-drop attempt limit.

Twitch answers `claimDropRewards` with status ELIGIBLE_FOR_ALL even when the
claim cannot complete because the drop needs a linked game account. The drop
then shows up unclaimed in the next inventory sync and the miner claims it
again, forever (upstream issues #844 and #845).

This module decides from the GQL response whether a claim really succeeded,
and counts the claim attempts per drop id. After MAX_CLAIM_ATTEMPTS attempts
that did not stick, the drop is skipped until the miner restarts. A claim that
Twitch reported as a success but that comes back unclaimed also counts as a
failed attempt, so the limit holds even if Twitch changes its status values.

The state is kept in memory only: the miner has no general state store, and a
restart is a reasonable moment to try again (for example after the user linked
the game account).
"""

import copy
import logging

from TwitchChannelPointsMiner.classes.Settings import Events
from TwitchChannelPointsMiner.constants import GQLOperations

logger = logging.getLogger(__name__)

MAX_CLAIM_ATTEMPTS = 3

# The drop is in the inventory as claimed. Nothing more to do.
STATUS_ALREADY_CLAIMED = "DROP_INSTANCE_ALREADY_CLAIMED"
# Twitch accepted the claim. It is only a success when the account link check
# did not fail (see claim_result).
STATUS_ELIGIBLE_FOR_ALL = "ELIGIBLE_FOR_ALL"

RESULT_CLAIMED = "claimed"
RESULT_NEEDS_ACCOUNT = "needs_account"
RESULT_FAILED = "failed"
# No usable answer (network error, empty response). Not counted as an attempt.
RESULT_NO_ANSWER = "no_answer"


def claim_result(response):
    """Classify a DropsPage_ClaimDropRewards response.

    Returns a (result, status) tuple. `status` is the claim status string from
    Twitch, or None when the response has no status.
    """
    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return RESULT_NO_ANSWER, None

    data = response["data"]
    if data.get("errors") or response.get("errors"):
        return RESULT_FAILED, None

    payload = data.get("claimDropRewards")
    if not isinstance(payload, dict):
        return RESULT_FAILED, None

    status = payload.get("status")
    if status == STATUS_ALREADY_CLAIMED:
        return RESULT_CLAIMED, status
    if payload.get("isUserAccountConnected") is False:
        return RESULT_NEEDS_ACCOUNT, status
    if status == STATUS_ELIGIBLE_FOR_ALL:
        return RESULT_CLAIMED, status
    return RESULT_FAILED, status


class DropClaimTracker(object):
    __slots__ = ["max_attempts", "attempts", "last_status", "skipped"]

    def __init__(self, max_attempts=MAX_CLAIM_ATTEMPTS):
        self.max_attempts = max_attempts
        self.attempts = {}
        self.last_status = {}
        self.skipped = set()

    def is_skipped(self, drop):
        return drop.id in self.skipped

    def claim(self, drop, post_gql_request):
        """Claim `drop` through `post_gql_request`. Returns True only when
        the claim really succeeded."""
        if drop.id in self.skipped:
            logger.debug(f"Skip claim for {drop.id}: attempt limit reached")
            return False

        if self.attempts.get(drop.id, 0) >= self.max_attempts:
            # The earlier claims reported success, but the drop is still
            # claimable. Treat that as a claim that did not stick.
            self.__give_up(drop)
            return False

        logger.info(
            f"Claim {drop}", extra={"emoji": ":package:", "event": Events.DROP_CLAIM}
        )
        json_data = copy.deepcopy(GQLOperations.DropsPage_ClaimDropRewards)
        json_data["variables"] = {"input": {"dropInstanceID": drop.drop_instance_id}}
        result, status = claim_result(post_gql_request(json_data))

        if result == RESULT_NO_ANSWER:
            logger.debug(f"No answer to the claim for {drop.id}, retry later")
            return False

        self.attempts[drop.id] = self.attempts.get(drop.id, 0) + 1
        self.last_status[drop.id] = status
        if result == RESULT_CLAIMED:
            return True

        logger.debug(
            f"Claim for {drop.id} failed ({result}, status={status}), "
            f"attempt {self.attempts[drop.id]}/{self.max_attempts}"
        )
        if self.attempts[drop.id] >= self.max_attempts:
            self.__give_up(drop)
        return False

    def __give_up(self, drop):
        if drop.id in self.skipped:
            return
        self.skipped.add(drop.id)
        logger.info(
            f"Stop claiming drop {drop.name} ({drop.id}) after "
            f"{self.attempts.get(drop.id, 0)} attempts "
            f"(last status: {self.last_status.get(drop.id)}). "
            "Needs a linked game account? Skipping it until the next restart.",
            extra={"emoji": ":package:"},
        )
