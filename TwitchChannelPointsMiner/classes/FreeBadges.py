# -*- coding: utf-8 -*-
"""Free watch-time badges on channels the account does not follow.

Many Twitch global badges are earned by watching any stream in a category
for N minutes ("earned by watching a streamer in the Witchspire category for
60 minutes"). They are ordinary drop campaigns with BADGE rewards, so Twitch's
own drops data lists them. The miner only watches configured streamers, so it
misses the ones no configured streamer plays.

Campaigns are found per channel for the games on the streamdatabase badge
list (BadgeGames.py), because the dashboard list is empty for this login.

TCPM_FREE_BADGES=on (default off) lets the miner watch one random live,
drops-enabled channel for such a campaign, in one of its 2 watch slots, after
the reward-choice channel and the configured game-drop streams.

Operator limit (2026-10-22, q-e860): at most ONE badge on a not-followed
channel at a time. When it is earned or abandoned, a random cooldown of
2.5-5 days (uniform, fresh draw) starts before the next one. The state is
kept in analytics/free_badges.json so a restart does not reset it.
Campaigns that a configured streamer can serve are left to the normal drops
logic and are not limited.

Weekend timer (operator, 2026-10-22): TCPM_FREE_BADGES_WEEKEND=on (default
off) adds a SEPARATE timer for a weekly window, by default Friday 18:00 to
Sunday 22:00 Europe/Berlin (TCPM_FREE_BADGES_WEEKEND_WINDOW, local wall time
via zoneinfo, so it is DST-safe; TCPM_FREE_BADGES_WEEKEND_TZ). Inside the
window a pick uses the weekend timer (a fresh 10-25 h cooldown after each
badge) when it is free, else the weekday timer if that one is free; the
weekday 2.5-5 day timer is neither used nor reset
by weekend picks, and it does not block them. A pick only STARTS inside the
window. A weekend badge still in progress when the window closes is left to
finish: abandoning it would waste the minutes already watched. There is still
only one not-followed-channel badge at a time.
"""

import json
import logging
import os
import random
import tempfile
import time
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

logger = logging.getLogger(__name__)

MODE_ENV = "TCPM_FREE_BADGES"
WEEKEND_ENV = "TCPM_FREE_BADGES_WEEKEND"
WINDOW_ENV = "TCPM_FREE_BADGES_WEEKEND_WINDOW"
TZ_ENV = "TCPM_FREE_BADGES_WEEKEND_TZ"
DEFAULT_WINDOW = "fri 18:00-sun 22:00"
DEFAULT_TZ = "Europe/Berlin"
COOLDOWN_DAYS = (2.5, 5.0)  # weekday timer
WEEKEND_COOLDOWN_HOURS = (10.0, 25.0)  # weekend timer
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DIRECTORY_LIMIT = 20  # channels per directory query
STALE_CHECKS = 3  # cycles watched without progress -> abandon
MISSING_CHECKS = 6  # cycles without a live channel -> abandon
STATE_FILE = "free_badges.json"

DOCUMENTS = {
    # The per-channel campaign list, with the fields the eligibility rule
    # needs. For the miner's TV-client login this answer is filled in, while
    # user.dropCampaign(id) comes back null (checked 2026-10-22).
    "FreeBadgeChannelDrops": (
        "query FreeBadgeChannelDrops($channelID: ID!) { channel(id: $channelID) {"
        " id viewerDropCampaigns {"
        " id name status startAt endAt accountLinkURL self { isAccountConnected }"
        " game { id name displayName slug } allow { isEnabled channels { id } }"
        " timeBasedDrops { id name requiredMinutesWatched startAt endAt"
        " benefitEdges { benefit { id name distributionType } }"
        " self { currentMinutesWatched isClaimed } } } } }"
    ),
    "FreeBadgeGame": (
        "query FreeBadgeGame($name: String!, $slug: String!) {"
        " byName: game(name: $name) { id slug } bySlug: game(slug: $slug) { id slug } }"
    ),
    "FreeBadgeDirectory": (
        "query FreeBadgeDirectory($slug: String!, $limit: Int, $options: GameStreamOptions) {"
        " game(slug: $slug) { id name streams(first: $limit, options: $options) {"
        " edges { node { id viewersCount broadcaster { id login } } } } } }"
    ),
}


def mode_from_env():
    mode = os.environ.get(MODE_ENV, "off").strip().lower() or "off"
    if mode not in ("off", "on"):
        logger.warning(f"{MODE_ENV}={mode!r} is not on/off; using off")
        return "off"
    return mode


