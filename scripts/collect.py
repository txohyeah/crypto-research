#!/usr/bin/env python3
"""P0 采集器：拉取启用源 → 归一化入库 → 更新源健康 → 记录 Run → 打印预览。

设计要点：
- 物理去重在入库时发生（id = sha1(source|url)），逻辑事件合并留给 Evaluator
- 解析结果为空视为该源故障（防 Odaily 式"200但返回HTML"陷阱）
- 直连优先、失败自动走代理重试一次
"""
import calendar
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import BASE, get_conn, load_registry, sync_sources  # noqa: E402

SH_TZ = timezone(timedelta(hours=8))
TAG_RE = re.compile(r"<[^>]+>")
UA = {"User-Agent": "crypto-news-bot/0.1 (+https://github.com/txohyeah/crypto-news)"}


def load_env() -> dict:
    env = {}
    path = os.path.join(BASE, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
PROXY_URL = ENV.get("PROXY_URL", "http://127.0.0.1:7890")


def clean(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s or "")).strip()


def fetch(url: str, timeout: int = 10) -> tuple[str, str]:
    """直连优先，失败换代理重试一次。返回 (text, via)。"""
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()
        return r.text, "direct"
    except Exception:
        proxies = {"http": PROXY_URL, "https": PROXY_URL}
        r = requests.get(url, headers=UA, timeout=timeout, proxies=proxies)
        r.raise_for_status()
        return r.text, "proxy"


def parse_rss(text: str) -> list[dict]:
    d = feedparser.parse(text)
    rows = []
    for e in d.entries:
        title = clean(getattr(e, "title", ""))
        if not title:
            continue
        ts = None
        pp = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if pp:
            ts = calendar.timegm(pp)
        rows.append({"title": title, "url": getattr(e, "link", ""),
                     "published_at": ts})
    return rows


def parse_blockbeats(text: str) -> list[dict]:
    data = json.loads(text)
    arr = data.get("data") or []
    if isinstance(arr, dict):
        arr = arr.get("data") or []
    rows = []
    for it in arr:
        ct = it.get("create_time")
        rows.append({"title": clean(it.get("title")),
                     "url": it.get("url") or "",
                     "published_at": int(ct) if ct else None})
    return rows


PARSERS = {"rss": parse_rss, "blockbeats_api": parse_blockbeats}


def item_id(source: str, url: str, title: str) -> str:
    return hashlib.sha1((source + "|" + (url or title)).encode()).hexdigest()


def main() -> None:
    t0 = int(time.time())
    run_date = datetime.now(SH_TZ).strftime("%Y-%m-%d")
    conn = get_conn()
    sync_sources(conn)
    sources = [r["name"] for r in conn.execute(
        "SELECT name FROM sources WHERE enabled=1 ORDER BY name")]
    registry = {s["name"]: s for s in load_registry()["sources"]}

    stats, errors, new_total = {}, [], 0
    for name in sources:
        cfg = registry[name]
        try:
            text, via = fetch(cfg["endpoint"])
            rows = PARSERS[cfg["type"]](text)
            if not rows:
                raise ValueError("解析到0条（源可能已退化为非RSS内容）")
            n = 0
            for row in rows:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO items
                       (id, source, title, url, published_at, fetched_at, run_id)
                       VALUES(?,?,?,?,?,?,?)""",
                    (item_id(name, row["url"], row["title"]), name,
                     row["title"], row["url"],
                     row["published_at"] or t0, t0, run_date))
                n += cur.rowcount
            conn.execute(
                "UPDATE sources SET last_ok_at=?, fail_count=0 WHERE name=?",
                (t0, name))
            stats[name] = {"via": via, "fetched": len(rows), "new": n}
            new_total += n
            print(f"[ok]   {name:<14} via={via:<6} 拉取{len(rows):>3} 新增{n:>3}")
        except Exception as ex:  # noqa: BLE001 —— 单源失败不拖垮整轮
            conn.execute(
                "UPDATE sources SET fail_count=fail_count+1 WHERE name=?",
                (name,))
            errors.append(f"{name}: {ex}")
            stats[name] = {"error": str(ex)[:150]}
            print(f"[fail] {name:<14} {str(ex)[:100]}")

    conn.execute(
        """INSERT INTO runs(date, state, stats, errors, started_at, finished_at)
           VALUES(?, 'collected', ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET state='collected',
             stats=excluded.stats, errors=excluded.errors,
             finished_at=excluded.finished_at""",
        (run_date, json.dumps(stats, ensure_ascii=False),
         json.dumps(errors, ensure_ascii=False), t0, int(time.time())))
    conn.commit()

    print(f"\n== 汇总：{len(sources)}个源，新增 {new_total} 条，"
          f"失败 {len(errors)} 个 ==")
    print("\n== 最新条目预览 ==")
    for r in conn.execute(
            """SELECT source, published_at, title FROM items
               ORDER BY fetched_at DESC, published_at DESC LIMIT 15"""):
        t = datetime.fromtimestamp(r["published_at"], SH_TZ).strftime("%m-%d %H:%M")
        print(f"  [{r['source']}] {t}  {r['title'][:70]}")


if __name__ == "__main__":
    main()
