#!/usr/bin/env python3
"""Verify GitHub Actions secrets for Bundesliga 32h ingestion."""
import os
import sys
import json
from pathlib import Path

def check_secret(name: str, value: str | None) -> dict:
    """Check if a secret is properly configured."""
    if not value:
        return {"name": name, "status": "MISSING", "value_preview": None}
    
    # Don't expose full secret values
    preview = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
    return {"name": name, "status": "CONFIGURED", "value_preview": preview}

def main():
    print("GitHub Actions Secrets Verification")
    print("=" * 50)
    
    secrets_to_check = [
        "DATABASE_URL",
        "R2_ENDPOINT", 
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET"
    ]
    
    results = {"all_configured": True, "secrets": []}
    
    for name in secrets_to_check:
        value = os.environ.get(name)
        result = check_secret(name, value)
        results["secrets"].append(result)
        
        status_symbol = "+" if result["status"] == "CONFIGURED" else "X"
        print(f"[{status_symbol}] {name}: {result['status']}")
        if result.get("value_preview"):
            print(f"    Preview: {result['value_preview']}")
        
        if result["status"] == "MISSING":
            results["all_configured"] = False
    
    print()
    
    # Summary
    if results["all_configured"]:
        print("[OK] All secrets are configured for production use.")
    else:
        print("[WARNING] Some secrets are missing. Dry-run mode will be used.")
        print()
        print("To enable production mode, set these secrets in GitHub:")
        print("  - Settings > Secrets and variables > Actions")
        for s in results["secrets"]:
            if s["status"] == "MISSING":
                print(f"    - {s['name']}")
    
    # Write report
    Path("r2-ingest-report.json").write_text(json.dumps(results, indent=2))
    print()
    print("Report written to r2-ingest-report.json")
    
    return 0 if results["all_configured"] else 1

if __name__ == "__main__":
    sys.exit(main())