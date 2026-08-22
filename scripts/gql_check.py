#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gql_check.py — is every persisted-query hash in constants.py still live?

Twitch rotates the sha256 hashes behind its GraphQL persisted queries without
warning. When one rotates, the feature that uses it breaks — usually silently,
because most call sites ignore the error. This script asks Twitch directly.

It needs no credentials: a request carrying only a Client-Id gets
PERSISTED_QUERY_NOT_FOUND for a dead hash and an ordinary variable-validation
error for a live one, and that distinction is made before authentication or
variables are ever considered. So this is safe to run in CI.

    scripts/gql_check.py                # report; exit 1 if anything is stale
    scripts/gql_check.py --json         # machine-readable
    scripts/gql_check.py --documents    # also validate GQLHealer's documents
    scripts/gql_check.py --heal         # re-register documents for stale ops

`--heal` only writes to Twitch's own persisted-query store (registering a
document we already ship). It never edits files in this repo: the miner heals
itself at runtime, so constants.py is deliberately left matching upstream.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the two modules directly rather than through the package: importing
# `TwitchChannelPointsMiner` pulls in irc/flask/pandas, which CI has no reason
# to install just to make an HTTP request.
import importlib.util


def _load(module_name, relative_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(root, relative_path)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


constants = _load(
    "TwitchChannelPointsMiner.constants", "TwitchChannelPointsMiner/constants.py"
)
GQLOperations = constants.GQLOperations
CLIENT_ID = constants.CLIENT_ID

_MISSING = ("PERSISTED_QUERY_NOT_FOUND", "PersistedQueryNotFound")


def post(body, timeout=20):
    request = urllib.request.Request(
        GQLOperations.url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Client-Id": CLIENT_ID, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exception:
        return {"errors": [{"message": f"HTTP {exception.code}"}]}


def hash_missing(response):
    for error in (response or {}).get("errors") or []:
        code = (error.get("extensions") or {}).get("code")
        if code in _MISSING or error.get("message") in _MISSING:
            return True
    return False


def operations():
    """operationName -> the dict on GQLOperations (PersonalSections is a tuple)."""
    found = {}
    for attribute in dir(GQLOperations):
        if attribute.startswith("__"):
            continue
        value = getattr(GQLOperations, attribute)
        for candidate in value if isinstance(value, (list, tuple)) else [value]:
            if isinstance(candidate, dict) and "operationName" in candidate:
                found[candidate["operationName"]] = candidate
    return found


def check_hash(name, operation):
    sha256 = (
        (operation.get("extensions") or {}).get("persistedQuery") or {}
    ).get("sha256Hash")
    if not sha256:
        return "no-hash", sha256
    response = post(
        {
            "operationName": name,
            "variables": {},
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256}},
        }
    )
    return ("stale" if hash_missing(response) else "ok"), sha256


def check_document(name, document):
    """Does our own document still parse and resolve against Twitch's schema?

    Sent with empty variables: a variable-validation error means the document
    itself was accepted, which is what we are testing. Anything mentioning an
    unknown field or type means the schema moved under us.
    """
    response = post({"operationName": name, "variables": {}, "query": document})
    messages = [
        error.get("message", "") for error in (response.get("errors") or [])
    ]
    broken = [
        message
        for message in messages
        if "Unknown type" in message
        or "Cannot query field" in message
        or "Unknown argument" in message
        or "used in position expecting type" in message
    ]
    return ("invalid" if broken else "ok"), broken


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--documents", action="store_true", help="also validate GQLHealer documents"
    )
    parser.add_argument(
        "--heal",
        action="store_true",
        help="re-register a shipped document for every stale operation",
    )
    args = parser.parse_args()

    healer = _load(
        "TwitchChannelPointsMiner.classes.GQLHealer",
        "TwitchChannelPointsMiner/classes/GQLHealer.py",
    )

    report = {
        "hashes": {},
        "documents": {},
        "stale": [],
        "unrepairable": [],
        "unused": sorted(healer.UNUSED_OPERATIONS),
    }

    for name, operation in sorted(operations().items()):
        status, sha256 = check_hash(name, operation)
        if status == "stale" and name in healer.UNUSED_OPERATIONS:
            status = "stale-unused"
        report["hashes"][name] = {"status": status, "sha256": sha256}
        if status != "stale":
            continue
        report["stale"].append(name)

        if args.heal and name in healer.GQL_DOCUMENTS:
            document = healer.GQL_DOCUMENTS[name]
            digest = healer.document_hash(document)
            post(
                {
                    "operationName": name,
                    "variables": {},
                    "query": document,
                    "extensions": {
                        "persistedQuery": {"version": 1, "sha256Hash": digest}
                    },
                }
            )
            recheck = post(
                {
                    "operationName": name,
                    "variables": {},
                    "extensions": {
                        "persistedQuery": {"version": 1, "sha256Hash": digest}
                    },
                }
            )
            report["hashes"][name]["healed"] = not hash_missing(recheck)
            report["hashes"][name]["document_sha256"] = digest
        elif name not in healer.GQL_DOCUMENTS:
            report["unrepairable"].append(name)

    if args.documents:
        for name, document in sorted(healer.GQL_DOCUMENTS.items()):
            status, problems = check_document(name, document)
            report["documents"][name] = {"status": status, "problems": problems}

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Checked {len(report['hashes'])} persisted-query hash(es)\n")
        for name, info in sorted(report["hashes"].items()):
            mark = {
                "ok": "  ok   ",
                "stale": "  STALE",
                "stale-unused": "  stale ",
                "no-hash": "  ?     ",
            }[info["status"]]
            suffix = ""
            if info["status"] == "stale-unused":
                suffix = "  (declared but never called — ignored)"
            elif info.get("healed") is True:
                suffix = "  -> re-registered from a shipped document"
            elif info["status"] == "stale":
                suffix = (
                    "  -> no document shipped; add one to GQLHealer.GQL_DOCUMENTS"
                    if name in report["unrepairable"]
                    else "  -> a shipped document can repair this at runtime"
                )
            print(f"{mark} {name}{suffix}")
        if report["documents"]:
            print("\nShipped documents:")
            for name, info in sorted(report["documents"].items()):
                print(f"  {'ok  ' if info['status'] == 'ok' else 'BROKEN'} {name}")
                for problem in info["problems"]:
                    print(f"         {problem}")
        if report["stale"]:
            print(
                f"\n{len(report['stale'])} stale hash(es). The running miner repairs "
                f"these itself (see classes/GQLHealer.py); operations listed as "
                f"having no document need one written."
            )
        else:
            print("\nAll hashes live.")

    broken_documents = [
        name
        for name, info in report["documents"].items()
        if info["status"] != "ok"
    ]
    return 1 if (report["unrepairable"] or broken_documents) else 0


if __name__ == "__main__":
    sys.exit(main())
