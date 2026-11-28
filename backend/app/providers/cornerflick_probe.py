"""Manual single-request Cornerflick probe: no app wiring, persistence or raw payload output.

Use only after reviewing Cornerflick access terms and with explicit opt-in.
Its shape report is NOT proof of data correctness, licensing or 90-minute score.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.providers.base import ProviderFailure
from app.providers.cornerflick_open_data import CornerflickOpenData, CornerflickDocument
from app.providers.cornerflick_schema_inspect import KEY, MAX_DEPTH, MAX_NODES, _kind


def describe_document(document: CornerflickDocument) -> dict[str, Any]:
    """Structural inventory only; never print raw JSON scalar values."""
    queue: list[tuple[str, Any, int]] = [("$", document.payload, 0)]
    fields: list[dict[str, str | int]] = []
    processed = 0
    while queue and processed < MAX_NODES:
        name, value, depth = queue.pop(0)
        processed += 1
        field: dict[str, str | int] = {"path": name, "type": _kind(value)}
        if type(value) in (list, dict):
            field["count"] = len(value)
        fields.append(field)
        if depth >= MAX_DEPTH:
            continue
        if type(value) is dict:
            for key in sorted(value)[:80]:
                safe = key if KEY.fullmatch(key) else "<unrecognized-key>"
                queue.append((name + "." + safe, value[key], depth + 1))
        elif type(value) is list:
            for idx, child in enumerate(value[:3]):
                queue.append((f"{name}[{idx}]", child, depth + 1))

    return {
        "source_url": document.source_url,
        "retrieved_at": document.retrieved_at.isoformat(),
        "sha256": document.content_sha256,
        "dataset": document.dataset,
        "competition": document.competition,
        "field_count_inspected": len(fields),
        "truncated": bool(queue),
        "fields": fields,
        "source_result_not_verified": True,
        "source_permissions_not_verified": True,
        "writes_or_ingestion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual single read of Cornerflick public JSON")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--season", action="store_true", help="Bundesliga season results JSON")
    group.add_argument("--match-slug", help="Exact Bundesliga match slug (no URL)")
    parser.add_argument("--allow-one-request", action="store_true", help="Explicitly authorize one external GET")
    parser.add_argument("--acknowledge-source-terms", action="store_true", help="Confirm access restrictions and third-party rights were reviewed")
    args = parser.parse_args()
    if not args.allow_one_request or not args.acknowledge_source_terms:
        parser.error("both --allow-one-request and --acknowledge-source-terms are required")
    try:
        with CornerflickOpenData(enabled=True) as provider:
            document = provider.season_results() if args.season else provider.match_record(args.match_slug)
        print(json.dumps(describe_document(document), ensure_ascii=False, indent=2))
        return 0
    except (ProviderFailure, ValueError) as exc:
        # Return codes only, do not include server body or user-supplied path.
        name = getattr(exc, "code", None) or type(exc).__name__.upper()
        parser.exit(2, "CORNERFLICK_PROBE_FAILED: " + str(name) + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
