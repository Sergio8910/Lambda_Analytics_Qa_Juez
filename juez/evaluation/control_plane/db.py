"""
Database Module
Manages PostgreSQL connections, session factory, and alembic migrations.
"""

import os
from typing import Generator
from sqlalchemy import create_engine, event, pool
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
import logging

logger = logging.getLogger(__name__)

# Database URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://juez_user:juez_pass_dev@localhost:5432/juez"
)

# SQLAlchemy engine configuration
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,   # Recycle connections after 1 hour
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)


def get_db_session() -> Generator[Session, None, None]:
    """
    Dependency injection for database sessions.
    Used in FastAPI endpoints and tests.
    
    Usage:
        async def my_endpoint(db: Session = Depends(get_db_session)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as exc:
        logger.exception(f"Database session error: {exc}")
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """
    Initialize database by creating all tables.
    Should be called during app startup.
    """
    from juez.evaluation.control_plane.models import Base
    
    logger.info("Initializing database...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully")


def drop_db() -> None:
    """Drop all tables. Use only for testing/cleanup."""
    from juez.evaluation.control_plane.models import Base
    
    logger.warning("Dropping all database tables!")
    Base.metadata.drop_all(bind=engine)


def get_engine():
    """Get SQLAlchemy engine (for migrations, testing, etc)"""
    return engine


def close_db() -> None:
    """Close all connections in the pool"""
    engine.dispose()


# Event listeners for logging
@event.listens_for(pool.QueuePool, "connect")
def receive_connect(dbapi_conn, connection_record):
    """Log successful connections"""
    logger.debug("Database connection established")


@event.listens_for(pool.QueuePool, "checkout")
def receive_checkout(dbapi_conn, connection_record, connection_proxy):
    """Log connection checkouts"""
    logger.debug("Database connection checked out from pool")


@event.listens_for(pool.QueuePool, "checkin")
def receive_checkin(dbapi_conn, connection_record):
    """Log connection checkins"""
    logger.debug("Database connection returned to pool")
