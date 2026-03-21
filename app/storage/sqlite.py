"""SQLite-backed metadata repository."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.domain.models import AnalysisResult, CaptureEvent, ExecutionResult, PipelineStage, TaskRecord, TradeIntent


class SQLiteRepository:
    """Persists task state, JSON contracts, and deduplication data."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    event_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    failure_reason TEXT,
                    trade_id TEXT,
                    source_message_id TEXT,
                    image_sha256 TEXT UNIQUE,
                    source_image_sha256 TEXT,
                    image_path TEXT,
                    received_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    event_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trade_intents (
                    trade_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS executions (
                    trade_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "source_image_sha256" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN source_image_sha256 TEXT")
            conn.execute("UPDATE tasks SET source_image_sha256 = image_sha256 WHERE source_image_sha256 IS NULL")

    def is_duplicate(self, source_message_id: str | None, image_sha256: str) -> bool:
        """Check whether a message or image hash was already seen."""

        query = "SELECT 1 FROM tasks WHERE COALESCE(source_image_sha256, image_sha256) = ?"
        params: tuple[object, ...] = (image_sha256,)
        if source_message_id is not None:
            query = "SELECT 1 FROM tasks WHERE COALESCE(source_image_sha256, image_sha256) = ? OR source_message_id = ?"
            params = (image_sha256, source_message_id)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return row is not None

    def create_task(self, event: CaptureEvent, *, allow_duplicate: bool = False) -> TaskRecord:
        """Insert a new task in `RECEIVED` state."""

        now = datetime.utcnow().isoformat()
        storage_sha256 = event.image_sha256 if not allow_duplicate else f"{event.image_sha256}:{event.event_id}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    event_id, status, failure_reason, trade_id, source_message_id,
                    image_sha256, source_image_sha256, image_path, received_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    PipelineStage.RECEIVED.value,
                    None,
                    None,
                    event.source_message_id,
                    storage_sha256,
                    event.image_sha256,
                    event.image_path,
                    event.received_at.isoformat(),
                    now,
                    now,
                ),
            )
        return self.get_task(event.event_id)

    def update_status(self, event_id: str, status: PipelineStage, failure_reason: str | None = None) -> TaskRecord:
        """Update task status and optional failure reason."""

        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, failure_reason = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (status.value, failure_reason, now, event_id),
            )
        return self.get_task(event_id)

    def attach_trade_id(self, event_id: str, trade_id: str) -> TaskRecord:
        """Link the generated trade intent to the task."""

        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET trade_id = ?, updated_at = ? WHERE event_id = ?",
                (trade_id, now, event_id),
            )
        return self.get_task(event_id)

    def save_analysis(self, result: AnalysisResult) -> None:
        """Persist the analysis result payload."""

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO analyses (event_id, analysis_id, status, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.event_id,
                    result.analysis_id,
                    result.status.value,
                    result.model_dump_json(),
                    datetime.utcnow().isoformat(),
                ),
            )
            if result.normalized_trade_intent:
                self.save_trade_intent(result.normalized_trade_intent, conn=conn)

    def save_trade_intent(self, intent: TradeIntent, *, conn: sqlite3.Connection | None = None) -> None:
        """Persist a trade intent payload."""

        owns_connection = conn is None
        if conn is None:
            conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO trade_intents (trade_id, source_event_id, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                intent.trade_id,
                intent.source_event_id,
                intent.model_dump_json(),
                datetime.utcnow().isoformat(),
            ),
        )
        if owns_connection:
            conn.commit()
            conn.close()

    def save_execution(self, result: ExecutionResult) -> None:
        """Persist the execution result payload."""

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO executions (trade_id, status, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    result.trade_id,
                    result.status,
                    result.model_dump_json(),
                    datetime.utcnow().isoformat(),
                ),
            )

    def get_task(self, event_id: str) -> TaskRecord:
        """Return a stored task record."""

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return TaskRecord(
            event_id=row["event_id"],
            status=PipelineStage(row["status"]),
            failure_reason=row["failure_reason"],
            trade_id=row["trade_id"],
            source_message_id=row["source_message_id"],
            image_sha256=row["source_image_sha256"] or row["image_sha256"],
            image_path=row["image_path"],
            received_at=datetime.fromisoformat(row["received_at"]) if row["received_at"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_raw_payload(self, table: str, key_name: str, key_value: str) -> dict[str, object] | None:
        """Fetch a stored JSON payload from `analyses`, `trade_intents`, or `executions`."""

        with self._connect() as conn:
            row = conn.execute(f"SELECT payload_json FROM {table} WHERE {key_name} = ?", (key_value,)).fetchone()
        return None if row is None else json.loads(row["payload_json"])
