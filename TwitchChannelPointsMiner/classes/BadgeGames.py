# -*- coding: utf-8 -*-
"""Which games have a free watch-time badge right now (option B, q-1219).

The miner's own campaign list (ViewerDropsDashboard) is empty for its TV-client
login, so free-badge campaigns are found another way:
1. streamdatabase.com/twitch/global-badges lists Twitch global badges, with
   cost (free/paid), a description ("earned by watching a streamer in the
   Witchspire category for 60 minutes") and often a start/end date. It is
   fetched at most once a day, with an honest User-Agent, and cached in
   analytics/badge_games.json. A failed fetch or parse keeps the last cache.
2. For a few of those games per cycle, a few live channels from the game
   directory are asked for their drop campaigns, with a hard request budget
   and jitter. That per-channel answer carries the reward types, the account
   link fields and the minutes, so it is all the free-badge filter
   (FreeBadges.py) needs; the detail query is null for this login.
"""

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SOURCE_URL = "https://www.streamdatabase.com/twitch/global-badges"
USER_AGENT = (
    "TwitchChannelPointsMiner-fork/2.0.7 "
    "(+https://github.com/polo-nyan/Twitch-Channel-Points-Miner-v2.1; "
    "free badge list, at most once a day)"
)
CACHE_FILE = "badge_games.json"
FETCH_EVERY = 86400.0  # one fetch attempt per day, also after a failure
SLUG_RETRY = 7 * 86400.0  # retry an unresolvable game name after a week

GAMES_PER_CYCLE = 3
CHANNELS_PER_GAME = 5
MAX_REQUESTS = 15  # extra GQL operations per drops cycle
DETAIL_RESERVE = 0  # no separate detail query any more (option D)
JITTER = (0.5, 2.0)  # seconds between discovery requests

_NAME_PATTERNS = [
    re.compile(r"\bminutes? (?:of|in) (?:the )?(.+?)(?: category| on | during | for |[.!]|$)", re.I),
    re.compile(r"\bin (?:the )?(.+?) category", re.I),
    re.compile(r"\bwatching (?:the )?(?!a |an |and )(.+?) category", re.I),

    re.compile(r"\bhours? of (.+?) content", re.I),
    re.compile(
        r"\bwatching (?!a |an |the |and |your |any |streamers? |creators? )"
        r"(.+?)(?: for \d| during | on launch)", re.I),
]


def _date(day, clock, end=False):
    """streamdatabase date + time (UTC, "HH:MM" or "HH:MM:SS"). A missing or
    odd time counts as the whole day (end of day for an end date)."""
    if not day:
        return None
    clock = (clock or "").strip()
    if clock.count(":") == 1:
        clock += ":00"
    try:
        return datetime.fromisoformat(f"{day}T{clock}+00:00").timestamp()
    except ValueError:
        try:
            base = datetime.fromisoformat(f"{day}T00:00:00+00:00").timestamp()
        except ValueError:
            return None
        return base + (86400 if end else 0)


def game_name(description):
    """The game or category named in a badge description, or None."""
    if not description or "watch" not in description.lower():
        return None
    for pattern in _NAME_PATTERNS:
        match = pattern.search(description)
        if match:
            name = match.group(1).strip(" .!")
            if 1 < len(name) <= 80:
                return name
    return None


def parse_badge_games(html, now):
    """Free, current watch-time badges -> sorted list of game names."""
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.S)
    if not match:
        raise ValueError("no __NEXT_DATA__ in the page")
    badges = json.loads(match.group(1))["props"]["pageProps"]["initialTwitchGlobalBadges"]
    names = set()
    for badge in badges:
        if badge.get("cost") != "free" or badge.get("removed"):
            continue
        end = _date(badge.get("end_at_date"), badge.get("end_at_time"), end=True)
        start = _date(badge.get("start_at_date"), badge.get("start_at_time"))
        if end is not None and end <= now:
            continue
        if start is not None and start > now:
            continue
        version = (badge.get("current") or {}).get("version") or {}
        name = game_name(version.get("description"))
        if name:
            names.add(name)
    return sorted(names)


