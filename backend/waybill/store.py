"""
SQLite persistence. Shipments are stored as JSON blobs keyed by id, events
are an append-only log. That's enough to make the orchestrator durable:
kill the server mid-run and every in-flight shipment resumes from its last
committed stage on restart.
"""
import json
import sqlite3
import threading
import time

from .models import Shipment, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    ts REAL NOT NULL,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_shipment ON events (shipment_id, seq);
"""


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def save(self, s: Shipment) -> None:
        s.updated_at = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO shipments (id, status, data, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data, updated_at=excluded.updated_at",
                (s.id, s.status.value, s.model_dump_json(), s.updated_at),
            )

    def get(self, shipment_id: str) -> Shipment | None:
        with self.lock:
            row = self.conn.execute("SELECT data FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
        return Shipment.model_validate_json(row["data"]) if row else None

    def all(self) -> list[Shipment]:
        with self.lock:
            rows = self.conn.execute("SELECT data FROM shipments ORDER BY json_extract(data, '$.created_at')").fetchall()
        return [Shipment.model_validate_json(r["data"]) for r in rows]

    def unfinished(self) -> list[Shipment]:
        done = (Status.DELIVERED.value, Status.FAILED.value, Status.NEEDS_APPROVAL.value)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT data FROM shipments WHERE status NOT IN ({','.join('?' * len(done))})", done
            ).fetchall()
        return [Shipment.model_validate_json(r["data"]) for r in rows]

    def append_event(self, event: dict) -> int:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO events (shipment_id, ts, agent, kind, message, data) VALUES (?, ?, ?, ?, ?, ?)",
                (event["shipment_id"], event["ts"], event["agent"], event["kind"],
                 event["message"], json.dumps(event["data"])),
            )
            return cur.lastrowid

    def events(self, shipment_id: str | None = None, since: int = 0) -> list[dict]:
        sql = "SELECT * FROM events WHERE seq > ?"
        args: list = [since]
        if shipment_id:
            sql += " AND shipment_id = ?"
            args.append(shipment_id)
        with self.lock:
            rows = self.conn.execute(sql + " ORDER BY seq", args).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def reset(self) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM shipments")
            self.conn.execute("DELETE FROM events")
