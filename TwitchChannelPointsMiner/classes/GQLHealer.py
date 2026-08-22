# -*- coding: utf-8 -*-
"""
GQLHealer — keep Twitch's rotating persisted-query hashes working at runtime.

Why this exists
---------------
Every GQL call the miner makes is an Apollo *persisted query*: instead of the
GraphQL document we send ``extensions.persistedQuery.sha256Hash`` and Twitch
looks the document up server-side. Twitch rotates those hashes whenever it
redeploys a query, and the moment one rotates the matching feature dies — often
*silently*, because most call sites swallow the error (``viewer_is_mod`` falls
back to ``False``, ``claim_moment`` ignores the response entirely). The only
historical fix was to wait for upstream to publish new constants, then redeploy.

Two properties of ``gql.twitch.tv/gql`` make that wait unnecessary:

1. It accepts a raw ``query`` document alongside (or instead of) the hash. Only
   a ``Client-Id`` header is required — no OAuth token.
2. It is a full APQ implementation: POST a document *once* together with its
   sha256 and the server registers the pair, after which that hash resolves on
   its own forever. Registration happens at parse time, so it succeeds
   unauthenticated and even when the variables fail validation.

Together those mean a running miner can repair its own hashes, with no redeploy
and without waiting on anyone. This module does that three ways:

* **Proactively** — :meth:`probe_all` asks Twitch (unauthenticated) which of our
  hashes it still recognises; :meth:`heal` re-registers the stale ones. Run at
  startup and on a timer, so silent breakage is caught before a user notices.
* **Reactively** — when a live request comes back ``PERSISTED_QUERY_NOT_FOUND``,
  :meth:`repair_payload` splices the document into that same payload so the
  retry both heals the hash and completes the original request.
* **As a fallback** — for operations we don't ship a document for, the hash is
  re-read from upstream's ``constants.py`` on ``raw.githubusercontent.com``.

Healed hashes are cached on disk so a restart doesn't re-probe. The cache is
pure derived state: deleting it costs one probe round at next startup.

Nothing here mutates ``constants.py``. Overrides are applied in memory to the
``GQLOperations`` attributes, which every call site ``deepcopy``s at call time.
"""

import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import requests

from TwitchChannelPointsMiner.constants import CLIENT_ID, GITHUB_url, GQLOperations

logger = logging.getLogger(__name__)

# Error code Twitch returns for a hash it no longer recognises. The legacy
# spelling shows up as a bare `message` on some edges, so we match both.
PERSISTED_QUERY_NOT_FOUND = "PERSISTED_QUERY_NOT_FOUND"
_MISSING_MARKERS = (PERSISTED_QUERY_NOT_FOUND, "PersistedQueryNotFound")

# How long to wait before re-probing an operation we just failed to heal, so a
# permanently broken operation can't turn into a request loop.
HEAL_COOLDOWN_SECONDS = 15 * 60

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# --------------------------------------------------------------------------- #
# GraphQL documents
# --------------------------------------------------------------------------- #
# Every document below was validated live against gql.twitch.tv (see
# `scripts/gql_check.py --documents`): each parses, resolves against the current
# schema, and returns exactly the fields the corresponding call site in
# `classes/Twitch.py` reads. A document that is merely *valid* is not good
# enough — one that omits a field the caller indexes into would trade a stale
# hash for a KeyError, which is strictly worse. Add entries only with the
# checker green.
#
# Formatting is load-bearing in one direction only: the sha256 is taken over
# this exact string, so editing a document changes its hash. That is fine (the
# new hash simply gets registered on first use) but it does mean documents must
# never be reformatted by a linter into something semantically different.
GQL_DOCUMENTS = {
    # -> data.user.self.isModerator   (Twitch.viewer_is_mod)
    "ModViewChannelQuery": (
        "query ModViewChannelQuery($channelLogin: String!) {"
        " user(login: $channelLogin) { id login self { isModerator } } }"
    ),
    # fire-and-forget mutation; the response is not inspected
    # (Twitch.claim_moment)
    "CommunityMomentCallout_Claim": (
        "mutation CommunityMomentCallout_Claim($input: ClaimCommunityMomentInput!) {"
        " claimCommunityMoment(input: $input) { __typename } }"
    ),
    # -> data.user.dropCampaign, consumed by entities/Campaign.py and
    # entities/Drop.py   (Twitch.__get_campaigns_details)
    "DropCampaignDetails": (
        "query DropCampaignDetails($dropID: ID!, $channelLogin: ID!) {"
        " user(id: $channelLogin) { id dropCampaign(id: $dropID) {"
        " id name status startAt endAt game { id displayName }"
        " allow { channels { id name } }"
        " timeBasedDrops { id name startAt endAt requiredMinutesWatched"
        " benefitEdges { benefit { id name } } } } } }"
    ),
}


