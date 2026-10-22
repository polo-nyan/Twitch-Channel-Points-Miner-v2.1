# -*- coding: utf-8 -*-
"""Download real HLS segments for the streams watched for drops.

Since about 2026-10, Twitch seems to count drop progress only while the
stream's HLS media segments are downloaded, as a player does (upstream #846).
Minute-watched events alone did not move any drop in a 22-minute check on
2026-10-22. The minute-watched loop only sends a HEAD request for the newest
segment.

TCPM_HLS_SEGMENT_FETCH:
    off         (default) nothing changes
    minute      for each drop-relevant watched stream (at most 2), download
                the newest 160p segment at a random interval of 30-60 s,
                drawn fresh for each fetch and for each stream
    continuous  not implemented; logs a warning and does nothing
TCPM_HLS_DAILY_BUDGET_MB: bytes per UTC day for all fetches (default 300).
TCPM_HLS_SEGMENT_CHANNELS: optional comma-separated logins; when set, only
    these channels are fetched.

The fetches run in their own thread, with small random delays between the
playlist and segment requests, so they do not line up with the minute-watched
events. The minute-watched events do not change.
"""

import logging
import os
import random
import threading
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

MODE_ENV = "TCPM_HLS_SEGMENT_FETCH"
BUDGET_ENV = "TCPM_HLS_DAILY_BUDGET_MB"
CHANNELS_ENV = "TCPM_HLS_SEGMENT_CHANNELS"
MODES = ("off", "minute", "continuous")

MIN_INTERVAL = 30.0
MAX_INTERVAL = 60.0
MAX_STREAMS = 2
DEFAULT_BUDGET_MB = 300
# Seconds before the next request in one fetch (playlist -> segment).
STEP_DELAY = (0.3, 2.5)
# Re-read the master playlist (new access token) after this many seconds.
MASTER_REFRESH = (8 * 60.0, 15 * 60.0)
REPORT_EVERY = 3600.0
TIMEOUT = (5, 20)


def mode_from_env():
    mode = os.environ.get(MODE_ENV, "off").strip().lower() or "off"
    if mode not in MODES:
        logger.warning(f"{MODE_ENV}={mode!r} is not one of {MODES}; using off")
        return "off"
    if mode == "continuous":
        logger.warning(
            f"{MODE_ENV}=continuous is not implemented yet; HLS segment fetch is off"
        )
        return "off"
    return mode


def budget_from_env():
    raw = os.environ.get(BUDGET_ENV, "").strip()
    try:
        mb = float(raw) if raw else DEFAULT_BUDGET_MB
    except ValueError:
        logger.warning(f"{BUDGET_ENV}={raw!r} is not a number; using {DEFAULT_BUDGET_MB}")
        mb = DEFAULT_BUDGET_MB
    return int(mb * 1024 * 1024)


def channels_from_env():
    raw = os.environ.get(CHANNELS_ENV, "")
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def lowest_variant(master_text):
    """URL of the 160p variant, else the last variant (Twitch lists the
    lowest quality last when allow_source is not set)."""
    lines = master_text.splitlines()
    variants = []
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            variants.append((line, lines[i + 1].strip()))
    for info, url in variants:
        if 'VIDEO="160p' in info:
            return url
    return variants[-1][1] if variants else None


