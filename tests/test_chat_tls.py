# -*- coding: utf-8 -*-
"""Tests for the IRC transport choice in classes/Chat.py (TLS on 6697, an
opt-in plaintext fallback on 6667 for the first connect only).

Offline: `connect` is stubbed. Needs the irc package, so the test is skipped
where only the minimal CI dependencies are installed.
"""

import logging

import pytest

pytest.importorskip("irc")

from irc.client import ServerConnectionError  # noqa: E402

from conftest import load_module  # noqa: E402

load_module(
    "TwitchChannelPointsMiner.classes.Settings",
    "TwitchChannelPointsMiner/classes/Settings.py",
)
chat = load_module(
    "TwitchChannelPointsMiner.classes.Chat",
    "TwitchChannelPointsMiner/classes/Chat.py",
)


class FakeSocket(object):
    def settimeout(self, value):
        self.timeout = value


class ScriptedClient(chat.ClientIRC):
    """`connect` pops the next outcome: True connects, False raises."""

    def __init__(self, outcomes):
        super(ScriptedClient, self).__init__("justinfan1", "token", "channel")
        self.outcomes = list(outcomes)
        self.calls = []
        self.events = []
        self.connection._handle_event = self.events.append

    def connect(self, server, port, nickname, password, **kwargs):
        self.calls.append((server, port, password, kwargs["connect_factory"]))
        if not self.outcomes.pop(0):
            raise ServerConnectionError("handshake failed")
        self.connection.socket = FakeSocket()


def ports(client):
    return [call[1] for call in client.calls]


def test_connects_over_tls_by_default(monkeypatch):
    monkeypatch.delenv(chat.PLAINTEXT_FALLBACK_ENV, raising=False)
    client = ScriptedClient([True])
    client._connect()
    assert ports(client) == [6697]
    assert client.calls[0][2] == "oauth:token"
    assert client.connection.socket.timeout is None
    assert client.events == []


def test_tls_failure_without_fallback_never_uses_plaintext(monkeypatch, caplog):
    monkeypatch.delenv(chat.PLAINTEXT_FALLBACK_ENV, raising=False)
    client = ScriptedClient([False])
    with caplog.at_level(logging.WARNING, logger=chat.__name__):
        client._connect()
    assert ports(client) == [6697]
    assert len(client.events) == 1  # reconnect strategy gets a disconnect
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_tls_failure_at_startup_falls_back_with_warning(monkeypatch, caplog):
    monkeypatch.setenv(chat.PLAINTEXT_FALLBACK_ENV, "1")
    client = ScriptedClient([False, True, True])
    with caplog.at_level(logging.WARNING, logger=chat.__name__):
        client._connect()
    assert ports(client) == [6697, 6667]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "plaintext" in warnings[0].getMessage()
    assert client.events == []

    # A reconnect keeps the transport chosen at startup.
    client._connect()
    assert ports(client) == [6697, 6667, 6667]


def test_fallback_only_applies_to_the_first_connect(monkeypatch):
    monkeypatch.setenv(chat.PLAINTEXT_FALLBACK_ENV, "1")
    client = ScriptedClient([True, False])
    client._connect()
    client._connect()
    assert ports(client) == [6697, 6697]
    assert len(client.events) == 1
