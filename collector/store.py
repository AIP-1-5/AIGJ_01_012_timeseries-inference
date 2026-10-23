"""SQLite 저장소. WAL 모드라 수집기가 쓰는 동안에도 다른 작업이 읽을 수 있다 (data-model.md)."""

import sqlite3
import threading
from pathlib import Path

from .timeutil import to_iso, utcnow

REJECT_PAYLOAD_LIMIT = 8 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY,
    message_key TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device_profile TEXT,
    space TEXT NOT NULL,
    spot TEXT,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    measured_at TEXT NOT NULL,
    time_source TEXT NOT NULL,
    gw_time TEXT,
    received_at TEXT NOT NULL,
    fcnt INTEGER,
    source TEXT NOT NULL,
    topic TEXT NOT NULL,
    UNIQUE (message_key, metric)
);
CREATE INDEX IF NOT EXISTS idx_measurements_device ON measurements (device_id, metric, measured_at);
CREATE INDEX IF NOT EXISTS idx_measurements_space ON measurements (space, measured_at);
CREATE INDEX IF NOT EXISTS idx_measurements_time ON measurements (measured_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events (at);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events (kind, at);

CREATE TABLE IF NOT EXISTS rejects (
    id INTEGER PRIMARY KEY,
    received_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_rejects_at ON rejects (received_at);

CREATE TABLE IF NOT EXISTS duplicates (
    received_at TEXT NOT NULL,
    message_key TEXT NOT NULL,
    device_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_duplicates_at ON duplicates (received_at);
"""

MEASUREMENT_COLUMNS = (
    "message_key", "device_id", "device_profile", "space", "spot", "metric", "value", "unit",
    "measured_at", "time_source", "gw_time", "received_at", "fcnt", "source", "topic",
)

EXPORT_COLUMNS = (
    "measured_at", "device_id", "device_profile", "space", "spot", "metric", "value", "unit",
    "gw_time", "received_at", "fcnt",
)


class Store:
    """수집기는 쓰기용으로, status/report/export는 readonly=True로 연다.

    사건(events)의 순서는 기록 순서(id)로 판단한다. 부팅 직후 시계가 맞춰지기 전에 기록된
    사건은 at이 실제보다 이를 수 있기 때문이다(2026-10-21 보드 B 재부팅에서 관찰).
    """

    def __init__(self, path, readonly=False):
        self.path = Path(path)
        self.readonly = readonly
        self._lock = threading.Lock()
        if readonly:
            if not self.path.exists():
                raise FileNotFoundError(f"저장소 파일이 없다: {self.path} (수집기를 먼저 실행)")
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def journal_mode(self):
        return self.conn.execute("PRAGMA journal_mode").fetchone()[0]

    # ---- 쓰기 ----

    def insert_message(self, rows):
        """메시지 1건의 측정값 행들을 넣는다. 반환: (새로 넣은 행 수, 중복 메시지 여부).

        모든 행이 이미 있으면(UNIQUE(message_key, metric)) 같은 메시지가 다시 온 것으로 보고
        duplicates에 한 줄 남긴다.
        """
        if not rows:
            return 0, False
        sql = (
            f"INSERT OR IGNORE INTO measurements ({', '.join(MEASUREMENT_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in MEASUREMENT_COLUMNS)})"
        )
        with self._lock:
            inserted = 0
            for row in rows:
                cur = self.conn.execute(sql, tuple(getattr(row, col) for col in MEASUREMENT_COLUMNS))
                inserted += cur.rowcount
            duplicate = inserted == 0
            if duplicate:
                first = rows[0]
                self.conn.execute(
                    "INSERT INTO duplicates (received_at, message_key, device_id) VALUES (?, ?, ?)",
                    (first.received_at, first.message_key, first.device_id),
                )
            self.conn.commit()
        return inserted, duplicate

    def merge_from(self, other_path, start_iso=None, end_iso=None, batch=5000):
        """다른 수집기 저장소(예: 보드 B)의 측정값을 이 저장소에 합친다. 반환: (읽은 행, 새로 넣은 행).

        두 보드는 같은 브로커에서 같은 메시지를 받으므로 message_key(deduplicationId)가 같다.
        UNIQUE(message_key, metric)로 이미 있는 행은 건너뛰어서, 이 저장소가 끊겼던 구간만 채워진다.
        여러 번 실행해도 결과가 같다. received_at은 원래 받은 보드의 값을 그대로 둔다.
        """
        other = Store(other_path, readonly=True)
        try:
            if Path(other_path).resolve() == self.path.resolve():
                raise ValueError("같은 저장소끼리는 합칠 수 없다")
            sql = f"SELECT {', '.join(MEASUREMENT_COLUMNS)} FROM measurements"
            where, params = [], []
            if start_iso:
                where.append("measured_at >= ?")
                params.append(start_iso)
            if end_iso:
                where.append("measured_at < ?")
                params.append(end_iso)
            if where:
                sql += " WHERE " + " AND ".join(where)
            insert = (
                f"INSERT OR IGNORE INTO measurements ({', '.join(MEASUREMENT_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in MEASUREMENT_COLUMNS)})"
            )
            cur = other.conn.execute(sql, params)
            scanned = inserted = 0
            while True:
                rows = [tuple(r) for r in cur.fetchmany(batch)]
                if not rows:
                    break
                # 묶음마다 짧게 잠그고 커밋해서, 돌고 있는 수집기의 저장을 오래 막지 않는다
                with self._lock:
                    before = self.conn.total_changes
                    self.conn.executemany(insert, rows)
                    inserted += self.conn.total_changes - before
                    self.conn.commit()
                scanned += len(rows)
        finally:
            other.close()
        return scanned, inserted

    def snapshot(self, out_path):
        """돌고 있는 저장소를 안전하게 한 파일로 복사한다(SQLite 백업 API, WAL 내용 포함)."""
        out_path = Path(out_path)
        if out_path.resolve() == self.path.resolve():
            raise ValueError("원본과 같은 경로로 복사할 수 없다")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(out_path)
        try:
            self.conn.backup(target)
            # 복사본은 한 파일로 다니므로 WAL을 끈다 (읽기 전용으로 열 때 -wal/-shm 파일이 필요 없게)
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
        return out_path

    def add_event(self, kind, detail=None, at=None):
        with self._lock:
            self.conn.execute(
                "INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
                (at or to_iso(utcnow()), kind, detail),
            )
            self.conn.commit()

    def add_reject(self, topic, reason, payload, received_at=None):
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        payload = (payload or "")[:REJECT_PAYLOAD_LIMIT]
        with self._lock:
            self.conn.execute(
                "INSERT INTO rejects (received_at, topic, reason, payload) VALUES (?, ?, ?, ?)",
                (received_at or to_iso(utcnow()), topic, reason, payload),
            )
            self.conn.commit()

    # ---- 읽기 ----

    def measurements_between(self, start_iso, end_iso, metrics=None):
        sql = "SELECT * FROM measurements WHERE measured_at >= ? AND measured_at < ?"
        params = [start_iso, end_iso]
        if metrics:
            sql += f" AND metric IN ({', '.join('?' for _ in metrics)})"
            params.extend(metrics)
        sql += " ORDER BY measured_at"
        return self.conn.execute(sql, params)

    def last_before(self, device_id, metric, before_iso):
        return self.conn.execute(
            "SELECT * FROM measurements WHERE device_id = ? AND metric = ? AND measured_at < ? "
            "ORDER BY measured_at DESC LIMIT 1",
            (device_id, metric, before_iso),
        ).fetchone()

    def series_keys(self):
        """저장소에 한 번이라도 나온 (device_id, metric)과 마지막 메타데이터."""
        return self.conn.execute(
            "SELECT device_id, metric, MAX(measured_at) AS last_measured, "
            "device_profile, space, spot FROM measurements GROUP BY device_id, metric"
        ).fetchall()

    def events_between(self, start_iso, end_iso):
        return self.conn.execute(
            "SELECT * FROM events WHERE at >= ? AND at < ? ORDER BY id", (start_iso, end_iso)
        ).fetchall()

    def last_event_before(self, before_iso, kinds):
        return self.conn.execute(
            f"SELECT * FROM events WHERE at < ? AND kind IN ({', '.join('?' for _ in kinds)}) "
            "ORDER BY id DESC LIMIT 1",
            (before_iso, *kinds),
        ).fetchone()

    def last_event(self, kinds):
        return self.conn.execute(
            f"SELECT * FROM events WHERE kind IN ({', '.join('?' for _ in kinds)}) ORDER BY id DESC LIMIT 1",
            tuple(kinds),
        ).fetchone()

    def duplicates_between(self, start_iso, end_iso):
        return self.conn.execute(
            "SELECT device_id, COUNT(*) AS n FROM duplicates WHERE received_at >= ? AND received_at < ? "
            "GROUP BY device_id ORDER BY n DESC",
            (start_iso, end_iso),
        ).fetchall()

    def rejects_between(self, start_iso, end_iso):
        return self.conn.execute(
            "SELECT reason, COUNT(*) AS n FROM rejects WHERE received_at >= ? AND received_at < ? "
            "GROUP BY reason ORDER BY n DESC",
            (start_iso, end_iso),
        ).fetchall()

    def export_rows(self, start_iso, end_iso, metrics=None):
        sql = f"SELECT {', '.join(EXPORT_COLUMNS)} FROM measurements WHERE measured_at >= ? AND measured_at < ?"
        params = [start_iso, end_iso]
        if metrics:
            sql += f" AND metric IN ({', '.join('?' for _ in metrics)})"
            params.extend(metrics)
        sql += " ORDER BY measured_at, device_id, metric"
        return self.conn.execute(sql, params)

    def row_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
