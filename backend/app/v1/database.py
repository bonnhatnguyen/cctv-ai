"""Private database bootstrap for the offline V1 service."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class V1Database:
    engine: Engine
    sessions: sessionmaker[Session]

    def create_schema(self) -> None:
        from .jobs import Base

        Base.metadata.create_all(self.engine)


def create_database(database_url: str) -> V1Database:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    return V1Database(engine, sessionmaker(bind=engine, expire_on_commit=False))