# Declared in constants.py but never called from anywhere in the miner (upstream
# leftovers). Their hashes are allowed to rot: nothing breaks when they do, so
# neither the runtime healer nor CI should treat them as actionable.
UNUSED_OPERATIONS = frozenset({"PersonalSections"})


def document_hash(document: str) -> str:
    """sha256 Twitch will file a document under — plain digest of the text."""
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Response inspection
# --------------------------------------------------------------------------- #
def _errors_of(node) -> list:
    return (node or {}).get("errors") or [] if isinstance(node, dict) else []


def _mentions_missing_hash(errors) -> bool:
    for err in errors:
        if not isinstance(err, dict):
            continue
        code = (err.get("extensions") or {}).get("code")
        if code in _MISSING_MARKERS or err.get("message") in _MISSING_MARKERS:
            return True
    return False


def stale_operations(payload, response) -> set:
    """Operation names in `payload` that `response` says have a dead hash.

    Handles both shapes Twitch replies with: a single object, and the list that
    comes back when the payload itself was a list (drop-campaign batches).
    """
    requests_ = payload if isinstance(payload, list) else [payload]
    replies = response if isinstance(response, list) else [response]
    stale = set()

    # A batched reply is positional, so pair them up; when the shapes disagree
    # (single reply to a batch, e.g. a top-level error) treat the error as
    # applying to every operation in the payload.
    if len(replies) == len(requests_):
        pairs = zip(requests_, replies)
    else:
        pairs = [(req, rep) for req in requests_ for rep in replies]

    for req, rep in pairs:
        if isinstance(req, dict) and _mentions_missing_hash(_errors_of(rep)):
            name = req.get("operationName")
            if name:
                stale.add(name)
    return stale


