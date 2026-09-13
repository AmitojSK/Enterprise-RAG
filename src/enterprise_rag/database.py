"""Database setup for durable V2 document metadata and ingestion state."""

from collections.abc import Generator
from pathlib import Path
from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from enterprise_rag.config import get_settings

settings = get_settings()


def _normalize_database_url(url: str) -> str:
    """Point bare PostgreSQL URLs at psycopg 3, the driver this project installs.

    Managed hosts (Render, Heroku, Neon) hand out ``postgres://`` or
    ``postgresql://`` URLs. SQLAlchemy reads both as a request for psycopg 2,
    which is not a dependency here, so the process would fail at import with a
    confusing ``ModuleNotFoundError`` instead of connecting.
    """

    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


# Anchored to this file, not the process working directory. The default
# ``sqlite:///./data/enterprise_rag.db`` is a *relative* path, so the API and a
# Celery worker started from different directories would silently open different
# database files -- the worker would then find no record for a document the API
# had just committed, and ingestion would stall with no error anywhere.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

database_url = make_url(_normalize_database_url(settings.database_url))

if database_url.drivername.startswith("sqlite"):
    sqlite_path = database_url.database
    if sqlite_path and sqlite_path != ":memory:":
        resolved = Path(sqlite_path)
        if not resolved.is_absolute():
            resolved = (_PROJECT_ROOT / resolved).resolve()
        # SQLite will not create missing parent directories on its own.
        resolved.parent.mkdir(parents=True, exist_ok=True)
        database_url = database_url.set(database=str(resolved))

# SQLite needs this flag because development requests may use different threads.
connect_args = {"check_same_thread": False} if database_url.drivername.startswith("sqlite") else {}
engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Base class shared by every relational database model."""


def get_db() -> Generator[Session, None, None]:
    """Provide one transaction-scoped database session to an API request."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def initialize_database() -> None:
    """Create the small local schema; production deployments use migrations later."""

    # Import registers all model classes before SQLAlchemy inspects metadata.
    import enterprise_rag.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
