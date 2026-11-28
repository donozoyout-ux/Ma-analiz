from collections.abc import Generator

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
database_url = settings.database_url
if settings.database_url_valid and database_url and database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True) if settings.database_url_valid else None
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False) if engine else None


def get_db() -> Generator[Session, None, None]:
    if engine is None or SessionLocal is None:
        raise HTTPException(status_code=503, detail="DATABASE_NOT_CONFIGURED")
    try:
        with engine.connect():
            pass
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="DATABASE_OFFLINE") from None
    with SessionLocal() as session:
        yield session
