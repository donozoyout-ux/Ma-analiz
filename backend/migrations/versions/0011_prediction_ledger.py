"""Introduce an independent append-only prediction event ledger.

Requires PostgreSQL. Deploy only after backup, isolated rehearsal and role review.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0011_prediction_ledger"
down_revision = "0010_active_statistics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("prediction_publications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("prediction_id", UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("provider_match_id", sa.String(160), nullable=False),
        sa.Column("league_id", sa.String(80), nullable=False),
        sa.Column("season", sa.String(32), nullable=False),
        sa.Column("home_team", sa.String(200), nullable=False),
        sa.Column("away_team", sa.String(200), nullable=False),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("market", sa.String(80), nullable=False),
        sa.Column("selection", sa.String(120), nullable=False),
        sa.Column("probability", sa.Numeric(6, 5), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
        sa.Column("feature_version", sa.String(80), nullable=False),
        sa.Column("feature_sha256", sa.String(64), nullable=False),
        sa.Column("decimal_odds", sa.Numeric(10, 4)),
        sa.Column("odds_snapshot_ref", sa.String(300)),
        sa.Column("odds_captured_at", sa.DateTime(timezone=True)),
        sa.Column("odds_permission_ref", sa.String(300)),
        sa.Column("publication_version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", UUID(as_uuid=True), sa.ForeignKey("prediction_publications.id", ondelete="RESTRICT"), unique=True),
        sa.CheckConstraint("published_at < kickoff_at", name="publication_before_kickoff"),
        sa.CheckConstraint("probability >= 0 AND probability <= 1", name="publication_probability"),
        sa.CheckConstraint("publication_version > 0", name="publication_version_positive"),
        sa.CheckConstraint("feature_sha256 ~ '^[0-9a-f]{64}$'", name="publication_feature_sha256"),
        sa.CheckConstraint("(decimal_odds IS NULL AND odds_snapshot_ref IS NULL AND odds_captured_at IS NULL AND odds_permission_ref IS NULL) OR (decimal_odds IS NOT NULL AND decimal_odds > 1 AND odds_snapshot_ref IS NOT NULL AND odds_captured_at IS NOT NULL AND odds_permission_ref IS NOT NULL AND odds_captured_at <= published_at AND odds_captured_at < kickoff_at)", name="publication_odds_evidence"),
        sa.UniqueConstraint("match_id", "market", "selection", "model_version", "publication_version", name="uq_publication_version"))
    op.create_table("prediction_settlements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("publication_id", UUID(as_uuid=True), sa.ForeignKey("prediction_publications.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", UUID(as_uuid=True), sa.ForeignKey("prediction_settlements.id", ondelete="RESTRICT")),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("result_id", UUID(as_uuid=True)),
        sa.Column("result_source", sa.String(120)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("home_score", sa.Integer()),
        sa.Column("away_score", sa.Integer()),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.CheckConstraint("version > 0", name="settlement_version_positive"),
        sa.CheckConstraint("outcome IN ('WON','LOST','VOID','UNSCORED')", name="settlement_outcome"),
        sa.CheckConstraint("outcome NOT IN ('WON','LOST') OR (result_id IS NOT NULL AND result_source IS NOT NULL AND length(trim(result_source)) > 0 AND verified_at IS NOT NULL AND home_score IS NOT NULL AND away_score IS NOT NULL AND home_score >= 0 AND away_score >= 0)", name="settlement_final_evidence"),
        sa.CheckConstraint("verified_at IS NULL OR verified_at <= settled_at", name="settlement_not_future"),
        sa.UniqueConstraint("publication_id", "version", name="uq_settlement_version"))
    for name, table, cols in (
        ("ix_publication_time_id", "prediction_publications", ["published_at", "id"]),
        ("ix_publication_filters", "prediction_publications", ["league_id", "market", "model_version"]),
        ("ix_settlement_latest", "prediction_settlements", ["publication_id", "version"]),
    ):
        op.create_index(name, table, cols)
    op.execute("""CREATE FUNCTION prediction_ledger_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'prediction ledger is append-only'; END $$""")
    for table in ("prediction_publications", "prediction_settlements"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION prediction_ledger_immutable()")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    op.execute("""CREATE FUNCTION prediction_publication_clock() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    BEGIN NEW.published_at := clock_timestamp();
      IF NEW.published_at >= NEW.kickoff_at THEN RAISE EXCEPTION 'kickoff already reached'; END IF;
      IF NEW.decimal_odds IS NOT NULL OR NEW.odds_snapshot_ref IS NOT NULL OR NEW.odds_captured_at IS NOT NULL OR NEW.odds_permission_ref IS NOT NULL THEN
        RAISE EXCEPTION 'licensed odds evidence integration not enabled'; END IF;
      PERFORM 1 FROM public.active_matches WHERE id=NEW.match_id FOR SHARE;
      PERFORM 1 FROM public.predictions WHERE id=NEW.prediction_id FOR SHARE;
      IF NOT EXISTS (SELECT 1 FROM public.predictions p JOIN public.active_matches m ON m.id=p.match_id
        WHERE p.id=NEW.prediction_id AND m.id=NEW.match_id AND m.provider=NEW.provider
        AND m.provider_match_id=NEW.provider_match_id AND m.league_id=NEW.league_id AND m.season=NEW.season
        AND m.home_team=NEW.home_team AND m.away_team=NEW.away_team AND m.start_time=NEW.kickoff_at
        AND p.market=NEW.market AND p.selection=NEW.selection AND p.probability=NEW.probability
        AND p.model_version=NEW.model_version AND p.feature_version=NEW.feature_version
        AND p.feature_snapshot IS NOT NULL AND length(p.feature_snapshot)>0
        AND encode(sha256(convert_to(p.feature_snapshot,'UTF8')),'hex')=NEW.feature_sha256
        AND p.analysis_timestamp <= NEW.published_at AND p.analysis_timestamp < m.start_time) THEN
          RAISE EXCEPTION 'publication source mismatch'; END IF;
      IF (NEW.publication_version=1 AND NEW.supersedes_id IS NOT NULL) OR
        (NEW.publication_version>1 AND NOT EXISTS (SELECT 1 FROM public.prediction_publications prior
          WHERE prior.id=NEW.supersedes_id AND prior.match_id=NEW.match_id AND prior.market=NEW.market
          AND prior.selection=NEW.selection AND prior.publication_version=NEW.publication_version-1)) THEN
          RAISE EXCEPTION 'invalid publication revision'; END IF;
      RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER prediction_publication_clock BEFORE INSERT ON prediction_publications FOR EACH ROW EXECUTE FUNCTION prediction_publication_clock()")
    op.execute("""CREATE FUNCTION prediction_publication_commit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF clock_timestamp() >= NEW.kickoff_at THEN RAISE EXCEPTION 'kickoff reached before commit'; END IF;
      RETURN NULL; END $$""")
    op.execute("CREATE CONSTRAINT TRIGGER prediction_publication_commit_guard AFTER INSERT ON prediction_publications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION prediction_publication_commit_guard()")
    op.execute("""CREATE FUNCTION prediction_settlement_clock() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    DECLARE prior_id uuid; prior_version integer;
    BEGIN NEW.settled_at := clock_timestamp();
      PERFORM pg_advisory_xact_lock(hashtextextended(NEW.publication_id::text, 0));
      PERFORM 1 FROM public.match_results WHERE id=NEW.result_id FOR SHARE;
      SELECT id,version INTO prior_id,prior_version FROM public.prediction_settlements
        WHERE publication_id=NEW.publication_id ORDER BY version DESC LIMIT 1;
      IF NEW.version <> COALESCE(prior_version,0)+1 OR NEW.supersedes_id IS DISTINCT FROM prior_id THEN
        RAISE EXCEPTION 'invalid settlement revision'; END IF;
      IF EXISTS (SELECT 1 FROM public.prediction_settlements s WHERE s.id=prior_id
        AND s.result_id IS NOT DISTINCT FROM NEW.result_id AND s.result_source IS NOT DISTINCT FROM NEW.result_source
        AND s.verified_at IS NOT DISTINCT FROM NEW.verified_at AND s.home_score IS NOT DISTINCT FROM NEW.home_score
        AND s.away_score IS NOT DISTINCT FROM NEW.away_score AND s.outcome=NEW.outcome) THEN
        RAISE EXCEPTION 'duplicate settlement snapshot'; END IF;
      IF NOT EXISTS (
        SELECT 1 FROM public.prediction_publications p JOIN public.match_results r ON r.id=NEW.result_id AND r.match_id=p.match_id
        WHERE p.id=NEW.publication_id AND r.provider=p.provider AND r.verified_at IS NOT NULL
        AND r.verified_at >= p.published_at AND r.verified_at <= NEW.settled_at
        AND r.verified_at=NEW.verified_at AND r.result_source=NEW.result_source AND length(trim(r.result_source))>0
        AND r.home_score IS NOT DISTINCT FROM NEW.home_score AND r.away_score IS NOT DISTINCT FROM NEW.away_score
        AND ((r.status='CANCELLED' AND NEW.outcome='VOID') OR
          (r.status='FINAL' AND r.verified_at >= p.kickoff_at AND r.home_score>=0 AND r.away_score>=0 AND
            NEW.outcome=CASE WHEN p.market NOT IN ('1X2','FULL_TIME_RESULT','MATCH_WINNER') OR p.selection NOT IN ('1','X','2','HOME','DRAW','AWAY') THEN 'UNSCORED'
              WHEN (CASE WHEN r.home_score>r.away_score THEN 'HOME' WHEN r.home_score<r.away_score THEN 'AWAY' ELSE 'DRAW' END)
                = CASE p.selection WHEN '1' THEN 'HOME' WHEN 'X' THEN 'DRAW' WHEN '2' THEN 'AWAY' ELSE p.selection END
              THEN 'WON' ELSE 'LOST' END))) THEN RAISE EXCEPTION 'unverified settlement'; END IF;
      RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER prediction_settlement_clock BEFORE INSERT ON prediction_settlements FOR EACH ROW EXECUTE FUNCTION prediction_settlement_clock()")
    # Cluster-wide role provisioning must be reviewed by the DBA. NOLOGIN roles
    # grant no access until explicitly assigned to an application identity.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('ma_ledger_reader', 'ma_ledger_writer')) THEN
        RAISE EXCEPTION 'ledger roles already exist; review ownership and privileges';
      END IF;
      CREATE ROLE ma_ledger_reader NOLOGIN;
      CREATE ROLE ma_ledger_writer NOLOGIN;
    END $$""")
    op.execute("GRANT SELECT ON prediction_publications, prediction_settlements TO ma_ledger_reader")
    op.execute("GRANT SELECT, INSERT ON prediction_publications, prediction_settlements TO ma_ledger_writer")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON prediction_publications, prediction_settlements FROM ma_ledger_reader, ma_ledger_writer")
    op.execute("GRANT SELECT (id, match_id, market, selection, probability, model_version, feature_version, feature_snapshot, analysis_timestamp) ON predictions TO ma_ledger_writer")
    op.execute("GRANT SELECT (id, provider, provider_match_id, league_id, season, home_team, away_team, start_time) ON active_matches TO ma_ledger_writer")
    op.execute("GRANT SELECT (id, match_id, provider, status, home_score, away_score, verified_at, result_source) ON match_results TO ma_ledger_writer")


