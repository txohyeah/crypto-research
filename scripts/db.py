#!/usr/bin/env python3
"""SQLite 连接与 schema（WAL 模式）。库里只存事实：采到什么、评成什么、推没推。"""
import json
import os
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "push.db")
SOURCES_JSON = os.path.join(BASE, "config", "sources.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id            TEXT PRIMARY KEY,   -- sha1(source|url)
  source        TEXT NOT NULL,
  title         TEXT NOT NULL,
  url           TEXT,
  published_at  INTEGER,
  fetched_at    INTEGER,
  category      TEXT,               -- 监管/安全事件/宏观流动性/项目大事/机会-*/其他
  importance    INTEGER,            -- 1-10，Evaluator 填写
  reason        TEXT,
  action        TEXT,
  run_id        TEXT,               -- 哪次 Run 采集的
  pushed_at     INTEGER             -- NULL = 未推送
);
CREATE INDEX IF NOT EXISTS idx_items_pub ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_cat ON items(category, importance);

CREATE TABLE IF NOT EXISTS runs (
  date        TEXT PRIMARY KEY,     -- 'YYYY-MM-DD'（东八区），同日幂等锚点
  state       TEXT,                 -- collected → evaluated → rendered → delivered / failed
  stats       TEXT,
  errors      TEXT,
  started_at  INTEGER,
  finished_at INTEGER
);

CREATE TABLE IF NOT EXISTS sources (
  name       TEXT PRIMARY KEY,
  type       TEXT,
  endpoint   TEXT,
  need_proxy INTEGER DEFAULT 0,
  enabled    INTEGER DEFAULT 1,
  last_ok_at INTEGER,
  fail_count INTEGER DEFAULT 0,
  note       TEXT
);

CREATE TABLE IF NOT EXISTS push_log (
  run_date     TEXT,
  channel      TEXT,
  status       TEXT,
  error        TEXT,
  delivered_at INTEGER
);
"""


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def load_registry() -> dict:
    with open(SOURCES_JSON, encoding="utf-8") as f:
        return json.load(f)


def sync_sources(conn: sqlite3.Connection) -> None:
    """config/sources.json 为准同步源配置；健康字段(last_ok_at/fail_count)保留不覆盖。"""
    for s in load_registry()["sources"]:
        if not s.get("name"):
            continue
        conn.execute(
            """INSERT INTO sources(name,type,endpoint,need_proxy,enabled,note)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 type=excluded.type, endpoint=excluded.endpoint,
                 need_proxy=excluded.need_proxy,
                 enabled=excluded.enabled, note=excluded.note""",
            (s["name"], s.get("type"), s.get("endpoint"),
             int(bool(s.get("need_proxy"))), int(bool(s.get("enabled"))),
             s.get("note", "")))
    conn.commit()
