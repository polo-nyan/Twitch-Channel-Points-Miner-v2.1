# -*- coding: utf-8 -*-
"""Tests for the persisted-query self-healing in classes/GQLHealer.py.

Everything here is offline. The live behaviour these tests stand in for — that
Twitch really does register a document under its own sha256 and really does
answer PERSISTED_QUERY_NOT_FOUND for a rotated hash — is checked by
`scripts/gql_check.py`, which talks to the real endpoint.
"""

import json

import pytest

from conftest import load_module

healer_module = load_module(
    "TwitchChannelPointsMiner.classes.GQLHealer",
    "TwitchChannelPointsMiner/classes/GQLHealer.py",
)
constants = load_module(
    "TwitchChannelPointsMiner.constants", "TwitchChannelPointsMiner/constants.py"
)

GQLHealer = healer_module.GQLHealer
stale_operations = healer_module.stale_operations
document_hash = healer_module.document_hash
GQL_DOCUMENTS = healer_module.GQL_DOCUMENTS


class PatchableHealer(GQLHealer):
    """GQLHealer uses __slots__ (house style), which blocks monkeypatching an
    instance method. Subclassing restores a __dict__ without touching the real
    class, so tests can stub the network-facing helpers."""

    __slots__ = ["__dict__"]


NOT_FOUND = {
    "errors": [
        {
            "message": "PersistedQueryNotFound",
            "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"},
        }
    ]
}


def op(name, sha256="a" * 64, **extra):
    payload = {
        "operationName": name,
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256}},
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# stale_operations
# --------------------------------------------------------------------------- #
def test_detects_missing_hash_in_single_response():
    assert stale_operations(op("JoinRaid"), NOT_FOUND) == {"JoinRaid"}


def test_detects_legacy_spelling_without_extensions_code():
    response = {"errors": [{"message": "PersistedQueryNotFound"}]}
    assert stale_operations(op("JoinRaid"), response) == {"JoinRaid"}


def test_healthy_response_is_not_stale():
    response = {"data": {"user": {"id": "1"}}}
    assert stale_operations(op("JoinRaid"), response) == set()


def test_unrelated_errors_are_not_stale():
    # The overwhelmingly common case: hash fine, arguments wrong.
    response = {"errors": [{"message": 'Variable "$id" of required type "ID!"...'}]}
    assert stale_operations(op("JoinRaid"), response) == set()


def test_batch_response_pairs_positionally():
    payload = [op("DropCampaignDetails"), op("Inventory")]
    response = [NOT_FOUND, {"data": {"currentUser": {}}}]
    assert stale_operations(payload, response) == {"DropCampaignDetails"}


def test_batch_with_single_error_reply_blames_every_operation():
    # Twitch can answer a batch with one top-level error object; we cannot tell
    # which entry it belongs to, so all of them are suspect.
    payload = [op("DropCampaignDetails"), op("Inventory")]
    assert stale_operations(payload, NOT_FOUND) == {
        "DropCampaignDetails",
        "Inventory",
    }


def test_malformed_response_does_not_raise():
    assert stale_operations(op("JoinRaid"), None) == set()
    assert stale_operations(op("JoinRaid"), []) == set()
    assert stale_operations(op("JoinRaid"), "nonsense") == set()


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #
def test_document_hash_is_plain_sha256_of_the_text():
    import hashlib

    document = "query Foo { __typename }"
    assert document_hash(document) == hashlib.sha256(document.encode()).hexdigest()


def test_every_shipped_document_declares_its_operation_name():
    # The document's operation name has to match the key, or Twitch resolves a
    # different operation than the caller thinks it does.
    for name, document in GQL_DOCUMENTS.items():
        assert f" {name}(" in document or f" {name} " in document, name


def test_shipped_documents_cover_operations_that_exist():
    known = set(GQLHealer.operations())
    assert set(GQL_DOCUMENTS) <= known, set(GQL_DOCUMENTS) - known


# --------------------------------------------------------------------------- #
# repair_payload
# --------------------------------------------------------------------------- #
def test_repair_payload_inlines_document_and_matching_hash():
    name = "ModViewChannelQuery"
    payload = op(name, variables={"channelLogin": "x"})
    repaired = GQLHealer().repair_payload(payload, {name})

    assert repaired["query"] == GQL_DOCUMENTS[name]
    assert repaired["extensions"]["persistedQuery"]["sha256Hash"] == document_hash(
        GQL_DOCUMENTS[name]
    )
    # the caller's variables must survive, or the retry answers a different question
    assert repaired["variables"] == {"channelLogin": "x"}


def test_repair_payload_does_not_mutate_the_original():
    name = "ModViewChannelQuery"
    payload = op(name)
    before = json.dumps(payload, sort_keys=True)
    GQLHealer().repair_payload(payload, {name})
    assert json.dumps(payload, sort_keys=True) == before


def test_repair_payload_returns_none_without_a_document():
    assert GQLHealer().repair_payload(op("Inventory"), {"Inventory"}) is None


def test_repair_payload_touches_only_the_stale_entries_of_a_batch():
    payload = [op("DropCampaignDetails"), op("ModViewChannelQuery")]
    repaired = GQLHealer().repair_payload(payload, {"DropCampaignDetails"})
    assert "query" in repaired[0]
    assert "query" not in repaired[1]


