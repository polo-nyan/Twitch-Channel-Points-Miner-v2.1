# -*- coding: utf-8 -*-
"""Tests for classes/HLSFetcher.py. Offline: the HTTP session, the clock and
sleep are fakes."""

import logging
import random
import statistics

from conftest import load_module

hls = load_module(
    "TwitchChannelPointsMiner.classes.HLSFetcher",
    "TwitchChannelPointsMiner/classes/HLSFetcher.py",
)
HLSFetcher = hls.HLSFetcher

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=3422999,RESOLUTION=1280x720,VIDEO="720p60"
https://example.test/720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=230000,RESOLUTION=284x160,VIDEO="160p30"
https://example.test/160p.m3u8
"""
MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:2
#EXTINF:2.000,live
https://example.test/seg-1.ts
#EXTINF:2.000,live
https://example.test/seg-2.ts
"""
SEGMENT = b"x" * 72380


class FakeResponse(object):
    def __init__(self, url):
        self.status_code = 200
        if url.endswith("master"):
            self.content = MASTER.encode()
        elif url.endswith(".m3u8"):
            self.content = MEDIA.encode()
        else:
            self.content = SEGMENT
        self.text = self.content.decode("latin-1")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]

    def close(self):
        pass


class FakeHead(object):
    status_code = 200
    headers = {"Content-Type": "video/mp2t", "Content-Length": "72380"}


class FakeSession(object):
    def __init__(self):
        self.urls = []
        self.heads = []

    def get(self, url, headers=None, timeout=None, stream=False):
        self.urls.append(url)
        return FakeResponse(url)

    def head(self, url, headers=None, timeout=None):
        self.heads.append(url)
        return FakeHead()

    def segments(self):
        return [u for u in self.urls if u.endswith(".ts")]


def fetcher(mode="minute", budget=300 * 1048576, channels=(), seed=1, sleeps=None):
    session = FakeSession()
    f = HLSFetcher(
        lambda login: f"https://usher.test/{login}/master",
        mode=mode, budget=budget, channels=list(channels), session=session,
        rng=random.Random(seed), clock=lambda: 0.0,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        threaded=False,
    )
    return f, session


def run(f, seconds, step=1.0, start=1_761_100_000.0):
    t = start
    while t < start + seconds:
        f.tick(t)
        t += step


def test_playlist_parsing():
    assert hls.lowest_variant(MASTER) == "https://example.test/160p.m3u8"
    assert hls.newest_segment(MEDIA) == "https://example.test/seg-2.ts"


def test_interval_is_random_within_30_to_60_seconds():
    f, _ = fetcher()
    draws = [f.next_interval() for _ in range(500)]
    assert all(30.0 <= d <= 60.0 for d in draws)
    assert len(set(draws)) > 400
    assert statistics.pstdev(draws) > 5


def test_fetch_times_per_stream_are_independent_and_not_constant():
    f, session = fetcher()
    f.set_targets(["a", "b"])
    times = {"a": [], "b": []}
    t, start = 1_761_100_000.0, 1_761_100_000.0
    while t < start + 3600:
        before = dict((k, v["next_due"]) for k, v in f.streams.items())
        f.tick(t)
        for login, state in f.streams.items():
            if state["next_due"] != before[login]:
                times[login].append(t)
        t += 0.5
    for login in times:
        gaps = [b - a for a, b in zip(times[login], times[login][1:])]
        assert gaps and all(29.5 <= g <= 61.0 for g in gaps)
        assert len({round(g) for g in gaps}) > 5
    assert times["a"] != times["b"]
    assert len(session.segments()) == len(times["a"]) + len(times["b"])


def test_off_makes_no_fetches():
    f, session = fetcher(mode="off")
    assert f.set_targets(["a"]) == []
    run(f, 600)
    assert session.urls == []


def test_continuous_is_not_implemented(caplog):
    with caplog.at_level(logging.WARNING, logger=hls.__name__):
        f, session = fetcher(mode="continuous")
    assert f.enabled is False
    assert any("not implemented" in r.getMessage() for r in caplog.records)
    f.set_targets(["a"])
    run(f, 600)
    assert session.urls == []


def test_budget_stops_fetching(caplog):
    f, session = fetcher(budget=3 * len(SEGMENT))
    f.set_targets(["a"])
    with caplog.at_level(logging.INFO, logger=hls.__name__):
        run(f, 3600)
    assert len(session.segments()) == 3
    assert sum("daily budget" in r.getMessage() for r in caplog.records) == 1


def test_budget_resets_next_utc_day():
    f, session = fetcher(budget=2 * len(SEGMENT))
    f.set_targets(["a"])
    day1 = 1_761_100_000.0  # 2025-10-22 02:26 UTC (any day works)
    run(f, 1800, start=day1)
    assert len(session.segments()) == 2
    run(f, 600, start=day1 + 86400)
    assert len(session.segments()) > 2


