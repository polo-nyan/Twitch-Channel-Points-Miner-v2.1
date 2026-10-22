# -*- coding: utf-8 -*-
"""Small, dependency-free scheduling rules for the drops loops."""

import os

# The drops loop refreshes campaigns, claims from the inventory and checks the
# reward-choice campaigns at this interval.
CAMPAIGNS_REFRESH_SECONDS = 30 * 60


def campaigns_refresh_due(last_update, now):
    """True when the campaigns were never read or the interval has passed.
    (The old check `((now - last) / 30) > 30` fired after 15 minutes.)"""
    return last_update == 0 or (now - last_update) > CAMPAIGNS_REFRESH_SECONDS


def is_drop_relevant(streamer, lane_channels):
    """A streamer is watched for drops when its game has a drops campaign
    (drops_condition), or when watching its channel now adds progress to a
    reward-choice campaign (channel id in `lane_channels`, see DropLanes)."""
    if streamer.drops_condition() is True:
        return True
    return (
        streamer.settings.claim_drops is True
        and streamer.is_online is True
        and bool(streamer.channel_id)
        and streamer.channel_id in lane_channels
    )


LANE_PRIORITY_ENV = "TCPM_DROP_LANE_PRIORITY"


def lane_priority_enabled(lane_switch_mode):
    """Watch reward-choice channels ahead of game-drop streams?
    TCPM_DROP_LANE_PRIORITY=on|off; default on only when the lane switch is on."""
    raw = os.environ.get(LANE_PRIORITY_ENV, "").strip().lower()
    if raw in ("on", "1", "true", "yes"):
        return True
    if raw in ("off", "0", "false", "no"):
        return False
    return lane_switch_mode == "on"


def drops_order(streamers, indexes, lane_channels, lane_first):
    """Indexes of drop-relevant streamers, in watch order for priority DROPS.
    With `lane_first`, channels with a reward-choice lane to progress come
    before game-drop streams; otherwise the order of `indexes` is kept."""
    relevant = [i for i in indexes if is_drop_relevant(streamers[i], lane_channels)]
    if not lane_first:
        return relevant
    lanes = [i for i in relevant if streamers[i].channel_id in lane_channels]
    return lanes + [i for i in relevant if i not in lanes]
