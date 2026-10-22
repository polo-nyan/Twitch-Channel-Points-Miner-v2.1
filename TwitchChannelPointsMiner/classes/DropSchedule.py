# -*- coding: utf-8 -*-
"""Small, dependency-free scheduling rules for the drops loops."""

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