def test_at_most_two_streams_and_channel_allowlist():
    f, _ = fetcher()
    assert f.set_targets(["a", "b", "c"]) == ["a", "b"]
    f, _ = fetcher(channels=["TwitchPlaysPokemon"])
    assert f.set_targets(["badgebase", "twitchplayspokemon"]) == ["twitchplayspokemon"]


def test_dropped_target_stops_being_fetched():
    f, session = fetcher()
    f.set_targets(["a"])
    run(f, 300)
    count = len(session.segments())
    assert count > 0
    f.set_targets([])
    run(f, 300, start=1_761_200_000.0)
    assert len(session.segments()) == count


# ------------------------------- head-all -------------------------------- #
def test_head_all_interval_is_random_within_15_to_25_seconds():
    f, _ = fetcher(mode="head-all")
    draws = [f.next_interval() for _ in range(500)]
    assert all(15.0 <= d <= 25.0 for d in draws)
    assert len(set(draws)) > 400 and statistics.pstdev(draws) > 2


def test_head_all_heads_every_segment_with_small_random_gaps():
    sleeps = []
    f, session = fetcher(mode="head-all", sleeps=sleeps)
    f.set_targets(["a"])
    f.streams["a"]["next_due"] = 0
    assert f.tick(1_761_100_000.0) == 1
    assert session.heads == hls.all_segments(MEDIA) == [
        "https://example.test/seg-1.ts", "https://example.test/seg-2.ts"]
    assert session.segments() == []  # no segment body is downloaded
    head_gaps = sleeps[-len(session.heads):]
    assert all(0.1 <= g <= 0.8 for g in head_gaps) and len(set(head_gaps)) == 2
    assert f.requests_today == 2 + len(session.heads)  # master + media + HEADs


def test_head_all_rounds_are_independent_per_stream():
    f, session = fetcher(mode="head-all")
    f.set_targets(["a", "b"])
    rounds = {"a": [], "b": []}
    t, start = 1_761_100_000.0, 1_761_100_000.0
    while t < start + 1800:
        before = {k: v["next_due"] for k, v in f.streams.items()}
        f.tick(t)
        for login, state in f.streams.items():
            if state["next_due"] != before[login]:
                rounds[login].append(t)
        t += 0.5
    for login in rounds:
        gaps = [b - a for a, b in zip(rounds[login], rounds[login][1:])]
        assert gaps and all(14.5 <= g <= 25.5 for g in gaps)
        assert len({round(g) for g in gaps}) > 5
    assert rounds["a"] != rounds["b"]


def test_head_all_budget_stops_requests():
    f, session = fetcher(mode="head-all", budget=2 * len(MEDIA))
    f.set_targets(["a"])
    run(f, 1800)
    heads = len(session.heads)
    run(f, 1800, start=1_761_100_000.0 + 1800)
    assert len(session.heads) == heads  # stopped


def test_head_all_off_makes_no_requests():
    f, session = fetcher(mode="off")
    f.set_targets(["a"])
    run(f, 600)
    assert session.urls == [] and session.heads == []



class SimClock(object):
    """Simulated time: sleep() advances it, as real requests would."""

    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_head_all_caps_a_round_at_the_newest_20_segments():
    long_media = "#EXTM3U\n" + "".join(
        f"#EXTINF:2.000,live\nhttps://example.test/seg-{i}.ts\n" for i in range(94))
    f, session = fetcher(mode="head-all")
    f.set_targets(["a"])
    f.streams["a"]["next_due"] = 0
    original_get = session.get

    def get(url, headers=None, timeout=None, stream=False):
        response = original_get(url, headers, timeout, stream)
        if url.endswith(".m3u8"):
            response.content = long_media.encode()
            response.text = long_media
        return response

    session.get = get
    f.tick(1_761_100_000.0)
    assert len(session.heads) == hls.MAX_HEADS_PER_ROUND == 20
    assert session.heads[-1] == "https://example.test/seg-93.ts"
    assert session.heads[0] == "https://example.test/seg-74.ts"


def test_head_all_pause_between_rounds_is_at_least_15_seconds():
    clock = SimClock(1_761_100_000.0)
    session = FakeSession()
    f = HLSFetcher(
        lambda login: f"https://usher.test/{login}/master",
        mode="head-all", budget=300 * 1048576, channels=[], session=session,
        rng=random.Random(3), clock=clock, sleep=clock.sleep, threaded=False,
    )
    # Slow segments: each HEAD costs 1 s on top of the random gap.
    original_head = session.head

    def slow_head(url, headers=None, timeout=None):
        clock.t += 1.0
        return original_head(url, headers, timeout)

    session.head = slow_head
    f.set_targets(["a"])
    rounds = []  # (start, end)
    end = clock.t + 1800
    while clock.t < end:
        due = f.streams["a"]["next_due"]
        if due <= clock.t:
            start = clock.t
            f.tick(start)
            rounds.append((start, clock.t))
        else:
            clock.t = due
    pauses = [b[0] - a[1] for a, b in zip(rounds, rounds[1:])]
    assert pauses and min(pauses) >= 15.0 and max(pauses) <= 25.0
    assert len({round(p, 1) for p in pauses}) > 5
