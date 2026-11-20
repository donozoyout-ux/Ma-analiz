"""Retired paid-provider command. No network access or persistence."""
import json

def main(argv=None):
    print(json.dumps({"provider": "sportmonks", "status": "DISABLED", "writes": False,
                      "error_code": "PAID_PROVIDER_DISABLED", "next_command": "python -m app.jobs.scraping_probe --league 8 --date YYYY-MM-DD"}))
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