def newest_segment(media_text):
    segments = [
        line.strip()
        for line in media_text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return segments[-1] if segments else None


def _day(now):
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


class HLSFetcher(object):
    __slots__ = [
        "mode", "budget", "channels", "playlist_url_for", "user_agent",
        "session", "rng", "clock", "sleep", "lock", "streams", "day",
        "bytes_today", "segments_today", "budget_logged", "next_report",
        "thread", "running", "threaded",
    ]

    def __init__(self, playlist_url_for, user_agent=None, mode=None, budget=None,
                 channels=None, session=None, rng=None, clock=time.time,
                 sleep=time.sleep, threaded=True):
        """`playlist_url_for(login)` returns the master playlist URL (with a
        fresh access token) or None."""
        self.mode = mode if mode is not None else mode_from_env()
        if self.mode == "continuous":
            logger.warning(f"{MODE_ENV}=continuous is not implemented yet; using off")
            self.mode = "off"
        self.budget = budget if budget is not None else budget_from_env()
        self.channels = (
            {c.lower() for c in channels} if channels is not None else channels_from_env()
        )
        self.playlist_url_for = playlist_url_for
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self.rng = rng or random.SystemRandom()
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.streams = {}  # login -> state dict
        self.day = None
        self.bytes_today = 0
        self.segments_today = 0
        self.budget_logged = False
        self.next_report = 0.0
        self.thread = None
        self.running = False
        self.threaded = threaded  # False in tests: call tick() by hand

    @property
    def enabled(self):
        return self.mode == "minute"

    def next_interval(self):
        return self.rng.uniform(MIN_INTERVAL, MAX_INTERVAL)

    def set_targets(self, logins):
        """Streams to fetch now: drop-relevant streams the miner watches."""
        if not self.enabled:
            return []
        wanted = []
        for login in logins:
            login = login.lower()
            if self.channels and login not in self.channels:
                continue
            if login not in wanted:
                wanted.append(login)
        wanted = wanted[:MAX_STREAMS]
        now = self.clock()
        with self.lock:
            for login in list(self.streams):
                if login not in wanted:
                    del self.streams[login]
            for login in wanted:
                if login not in self.streams:
                    # First fetch after a random delay too: no fixed start.
                    self.streams[login] = {
                        "next_due": now + self.rng.uniform(0, MAX_INTERVAL - MIN_INTERVAL),
                        "media_url": None,
                        "media_refresh_at": 0.0,
                    }
        if wanted:
            self.start()
        return wanted

    # ------------------------------------------------------------------ #
    def __roll_day(self, now):
        day = _day(now)
        if self.day is None:
            self.day = day
        elif day != self.day:
            logger.info(
                f"HLS segment fetch on {self.day}: "
                f"{self.bytes_today / 1048576:.1f} MB, {self.segments_today} segments"
            )
            self.day, self.bytes_today, self.segments_today = day, 0, 0
            self.budget_logged = False
        if now >= self.next_report:
            if self.next_report:
                logger.info(
                    f"HLS segment fetch today: {self.bytes_today / 1048576:.1f} MB, "
                    f"{self.segments_today} segments (budget "
                    f"{self.budget / 1048576:.0f} MB)"
                )
            self.next_report = now + REPORT_EVERY

    def __over_budget(self):
        if self.bytes_today < self.budget:
            return False
        if not self.budget_logged:
            self.budget_logged = True
            logger.info(
                f"HLS segment fetch: daily budget of {self.budget / 1048576:.0f} MB "
                "reached; no more fetches until 00:00 UTC"
            )
        return True

    def __get(self, url, stream=False):
        headers = {"User-Agent": self.user_agent} if self.user_agent else {}
        response = self.session.get(url, headers=headers, timeout=TIMEOUT, stream=stream)
        size = 0
        try:
            if stream:
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)  # body is discarded
            else:
                size = len(response.content or b"")
        finally:
            if stream:
                response.close()
        self.bytes_today += size
        return response

    def fetch(self, login, state, now):
        """One fetch for one stream. Returns True when a segment was read."""
        if state["media_url"] is None or now >= state["media_refresh_at"]:
            master_url = self.playlist_url_for(login)
            if not master_url:
                return False
            master = self.__get(master_url)
            if master.status_code != 200:
                return False
            state["media_url"] = lowest_variant(master.text)
            state["media_refresh_at"] = now + self.rng.uniform(*MASTER_REFRESH)
            if not state["media_url"]:
                return False
            self.sleep(self.rng.uniform(*STEP_DELAY))

        media = self.__get(state["media_url"])
        if media.status_code != 200:
            state["media_url"] = None  # token expired: new master next time
            return False
        segment_url = newest_segment(media.text)
        if not segment_url:
            return False
        if self.__over_budget():
            return False
        self.sleep(self.rng.uniform(*STEP_DELAY))
        segment = self.__get(segment_url, stream=True)
        if segment.status_code != 200:
            return False
        self.segments_today += 1
        logger.debug(f"HLS segment fetched for {login}")
        return True

    def tick(self, now=None):
        """Run the fetches that are due. Returns the number of segments read."""
        now = self.clock() if now is None else now
        self.__roll_day(now)
        with self.lock:
            due = [(l, s) for l, s in self.streams.items() if s["next_due"] <= now]
        self.rng.shuffle(due)
        done = 0
        for login, state in due:
            # Fresh, independent draw for each fetch and each stream.
            state["next_due"] = now + self.next_interval()
            if self.__over_budget():
                continue
            try:
                done += bool(self.fetch(login, state, now))
            except requests.exceptions.RequestException as e:
                state["media_url"] = None
                logger.debug(f"HLS segment fetch for {login} failed: {e}")
        return done

    def seconds_to_next(self, now):
        with self.lock:
            dues = [s["next_due"] for s in self.streams.values()]
        if not dues:
            return MAX_INTERVAL
        return min(max(min(dues) - now, 1.0), MAX_INTERVAL)

    def start(self):
        if self.thread is not None or not self.enabled or not self.threaded:
            return
        self.running = True
        self.thread = threading.Thread(target=self.__loop, name="HLSFetcher", daemon=True)
        self.thread.start()
        logger.info(
            f"HLS segment fetch on ({MODE_ENV}=minute, every {MIN_INTERVAL:.0f}-"
            f"{MAX_INTERVAL:.0f} s, budget {self.budget / 1048576:.0f} MB/day)"
        )

    def stop(self):
        self.running = False

    def __loop(self):
        while self.running:
            try:
                self.tick()
            except Exception as e:  # never kill the thread
                logger.error(f"HLS segment fetch error: {e}")
            self.sleep(self.seconds_to_next(self.clock()))