class RequestBudget(object):
    __slots__ = ["limit", "used"]

    def __init__(self, limit=MAX_REQUESTS):
        self.limit, self.used = limit, 0

    def take(self, n=1, reserve=0):
        if self.used + n > self.limit - reserve:
            return False
        self.used += n
        return True


class BadgeGameList(object):
    """The streamdatabase badge-game list, fetched at most once a day."""

    __slots__ = ["path", "session", "clock", "state", "warned"]

    def __init__(self, path, session=None, clock=time.time):
        self.path = path
        self.session = session
        self.clock = clock
        self.warned = set()
        self.state = self.__load()

    def __load(self):
        try:
            with open(self.path) as f:
                state = json.load(f)
            if isinstance(state, dict):
                state.setdefault("games", [])
                state.setdefault("slugs", {})
                return state
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as e:
            logger.warning(f"Free badges: cannot read {self.path} ({e})")
        return {"games": [], "fetched_at": 0.0, "attempted_at": 0.0, "slugs": {}}

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".badge_games.")
        with os.fdopen(fd, "w") as f:
            json.dump(self.state, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def games(self):
        """Game names; fetches the page when the last attempt is a day old."""
        now = self.clock()
        if now - float(self.state.get("attempted_at") or 0) >= FETCH_EVERY:
            self.state["attempted_at"] = now
            try:
                response = self.session.get(
                    SOURCE_URL, headers={"User-Agent": USER_AGENT}, timeout=(5, 30))
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                games = parse_badge_games(response.text, now)
                self.state["games"], self.state["fetched_at"] = games, now
                logger.info(f"Free badges: badge-game list fetched, {len(games)} games")
            except Exception as e:  # network, HTTP, parse: keep the cache
                logger.warning(
                    f"Free badges: badge-game list fetch failed ({e}); using the "
                    f"cache from {self.fetched_text()}")
            self.save()
        return list(self.state.get("games") or [])

    def fetched_text(self):
        fetched = float(self.state.get("fetched_at") or 0)
        if not fetched:
            return "never"
        return datetime.fromtimestamp(fetched, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def slug(self, name, resolve, budget):
        """Twitch slug for a game name, cached. `resolve(name)` -> slug or None
        costs one request; None when unknown or the budget is spent."""
        cached = self.state["slugs"].get(name)
        now = self.clock()
        if cached is not None:
            if cached.get("slug") or now - cached.get("at", 0) < SLUG_RETRY:
                return cached.get("slug")
        if not budget.take(1, reserve=DETAIL_RESERVE):
            return None
        slug = resolve(name)
        self.state["slugs"][name] = {"slug": slug, "at": now}
        self.save()
        return slug


def discover(games, game_list, resolve, directory, channel_campaigns, budget,
             rng, sleep, skip_ids=()):
    """Find drop campaigns on live channels of a few badge games.

    resolve(name) -> slug; directory(slug) -> [(channel_id, login)];
    channel_campaigns(channel_id) -> [campaign dicts]. Every call costs one
    request from `budget`, and calls are spread with random jitter.
    Returns (campaigns, probed_channels, games_tried, seen_on): campaigns are
    unique by id, without the ids in `skip_ids`; seen_on maps a campaign id
    to the probed (channel_id, login) pairs that carry it.
    """
    found, probed, tried, seen = {}, 0, [], {}
    for name in rng.sample(games, min(GAMES_PER_CYCLE, len(games))):
        slug = game_list.slug(name, resolve, budget)
        if not slug:
            continue
        if not budget.take(1, reserve=DETAIL_RESERVE):
            break
        sleep(rng.uniform(*JITTER))
        channels = directory(slug)
        tried.append(name)
        for channel_id, login in rng.sample(channels, min(CHANNELS_PER_GAME, len(channels))):
            if not budget.take(1, reserve=DETAIL_RESERVE):
                return list(found.values()), probed, tried, seen
            sleep(rng.uniform(*JITTER))
            probed += 1
            for campaign in channel_campaigns(channel_id) or []:
                cid = (campaign or {}).get("id")
                if not cid or cid in skip_ids:
                    continue
                found.setdefault(cid, campaign)
                seen.setdefault(cid, []).append((channel_id, login))
    return list(found.values()), probed, tried, seen
