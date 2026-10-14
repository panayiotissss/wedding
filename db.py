"""
Database layer — Postgres via SQLAlchemy 2.0.

One table: `events`.  Each row is a couple's gallery (a tenant).  Photos live in
R2 under a per-event prefix (see storage.py), so the DB only holds metadata.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    # Internal id — also used as the R2 storage prefix (unguessable).
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Short public identifier used in URLs (/e/<slug>).
    slug: Mapped[str] = mapped_column(String(16), unique=True, index=True)

    couple_names: Mapped[str] = mapped_column(String(200))       # WED-4 dynamic name
    password_hash: Mapped[str] = mapped_column(String(255))      # couple's gallery pw
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Human-readable R2 folder, e.g. "orestis-athina-df75db50" (name + slug for
    # uniqueness). Nullable so pre-existing rows fall back to the id-based prefix.
    storage_prefix: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # pending_payment → active
    status: Mapped[str] = mapped_column(String(20), default="pending_payment")
    stripe_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # WED-6: set to paid_at + EVENT_TTL_DAYS; cleanup deletes everything past this.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _normalize_url(url: str) -> str:
    """Neon/Render hand out postgres:// URLs; SQLAlchemy + psycopg3 wants
    postgresql+psycopg://.  Normalize so either form in DATABASE_URL works."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


_url = _normalize_url(config.DATABASE_URL)

# Neon's pooled endpoint (…-pooler…) is PgBouncer in transaction mode, which
# doesn't play well with psycopg's automatic server-side prepared statements.
# Disabling them keeps pooled connections safe. (No-op / skipped for sqlite.)
_connect_args = {"prepare_threshold": None} if _url.startswith("postgresql") else {}

engine = create_engine(_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables if they don't exist (fine for a POC — swap to Alembic later)."""
    Base.metadata.create_all(engine)
    # Additive migration: add storage_prefix to DBs created before it existed.
    # (create_all() only creates missing tables, never missing columns.)
    if _url.startswith("postgresql"):
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE events ADD COLUMN IF NOT EXISTS storage_prefix VARCHAR(120)")
            )


def get_db():
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
