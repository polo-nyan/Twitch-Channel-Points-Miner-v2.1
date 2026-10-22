# -*- coding: utf-8 -*-
"""Reward choice ("lanes") in multi-day channel drop campaigns.

Some channel campaigns (first seen on twitchplayspokemon, 2026-10) offer a
choice of rewards. Each choice is a reward group with the same `track` and its
own `lane`. Watch progress goes to the lane that is selected for the track.
When the selected reward is done for the day (or done for good), progress is
wasted until a different, not yet earned reward is selected.

This module reads the campaigns and the current selection, and selects the
next unearned reward when the selected one is done.

The operations and their documents come from the public Twitch web client
(chunk `features.drops.components.drops-panel`, module "LaneSelector"):
    DropChannelCampaignsProgress(channelID)  campaigns, reward groups, progress
    ProgressionTrackSelections(tracks)       selected lane per track
    SetProgressionTrackSelection(input: {track, lane})   change the selection
The completion rules copy the web client's rules (see `lane_state`).

Off by default. TCPM_DROP_LANE_SWITCH:
    off      (default) no requests at all
    dry-run  read the campaigns and log the selection it would make
    on       also change the selection
TCPM_DROP_LANE_PREFERENCE: optional comma-separated reward names, most wanted
first. Rewards not in the list follow in the order Twitch returns them.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MODE_ENV = "TCPM_DROP_LANE_SWITCH"
PREFERENCE_ENV = "TCPM_DROP_LANE_PREFERENCE"
MODES = ("off", "dry-run", "on")

# Attempts to select one lane. A selection that Twitch ignores or rejects is
# not retried forever.
MAX_SELECT_ATTEMPTS = 2

# Trimmed copies of the web client's documents (same operation names, fields,
# variables and input). They are sent as documents, not as persisted-query
# hashes: the hashes of the web client's printed documents were not
# registered on 2026-10-22, and Twitch accepts a document from any client.
DOCUMENTS = {
    "DropChannelCampaignsProgress": (
        "query DropChannelCampaignsProgress($channelID: ID!) {"
        " channelDropCampaignsProgress(channelID: $channelID) {"
        " id name brandName startAt endAt game { id name }"
        " rewardGroups { id name track lane"
        " progressCriteria { requirementType"
        " requirements { minutesWatched subs turboSubs }"
        " isRepeatable isAutoProgressable"
        " repeatableConfig { repeatableTimes resetIntervalSeconds"
        " isLastChanceExtensionEnabled shouldCompleteAllRepeatsToEarn } }"
        " rewards { id name distributionType isAccountConnected }"
        " self { status currentMinutesWatched grantCount nextEligibleAt"
        " currentWindow { startedAt expiresAt } } } } }"
    ),
    "ProgressionTrackSelections": (
        "query ProgressionTrackSelections($tracks: [ID!]!) {"
        " trackSelections(tracks: $tracks) { id track lane } }"
    ),
    "SetProgressionTrackSelection": (
        "mutation SetProgressionTrackSelection($input: SetTrackSelectionInput!) {"
        " setTrackSelection(input: $input) { error } }"
    ),
}

# Reward group status values (web client enum).
IN_PROGRESS = "IN_PROGRESS"
CLAIMABLE = "CLAIMABLE"
FINISHED_STATUSES = ("CLAIMED", "FULFILLED", "EXPIRED")

# Lane states.
AVAILABLE = "available"  # progress can be made now (not started or in progress)
DONE_TODAY = "done_today"  # today's minutes are watched, next window later
FINISHED = "finished"  # nothing more to earn


def mode_from_env():
    mode = os.environ.get(MODE_ENV, "off").strip().lower() or "off"
    if mode not in MODES:
        logger.warning(f"{MODE_ENV}={mode!r} is not one of {MODES}; using off")
        return "off"
    return mode


def preference_from_env():
    raw = os.environ.get(PREFERENCE_ENV, "")
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def group_state(group, now):
    """State of one reward group, with the web client's rules
    (drops-panel: day list builder and the "is complete" check)."""
    me = group.get("self")
    if me is None:
        # Not started. The web client shows it as a reward to pick.
        return AVAILABLE

    status = me.get("status")
    criteria = group.get("progressCriteria") or {}
    repeat = criteria.get("repeatableConfig") or {}
    days = repeat.get("repeatableTimes") or 0
    complete_all = repeat.get("shouldCompleteAllRepeatsToEarn") is True

    if status in FINISHED_STATUSES:
        return FINISHED
    if complete_all and days > 1 and (me.get("grantCount") or 0) >= days:
        return FINISHED
    if status == CLAIMABLE:
        return DONE_TODAY if days > 1 else FINISHED
    if status != IN_PROGRESS:
        return FINISHED

    next_eligible = _parse_time(me.get("nextEligibleAt"))
    if next_eligible is not None and next_eligible > now:
        return DONE_TODAY

    required = (criteria.get("requirements") or {}).get("minutesWatched") or 0
    current = me.get("currentMinutesWatched") or 0
    expires = _parse_time((me.get("currentWindow") or {}).get("expiresAt"))
    if expires is not None and expires <= now:
        current = 0  # the window is over: its minutes no longer count
    if required > 0 and current >= required:
        return DONE_TODAY
    return AVAILABLE


def current_stage(groups):
    """The group that gets progress now. A lane can hold several groups that
    are stages of one reward line (live data: Ivysaur, then Venusaur). Like
    the web client: the first IN_PROGRESS group, else the first group that is
    not started, else the first group."""
    for group in groups:
        if (group.get("self") or {}).get("status") == IN_PROGRESS:
            return group
    for group in groups:
        if group.get("self") is None:
            return group
    return groups[0]


def lane_state(groups, now):
    """State of the lane's current stage. A later stage that is not started
    does not make the lane available while the current stage is done for
    today."""
    return group_state(current_stage(groups), now)


def lane_names(groups):
    """Names for the lane: the current stage first, then the other stages."""
    names = []
    first = current_stage(groups)
    for group in [first] + [g for g in groups if g is not first]:
        if group.get("name"):
            names.append(group["name"])
        names.extend(r["name"] for r in group.get("rewards") or [] if r.get("name"))
    return names


def tracks_from_campaigns(campaigns):
    """{track: {lane: [groups]}} for tracks with more than one lane, in the
    order Twitch returns them."""
    tracks = {}
    for campaign in campaigns or []:
        for group in campaign.get("rewardGroups") or []:
            track, lane = group.get("track"), group.get("lane")
            if not track or not lane:
                continue
            tracks.setdefault(track, {}).setdefault(lane, []).append(group)
    return {t: lanes for t, lanes in tracks.items() if len(lanes) > 1}


def decide(lanes, selected, preference=(), now=None):
    """Return (action, lane) for one track.

    action is "keep" (the selected lane can still make progress), "switch"
    (select `lane`), "wait" (every lane is done for today) or "finished"
    (nothing more to earn on this track).
    """
    now = now or datetime.now(timezone.utc)
    states = {lane: lane_state(groups, now) for lane, groups in lanes.items()}
    if all(state == FINISHED for state in states.values()):
        return "finished", None
    if states.get(selected) == AVAILABLE:
        return "keep", selected

    def rank(item):
        index, lane = item
        names = [n.lower() for n in lane_names(lanes[lane])]
        hits = [preference.index(n) for n in names if n in preference]
        return (min(hits) if hits else len(preference), index)

    candidates = [
        lane
        for _, lane in sorted(enumerate(lanes), key=rank)
        if states[lane] == AVAILABLE
    ]
    if not candidates:
        return "wait", None
    return "switch", candidates[0]


def _data(response, key):
    if not isinstance(response, dict):
        return None
    return (response.get("data") or {}).get(key)


class DropLaneSwitcher(object):
    __slots__ = ["mode", "preference", "attempts", "finished", "logged"]

    def __init__(self, mode=None, preference=None):
        self.mode = mode if mode is not None else mode_from_env()
        self.preference = (
            [p.lower() for p in preference]
            if preference is not None
            else preference_from_env()
        )
        self.attempts = {}  # (track, lane) -> select attempts
        self.finished = set()  # channel ids with nothing left to earn
        self.logged = set()  # one-time log keys

    def __log_once(self, key, level, message):
        if key in self.logged:
            return
        self.logged.add(key)
        logger.log(level, message, extra={"emoji": ":package:"})

    def run(self, channel_id, channel_name, post_gql_request, now=None):
        """Check one channel. Returns the list of (track, lane) selections made
        (or that would be made, in dry-run)."""
        if self.mode == "off" or not channel_id or channel_id in self.finished:
            return []
        now = now or datetime.now(timezone.utc)

        campaigns = _data(
            post_gql_request(
                {
                    "operationName": "DropChannelCampaignsProgress",
                    "query": DOCUMENTS["DropChannelCampaignsProgress"],
                    "variables": {"channelID": channel_id},
                }
            ),
            "channelDropCampaignsProgress",
        )
        if campaigns is None:
            return []  # no answer; try again next cycle
        tracks = tracks_from_campaigns(campaigns)
        if not tracks:
            return []

        selections = _data(
            post_gql_request(
                {
                    "operationName": "ProgressionTrackSelections",
                    "query": DOCUMENTS["ProgressionTrackSelections"],
                    "variables": {"tracks": list(tracks)},
                }
            ),
            "trackSelections",
        )
        if selections is None:
            return []
        selected = {s.get("track"): s.get("lane") for s in selections if s}

        changes = []
        finished_tracks = 0
        for track, lanes in tracks.items():
            action, lane = decide(lanes, selected.get(track), self.preference, now)
            if action == "finished":
                finished_tracks += 1
                continue
            if action != "switch":
                if self.mode == "dry-run":
                    current = selected.get(track)
                    label = (
                        lane_names(lanes[current])[:1] if current in lanes else []
                    )
                    self.__log_once(
                        ("dry-run", track, current, action),
                        logging.INFO,
                        f"Reward choice on {channel_name}: "
                        + (
                            f"selected {', '.join(label) or current} can still make "
                            "progress; keeping it"
                            if action == "keep"
                            else "every reward is done for today; nothing to select"
                        )
                        + f" ({MODE_ENV}=dry-run)",
                    )
                continue
            name = ", ".join(lane_names(lanes[lane])[:1]) or lane
            key = (track, lane)
            if self.attempts.get(key, 0) >= MAX_SELECT_ATTEMPTS:
                self.__log_once(
                    ("gave-up",) + key,
                    logging.WARNING,
                    f"Reward choice on {channel_name}: Twitch did not accept the "
                    f"selection of {name} after {MAX_SELECT_ATTEMPTS} attempts. "
                    "Not trying again until the next restart.",
                )
                continue
            if self.mode == "dry-run":
                self.__log_once(
                    ("dry-run", track, selected.get(track), lane),
                    logging.INFO,
                    f"Reward choice on {channel_name}: the selected reward is done; "
                    f"would select {name} ({MODE_ENV}=dry-run)",
                )
                changes.append(key)
                continue

            self.attempts[key] = self.attempts.get(key, 0) + 1
            result = _data(
                post_gql_request(
                    {
                        "operationName": "SetProgressionTrackSelection",
                        "query": DOCUMENTS["SetProgressionTrackSelection"],
                        "variables": {"input": {"track": track, "lane": lane}},
                    }
                ),
                "setTrackSelection",
            )
            if isinstance(result, dict) and result.get("error") is None:
                logger.info(
                    f"Reward choice on {channel_name}: the selected reward is done; "
                    f"selected {name}",
                    extra={"emoji": ":package:"},
                )
                changes.append(key)
            else:
                error = result.get("error") if isinstance(result, dict) else result
                logger.warning(
                    f"Reward choice on {channel_name}: selecting {name} failed: {error}"
                )

        if finished_tracks == len(tracks):
            self.finished.add(channel_id)
            self.__log_once(
                ("finished", channel_id),
                logging.INFO,
                f"Reward choice on {channel_name}: every reward is earned. "
                "Not checking this channel again until the next restart.",
            )
        return changes
