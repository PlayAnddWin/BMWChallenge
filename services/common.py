import hashlib
import json
from typing import Any

from contracts.intelligence import Scope


class EngineError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def scope_matches(actual: Scope, requested: Scope) -> bool:
    return bool(set(actual.derivatives) & set(requested.derivatives)) and bool(
        set(actual.regions) & set(requested.regions)
    ) and (not requested.powertrains or not actual.powertrains or bool(
        set(actual.powertrains) & set(requested.powertrains)
    ))


def keyed_rows(rows: list[dict], expected: set[str], key: str) -> dict[str, dict]:
    mapped = {}
    for row in rows:
        identifier = row.get(key)
        if not isinstance(identifier, str) or identifier not in expected:
            raise EngineError(f"unknown {key}: {identifier!r}")
        if identifier in mapped:
            raise EngineError(f"duplicate {key}: {identifier}")
        mapped[identifier] = row
    return mapped
