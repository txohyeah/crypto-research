#!/usr/bin/env python3
"""输出过去 N 小时（默认24h）的条目清单，供每日早报的 Evaluator 阶段阅读。

用法：python3 scripts/digest_raw.py [--hours 24] [--limit 120]
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_conn  # noqa: E402

SH_TZ = timezone(timedelta(hours=8))

# 来源粗权重：仅影响生肉版排序，正式排序由 Evaluator 决定
SOURCE_ORDER = ["sec_press", "coindesk", "theblock", "cointelegraph"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--limit", type=int, default=120)
    args = ap.parse_args()

    since = int(time.time()) - args.hours * 3600
    conn = get_conn()
    rows = list(conn.execute(
        """SELECT source, title, url, published_at FROM items
           WHERE published_at >= ?
           ORDER BY published_at DESC LIMIT ?""", (since, args.limit)))

    order = {n: i for i, n in enumerate(SOURCE_ORDER)}
    rows.sort(key=lambda r: (order.get(r["source"], 99), -r["published_at"]))

    print(f"# 过去{args.hours}小时条目 共{len(rows)}条 "
          f"({datetime.now(SH_TZ):%m-%d %H:%M} 生成)\n")
    cur_src = None
    for r in rows:
        if r["source"] != cur_src:
            cur_src = r["source"]
            print(f"\n## {cur_src}")
        t = datetime.fromtimestamp(r["published_at"], SH_TZ).strftime("%d日%H:%M")
        print(f"- [{t}] {r['title']}\n  {r['url']}")

    if not rows:
        print("（无条目——检查采集是否正常）")


if __name__ == "__main__":
    main()
