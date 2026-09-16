from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import app.annotation.database as database_module
from app.annotation.database import AnnotationDatabase


def create_v1_database(root: Path, source: Path, monkeypatch) -> None:
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 1)
    database = AnnotationDatabase(root, source)
    database.initialize()
    database.close()
    connection = sqlite3.connect(root / "annotations.db")
    connection.execute(
        "INSERT INTO camera_setups(id,name,revision,template_revision_id,created_at,updated_at) "
        "VALUES('setup-v1','Quay cu',0,NULL,'now','now')"
    )
    connection.commit()
    connection.close()


def test_v2_upgrade_preserves_v1_rows_and_creates_backup(tmp_path: Path, monkeypatch):
    root = tmp_path / "annotations"
    source = tmp_path / "source"
    create_v1_database(root, source, monkeypatch)
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 2)

    database = AnnotationDatabase(root, source)
    database.initialize()
    try:
        with database.read_connection() as connection:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            setup = connection.execute(
                "SELECT name FROM camera_setups WHERE id='setup-v1'"
            ).fetchone()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert version == 2
        assert setup[0] == "Quay cu"
        assert {
            "interactions",
            "action_annotations",
            "action_revisions",
            "review_coverage",
            "coverage_revisions",
        } <= tables
        assert (root / "annotations.db.backup-v1").is_file()
    finally:
        database.close()


def test_failed_v2_upgrade_rolls_back_all_v2_tables(tmp_path: Path, monkeypatch):
    root = tmp_path / "annotations"
    source = tmp_path / "source"
    create_v1_database(root, source, monkeypatch)
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 2)

    broken = AnnotationDatabase(
        root,
        source,
        before_version_write=lambda: (_ for _ in ()).throw(RuntimeError("boom-v2")),
    )
    with pytest.raises(RuntimeError, match="boom-v2"):
        broken.initialize()

    connection = sqlite3.connect(root / "annotations.db")
    try:
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert version == 1
    assert "action_annotations" not in tables
