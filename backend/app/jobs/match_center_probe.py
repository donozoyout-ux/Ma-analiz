"""Manual read-only Stage 7E probe; never imports, repairs, archives or audits SQL."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import UUID

from app.api import match_center as api
from app.core.config import get_settings
from app.models import SystemSetting
from app.providers.identity import REVIEWED_IMPORT_KEY


def run():
    output={"stage":"7E","status":"NOT_VERIFIED","observed_at":datetime.now(timezone.utc).isoformat(),
        "SQL_WRITE":"NOT_RUN","R2_WRITE":"NOT_RUN","R2_DELETE":"NOT_RUN","LIVE_DELETE":"NOT_RUN"}
    cfg=get_settings()
    if cfg.auto_retention_delete_enabled or cfg.operational_delete_enabled:
        return {**output,"error_code":"DELETE_FLAGS_MUST_BE_FALSE"}
    if not cfg.database_url_valid or not cfg.r2_configured:
        return {**output,"error_code":"STORAGE_NOT_CONFIGURED"}
    try:
        with contextmanager(api.read_db)() as session:
            journal=session.get(SystemSetting,REVIEWED_IMPORT_KEY)
            if not journal:raise ValueError()
            uid=UUID(journal.value["match_id"])
            now=datetime.now(timezone.utc)
            active=api.detail_view(session,uid,now=now)
            historical=api.detail_view(session,uid,source="history",now=now)
            combined=api.list_view(session,day=date(2026,11,20),league="de.bl1",team="Bayern",now=now)
            for row in (active,historical):
                if (row["id"]!=str(uid) or row["start_time"]!="2026-11-20T19:30:00+00:00"
                        or not row["result"] or (row["result"]["home_score"],row["result"]["away_score"])!=(1,1)):
                    raise ValueError()
            if len([r for r in combined["items"] if r["id"]==str(uid)])!=1:raise ValueError()
            output.update(status="VERIFIED",active_detail=active,history_detail=historical,
                          combined_count=len(combined["items"]),canonical_uuid_dedup="PASS")
    except Exception:
        output["error_code"]="MATCH_CENTER_READ_NOT_VERIFIED"
    finally:
        if api.read_engine.cache_info().currsize:api.read_engine().dispose()
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path);args=parser.parse_args(argv)
    try:result=run()
    except Exception:result={"status":"NOT_VERIFIED","error_code":"MATCH_CENTER_CONFIGURATION_FAILED",
        "SQL_WRITE":"NOT_RUN","R2_WRITE":"NOT_RUN","R2_DELETE":"NOT_RUN","LIVE_DELETE":"NOT_RUN"}
    rendered=json.dumps(result,indent=2,ensure_ascii=True)
    if args.output:args.output.write_text(rendered+"\n",encoding="utf-8")
    print(rendered);return 0 if result["status"]=="VERIFIED" else 1


if __name__=="__main__":raise SystemExit(main())
