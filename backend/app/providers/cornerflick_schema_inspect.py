"""Inspect downloaded Cornerflick JSON structure offline without ingesting or copying data.

A schema inventory is not evidence that the match score is correct or licensed.
No HTTP requests, database access, R2 writes, scheduler or user-content prints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 5
MAX_NODES = 180
KEY = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


def _unique_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in items:
        if key in output:
            raise ValueError("CORNERFLICK_DUPLICATE_JSON_KEY")
        output[key] = value
    return output


def _reject_constant(_name: str) -> None:
    raise ValueError("CORNERFLICK_NONFINITE_JSON_NUMBER")


def _load(path: Path) -> tuple[bytes, Any]:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("CORNERFLICK_FILE_MISSING_OR_TOO_LARGE")
    data = path.read_bytes()
    if len(data) > MAX_BYTES or not data:
        raise ValueError("CORNERFLICK_FILE_MISSING_OR_TOO_LARGE")
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("CORNERFLICK_INVALID_JSON") from exc
    if type(payload) not in (dict, list):
        raise ValueError("CORNERFLICK_INVALID_ROOT")
    return data, payload


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    return "string"


def inspect_local_json(path: str | Path, *, label: str) -> dict[str, Any]:
    """Return shape names, primitive types and counts; never raw field values.

    This deliberately does NOT map season rows to matches or assert that
    any raw key signifies a verified result, odds, licence or 90-min score.
    """
    if label not in {"season", "match"}:
        raise ValueError("CORNERFLICK_INVALID_DOCUMENT_LABEL")
    data, payload = _load(Path(path))
    fields: list[dict[str, str | int]] = []
    queue: list[tuple[str, Any, int]] = [("$", payload, 0)]
    visited = 0
    while queue and visited < MAX_NODES:
        name, value, depth = queue.pop(0)
        visited += 1
        item: dict[str, str | int] = {"path": name, "type": _kind(value)}
        if type(value) in (list, dict):
            item["count"] = len(value)
        fields.append(item)
        if depth >= MAX_DEPTH:
            continue
        if type(value) is dict:
            for key in sorted(value)[:80]:
                safe = key if KEY.fullmatch(key) else "<unrecognized-key>"
                queue.append((name + "." + safe, value[key], depth + 1))
        elif type(value) is list and value:
            # Sample at most 3 array items, preserving element type differences
            for i, child in enumerate(value[:3]):
                queue.append((f"{name}[{i}]", child, depth + 1))
    return {
        "label": label,
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_count": len(data),
        "field_count_inspected": len(fields),
        "truncated": bool(queue),
        "fields": fields,
        "verified_score": False,
        "verified_source_permission": False,
        "normalized_or_ingested": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Cornerflick schema inspection (no requests)")
    parser.add_argument("--season", type=Path, help="Downloaded Bundesliga season.json path")
    parser.add_argument("--match", type=Path, help="Downloaded Bundesliga finished-match data.json path")
    args = parser.parse_args()
    if not args.season and not args.match:
        parser.error("at least one of --season or --match is required")
    reports = []
    for label, path in (("season", args.season), ("match", args.match)):
        if path is None:
            continue
        try:
            reports.append(inspect_local_json(path, label=label))
        except (OSError, ValueError) as exc:
            # Avoid printing path or content from untrusted input.
            code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("CORNERFLICK_") else "CORNERFLICK_FILE_UNREADABLE"
            parser.exit(2, code + "\n")
    print(json.dumps({"source": "LOCAL_USER_SUPPLIED_FILE_ONLY",
                      "source_data_not_verified": True, "reports": reports},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
