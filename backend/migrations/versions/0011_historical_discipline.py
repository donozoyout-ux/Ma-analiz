"""Historical football-data.co.uk discipline archive (independent schema).

Conflict warning: PR #71 independently introduces another Alembic revision
descending from 0010; create an Alembic merge revision before combining PRs.
This migration must not be applied to production until source rights, backups
and data owner approve.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from app.models.historical_discipline import HistoricalDisciplineMatch

revision = "0011_historical_discipline"
down_revision = "0010_active_statistics"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("POSTGRESQL_REQUIRED")
    HistoricalDisciplineMatch.__table__.create(bind=bind, checkfirst=False)
    op.create_index("ix_discipline_league_season_date", "historical_discipline_matches", ["league_id", "season", "match_date"])
    op.create_index("ix_discipline_teams", "historical_discipline_matches", ["home_team", "away_team"])
    op.execute("""CREATE FUNCTION historical_discipline_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'historical discipline rows must not be silently altered'; END $$""")
    op.execute("""CREATE TRIGGER historical_discipline_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON historical_discipline_matches FOR EACH STATEMENT EXECUTE FUNCTION historical_discipline_immutable()""")


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM historical_discipline_matches)")).scalar():
        raise RuntimeError("DISCIPLINE_ARCHIVE_NOT_EMPTY_BACKUP_REQUIRED")
    op.drop_index("ix_discipline_teams", table_name="historical_discipline_matches")
    op.drop_index("ix_discipline_league_season_date", table_name="historical_discipline_matches")
    op.drop_table("historical_discipline_matches")
    op.execute("DROP FUNCTION historical_discipline_immutable()")
