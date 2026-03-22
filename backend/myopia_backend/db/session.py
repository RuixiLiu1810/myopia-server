from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings


def create_engine_from_url(database_url: str | None = None, echo: bool = False) -> Engine:
    url = database_url or get_settings().database_url
    if not url:
        raise RuntimeError(
            "Database URL is empty. Set MYOPIA_DATABASE_URL, "
            'for example "postgresql+psycopg://user:pass@host:5432/dbname".'
        )
    return create_engine(url, future=True, pool_pre_ping=True, echo=echo)


def create_session_factory(
    database_url: str | None = None, echo: bool = False
) -> sessionmaker[Session]:
    engine = create_engine_from_url(database_url=database_url, echo=echo)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@lru_cache(maxsize=1)
def get_default_session_factory() -> sessionmaker[Session]:
    return create_session_factory()


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] | None = None,
    database_url: str | None = None,
    echo: bool = False,
) -> Generator[Session, None, None]:
    if session_factory is not None:
        factory = session_factory
    elif database_url is None and not echo:
        factory = get_default_session_factory()
    else:
        factory = create_session_factory(database_url=database_url, echo=echo)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