# --------------------------------------------------------------------------- #
# hash registry / cache
# --------------------------------------------------------------------------- #
def test_operations_unwraps_the_personal_sections_tuple():
    # constants.py declares PersonalSections as a 1-tuple; a naive scan misses it.
    assert "PersonalSections" in GQLHealer.operations()


def test_set_hash_writes_through_to_the_constant():
    healer = GQLHealer()
    original = GQLHealer.current_hash("JoinRaid")
    try:
        assert healer._set_hash("JoinRaid", "b" * 64) is True
        assert GQLHealer.current_hash("JoinRaid") == "b" * 64
        assert constants.GQLOperations.JoinRaid["extensions"]["persistedQuery"][
            "sha256Hash"
        ] == ("b" * 64)
    finally:
        healer._set_hash("JoinRaid", original)


def test_set_hash_rejects_things_that_are_not_hashes():
    healer = GQLHealer()
    original = GQLHealer.current_hash("JoinRaid")
    assert healer._set_hash("JoinRaid", "not-a-hash") is False
    assert healer._set_hash("NoSuchOperation", "c" * 64) is False
    assert GQLHealer.current_hash("JoinRaid") == original


def test_cache_round_trips(tmp_path):
    path = tmp_path / "nested" / "gql_hashes.json"
    healer = GQLHealer(cache_path=path)
    original = GQLHealer.current_hash("JoinRaid")
    try:
        healer._set_hash("JoinRaid", "d" * 64)
        healer._save_cache()

        healer._set_hash("JoinRaid", original)
        assert GQLHealer(cache_path=path).load_cache() == 1
        assert GQLHealer.current_hash("JoinRaid") == "d" * 64
    finally:
        healer._set_hash("JoinRaid", original)


def test_missing_cache_is_not_an_error(tmp_path):
    assert GQLHealer(cache_path=tmp_path / "absent.json").load_cache() == 0


def test_corrupt_cache_is_ignored(tmp_path):
    path = tmp_path / "gql_hashes.json"
    path.write_text("{ not json")
    assert GQLHealer(cache_path=path).load_cache() == 0


def test_cache_ignores_entries_that_are_not_hashes(tmp_path):
    path = tmp_path / "gql_hashes.json"
    path.write_text(json.dumps({"hashes": {"JoinRaid": "../../etc/passwd"}}))
    original = GQLHealer.current_hash("JoinRaid")
    assert GQLHealer(cache_path=path).load_cache() == 0
    assert GQLHealer.current_hash("JoinRaid") == original


# --------------------------------------------------------------------------- #
# heal() control flow
# --------------------------------------------------------------------------- #
def test_heal_falls_back_to_upstream_when_no_document_exists(tmp_path, monkeypatch):
    healer = PatchableHealer(cache_path=tmp_path / "c.json")
    monkeypatch.setattr(
        healer, "_fetch_upstream_hashes", lambda: {"Inventory": "e" * 64}
    )
    monkeypatch.setattr(healer, "_verify", lambda name, sha256: True)

    original = GQLHealer.current_hash("Inventory")
    try:
        assert healer.heal({"Inventory"}) == {"Inventory"}
        assert GQLHealer.current_hash("Inventory") == "e" * 64
    finally:
        healer._set_hash("Inventory", original)


def test_heal_reports_nothing_when_every_strategy_fails(tmp_path, monkeypatch):
    healer = PatchableHealer(cache_path=tmp_path / "c.json")
    monkeypatch.setattr(healer, "_fetch_upstream_hashes", dict)
    monkeypatch.setattr(healer, "_verify", lambda name, sha256: False)
    assert healer.heal({"Inventory"}) == set()


def test_heal_respects_the_cooldown(tmp_path, monkeypatch):
    """A permanently broken operation must not turn into a request loop."""
    healer = PatchableHealer(cache_path=tmp_path / "c.json")
    attempts = []
    monkeypatch.setattr(
        healer, "_fetch_upstream_hashes", lambda: attempts.append(1) or {}
    )
    healer.heal({"Inventory"})
    healer.heal({"Inventory"})
    assert len(attempts) == 1


def test_probe_all_skips_operations_nothing_calls(monkeypatch):
    probed = []
    healer = PatchableHealer()
    monkeypatch.setattr(
        healer, "_verify", lambda name, sha256: probed.append(name) or True
    )
    healer.probe_all()
    assert "PersonalSections" not in probed
    assert "JoinRaid" in probed


def test_upstream_hash_parsing_pairs_each_operation_with_its_own_hash():
    healer = GQLHealer()
    source = '''
    ClaimCommunityPoints = {
        "operationName": "ClaimCommunityPoints",
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": "%s"}},
    }
    JoinRaid = {
        "operationName": "JoinRaid",
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": "%s"}},
    }
    ''' % ("1" * 64, "2" * 64)

    class FakeResponse:
        status_code = 200
        text = source

    healer._upstream_hashes = None
    import requests

    original_get = requests.get
    requests.get = lambda *a, **k: FakeResponse()
    try:
        parsed = healer._fetch_upstream_hashes()
    finally:
        requests.get = original_get

    assert parsed["ClaimCommunityPoints"] == "1" * 64
    assert parsed["JoinRaid"] == "2" * 64


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
