"""Bounded, read-only SQL + ledger/index-addressed R2 match views. No ingest or analysis runs."""
from functools import lru_cache
from datetime import date, datetime, time as day_time, timedelta, timezone
import hashlib
import json
import re
import threading
import time
import unicodedata
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import String, cast, create_engine, func, or_, select, text, union
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.historical.repository import HistoricalRepository
from app.models import ActiveMatch, ActiveStatistic, ArchiveJob, MatchAnalysis, MatchResult, SystemSetting
from app.providers.identity import REVIEWED_IMPORT_KEY, reviewed_fixture_key
from app.providers.multileague import digest
from app.providers.result_sources import utc
from app.storage import lifecycle as lc
from app.storage.r2 import R2ObjectStore

router = APIRouter(prefix="/match-center", tags=["match-center"])
ISTANBUL = ZoneInfo("Europe/Istanbul")
HISTORY_SLOTS = threading.BoundedSemaphore(2)
ALIASES = {"de.bl1": ("de.bl1", "bl1", "8"), "en.pl": ("en.pl", "pl", "pl1", "36"), "tr.sl": ("tr.sl",)}
GROUPS = {"scheduled": {"SCHEDULED", "NS", "POSTPONED"},
    "in_progress": {"LIVE", "IN_PLAY", "IN_PROGRESS", "PAUSED", "HT"},
    "finished": {"FINISHED", "FT", "COMPLETED", "FINAL", "CANCELLED"}}


def unavailable(code="MATCH_DATA_UNAVAILABLE"):
    return HTTPException(503, detail=code, headers={"Cache-Control": "no-store", "Retry-After": "5"})


@lru_cache(maxsize=1)
def read_engine():
    cfg = get_settings()
    if not cfg.database_url_valid: raise unavailable()
    url = cfg.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, hide_parameters=True, pool_pre_ping=True, pool_size=1, max_overflow=1,
        pool_timeout=2, connect_args={"connect_timeout":3})


def read_db():
    """Reuse application's pool; fail boundedly instead of holding health-check workers."""
    try:
        # A dedicated connection is returned on every outcome, never committed.
        with read_engine().connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            conn.execute(text("SET LOCAL statement_timeout = '4s'"))
            with Session(bind=conn, expire_on_commit=False) as session:
                yield session
            conn.rollback()
    except HTTPException:
        raise
    except Exception:
        raise unavailable() from None


class PublicReadStore(R2ObjectStore):
    def get_object(self, key):
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            data = body.read(1_048_577)
            if len(data) > 1_048_576: raise IOError("PART_LIMIT")
            return data
        finally: body.close()
    def put_object(self, *a, **kw): raise PermissionError("READ_ONLY")
    def put_object_if_absent(self, *a, **kw): raise PermissionError("READ_ONLY")
    def delete_object(self, *a, **kw): raise PermissionError("READ_ONLY")
    def list_objects(self, *a, **kw): raise PermissionError("NO_BUCKET_SCAN")


def store_factory():
    from botocore.config import Config
    return PublicReadStore(settings=get_settings(), client_config=Config(connect_timeout=2, read_timeout=2,
        retries={"total_max_attempts": 1}))


def day_bounds(day=None, *, now):
    if day is not None and not 2000 <= day.year <= 2100:
        raise HTTPException(422,detail="MATCH_DATE_OUT_OF_RANGE")
    end_day = day or now.astimezone(ISTANBUL).date()
    start_day = day or end_day - timedelta(days=30)
    return (datetime.combine(start_day, day_time.min, ISTANBUL).astimezone(timezone.utc),
            datetime.combine(end_day+timedelta(days=1), day_time.min, ISTANBUL).astimezone(timezone.utc))


def league_id(value):
    return next((k for k, aliases in ALIASES.items() if value in aliases), value)


