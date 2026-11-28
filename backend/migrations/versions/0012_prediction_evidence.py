"""Independent evidence, retention-safe settlement and least-privilege verifier.

No backfill. Requires DBA review, CREATE ROLE, public-schema privilege audit.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_prediction_evidence"
down_revision = "0011_prediction_ledger"
branch_labels = None
depends_on = None


def upgrade():
    # Do not silently alter unrelated applications' public-schema grants.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_namespace n, LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
                 WHERE n.nspname='public' AND a.grantee=0 AND a.privilege_type='CREATE') THEN
        RAISE EXCEPTION 'PUBLIC_SCHEMA_CREATE_REQUIRES_DBA_REVIEW'; END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ma_ledger_verifier') THEN
        RAISE EXCEPTION 'VERIFIER_ROLE_ALREADY_EXISTS'; END IF;
      CREATE ROLE ma_ledger_verifier NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END $$""")
    op.execute("""CREATE TABLE prediction_evidence (
      id uuid PRIMARY KEY, publication_id uuid NOT NULL REFERENCES prediction_publications(id) ON DELETE RESTRICT,
      kind varchar(16) NOT NULL CHECK(kind IN ('DELIVERY','RESULT')),
      authority varchar(120) NOT NULL, event_id varchar(128) NOT NULL, key_id varchar(120) NOT NULL,
      public_key_sha256 varchar(64) NOT NULL CHECK(public_key_sha256 ~ '^[0-9a-f]{64}$'),
      payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
      payload_sha256 varchar(64) NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
      signature text NOT NULL CHECK(length(signature)=88),
      observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      supersedes_id uuid REFERENCES prediction_evidence(id) ON DELETE RESTRICT UNIQUE,
      UNIQUE(authority,event_id), UNIQUE(publication_id,kind,payload_sha256),
      CHECK(observed_at <= received_at))""")
    op.execute("CREATE UNIQUE INDEX uq_delivery_publication ON prediction_evidence(publication_id) WHERE kind='DELIVERY'")
    op.execute("CREATE INDEX ix_evidence_publication_kind ON prediction_evidence(publication_id,kind)")
    op.execute("ALTER TABLE prediction_settlements ADD COLUMN evidence_id uuid REFERENCES prediction_evidence(id) ON DELETE RESTRICT")
    op.execute("CREATE UNIQUE INDEX uq_settlement_evidence ON prediction_settlements(evidence_id) WHERE evidence_id IS NOT NULL")
    op.execute("CREATE TRIGGER prediction_evidence_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON prediction_evidence FOR EACH STATEMENT EXECUTE FUNCTION prediction_ledger_immutable()")
    op.execute("""CREATE FUNCTION prediction_evidence_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE p public.prediction_publications; prior uuid;
    BEGIN
      NEW.received_at := clock_timestamp();
      SELECT * INTO STRICT p FROM public.prediction_publications WHERE id=NEW.publication_id;
      PERFORM pg_advisory_xact_lock(hashtextextended('evidence-chain:'||p.id::text,0));
      IF NEW.observed_at > NEW.received_at OR NEW.payload->>'kind' IS DISTINCT FROM NEW.kind
        OR NEW.payload->>'authority' IS DISTINCT FROM NEW.authority
        OR NEW.payload->>'key_id' IS DISTINCT FROM NEW.key_id
        OR NEW.payload->>'event_id' IS DISTINCT FROM NEW.event_id
        OR NEW.payload->>'schema' IS DISTINCT FROM 'ma-ledger-evidence/v1'
        OR (NEW.payload->>'observed_at')::timestamptz IS DISTINCT FROM NEW.observed_at
        OR (NEW.payload->>'supersedes_id')::uuid IS DISTINCT FROM NEW.supersedes_id
        OR NEW.payload->'match'->>'match_id' IS DISTINCT FROM p.match_id::text
        OR NEW.payload->'match'->>'provider' IS DISTINCT FROM p.provider
        OR NEW.payload->'match'->>'provider_match_id' IS DISTINCT FROM p.provider_match_id
        OR NEW.payload->'match'->>'league_id' IS DISTINCT FROM p.league_id
        OR NEW.payload->'match'->>'season' IS DISTINCT FROM p.season
        OR NEW.payload->'match'->>'home_team' IS DISTINCT FROM p.home_team
        OR NEW.payload->'match'->>'away_team' IS DISTINCT FROM p.away_team
        OR (NEW.payload->'match'->>'kickoff_at')::timestamptz IS DISTINCT FROM p.kickoff_at
      THEN RAISE EXCEPTION 'EVIDENCE_IDENTITY_MISMATCH'; END IF;
      IF NEW.kind='DELIVERY' THEN
        IF NEW.observed_at < p.published_at OR NEW.received_at >= p.kickoff_at OR NEW.supersedes_id IS NOT NULL
          OR NEW.payload->>'publication_id' IS DISTINCT FROM p.id::text
          OR NEW.payload->>'access' IS DISTINCT FROM 'PUBLICLY_RETRIEVED'
          OR COALESCE(length(NEW.payload->>'audience'),0)=0 THEN
          RAISE EXCEPTION 'PUBLICATION_DELIVERY_UNVERIFIED'; END IF;
      ELSE
        SELECT e.id INTO prior FROM public.prediction_evidence e WHERE e.publication_id=p.id AND e.kind='RESULT'
          AND NOT EXISTS(SELECT 1 FROM public.prediction_evidence next WHERE next.supersedes_id=e.id);
        IF NEW.supersedes_id IS DISTINCT FROM prior OR NEW.observed_at < p.kickoff_at
          OR NEW.payload->>'period' IS DISTINCT FROM 'REGULATION_90_PLUS_STOPPAGE'
          OR COALESCE(NEW.payload->>'status','') NOT IN ('FINAL','CANCELLED','RETRACTED')
          OR COALESCE(jsonb_array_length(NEW.payload->'sources'),0)<2 THEN
          RAISE EXCEPTION 'INVALID_RESULT_EVIDENCE'; END IF;
        IF prior IS NOT NULL AND NEW.observed_at < (SELECT observed_at FROM public.prediction_evidence WHERE id=prior) THEN
          RAISE EXCEPTION 'STALE_RESULT_CORRECTION'; END IF;
        IF NEW.payload->>'status'='FINAL' AND (COALESCE(NEW.payload->>'home_score','') !~ '^[0-9]+$'
          OR COALESCE(NEW.payload->>'away_score','') !~ '^[0-9]+$') THEN
          RAISE EXCEPTION 'INVALID_REGULATION_SCORE'; END IF;
      END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER prediction_evidence_guard BEFORE INSERT ON prediction_evidence FOR EACH ROW EXECUTE FUNCTION prediction_evidence_guard()")
    op.execute("""CREATE FUNCTION prediction_delivery_commit_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN IF NEW.kind='DELIVERY' AND clock_timestamp() >= (SELECT kickoff_at FROM public.prediction_publications WHERE id=NEW.publication_id) THEN
      RAISE EXCEPTION 'delivery reached kickoff before commit'; END IF; RETURN NULL; END $$""")
    op.execute("CREATE CONSTRAINT TRIGGER prediction_delivery_commit_guard AFTER INSERT ON prediction_evidence DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION prediction_delivery_commit_guard()")
    op.execute("""CREATE FUNCTION prediction_settlement_evidence_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE p public.prediction_publications; e public.prediction_evidence; prior_id uuid; prior_version integer; expected text;
    BEGIN
      NEW.settled_at := clock_timestamp();
      PERFORM pg_advisory_xact_lock(hashtextextended(NEW.publication_id::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('evidence-chain:'||NEW.publication_id::text,0));
      SELECT * INTO STRICT p FROM public.prediction_publications WHERE id=NEW.publication_id;
      SELECT * INTO e FROM public.prediction_evidence WHERE id=NEW.evidence_id AND kind='RESULT' AND publication_id=p.id;
      IF e.id IS NULL OR EXISTS(SELECT 1 FROM public.prediction_evidence WHERE supersedes_id=e.id) THEN
        RAISE EXCEPTION 'VERIFIED_REGULATION_EVIDENCE_REQUIRED'; END IF;
      SELECT id,version INTO prior_id,prior_version FROM public.prediction_settlements
        WHERE publication_id=p.id ORDER BY version DESC LIMIT 1;
      IF NEW.version <> COALESCE(prior_version,0)+1 OR NEW.supersedes_id IS DISTINCT FROM prior_id THEN
        RAISE EXCEPTION 'invalid settlement revision'; END IF;
      expected := CASE WHEN e.payload->>'status'='CANCELLED' THEN 'VOID'
        WHEN e.payload->>'status'='RETRACTED' OR p.market NOT IN ('1X2','FULL_TIME_RESULT','MATCH_WINNER')
          OR p.selection NOT IN ('1','X','2','HOME','DRAW','AWAY') THEN 'UNSCORED'
        WHEN (CASE WHEN (e.payload->>'home_score')::int>(e.payload->>'away_score')::int THEN 'HOME'
              WHEN (e.payload->>'home_score')::int<(e.payload->>'away_score')::int THEN 'AWAY' ELSE 'DRAW' END)
            = CASE p.selection WHEN '1' THEN 'HOME' WHEN 'X' THEN 'DRAW' WHEN '2' THEN 'AWAY' ELSE p.selection END THEN 'WON' ELSE 'LOST' END;
      IF NEW.outcome IS DISTINCT FROM expected OR NEW.result_id IS DISTINCT FROM e.id
        OR NEW.result_source IS DISTINCT FROM e.authority OR NEW.verified_at IS DISTINCT FROM e.received_at
        OR NEW.home_score IS DISTINCT FROM (e.payload->>'home_score')::int
        OR NEW.away_score IS DISTINCT FROM (e.payload->>'away_score')::int THEN
        RAISE EXCEPTION 'SETTLEMENT_EVIDENCE_MISMATCH'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("DROP TRIGGER prediction_settlement_clock ON prediction_settlements")
    op.execute("CREATE TRIGGER prediction_settlement_clock BEFORE INSERT ON prediction_settlements FOR EACH ROW EXECUTE FUNCTION prediction_settlement_evidence_guard()")
    op.execute("REVOKE ALL ON prediction_evidence FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO ma_ledger_reader,ma_ledger_writer,ma_ledger_verifier")
    op.execute("GRANT SELECT ON prediction_publications,prediction_evidence TO ma_ledger_verifier")
    op.execute("GRANT INSERT ON prediction_evidence TO ma_ledger_verifier")
    op.execute("GRANT SELECT ON prediction_evidence TO ma_ledger_reader,ma_ledger_writer")
    # Trigger functions are not callable APIs. No PUBLIC EXECUTE on privileged code.
    for name in ("prediction_publication_clock", "prediction_settlement_clock", "prediction_evidence_guard",
                 "prediction_delivery_commit_guard", "prediction_settlement_evidence_guard"):
        op.execute(f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC")
    op.execute("ALTER FUNCTION prediction_publication_clock() SET search_path=pg_catalog,public,pg_temp")


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("LOCK TABLE prediction_publications,prediction_settlements,prediction_evidence IN ACCESS EXCLUSIVE MODE"))
    if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM prediction_publications) OR EXISTS(SELECT 1 FROM prediction_evidence)")):
        raise RuntimeError("LEDGER_NOT_EMPTY_BACKUP_AND_MANUAL_REVIEW_REQUIRED")
    if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid OR r.oid=m.member WHERE r.rolname='ma_ledger_verifier')")):
        raise RuntimeError("LEDGER_ROLES_ASSIGNED_MANUAL_REVIEW_REQUIRED")
    op.execute("DROP TRIGGER prediction_settlement_clock ON prediction_settlements")
    op.execute("CREATE TRIGGER prediction_settlement_clock BEFORE INSERT ON prediction_settlements FOR EACH ROW EXECUTE FUNCTION prediction_settlement_clock()")
    op.execute("ALTER TABLE prediction_settlements DROP COLUMN evidence_id")
    op.execute("DROP TABLE prediction_evidence")
    for name in ("prediction_evidence_guard", "prediction_delivery_commit_guard", "prediction_settlement_evidence_guard"):
        op.execute(f"DROP FUNCTION {name}()")
    op.execute("REVOKE SELECT ON prediction_publications FROM ma_ledger_verifier")
    op.execute("REVOKE USAGE ON SCHEMA public FROM ma_ledger_verifier")
    op.execute("DROP ROLE ma_ledger_verifier")
    # 0012's explicit USAGE grants must not prevent 0011's empty-role downgrade.
    op.execute("REVOKE USAGE ON SCHEMA public FROM ma_ledger_reader,ma_ledger_writer")
