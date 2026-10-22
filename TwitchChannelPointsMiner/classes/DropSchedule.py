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


def drops_order(streamers, indexes, lane_channels, lane_first, sticky=None):
    """Indexes of drop-relevant streamers, in watch order for priority DROPS.

    `lane_channels` maps channel id -> tracks (DropLanes). With `lane_first`,
    ONE channel per reward track comes first: several channels often carry the
    same track (twitchplayspokemon and badgebase, 2026-10-22), and watching
    two of them adds nothing. The pick per track is the channel already picked
    last time (`sticky`, track -> channel id, updated in place) if it is still
    a candidate, else the one with the most viewers. Then the game-drop
    streams, then the other reward-choice channels. Without `lane_first` the
    order of `indexes` is kept.
    """
    relevant = [i for i in indexes if is_drop_relevant(streamers[i], lane_channels)]
    if not lane_first:
        return relevant
    if sticky is None:
        sticky = {}

    candidates = [i for i in relevant if streamers[i].channel_id in lane_channels]
    tracks = []
    for i in candidates:
        for track in sorted(lane_channels[streamers[i].channel_id]):
            if track not in tracks:
                tracks.append(track)

    chosen = []
    for track in tracks:
        carriers = [i for i in candidates if track in lane_channels[streamers[i].channel_id]]
        if any(i in chosen for i in carriers):
            continue  # a chosen channel already covers this track

        def rank(item):
            position, i = item
            viewers = getattr(getattr(streamers[i], "stream", None), "viewers_count", 0) or 0
            return (sticky.get(track) == streamers[i].channel_id, viewers, -position)

        best = max(enumerate(carriers), key=rank)[1]
        sticky[track] = streamers[best].channel_id
        chosen.append(best)
    for track in [t for t in sticky if t not in tracks]:
        del sticky[track]

    games = [i for i in relevant if i not in candidates]
    others = [i for i in candidates if i not in chosen]
    return chosen + games + others