def downgrade():
    conn = op.get_bind()
    # Lock before checking emptiness so a concurrent insert cannot race the drop.
    conn.execute(sa.text("LOCK TABLE prediction_publications, prediction_settlements IN ACCESS EXCLUSIVE MODE"))
    if conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM prediction_publications) OR EXISTS (SELECT 1 FROM prediction_settlements)")).scalar():
        raise RuntimeError("LEDGER_NOT_EMPTY_BACKUP_AND_MANUAL_REVIEW_REQUIRED")
    if conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid OR r.oid=m.member WHERE r.rolname IN ('ma_ledger_reader','ma_ledger_writer'))")).scalar():
        raise RuntimeError("LEDGER_ROLES_ASSIGNED_MANUAL_REVIEW_REQUIRED")
    op.drop_table("prediction_settlements")
    op.drop_table("prediction_publications")
    op.execute("REVOKE SELECT (id, match_id, market, selection, probability, model_version, feature_version, feature_snapshot, analysis_timestamp) ON predictions FROM ma_ledger_writer")
    op.execute("REVOKE SELECT (id, provider, provider_match_id, league_id, season, home_team, away_team, start_time) ON active_matches FROM ma_ledger_writer")
    op.execute("REVOKE SELECT (id, match_id, provider, status, home_score, away_score, verified_at, result_source) ON match_results FROM ma_ledger_writer")
    op.execute("DROP FUNCTION prediction_settlement_clock()")
    op.execute("DROP FUNCTION IF EXISTS prediction_publication_commit_guard()")
    op.execute("DROP FUNCTION prediction_publication_clock()")
    op.execute("DROP FUNCTION prediction_ledger_immutable()")
    op.execute("DROP ROLE ma_ledger_reader")
    op.execute("DROP ROLE ma_ledger_writer")
