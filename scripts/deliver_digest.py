#!/usr/bin/env python3
"""投递编排（接缝②之后的一切都是确定性代码，不再依赖模型判断）。

流水线：幂等检查 → digest 校验 → 源健康/行情快照 → HTML 渲染
       → 飞书投递 → runs 状态推进 + push_log 记账

Digest JSON 由 Evaluator（Phase A 为 hanli agent）按 render_digest 的
schema 产出到临时文件。同日 state=delivered 直接退出——cron 重跑、
手动重跑都不会重复推送；失败标记 failed 后可重试（幂等检查只拦 delivered）。

用法：
    python3 scripts/deliver_digest.py --digest /tmp/web3_digest.json
    python3 scripts/deliver_digest.py --digest /tmp/web3_digest.json --dry-run
                                                       （跳过真实投递与 push_log）
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_conn  # noqa: E402
from market import snapshot  # noqa: E402
from render_digest import render_to_file, validate  # noqa: E402
from send_feishu_file import send_file  # noqa: E402

SH_TZ = timezone(timedelta(hours=8))


def health_line(conn) -> str:
    """从 sources 表自动生成 'N/M源正常'（last_ok_at 在今天东八区零点之后）。"""
    now = datetime.now(SH_TZ)
    day_start = int(datetime(now.year, now.month, now.day, tzinfo=SH_TZ).timestamp())
    total, ok = conn.execute(
        """SELECT COUNT(*),
                  COALESCE(SUM(CASE WHEN last_ok_at >= ? THEN 1 ELSE 0 END), 0)
           FROM sources WHERE enabled=1""", (day_start,)).fetchone()
    return f"{ok}/{total}源正常"


def mark(conn, run_date: str, state: str, stats=None, errors=None) -> None:
    conn.execute(
        """INSERT INTO runs(date,state,stats,errors,started_at,finished_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET state=excluded.state,
             stats=COALESCE(excluded.stats, runs.stats),
             errors=excluded.errors, finished_at=excluded.finished_at""",
        (run_date, state,
         json.dumps(stats, ensure_ascii=False) if stats else None,
         json.dumps(errors, ensure_ascii=False) if errors else None,
         int(time.time()), int(time.time())))
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", required=True, help="Evaluator 产出的 digest JSON 文件")
    ap.add_argument("--dry-run", action="store_true",
                    help="渲染但跳过真实投递与 push_log，state 停在 rendered")
    args = ap.parse_args()

    with open(args.digest, encoding="utf-8") as f:
        digest = json.load(f)

    errs = validate(digest)
    if errs:
        print("[fail] digest 校验未通过：", file=sys.stderr)
        for e in errs:
            print("  -", e, file=sys.stderr)
        sys.exit(2)

    run_date = digest["date"]
    conn = get_conn()
    row = conn.execute("SELECT state FROM runs WHERE date=?", (run_date,)).fetchone()
    if row and row["state"] == "delivered":
        print(f"[skip] {run_date} 已 delivered，幂等跳过（不重推）")
        return

    try:
        digest.setdefault("health", health_line(conn))
        digest.setdefault("market", snapshot())
        html_path = render_to_file(digest)
        print(f"[ok] 渲染完成 {html_path}")

        if args.dry_run:
            print("[dry-run] 跳过真实投递，state 停在 rendered")
            mark(conn, run_date, "rendered", stats={"note": "dry-run"})
            return

        send_file(str(html_path))
        print("[ok] feishu_file 投递成功")
        conn.execute(
            """INSERT INTO push_log(run_date, channel, status, error, delivered_at)
               VALUES(?, 'feishu_file', 'delivered', '', ?)""",
            (run_date, int(time.time())))
        mark(conn, run_date, "delivered",
             stats={"pushed": len(digest.get("major") or []) + len(digest.get("opps") or [])})
        print(f"[done] {run_date} state=delivered，push_log 已记账")
    except Exception as ex:  # 失败落账：三周后翻 runs.errors 能查到原因
        mark(conn, run_date, "failed", errors={"deliver": str(ex)[:300]})
        raise


if __name__ == "__main__":
    main()
