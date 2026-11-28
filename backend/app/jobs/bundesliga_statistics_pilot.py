"""Read local JSON fixtures and observations, then report an offline statistics pilot."""
import argparse
import json
from pathlib import Path

from app.statistics.offline_pilot import build_pilot


def load_json(path: str):
    file = Path(path)
    if file.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("INPUT_SIZE_LIMIT")
    return json.loads(file.read_text(encoding="utf8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--permission", required=True)
    parser.add_argument("--season", choices=("2024-2025", "2025-2026", "2026-2027"), required=True)
    args = parser.parse_args(argv)
    try:
        result = build_pilot(load_json(args.fixtures), load_json(args.observations),
                             season=args.season, permission=load_json(args.permission))
    except (ValueError, TypeError, KeyError, OSError):
        print(json.dumps({"status": "BLOCKED", "reason": "INVALID_OFFLINE_INPUT"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "READY_FOR_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