def folded(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def projection(row, *, outcome=None, policy=None, storage, now, analysis=None):
    kickoff = utc(row.get("start_time"))
    if kickoff is None: raise ValueError("EXPLICIT_UTC_REQUIRED")
    verified = utc((outcome or {}).get("verified_at"))
    score = None
    if (policy and row.get("status") in GROUPS["finished"]-{"CANCELLED"} and outcome and outcome.get("status") == "FINAL" and verified and kickoff <= verified <= now
            and all(type(outcome.get(k)) is int and outcome[k] >= 0 for k in ("home_score", "away_score"))):
        score = {"home_score": outcome["home_score"], "away_score": outcome["away_score"], "status": "FINAL", "verified_at": verified.isoformat()}
    # Only reviewed, public licence attribution is exposed. Never serialize ORM/journal bodies.
    source = None
    if policy and policy.get("licence") in {"ODbL-1.0", "CC0-1.0"}:
        primary = "OpenLigaDB" if policy["licence"] == "ODbL-1.0" else "OpenFootball"
        source = {"name": primary, "licence": policy["licence"], "attribution":
            "Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0" if primary == "OpenLigaDB" else "OpenFootball football.json (CC0-1.0)",
            "licence_url": "https://www.openligadb.de/lizenz" if primary == "OpenLigaDB" else "https://github.com/openfootball/football.json/blob/master/LICENSE.md"}
        source["native_id"] = str(row.get("provider_match_id")) if row.get("provider") in {"openligadb","openfootball"} else None
    updated = utc(row.get("last_updated_at")) or utc(row.get("available_at"))
    return {"id": str(UUID(str(row["id"]))), "home_team": row["home_team"], "away_team": row["away_team"],
        "league_id": league_id(row.get("league_id")), "league_name": row.get("league_name"), "season": row.get("season"),
        "start_time": kickoff.isoformat(), "status": row["status"], "result": score,
        "last_updated_at": updated.isoformat() if updated else None, "storage": storage, "source": source,
        "analysis_summary": analysis, "statistics": [], "archive_verified_at": now.isoformat() if storage == "R2" else None}


def sql_match(session, match, *, now):
    policy = lc.policy_for(session, match, "matches")
    row = lc.sql_snapshot(match)
    journal = session.get(SystemSetting, REVIEWED_IMPORT_KEY)
    if journal and journal.value.get("match_id") == str(match.id) and reviewed_fixture_key(row):
        event = journal.value["candidate"]["event"]
        row.update({k:event[k] for k in ("home_team", "away_team", "league_id", "league_name", "season","provider","provider_match_id")})
    result = session.scalar(select(MatchResult).where(MatchResult.match_id == match.id))
    analysis = session.execute(select(MatchAnalysis.analysis_status,MatchAnalysis.final_decision,
        MatchAnalysis.analysis_timestamp,MatchAnalysis.model_version).where(MatchAnalysis.match_id == match.id,
        MatchAnalysis.analysis_timestamp <= now, MatchAnalysis.analysis_status == "ANALYZED")
        .order_by(MatchAnalysis.analysis_timestamp.desc(), MatchAnalysis.id.desc()).limit(1)).mappings().first()
    summary = {"analysis_status": analysis["analysis_status"], "signal": analysis["final_decision"],
        "analysis_timestamp": lc.aware(analysis["analysis_timestamp"]).isoformat(), "model_version": analysis["model_version"]} if analysis else None
    view = projection(row, outcome=lc.sql_snapshot(result) if result else None, policy=policy, storage="POSTGRESQL", now=now, analysis=summary)
    for stat in session.scalars(select(ActiveStatistic).where(ActiveStatistic.match_id == match.id).limit(20)):
        permission = lc.policy_for(session,match,"statistics",stat)
        if (permission and view["result"] and lc.technical_valid(stat,result,now,match)
                and permission["licence"] == stat.licence and permission["reference"] == stat.permission_reference):
            view["statistics"].append({"name":stat.name,"home":float(stat.home_value),"away":float(stat.away_value),"unit":stat.unit})
    return view


class IndexedHistory:
    def __init__(self, session, *, store=None, clock=time.monotonic):
        self.session, self.store, self.clock = session, store, clock
        self.deadline = clock()+8
        self.buffer = {}
        self.results = {}
        self.acquired = False

    def close(self):
        if self.acquired:
            try:
                if hasattr(self.store, "client"): self.store.client.close()
            finally: HISTORY_SLOTS.release()

    def part(self, ledger):
        if self.clock() > self.deadline: raise TimeoutError()
        if (not ledger or ledger.status != "VERIFIED" or not ledger.verified_at or not ledger.checksum
                or ledger.row_count != 1): raise IOError("UNVERIFIED_LEDGER")
        if ledger.r2_key not in self.buffer:
            if not self.acquired:
                if not HISTORY_SLOTS.acquire(blocking=False): raise TimeoutError()
                self.acquired = True
                self.store = self.store or store_factory()
            self.buffer[ledger.r2_key] = HistoricalRepository(self.store).read_verified_part(ledger.r2_key,
                checksum=ledger.checksum, size=ledger.file_size_bytes or 0, row_count=ledger.row_count)[0]
        return self.buffer[ledger.r2_key]

    def match(self, index, *, now):
        value = index.value
        uid = UUID(str(value["match_id"]))
        if index.key != "source-identity:"+digest([value["provider"], value["provider_match_id"]]):
            raise ValueError("IDENTITY_INDEX_CONFLICT")
        key = value.get("first_verified_fixture_key", "")
        pattern = r"matches/season=([\w.-]+)/league=([\w.-]+)/date=(\d{4}-\d{2}-\d{2})/part=[\w.-]+\.parquet"
        scope = re.fullmatch(pattern, key)
        if not scope: raise ValueError("INDEXED_PART_REQUIRED")
        prefix = key.rsplit("/", 1)[0] + "/part=lifecycle-" + hashlib.sha256(f"active_matches:{uid}".encode()).hexdigest()[:20] + "-"
        ledgers = self.session.scalars(select(ArchiveJob).where(ArchiveJob.dataset_type == "matches",
            or_(ArchiveJob.r2_key == key, ArchiveJob.r2_key.startswith(prefix)))
            .order_by(ArchiveJob.verified_at.desc(), ArchiveJob.r2_key).limit(9)).all()
        if not ledgers or len(ledgers)>8: raise IOError("REVISION_SCOPE_LIMIT")
        candidates = []
        for ledger in ledgers:
            row = self.part(ledger)
            if row.get("archive_format") not in {None,"lifecycle-v1"}: raise ValueError("UNKNOWN_ARCHIVE_FORMAT")
            if str(row.get("id")) != str(uid): raise ValueError("UUID_MISMATCH")
            if (row.get("operational_provider"), row.get("operational_provider_match_id")) != (value["provider"], value["provider_match_id"]):
                raise ValueError("NATIVE_ID_MISMATCH")
            if row.get("archive_format") == "lifecycle-v1":
                snapshot = json.loads(row["sql_snapshot_json"])
                if row.get("sql_table") != "active_matches" or row.get("sql_identity") != str(uid) or str(snapshot["id"]) != str(uid):
                    raise ValueError("SQL_SNAPSHOT_MISMATCH")
                if (snapshot["provider"], snapshot["provider_match_id"]) != (value["provider"], value["provider_match_id"]):
                    raise ValueError("SQL_IDENTITY_MISMATCH")
                if (snapshot["status"] != row["status"] or utc(snapshot["start_time"]) != utc(row["start_time"])
                        or snapshot["season"] != row["season"]):
                    raise ValueError("SQL_CONTENT_MISMATCH")
                same_names = (snapshot["home_team"],snapshot["away_team"]) == (row["home_team"],row["away_team"])
                if not same_names and (not reviewed_fixture_key(snapshot) or reviewed_fixture_key(snapshot) != reviewed_fixture_key(row)):
                    raise ValueError("TEAM_IDENTITY_CONFLICT")
            if row.get("season") != value.get("season") or league_id(row.get("league_id")) != league_id(value.get("league_id")):
                raise ValueError("PARTITION_IDENTITY_CONFLICT")
            available = utc(row.get("available_at")) or utc(row.get("verified_at"))
            if available and available <= now: candidates.append((available, row))
        if not candidates: raise ValueError("NO_AVAILABLE_REVISION")
        candidates.sort(key=lambda x:x[0], reverse=True)
        latest = candidates[0][1]
        if any(at == candidates[0][0] and (r["home_team"],r["away_team"],r["status"],r.get("start_time")) !=
               (latest["home_team"],latest["away_team"],latest["status"],latest.get("start_time")) for at,r in candidates[1:]):
            raise ValueError("REVISION_CONFLICT")
        original = json.loads(latest["sql_snapshot_json"]) if latest.get("archive_format") == "lifecycle-v1" else latest
        proxy = type("Fixture", (), {"id":uid,"provider":value["provider"],"provider_match_id":value["provider_match_id"],
            "league_id":value.get("league_id"),"home_team":original["home_team"],"away_team":original["away_team"],
            "start_time":utc(latest["start_time"])})()
        policy = lc.policy_for(self.session, proxy, "matches")
        if not policy and latest.get("archive_format") != "lifecycle-v1":
            journal = self.session.get(SystemSetting, REVIEWED_IMPORT_KEY)
            if (journal and journal.value.get("match_id") == str(uid) and reviewed_fixture_key(latest)
                    and journal.value.get("archive_rows",{}).get("matches") == lc.canonical(latest)):
                policy = {"licence":journal.value["candidate"]["licence"],"attribution":journal.value["candidate"]["attribution"]}
        if not policy or latest.get("licence") != policy["licence"] or latest.get("attribution") != policy["attribution"]:
            raise ValueError("LICENCE_NOT_VERIFIED")
        result = self.outcome(latest, uid=uid, now=now, scope=scope)
        return projection(latest, outcome=result, policy=policy, storage="R2", now=now)

    def outcome(self, match, *, uid, now, scope):
        prefix = f"results/season={scope[1]}/league={scope[2]}/date={scope[3]}/"
        if prefix not in self.results:
            ledgers = self.session.scalars(select(ArchiveJob).where(ArchiveJob.dataset_type == "results",
                ArchiveJob.r2_key.startswith(prefix)).order_by(ArchiveJob.verified_at.desc(), ArchiveJob.r2_key).limit(17)).all()
            if len(ledgers)>16: raise IOError("RESULT_SCOPE_LIMIT")
            self.results[prefix] = [self.part(ledger) for ledger in ledgers]
        found = []
        for row in self.results[prefix]:
            if str(row.get("match_id")) != str(uid): continue
            if row.get("provider") != match["provider"] or str(row.get("provider_match_id")) != str(match["provider_match_id"]):
                raise ValueError("RESULT_SOURCE_CONFLICT")
            if row.get("licence") != match["licence"]: raise ValueError("RESULT_LICENCE_CONFLICT")
            snapshot = json.loads(row["sql_snapshot_json"]) if row.get("archive_format") == "lifecycle-v1" else row
            if str(snapshot.get("match_id")) != str(uid): raise ValueError("RESULT_UUID_CONFLICT")
            if row.get("archive_format") == "lifecycle-v1" and (
                    row.get("sql_table") != "match_results" or row.get("sql_identity") != str(snapshot.get("id"))
                    or any(row.get(k) != snapshot.get(k) for k in ("home_score","away_score","status"))
                    or utc(row.get("verified_at")) != utc(snapshot.get("verified_at"))):
                raise ValueError("RESULT_SNAPSHOT_CONFLICT")
            verified = utc(snapshot.get("verified_at"))
            available = utc(row.get("available_at")) or verified
            if verified and available and available <= now:
                found.append((available, {**snapshot,"verified_at":verified.isoformat()}))
        if not found: return None
        found.sort(key=lambda x:x[0], reverse=True)
        if any(at == found[0][0] and (r.get("home_score"),r.get("away_score")) !=
            (found[0][1].get("home_score"),found[0][1].get("away_score")) for at,r in found[1:]):
            raise ValueError("RESULT_REVISION_CONFLICT")
        return found[0][1]

    def analysis(self, row, *, now):
        kickoff = utc(row["start_time"])
        prefix = f"analyses/season={row['season']}/league={row['league_id']}/date={kickoff.date()}/"
        ledgers = self.session.scalars(select(ArchiveJob).where(ArchiveJob.dataset_type == "analyses",
            ArchiveJob.r2_key.startswith(prefix)).order_by(ArchiveJob.verified_at.desc()).limit(9)).all()
        if len(ledgers)>8: raise IOError("ANALYSIS_SCOPE_LIMIT")
        rows = []
        for ledger in ledgers:
            item = self.part(ledger)
            if str(item.get("match_id")) != row["id"]: continue
            snapshot = json.loads(item["sql_snapshot_json"])
            stamp = utc(snapshot.get("analysis_timestamp"))
            if (item.get("sql_table") != "match_analyses" or str(snapshot.get("match_id")) != row["id"]
                    or item.get("sql_identity") != str(snapshot.get("id"))
                    or item.get("licence") != row["source"]["licence"]): raise ValueError("ANALYSIS_NOT_VERIFIED")
            if snapshot.get("analysis_status") == "ANALYZED" and stamp and stamp<=now:
                rows.append({"analysis_status":"ANALYZED","signal":snapshot["final_decision"],
                    "analysis_timestamp":stamp.isoformat(),"model_version":snapshot["model_version"]})
        return max(rows,key=lambda r:r["analysis_timestamp"],default=None)


def catalog(session, *, source, cursor, start, end, league):
    sql = select(func.lower(func.replace(cast(ActiveMatch.id, String), "-", "")).label("uid")).where(
        ActiveMatch.start_time >= start, ActiveMatch.start_time < end)
    idx = SystemSetting.value
    history = select(func.lower(func.replace(idx["match_id"].as_string(), "-", "")).label("uid")).where(
        SystemSetting.key.startswith("source-identity:"), idx["first_verified_fixture_key"].as_string().is_not(None))
    days = []
    day = start.date()
    while day <= (end-timedelta(microseconds=1)).date():
        days.append(idx["first_verified_fixture_key"].as_string().contains(f"/date={day.isoformat()}/", autoescape=True))
        day += timedelta(days=1)
    history = history.where(or_(*days))
    if league:
        sql = sql.where(ActiveMatch.league_id.in_(ALIASES[league]))
        history = history.where(idx["league_id"].as_string().in_(ALIASES[league]))
    unioned = (sql if source == "active" else history if source == "history" else union(sql,history)).subquery()
    query = select(unioned.c.uid).distinct().order_by(unioned.c.uid)
    if cursor: query = query.where(unioned.c.uid > cursor.hex)
    return session.scalars(query.limit(33)).all()


def list_view(session, *, source="all", day=None, league=None, team="", status=None, cursor=None, limit=12, now=None, store=None):
    now = now or datetime.now(timezone.utc)
    start,end = day_bounds(day, now=now)
    ids = catalog(session,source=source,cursor=cursor,start=start,end=end,league=league)
    history = IndexedHistory(session,store=store)
    items, scanned, failed, read = [], 0, 0, 0
    last = None
    try:
        for raw in ids[:32]:
            if history.clock()>history.deadline: raise unavailable("MATCH_READ_TIMEOUT")
            scanned += 1
            try:
                uid = UUID(str(raw)); last = uid
                active = session.get(ActiveMatch, uid) if source != "history" else None
                if active:
                    row = sql_match(session,active,now=now)
                else:
                    indexes = session.scalars(select(SystemSetting).where(SystemSetting.key.startswith("source-identity:"),
                        func.lower(func.replace(SystemSetting.value["match_id"].as_string(),"-","")) == uid.hex).limit(3)).all()
                    if len(indexes)!=1: raise ValueError("AMBIGUOUS_IDENTITY")
                    row = history.match(indexes[0],now=now); read += 1
                if not start <= utc(row["start_time"]) < end: continue
                if league and row["league_id"] != league: continue
                if status and row["status"] not in GROUPS[status]: continue
                if team and folded(team) not in folded(row["home_team"]+" "+row["away_team"]): continue
                items.append(row)
                if len(items) == limit: break
            except Exception:
                failed += 1
        if failed and not items: raise unavailable("MATCH_ARCHIVE_NOT_VERIFIED")
        more = len(ids)>scanned
        return {"items":items,"next_cursor":str(last) if more and last else None,
            "as_of":now.isoformat(),"source":source,"history_status":"PARTIAL" if failed else "VERIFIED" if read else "NOT_READ",
            "scope":{"scanned":scanned,"limit":limit,"start_utc":start.isoformat(),"end_utc":end.isoformat()},
            "warnings":["SOME_MATCHES_NOT_VERIFIED"] if failed else []}
    finally: history.close()


def detail_view(session, match_id, *, source="all", now=None, store=None):
    now = now or datetime.now(timezone.utc)
    active = session.get(ActiveMatch, match_id) if source != "history" else None
    if active: return sql_match(session,active,now=now)
    history = IndexedHistory(session,store=store)
    try:
        indexes = session.scalars(select(SystemSetting).where(SystemSetting.key.startswith("source-identity:"),
            func.lower(func.replace(SystemSetting.value["match_id"].as_string(),"-","")) == match_id.hex).limit(3)).all()
        if not indexes: raise HTTPException(404, detail="MATCH_NOT_FOUND")
        if len(indexes)!=1: raise unavailable("MATCH_ARCHIVE_NOT_VERIFIED")
        result = history.match(indexes[0],now=now)
        result["analysis_summary"] = history.analysis(result,now=now)
        return result
    except HTTPException: raise
    except Exception: raise unavailable("MATCH_ARCHIVE_NOT_VERIFIED") from None
    finally: history.close()


@router.get("")
def matches(response:Response, source:str=Query("all",pattern="^(all|active|history)$"), day:date|None=None,
            league:str|None=Query(None,pattern="^(de.bl1|en.pl|tr.sl)$"), team:str=Query("",max_length=80),
            status:str|None=Query(None,pattern="^(scheduled|in_progress|finished)$"), cursor:UUID|None=None,
            limit:int=Query(12,ge=1,le=12), db:Session=Depends(read_db)):
    response.headers["Cache-Control"]="no-store"
    try: return list_view(db,source=source,day=day,league=league,team=team,status=status,cursor=cursor,limit=limit)
    except HTTPException: raise
    except Exception: raise unavailable() from None


@router.get("/{match_id}")
def match_detail(match_id:UUID,response:Response,source:str=Query("all",pattern="^(all|history)$"), db:Session=Depends(read_db)):
    response.headers["Cache-Control"]="no-store"
    try: return detail_view(db,match_id,source=source)
    except HTTPException: raise
    except Exception: raise unavailable() from None
