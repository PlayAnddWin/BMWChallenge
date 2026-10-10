"""Canonical, checkpoint-relative tamper evidence for the transactional ledger.

The repository must serialize calls to append_event inside its write transaction.
A retained checkpoint is the trust anchor; this does not prevent administrator edits.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

GENESIS_HASH = "0" * 64


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def event_hash(event):
    """Hash every event field except the hash itself, including chain position."""
    return hashlib.sha256(canonical_json({k: v for k, v in event.items() if k != "hash"}).encode("utf-8")).hexdigest()


def checkpoint_for(events):
    return {"sequence": len(events), "hash": events[-1]["hash"] if events else GENESIS_HASH}


def verify_chain(events, checkpoint):
    """Verify an entire prefix, including its length, against a trusted checkpoint."""
    try:
        if not isinstance(events, (list, tuple)) or not isinstance(checkpoint, dict):
            return False
        if set(checkpoint) != {"sequence", "hash"} or type(checkpoint["sequence"]) is not int:
            return False
        if checkpoint["sequence"] != len(events):
            return False
        previous = GENESIS_HASH
        for sequence, event in enumerate(events, 1):
            if not isinstance(event, dict):
                return False
            if type(event.get("sequence")) is not int or event["sequence"] != sequence:
                return False
            if event.get("previous_hash") != previous or event.get("hash") != event_hash(event):
                return False
            previous = event["hash"]
        return previous == checkpoint["hash"]
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def verify_or_raise(events, checkpoint):
    if not verify_chain(events, checkpoint):
        raise ValueError("Audit ledger does not match the retained checkpoint")


def append_event(state, event, fail_injection=None):
    """Append to a transaction-local state. Failure never partially changes the ledger."""
    if not isinstance(event, dict):
        raise ValueError("Audit event must be a mapping")
    events = state.get("events", [])
    checkpoint = state.get("checkpoint", {"sequence": 0, "hash": GENESIS_HASH})
    verify_or_raise(events, checkpoint)
    if any(key in event for key in ("sequence", "previous_hash", "hash")):
        raise ValueError("Ledger sequence and hashes are server-owned")
    entry = deepcopy(event)
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    entry.update(sequence=len(events) + 1, previous_hash=checkpoint["hash"])
    entry["hash"] = event_hash(entry)
    if fail_injection is not None:
        fail_injection()
    state["events"] = [*events, entry]
    state["checkpoint"] = checkpoint_for(state["events"])
    return deepcopy(entry)
