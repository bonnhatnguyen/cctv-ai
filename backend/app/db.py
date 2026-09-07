"""Database engine/session factory and explicit schema bootstrap."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def create_all(database_url: str) -> None:
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)


def session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=create_engine(database_url), expire_on_commit=False)