def parse_window(text):
    """'fri 18:00-sun 22:00' -> (start, end) in minutes since Monday 00:00."""
    def point(part):
        day, clock = part.strip().lower().split()
        hours, minutes = clock.split(":")
        return DAYS.index(day[:3]) * 1440 + int(hours) * 60 + int(minutes)

    start, end = text.split("-")
    return point(start), point(end)


class WeekendWindow(object):
    """A weekly local-time window, e.g. Friday 18:00 to Sunday 22:00."""

    __slots__ = ["start", "end", "tz", "text"]

    def __init__(self, text=DEFAULT_WINDOW, tz=DEFAULT_TZ):
        self.text = text
        self.start, self.end = parse_window(text)
        self.tz = ZoneInfo(tz)

    def contains(self, ts):
        local = datetime.fromtimestamp(ts, self.tz)
        minute = local.weekday() * 1440 + local.hour * 60 + local.minute
        if self.start <= self.end:
            return self.start <= minute < self.end
        return minute >= self.start or minute < self.end  # wraps over Monday


def weekend_from_env():
    if os.environ.get(WEEKEND_ENV, "off").strip().lower() != "on":
        return None
    text = os.environ.get(WINDOW_ENV, "").strip() or DEFAULT_WINDOW
    tz = os.environ.get(TZ_ENV, "").strip() or DEFAULT_TZ
    try:
        return WeekendWindow(text, tz)
    except Exception as e:
        logger.warning(f"Free badges: weekend window {text!r} ({tz}) unusable ({e}); weekend timer off")
        return None


def _time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _drop_open(drop, now):
    start, end = _time(drop.get("startAt")), _time(drop.get("endAt"))
    if start is not None and now < start:
        return False
    if end is not None and now >= end:
        return False
    return not (drop.get("self") or {}).get("isClaimed")


def eligibility(campaign, now):
    """(eligible, reason) for a campaign from the per-channel answer.

    Rule (q-6725): every drop's reward is a BADGE; no accountLinkURL, or
    self.isAccountConnected is true; allow.isEnabled is known (false means no
    channel restriction, true means only the listed channels); every drop
    needs more than 0 minutes; the campaign is active and not finished. A
    field that is missing or null means "unknown", and unknown means no pick.
    """
    if not isinstance(campaign, dict):
        return False, "no data"
    status = campaign.get("status")
    if status is None:
        return False, "status missing"
    if status != "ACTIVE":
        return False, f"status {status}"
    start, end = _time(campaign.get("startAt")), _time(campaign.get("endAt"))
    if start is None or end is None:
        return False, "start/end missing"
    if not (start <= now < end):
        return False, "outside its dates"
    if "accountLinkURL" not in campaign:
        return False, "accountLinkURL missing"
    me = campaign.get("self")
    if not isinstance(me, dict) or not isinstance(me.get("isAccountConnected"), bool):
        return False, "self.isAccountConnected missing"
    if campaign.get("accountLinkURL") and me["isAccountConnected"] is not True:
        return False, "needs a linked game account"
    allow = campaign.get("allow")
    if not isinstance(allow, dict) or not isinstance(allow.get("isEnabled"), bool):
        return False, "allow.isEnabled missing"
    if allow["isEnabled"] and not isinstance(allow.get("channels"), list):
        return False, "allow.channels missing"
    if not (campaign.get("game") or {}).get("slug"):
        return False, "game slug missing"
    drops = campaign.get("timeBasedDrops")
    if not isinstance(drops, list) or not drops:
        return False, "no time-based drops"
    any_open = False
    for drop in drops:
        minutes = drop.get("requiredMinutesWatched")
        if not isinstance(minutes, int):
            return False, "requiredMinutesWatched missing"
        if minutes <= 0:
            return False, "not a watch-time drop (0 minutes)"
        edges = drop.get("benefitEdges")
        if not isinstance(edges, list) or not edges:
            return False, "rewards missing"
        for edge in edges:
            kind = (edge.get("benefit") or {}).get("distributionType")
            if kind is None:
                return False, "reward type missing"
            if kind != "BADGE":
                return False, f"reward type {kind}"
        state = drop.get("self")
        if not isinstance(state, dict) or not isinstance(state.get("isClaimed"), bool):
            return False, "drop progress missing"
        any_open = any_open or _drop_open(drop, now)
    if not any_open:
        return False, "all drops claimed or closed"
    return True, "eligible"


def campaign_eligible(campaign, now):
    return eligibility(campaign, now)[0]


