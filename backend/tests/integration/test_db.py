from sqlalchemy import create_engine, inspect

from app.db import create_all


def test_create_all_creates_transaction_table(tmp_path):
    url = f"sqlite:///{tmp_path / 'pilot.db'}"
    create_all(url)
    assert inspect(create_engine(url)).has_table("transactions")
