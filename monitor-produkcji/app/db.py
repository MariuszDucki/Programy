"""Warstwa danych – SQLite (jeden plik, bez serwera)."""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS state_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id TEXT NOT NULL,
    state TEXT NOT NULL,
    category TEXT NOT NULL,
    reason TEXT,
    comment TEXT,
    start_ts REAL NOT NULL,
    end_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_state_line ON state_log(line_id, start_ts);

CREATE TABLE IF NOT EXISTS counts (
    line_id TEXT NOT NULL,
    minute_ts INTEGER NOT NULL,      -- początek minuty (epoch)
    good INTEGER NOT NULL DEFAULT 0,
    scrap INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (line_id, minute_ts)
);

CREATE TABLE IF NOT EXISTS alarms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    raised_ts REAL NOT NULL,
    cleared_ts REAL,
    acked_ts REAL,
    acked_by TEXT
);
CREATE INDEX IF NOT EXISTS ix_alarms_line ON alarms(line_id, kind, cleared_ts);
"""


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def q(self, sql: str, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # --- stany ---------------------------------------------------------
    def open_state(self, line_id):
        r = self.q("SELECT * FROM state_log WHERE line_id=? AND end_ts IS NULL "
                   "ORDER BY start_ts DESC LIMIT 1", (line_id,))
        return r[0] if r else None

    def change_state(self, line_id, state, category, reason=None, ts=None):
        ts = ts or time.time()
        with self.lock:
            self.conn.execute("UPDATE state_log SET end_ts=? WHERE line_id=? AND end_ts IS NULL",
                              (ts, line_id))
            cur = self.conn.execute(
                "INSERT INTO state_log(line_id,state,category,reason,start_ts) VALUES (?,?,?,?,?)",
                (line_id, state, category, reason, ts))
            self.conn.commit()
            return cur.lastrowid

    def intervals(self, line_id, start, end):
        return self.q("SELECT * FROM state_log WHERE line_id=? AND start_ts<? "
                      "AND (end_ts IS NULL OR end_ts>?) ORDER BY start_ts",
                      (line_id, end, start))

    def set_reason(self, stop_id, reason, comment=None):
        self.x("UPDATE state_log SET reason=?, comment=? WHERE id=?", (reason, comment, stop_id))

    # --- liczniki ------------------------------------------------------
    def add_counts(self, line_id, good, scrap, ts=None):
        ts = ts or time.time()
        minute = int(ts // 60 * 60)
        self.x("INSERT INTO counts(line_id,minute_ts,good,scrap) VALUES (?,?,?,?) "
               "ON CONFLICT(line_id,minute_ts) DO UPDATE SET good=good+excluded.good, "
               "scrap=scrap+excluded.scrap", (line_id, minute, int(good), int(scrap)))

    def sum_counts(self, line_id, start, end):
        r = self.q("SELECT COALESCE(SUM(good),0) g, COALESCE(SUM(scrap),0) s FROM counts "
                   "WHERE line_id=? AND minute_ts>=? AND minute_ts<?",
                   (line_id, int(start // 60 * 60), end))
        return int(r[0]["g"]), int(r[0]["s"])

    def counts_by_hour(self, line_id, start, end):
        return self.q("SELECT CAST((minute_ts-?)/3600 AS INTEGER) h, SUM(good) good, SUM(scrap) scrap "
                      "FROM counts WHERE line_id=? AND minute_ts>=? AND minute_ts<? GROUP BY h ORDER BY h",
                      (int(start), line_id, int(start // 60 * 60), end))

    # --- alarmy --------------------------------------------------------
    def active_alarm(self, line_id, kind):
        r = self.q("SELECT * FROM alarms WHERE line_id=? AND kind=? AND cleared_ts IS NULL",
                   (line_id, kind))
        return r[0] if r else None
