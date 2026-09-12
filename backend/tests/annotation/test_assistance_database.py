from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import app.annotation.database as database_module
from app.annotation.database import AnnotationDatabase


def _create_v2_database(root: Path, source: Path, monkeypatch) -> None:
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 2)
    database = AnnotationDatabase(root, source)
    database.initialize()
    database.close()
    with sqlite3.connect(root / "annotations.db") as connection:
        connection.execute(
            "INSERT INTO camera_setups(id,name,revision,template_revision_id,created_at,updated_at) "
            "VALUES('setup-v2','Quay cu',0,NULL,'now','now')"
        )


def test_v3_upgrade_preserves_v2_rows_and_creates_assistance_tables(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "annotations"
    source = tmp_path / "source"
    _create_v2_database(root, source, monkeypatch)
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 3)

    database = AnnotationDatabase(root, source)
    database.initialize()
    try:
        with database.read_connection() as connection:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            setup = connection.execute(
                "SELECT name FROM camera_setups WHERE id='setup-v2'"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
    finally:
        database.close()

    assert version == 3
    assert setup == "Quay cu"
    assert {"assistance_runs", "assistance_suggestions"} <= tables
    assert (root / "annotations.db.backup-v2").is_file()


def test_failed_v3_upgrade_rolls_back_assistance_tables(tmp_path: Path, monkeypatch):
    root = tmp_path / "annotations"
    source = tmp_path / "source"
    _create_v2_database(root, source, monkeypatch)
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 3)

    broken = AnnotationDatabase(
        root,
        source,
        before_version_write=lambda: (_ for _ in ()).throw(RuntimeError("boom-v3")),
    )
    with pytest.raises(RuntimeError, match="boom-v3"):
        broken.initialize()

    with sqlite3.connect(root / "annotations.db") as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert version == 2
    assert "assistance_runs" not in tables
    assert "assistance_suggestions" not in tables
