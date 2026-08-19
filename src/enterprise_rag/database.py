"""Database setup for durable V2 document metadata and ingestion state."""

from collections.abc import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from enterprise_rag.config import get_settings

settings = get_settings()

# SQLite needs this flag because development requests may use different threads.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
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
