from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


SCHEMA_VERSION = 2


class SchemaVersionError(RuntimeError):
    pass


class DatabaseBindingError(RuntimeError):
    pass


class RootInUseError(RuntimeError):
    pass


def canonical_root(path: Path) -> str:
    return str(path.resolve()).casefold()


def root_fingerprint(path: Path) -> str:
    return hashlib.sha256(canonical_root(path).encode("utf-8")).hexdigest()


class AnnotationDatabase:
    def __init__(
        self,
        root: Path,
        source_data_root: Path,
        *,
        before_version_write: Callable[[], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.source_data_root = source_data_root.resolve()
        self.path = self.root / "annotations.db"
        self._before_version_write = before_version_write
        self._lock_file: BinaryIO | None = None
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _acquire_lock(self) -> None:
        lock_path = self.root / ".annotation-owner.lock"
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RootInUseError("annotation root is already owned by another process") from exc
        self._lock_file = handle

    def _release_lock(self) -> None:
        if self._lock_file is None:
            return
        handle = self._lock_file
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._lock_file = None

    @staticmethod
    def _has_table(connection: sqlite3.Connection, name: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    def _existing_version(self, connection: sqlite3.Connection) -> int:
        if not self._has_table(connection, "schema_migrations"):
            return 0
        row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return int(row["version"] or 0)

    def _verify_binding(self, connection: sqlite3.Connection) -> None:
        if not self._has_table(connection, "annotation_binding"):
            return
        row = connection.execute(
            "SELECT source_data_root FROM annotation_binding WHERE singleton=1"
        ).fetchone()
        if row is not None and row["source_data_root"] != canonical_root(self.source_data_root):
            raise DatabaseBindingError(
                "annotation database belongs to a different private source data root"
            )

    def _backup_before_upgrade(self, connection: sqlite3.Connection, version: int) -> None:
        if version <= 0:
            return
        backup_path = self.root / f"annotations.db.backup-v{version}"
        if backup_path.exists():
            return
        backup = sqlite3.connect(backup_path)
        try:
            connection.backup(backup)
        finally:
            backup.close()

    def _migrate_v1(self, connection: sqlite3.Connection) -> None:
        schema = """
            CREATE TABLE schema_migrations(
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE annotation_binding(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                source_data_root TEXT NOT NULL,
                data_root_fingerprint TEXT NOT NULL
            );
            CREATE TABLE roi_revisions(
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('clip','template')),
                clip_id TEXT,
                camera_setup_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                polygon_json TEXT NOT NULL,
                template_revision_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(clip_id) REFERENCES annotation_clips(id) ON DELETE CASCADE,
                FOREIGN KEY(camera_setup_id) REFERENCES camera_setups(id),
                FOREIGN KEY(template_revision_id) REFERENCES roi_revisions(id)
            );
            CREATE TABLE camera_setups(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                revision INTEGER NOT NULL,
                template_revision_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(template_revision_id) REFERENCES roi_revisions(id)
            );
            CREATE TABLE annotation_clips(
                id TEXT PRIMARY KEY,
                source_job_id TEXT NOT NULL UNIQUE,
                original_name TEXT NOT NULL,
                revision INTEGER NOT NULL,
                preparation_state TEXT NOT NULL,
                source_state TEXT NOT NULL,
                failure_code TEXT,
                source_sha256 TEXT,
                media_json TEXT,
                artifact_manifest_json TEXT,
                generation_id TEXT,
                roi_revision_id TEXT,
                preview_relative_path TEXT,
                prepared_bytes INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(roi_revision_id) REFERENCES roi_revisions(id)
            );
            CREATE TABLE clip_revisions(
                clip_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(clip_id, revision),
                FOREIGN KEY(clip_id) REFERENCES annotation_clips(id) ON DELETE CASCADE
            );
            CREATE TABLE operations(
                resource_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(resource_id, operation_id)
            );
            CREATE INDEX ix_annotation_clips_created
                ON annotation_clips(created_at, id);
            CREATE INDEX ix_camera_setups_created
                ON camera_setups(created_at, id);
            CREATE INDEX ix_roi_revisions_clip
                ON roi_revisions(clip_id, revision);
            """
        # sqlite3.executescript commits an open transaction before executing.
        # Execute these fixed DDL statements individually so migration data and
        # the version marker remain one rollback-safe BEGIN IMMEDIATE unit.
        for statement in schema.split(";"):
            if statement.strip():
                connection.execute(statement)
        if self._before_version_write is not None:
            self._before_version_write()
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "INSERT INTO annotation_binding(singleton, source_data_root, data_root_fingerprint) VALUES(1, ?, ?)",
            (canonical_root(self.source_data_root), root_fingerprint(self.source_data_root)),
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            (1, now),
        )

    def _migrate_v2(self, connection: sqlite3.Connection) -> None:
        schema = """
            CREATE TABLE interactions(
                id TEXT PRIMARY KEY,
                clip_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                hand TEXT NOT NULL CHECK(hand IN ('left','right','unknown')),
                tracking_job_id TEXT,
                local_track_id INTEGER CHECK(local_track_id >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK((tracking_job_id IS NULL) = (local_track_id IS NULL)),
                FOREIGN KEY(clip_id) REFERENCES annotation_clips(id) ON DELETE CASCADE
            );
            CREATE TABLE action_annotations(
                id TEXT PRIMARY KEY,
                clip_id TEXT NOT NULL,
                interaction_id TEXT,
                roi_revision_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                label TEXT NOT NULL CHECK(label IN ('hand_in','hand_out','take_out','put_in','unclear')),
                start_frame INTEGER NOT NULL CHECK(start_frame >= 0),
                end_frame INTEGER NOT NULL CHECK(end_frame >= start_frame),
                crossing_frame INTEGER,
                object_kind TEXT NOT NULL CHECK(object_kind IN ('cash','other','unknown')),
                visibility TEXT NOT NULL CHECK(visibility IN ('clear','occluded')),
                uncertain_labels_json TEXT NOT NULL,
                unclear_reason TEXT,
                review_state TEXT NOT NULL CHECK(review_state IN ('draft','confirmed','needs_review')),
                guideline_version INTEGER NOT NULL CHECK(guideline_version = 1),
                deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(crossing_frame IS NULL OR crossing_frame BETWEEN start_frame AND end_frame),
                CHECK((label IN ('hand_in','hand_out') AND crossing_frame IS NOT NULL) OR
                      (label NOT IN ('hand_in','hand_out') AND crossing_frame IS NULL)),
                CHECK(label = 'unclear' OR interaction_id IS NOT NULL),
                FOREIGN KEY(clip_id) REFERENCES annotation_clips(id) ON DELETE CASCADE,
                FOREIGN KEY(interaction_id) REFERENCES interactions(id),
                FOREIGN KEY(roi_revision_id) REFERENCES roi_revisions(id)
            );
            CREATE TABLE action_revisions(
                annotation_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                snapshot_json TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('create','update','delete','restore','confirm','roi_invalidate','interaction_invalidate')),
                created_at TEXT NOT NULL,
                PRIMARY KEY(annotation_id, revision),
                FOREIGN KEY(annotation_id) REFERENCES action_annotations(id) ON DELETE CASCADE
            );
            CREATE TABLE review_coverage(
                id TEXT PRIMARY KEY,
                clip_id TEXT NOT NULL,
                roi_revision_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                start_frame INTEGER NOT NULL CHECK(start_frame >= 0),
                end_frame INTEGER NOT NULL CHECK(end_frame >= start_frame),
                reviewed_labels_json TEXT NOT NULL,
                guideline_version INTEGER NOT NULL CHECK(guideline_version = 1),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(clip_id) REFERENCES annotation_clips(id) ON DELETE CASCADE,
                FOREIGN KEY(roi_revision_id) REFERENCES roi_revisions(id)
            );
            CREATE TABLE coverage_revisions(
                coverage_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                snapshot_json TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('create','invalidate')),
                created_at TEXT NOT NULL,
                PRIMARY KEY(coverage_id, revision),
                FOREIGN KEY(coverage_id) REFERENCES review_coverage(id) ON DELETE CASCADE
            );
            CREATE INDEX ix_interactions_clip ON interactions(clip_id, created_at, id);
            CREATE INDEX ix_action_annotations_clip_interval
                ON action_annotations(clip_id, deleted, start_frame, end_frame, id);
            CREATE INDEX ix_action_annotations_interaction ON action_annotations(interaction_id);
            CREATE INDEX ix_review_coverage_clip_interval
                ON review_coverage(clip_id, active, start_frame, end_frame, id);
        """
        for statement in schema.split(";"):
            if statement.strip():
                connection.execute(statement)
        if self._before_version_write is not None:
            self._before_version_write()
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            (2, now),
        )

    def initialize(self) -> None:
        if self._initialized:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._acquire_lock()
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            version = self._existing_version(connection)
            if version > SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"annotation schema {version} is newer than supported version {SCHEMA_VERSION}"
                )
            self._verify_binding(connection)
            if version < SCHEMA_VERSION:
                self._backup_before_upgrade(connection, version)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if version == 0:
                        self._migrate_v1(connection)
                        version = 1
                    if version == 1 and SCHEMA_VERSION >= 2:
                        self._migrate_v2(connection)
                        version = 2
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            self._verify_binding(connection)
            connection.execute("PRAGMA journal_mode = WAL")
            self._initialized = True
        except BaseException:
            self._release_lock()
            raise
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        if not self._initialized:
            raise RuntimeError("annotation database is not initialized")
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        if not self._initialized:
            raise RuntimeError("annotation database is not initialized")
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        self._initialized = False
        self._release_lock()