def allowed_channel_ids(campaign):
    allow = campaign.get("allow") or {}
    if allow.get("isEnabled") is False:
        return set()
    return {c["id"] for c in allow.get("channels") or [] if c and c.get("id")}


def watched_minutes(campaign):
    return sum(
        (d.get("self") or {}).get("currentMinutesWatched") or 0
        for d in campaign.get("timeBasedDrops") or []
    )


def badge_names(campaign):
    names = []
    for drop in campaign.get("timeBasedDrops") or []:
        for edge in drop.get("benefitEdges") or []:
            name = (edge.get("benefit") or {}).get("name")
            if name and name not in names:
                names.append(name)
    return names


def covered_by_configured(campaign, configured):
    """True when a configured, online streamer with drops on can serve the
    campaign: same game, and in the allow list when there is one.
    `configured` is a list of (channel_id, game_id)."""
    game_id = (campaign.get("game") or {}).get("id")
    allowed = allowed_channel_ids(campaign)
    for channel_id, streamer_game in configured:
        if streamer_game and str(streamer_game) == str(game_id):
            if not allowed or channel_id in allowed:
                return True
    return False


def _fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class FreeBadgePlanner(object):
    __slots__ = ["mode", "path", "rng", "clock", "state", "logged", "watched", "weekend"]

    def __init__(self, path, mode=None, rng=None, clock=time.time, weekend="env"):
        """`weekend`: a WeekendWindow, None (no weekend timer), or "env"."""
        self.mode = mode if mode is not None else mode_from_env()
        self.weekend = weekend_from_env() if weekend == "env" else weekend
        self.path = path
        self.rng = rng or random.SystemRandom()
        self.clock = clock
        self.logged = set()
        self.watched = False  # a minute-watched went to the target since the last check
        self.state = self.__load()

    @property
    def enabled(self):
        return self.mode == "on"

    @property
    def active(self):
        return self.state.get("active")

    @property
    def target(self):
        """Login of the not-followed channel to watch now, or None."""
        active = self.active
        return active.get("channel") if active else None

    # ----------------------------- state file ----------------------------- #
    def __load(self):
        try:
            with open(self.path) as f:
                state = json.load(f)
            if isinstance(state, dict):
                return {"cooldown_until": float(state.get("cooldown_until") or 0),
                        "weekend_cooldown_until": float(state.get("weekend_cooldown_until") or 0),
                        "active": state.get("active")}
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as e:
            logger.warning(f"Free badges: cannot read {self.path} ({e}); starting clean")
        return {"cooldown_until": 0.0, "weekend_cooldown_until": 0.0, "active": None}

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".free_badges.")
        with os.fdopen(fd, "w") as f:
            json.dump(self.state, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def draw_cooldown(self):
        return self.rng.uniform(*COOLDOWN_DAYS) * 86400.0

    def draw_weekend_cooldown(self):
        return self.rng.uniform(*WEEKEND_COOLDOWN_HOURS) * 3600.0

    def free_timer(self, now):
        """'weekend' or 'weekday' if a pick may start now, else None. Inside
        the weekend window the weekend timer is used first, so the weekday
        timer stays untouched."""
        if self.weekend is not None and self.weekend.contains(now):
            if now >= self.state["weekend_cooldown_until"]:
                return "weekend"
        if now >= self.state["cooldown_until"]:
            return "weekday"
        return None

    # ------------------------------- logic -------------------------------- #
    def __finish(self, reason, now):
        active = self.active
        timer = active.get("timer", "weekday")
        if timer == "weekend":
            key, until = "weekend_cooldown_until", now + self.draw_weekend_cooldown()
        else:
            key, until = "cooldown_until", now + self.draw_cooldown()
        self.state[key] = until
        self.state["active"] = None
        self.save()
        logger.info(
            f"Free badge {active.get('badge')} ({active.get('game')}) {reason}. "
            f"{timer} timer: next {timer} pick not before {_fmt(until)}",
            extra={"emoji": ":package:"},
        )

    def __pick_channel(self, campaign, directory, exclude=()):
        """Random live drops-enabled channel for the campaign's game,
        respecting the allow list. `directory(slug)` returns
        [(channel_id, login)]. Returns (channel_id, login) or (None, None)."""
        allowed = allowed_channel_ids(campaign)
        live = [
            (cid, login)
            for cid, login in directory(campaign["game"]["slug"])
            if login and login not in exclude and (not allowed or cid in allowed)
        ]
        return self.rng.choice(live) if live else (None, None)

    def update(self, campaigns, configured, directory, followed=(), now=None):
        """Run once per drops cycle.

        campaigns:  campaign dicts from the per-channel answer (FreeBadgeChannelDrops)
        configured: [(channel_id, game_id)] of online configured streamers
                    with drops on
        directory:  callable(slug) -> [(channel_id, login)] live channels
        followed:   logins of configured streamers (never picked here)
        Returns the login to watch, or None.
        """
        if not self.enabled:
            return None
        now = self.clock() if now is None else now
        followed = {f.lower() for f in followed}
        by_id = {c["id"]: c for c in campaigns if c and c.get("id")}

        active = self.active
        if active and active["campaign_id"] not in by_id:
            # No answer for the active campaign (channel offline, request
            # failed): keep it, but not forever.
            if now >= float(active.get("end_at") or 0) and active.get("end_at"):
                self.__finish("ended", now)
                return None
            active["missing"] = active.get("missing", 0) + 1
            active["channel"] = active["channel_id"] = None
            if active["missing"] >= MISSING_CHECKS:
                self.__finish("abandoned (no answer for the campaign)", now)
                return None
            self.save()
            return None
        if active:
            campaign = by_id.get(active["campaign_id"])
            if campaign is None or not campaign_eligible(campaign, now):
                done = campaign is not None and not any(
                    _drop_open(d, now) for d in campaign.get("timeBasedDrops") or [])
                self.__finish("earned" if done else "ended or no longer eligible", now)
                return None
            minutes = watched_minutes(campaign)
            if minutes > active.get("minutes", 0):
                active["stale"] = 0
            elif self.watched:
                # Watched since the last check, but no progress.
                active["stale"] = active.get("stale", 0) + 1
            self.watched = False
            active["minutes"] = minutes
            if active["stale"] >= STALE_CHECKS:
                self.__finish(f"abandoned (no progress in {STALE_CHECKS} checks)", now)
                return None
            active["missing"] = 0
            if not active.get("channel"):
                channel_id, channel = self.__pick_channel(campaign, directory, followed)
                active["channel"], active["channel_id"] = channel, channel_id
                if channel:
                    active["missing"] = 0
                    logger.info(
                        f"Free badge {active['badge']} ({active['game']}): watching "
                        f"{channel}", extra={"emoji": ":package:"})
                else:
                    active["missing"] = active.get("missing", 0) + 1
                    if active["missing"] >= MISSING_CHECKS:
                        self.__finish("abandoned (no live channel)", now)
                        return None
            self.save()
            return active.get("channel")

        timer = self.free_timer(now)
        if timer is None:
            weekend = (
                f", weekend timer until {_fmt(self.state['weekend_cooldown_until'])}"
                if self.weekend is not None else ""
            )
            self.__log_once(
                ("cooldown", self.state["cooldown_until"], self.state["weekend_cooldown_until"]),
                f"Free badges: weekday timer until {_fmt(self.state['cooldown_until'])}{weekend}")
            return None

        candidates = []
        for c in by_id.values():
            ok, reason = eligibility(c, now)
            if not ok:
                game = (c.get("game") or {}).get("displayName") or "?"
                self.__log_once(("skip", c.get("id"), reason),
                                f"Free badges: skip {c.get('name')} ({game}): {reason}")
            elif not covered_by_configured(c, configured):
                candidates.append(c)
        self.rng.shuffle(candidates)
        for campaign in candidates:
            channel_id, channel = self.__pick_channel(campaign, directory, followed)
            if not channel:
                continue
            badge = ", ".join(badge_names(campaign)) or campaign.get("name")
            self.state["active"] = {
                "campaign_id": campaign["id"],
                "badge": badge,
                "game": campaign["game"].get("displayName") or campaign["game"].get("name"),
                "channel": channel,
                "channel_id": channel_id,
                "slug": campaign["game"]["slug"],
                "end_at": _time(campaign.get("endAt")),
                "picked_at": now,
                "minutes": watched_minutes(campaign),
                "stale": 0,
                "missing": 0,
                "timer": timer,
            }
            self.save()
            after = "10-25 h" if timer == "weekend" else "2.5-5 day"
            logger.info(
                f"Free badge pick ({timer} timer): {badge} "
                f"({self.state['active']['game']}) on {channel}; after it, a random "
                f"{after} {timer} cooldown starts",
                extra={"emoji": ":package:"},
            )
            return channel
        self.__log_once(("nothing", int(now // 86400)),
                        "Free badges: nothing eligible right now")
        return None

    def note_watched(self, login):
        """A minute-watched event went to `login`."""
        if self.target and login == self.target:
            self.watched = True

    def channel_offline(self):
        """The watched channel went offline: pick another one next cycle."""
        if self.active:
            self.state["active"]["channel"] = None
            self.save()

    def __log_once(self, key, message):
        self.log_once(key, message, stacklevel=3)

    def log_once(self, key, message, stacklevel=2):
        if key not in self.logged:
            self.logged.add(key)
            logger.info(message, extra={"emoji": ":package:"}, stacklevel=stacklevel)


def pick_slot(targets, free, is_relevant, keeps_streak):
    """Watch list with the free-badge streamer in at most one of the 2 slots,
    after the reward-choice and game-drop streams: it takes a free slot, or
    the slot of the last stream that is neither drop-relevant nor collecting
    a watch streak. Returns the new list."""
    targets = list(targets)
    if free is None or any(t is free for t in targets):
        return targets
    if len(targets) < 2:
        return targets + [free]
    for i in range(len(targets) - 1, -1, -1):
        if not is_relevant(targets[i]) and not keeps_streak(targets[i]):
            return targets[:i] + targets[i + 1:] + [free]
    return targets


def free_badge_cycle(planner, game_list, ops, configured, followed, now, skip_ids,
                     sleep=time.sleep):
    """One drops cycle of the free-badge feature, with a hard request budget.

    ops: object with directory(slug) -> [(channel_id, login)],
    game(name) -> slug and channel_campaigns(channel_id) -> [campaign dicts]
    (the FreeBadgeChannelDrops answer). Each call is one request.
    Returns (login or None, summary text, requests used).
    """
    from TwitchChannelPointsMiner.classes.BadgeGames import RequestBudget, discover

    budget = RequestBudget()
    memo = {}
    seen_by_slug = {}  # game slug -> probed live channels that carry a campaign

    def probe_directory(slug):  # discover() takes the budget itself
        if slug not in memo:
            memo[slug] = ops.directory(slug)
        return memo[slug]

    def directory(slug):  # for the planner: probed channels, else one request
        if slug not in memo and slug in seen_by_slug:
            return seen_by_slug[slug]
        if slug not in memo:
            if not budget.take(1):
                return []
            memo[slug] = ops.directory(slug)
        return memo[slug]

    if planner.active is None and planner.free_timer(now) is None:
        # Both timers are cooling down: no request at all.
        planner.update([], configured, lambda slug: [], followed=followed, now=now)
        return None, "both timers cooling down", 0

    active = planner.active
    if active is not None:
        # Progress of the active badge, from the watched channel's answer, or
        # from up to 2 other live channels of the game.
        campaigns = []

        def ask(channel_id):
            if not budget.take(1):
                return []
            return ops.channel_campaigns(channel_id) or []

        def has_active(answer):
            return any(c.get("id") == active["campaign_id"] for c in answer)

        if active.get("channel_id"):
            campaigns = ask(active["channel_id"])
        if not has_active(campaigns) and active.get("slug"):
            others = {f.lower() for f in followed} | {(active.get("channel") or "").lower()}
            for channel_id, login in directory(active["slug"])[:2]:
                if login.lower() in others:
                    continue
                campaigns = ask(channel_id)
                if has_active(campaigns):
                    break
        summary = "tracking the active badge"
    else:
        games = game_list.games()
        if not games:
            planner.log_once(("no-list", int(now // 86400)),
                             "Free badges: nothing eligible (no badge-game list)")
            return None, "no badge-game list", budget.used
        campaigns, probed, tried, seen = discover(
            games, game_list, ops.game, probe_directory, ops.channel_campaigns,
            budget, planner.rng, sleep, skip_ids=skip_ids)
        for campaign in campaigns:
            slug = (campaign.get("game") or {}).get("slug")
            if slug:
                channels = seen_by_slug.setdefault(slug, [])
                channels += [c for c in seen.get(campaign["id"], []) if c not in channels]
        for campaign in campaigns:
            if not campaign_eligible(campaign, now):
                skip_ids.add(campaign["id"])
        summary = (
            f"{len(games)} games listed (fetched {game_list.fetched_text()}), "
            f"tried {', '.join(tried) or 'none'}, {probed} channels probed, "
            f"{len(campaigns)} campaigns found, "
            f"{sum(campaign_eligible(c, now) for c in campaigns)} eligible"
        )
    login = planner.update(campaigns, configured, directory, followed=followed, now=now)
    return login, summary, budget.used
