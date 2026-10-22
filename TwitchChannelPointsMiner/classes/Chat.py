import functools
import logging
import os
import ssl
import time
from enum import Enum, auto
from threading import Thread

from irc.bot import SingleServerIRCBot
from irc.client import Event, ServerConnectionError
from irc.connection import Factory

from TwitchChannelPointsMiner.constants import IRC, IRC_PLAINTEXT_PORT, IRC_PORT
from TwitchChannelPointsMiner.classes.Settings import Events, Settings

logger = logging.getLogger(__name__)

# IRC goes over TLS (port 6697) so the OAuth token is never sent in clear.
# Set this to 1 to allow plaintext port 6667 when the first TLS connection
# of a chat client fails. Off by default.
PLAINTEXT_FALLBACK_ENV = "TCPM_IRC_PLAINTEXT_FALLBACK"


def plaintext_fallback_enabled():
    return os.environ.get(PLAINTEXT_FALLBACK_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# Seconds for the TCP connect and the TLS handshake. Without a limit a
# filtered port or a stalled handshake blocks the chat thread for good.
CONNECT_TIMEOUT = 15


def _with_timeout(wrap, sock):
    sock.settimeout(CONNECT_TIMEOUT)
    return wrap(sock)


def tls_factory():
    ssl_context = ssl.create_default_context()
    return Factory(
        wrapper=functools.partial(
            _with_timeout,
            functools.partial(ssl_context.wrap_socket, server_hostname=IRC),
        )
    )


def plaintext_factory():
    return Factory(wrapper=functools.partial(_with_timeout, lambda sock: sock))


class ChatPresence(Enum):
    ALWAYS = auto()
    NEVER = auto()
    ONLINE = auto()
    OFFLINE = auto()

    def __str__(self):
        return self.name


class ClientIRC(SingleServerIRCBot):
    def __init__(self, username, token, channel):
        self.token = token
        self.channel = "#" + channel
        self.__active = False
        self.__use_tls = True
        self.__first_connect = True

        super(ClientIRC, self).__init__(
            [(IRC, IRC_PORT, f"oauth:{token}")], username, username
        )

    def _connect(self):
        """Connect over TLS. Replaces SingleServerIRCBot._connect, which can
        only use one connect factory for all attempts."""
        first_connect, self.__first_connect = self.__first_connect, False
        try:
            self.__connect_once()
            return
        except ServerConnectionError as e:
            if not (self.__use_tls and first_connect and plaintext_fallback_enabled()):
                logger.error(
                    f"IRC connection to {IRC}:{IRC_PORT if self.__use_tls else IRC_PLAINTEXT_PORT} "
                    f"for {self.channel} failed: {e}"
                )
                self.__on_connect_failed()
                return
            logger.warning(
                f"IRC over TLS to {IRC}:{IRC_PORT} for {self.channel} failed ({e}). "
                f"{PLAINTEXT_FALLBACK_ENV} is set: falling back to plaintext port "
                f"{IRC_PLAINTEXT_PORT}. The OAuth token is sent unencrypted."
            )
            self.__use_tls = False
        try:
            self.__connect_once()
        except ServerConnectionError as e:
            logger.error(
                f"IRC connection to {IRC}:{IRC_PLAINTEXT_PORT} for {self.channel} failed: {e}"
            )
            self.__on_connect_failed()

    def __connect_once(self):
        if self.__use_tls:
            port, factory = IRC_PORT, tls_factory()
        else:
            port, factory = IRC_PLAINTEXT_PORT, plaintext_factory()
        self.connect(
            IRC,
            port,
            self._nickname,
            f"oauth:{self.token}",
            ircname=self._realname,
            connect_factory=factory,
        )
        # The timeout is for connecting only; the reactor expects a
        # blocking socket afterwards.
        self.connection.socket.settimeout(None)

    def __on_connect_failed(self):
        # Same as SingleServerIRCBot._connect: a disconnect event lets the
        # reconnect strategy try again later.
        self.connection._handle_event(
            Event("disconnect", getattr(self.connection, "server", IRC), "", [""])
        )

    def on_welcome(self, client, event):
        client.join(self.channel)

    def start(self):
        self.__active = True
        self._connect()
        while self.__active:
            try:
                self.reactor.process_once(timeout=0.2)
                time.sleep(0.01)
            except Exception as e:
                if self.__active is False:
                    break
                logger.error(
                    f"Exception raised: {e}. Thread is active: {self.__active}"
                )

    def die(self, msg="Bye, cruel world!"):
        # Stop the loop first, so the disconnect does not log an error.
        self.__active = False
        self.connection.disconnect(msg)

    """
    def on_join(self, connection, event):
        logger.info(f"Event: {event}", extra={"emoji": ":speech_balloon:"})
    """

    # """
    def on_pubmsg(self, connection, event):
        msg = event.arguments[0]
        mention = None

        if Settings.disable_at_in_nickname is True:
            mention = f"{self._nickname.lower()}"
        else:
            mention = f"@{self._nickname.lower()}"

        # also self._realname
        # if msg.startswith(f"@{self._nickname}"):
        if mention != None and mention in msg.lower():
            # nickname!username@nickname.tmi.twitch.tv
            nick = event.source.split("!", 1)[0]
            # chan = event.target

            logger.info(f"{nick} at {self.channel} wrote: {msg}", extra={
                        "emoji": ":speech_balloon:", "event": Events.CHAT_MENTION})
    # """


class ThreadChat(Thread):
    def __deepcopy__(self, memo):
        return None

    def __init__(self, username, token, channel):
        super(ThreadChat, self).__init__()

        self.username = username
        self.token = token
        self.channel = channel

        self.chat_irc = None

    def run(self):
        self.chat_irc = ClientIRC(self.username, self.token, self.channel)
        logger.info(
            f"Join IRC Chat: {self.channel}", extra={"emoji": ":speech_balloon:"}
        )
        self.chat_irc.start()

    def stop(self):
        if self.chat_irc is not None:
            logger.info(
                f"Leave IRC Chat: {self.channel}", extra={"emoji": ":speech_balloon:"}
            )
            self.chat_irc.die()