# --------------------------------------------------------------------------- #
# Healer
# --------------------------------------------------------------------------- #
class GQLHealer(object):
    __slots__ = [
        "cache_path",
        "user_agent",
        "_lock",
        "_last_attempt",
        "_healed",
        "_upstream_hashes",
    ]

    def __init__(self, cache_path=None, user_agent=None):
        self.cache_path = Path(
            cache_path
            or os.environ.get("TCPM_GQL_CACHE")
            or Path().absolute() / "cache" / "gql_hashes.json"
        )
        self.user_agent = user_agent
        self._lock = threading.RLock()
        self._last_attempt = {}
        self._healed = {}
        self._upstream_hashes = None

    # -- operation registry ------------------------------------------------- #
    @staticmethod
    def operations() -> dict:
        """Map operationName -> the mutable dict living on `GQLOperations`.

        Returns the real dict, not a copy, so writing to it updates the constant
        every call site deepcopies from. `PersonalSections` is declared as a
        1-tuple upstream, hence the unwrapping.
        """
        found = {}
        for attr in dir(GQLOperations):
            if attr.startswith("__"):
                continue
            value = getattr(GQLOperations, attr)
            candidates = value if isinstance(value, (list, tuple)) else [value]
            for candidate in candidates:
                if isinstance(candidate, dict) and "operationName" in candidate:
                    found[candidate["operationName"]] = candidate
        return found

    @staticmethod
    def current_hash(operation_name):
        operation = GQLHealer.operations().get(operation_name)
        if operation is None:
            return None
        return ((operation.get("extensions") or {}).get("persistedQuery") or {}).get(
            "sha256Hash"
        )

    def _set_hash(self, operation_name, sha256) -> bool:
        operation = self.operations().get(operation_name)
        if operation is None or not _SHA256_RE.match(sha256 or ""):
            return False
        operation.setdefault("extensions", {}).setdefault(
            "persistedQuery", {"version": 1}
        )["sha256Hash"] = sha256
        self._healed[operation_name] = sha256
        return True

    # -- cache -------------------------------------------------------------- #
    def load_cache(self) -> int:
        """Apply previously healed hashes. Returns how many were applied."""
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as exception:
            logger.debug(f"Ignoring unreadable GQL hash cache: {exception}")
            return 0

        applied = 0
        with self._lock:
            for name, sha256 in (cached.get("hashes") or {}).items():
                if self._set_hash(name, sha256):
                    applied += 1
        if applied:
            logger.debug(f"Restored {applied} healed GQL hash(es) from cache")
        return applied

    def _save_cache(self) -> None:
        if not self._healed:
            return
        payload = {"updated_at": int(time.time()), "hashes": dict(self._healed)}
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(temporary, self.cache_path)
        except OSError as exception:
            # A read-only working directory is a perfectly fine way to run the
            # miner; it just means we re-probe on every start.
            logger.debug(f"Could not persist GQL hash cache: {exception}")

    # -- transport ---------------------------------------------------------- #
    def _headers(self) -> dict:
        headers = {"Client-Id": CLIENT_ID, "Content-Type": "application/json"}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        return headers

    def _post(self, payload):
        response = requests.post(
            GQLOperations.url,
            json=payload,
            headers=self._headers(),
            timeout=(5, 15),
        )
        return response.json()

    # -- healing strategies ------------------------------------------------- #
    def _heal_from_document(self, operation_name) -> bool:
        """Register our own document for `operation_name` and adopt its hash.

        Sends the document with empty variables: Twitch registers on parse, so
        the variable-validation error we get back is expected and harmless.
        """
        document = GQL_DOCUMENTS.get(operation_name)
        if document is None:
            return False
        sha256 = document_hash(document)
        try:
            self._post(
                {
                    "operationName": operation_name,
                    "variables": {},
                    "query": document,
                    "extensions": {
                        "persistedQuery": {"version": 1, "sha256Hash": sha256}
                    },
                }
            )
        except (requests.exceptions.RequestException, ValueError) as exception:
            logger.debug(
                f"Could not register document for {operation_name}: {exception}"
            )
            return False

        if not self._verify(operation_name, sha256):
            logger.debug(f"Document for {operation_name} did not register")
            return False
        return self._set_hash(operation_name, sha256)

    def _fetch_upstream_hashes(self) -> dict:
        """Scrape operationName -> sha256Hash out of upstream's constants.py."""
        if self._upstream_hashes is not None:
            return self._upstream_hashes
        self._upstream_hashes = {}
        url = f"{GITHUB_url}/TwitchChannelPointsMiner/constants.py"
        try:
            response = requests.get(url, timeout=(5, 15))
            if response.status_code != 200:
                logger.debug(f"Upstream constants.py returned {response.status_code}")
                return self._upstream_hashes
            source = response.text
        except requests.exceptions.RequestException as exception:
            logger.debug(f"Could not fetch upstream constants.py: {exception}")
            return self._upstream_hashes

        # Each block reads `"operationName": "X", ... "sha256Hash": "abc…"`, so
        # pair every operation name with the next hash that follows it.
        for match in re.finditer(
            r'"operationName":\s*"(?P<name>\w+)".*?'
            r'"sha256Hash":\s*"(?P<hash>[0-9a-f]{64})"',
            source,
            re.DOTALL,
        ):
            self._upstream_hashes.setdefault(match.group("name"), match.group("hash"))
        logger.debug(f"Read {len(self._upstream_hashes)} hash(es) from upstream")
        return self._upstream_hashes

    def _heal_from_upstream(self, operation_name) -> bool:
        sha256 = self._fetch_upstream_hashes().get(operation_name)
        if not sha256 or sha256 == self.current_hash(operation_name):
            return False
        if not self._verify(operation_name, sha256):
            return False
        return self._set_hash(operation_name, sha256)

    def _verify(self, operation_name, sha256) -> bool:
        """True when Twitch recognises `sha256` for `operation_name`.

        Deliberately unauthenticated and variable-free: a missing hash answers
        PERSISTED_QUERY_NOT_FOUND before variables are ever looked at, so any
        other reply — including "Variable $x was not provided" — means the hash
        resolved.
        """
        try:
            response = self._post(
                {
                    "operationName": operation_name,
                    "variables": {},
                    "extensions": {
                        "persistedQuery": {"version": 1, "sha256Hash": sha256}
                    },
                }
            )
        except (requests.exceptions.RequestException, ValueError) as exception:
            logger.debug(f"Could not verify {operation_name}: {exception}")
            return False
        return not _mentions_missing_hash(_errors_of(response))

    # -- public API --------------------------------------------------------- #
    def probe_all(self) -> list:
        """Operation names whose hash Twitch no longer recognises."""
        stale = []
        for name, operation in sorted(self.operations().items()):
            if name in UNUSED_OPERATIONS:
                continue
            sha256 = (
                (operation.get("extensions") or {}).get("persistedQuery") or {}
            ).get("sha256Hash")
            if sha256 and not self._verify(name, sha256):
                stale.append(name)
        return stale

    def heal(self, operation_names) -> set:
        """Try to repair each named operation. Returns the ones now working."""
        healed = set()
        with self._lock:
            for name in operation_names:
                last = self._last_attempt.get(name, 0)
                if time.time() - last < HEAL_COOLDOWN_SECONDS:
                    continue
                self._last_attempt[name] = time.time()

                if self._heal_from_document(name):
                    logger.info(
                        f"Re-registered GQL operation {name} from a local document "
                        f"(hash had rotated)"
                    )
                    healed.add(name)
                elif self._heal_from_upstream(name):
                    logger.info(
                        f"Updated GQL hash for {name} from upstream constants.py"
                    )
                    healed.add(name)
                else:
                    logger.warning(
                        f"GQL operation {name} has a stale persisted-query hash and "
                        f"could not be repaired — features using it will misbehave "
                        f"until a document for it is added to GQLHealer.GQL_DOCUMENTS"
                    )
            if healed:
                self._save_cache()
        return healed

    def refresh(self) -> set:
        """Probe every operation and heal whatever is stale. Returns healed."""
        stale = self.probe_all()
        if not stale:
            logger.debug("All GQL persisted-query hashes still valid")
            return set()
        logger.info(f"Stale GQL hash(es) detected: {', '.join(stale)} — repairing")
        return self.heal(stale)

    def repair_payload(self, payload, operation_names):
        """Copy of `payload` with documents spliced in for the named operations.

        Used for the in-flight retry: sending the document registers the hash
        *and* answers the original request in one round trip. Returns None when
        we have no document for any of the stale operations, in which case the
        caller should fall back to :meth:`heal`.
        """
        usable = {name for name in operation_names if name in GQL_DOCUMENTS}
        if not usable:
            return None

        repaired = copy.deepcopy(payload)
        entries = repaired if isinstance(repaired, list) else [repaired]
        touched = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("operationName")
            if name in usable:
                document = GQL_DOCUMENTS[name]
                entry["query"] = document
                entry.setdefault("extensions", {}).setdefault(
                    "persistedQuery", {"version": 1}
                )["sha256Hash"] = document_hash(document)
                touched = True
        return repaired if touched else None

    def adopt_repaired(self, operation_names) -> None:
        """Record the document hashes for operations a retry just fixed."""
        with self._lock:
            changed = False
            for name in operation_names:
                document = GQL_DOCUMENTS.get(name)
                if document and self._set_hash(name, document_hash(document)):
                    changed = True
                    logger.info(
                        f"Re-registered GQL operation {name} from a local document "
                        f"after a failed request (hash had rotated)"
                    )
            if changed:
                self._save_cache()
