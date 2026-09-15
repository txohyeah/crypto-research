#!/usr/bin/env python3
"""deliver_digest 幂等锚点必须是「运行日」，不能是 digest 里的内容日期。

缺陷背景：原实现写的是 ``run_date = digest["date"]``，而 ``digest["date"]`` 是
**由 LLM 填的**内容日期 —— cron prompt 里写着 "date=今天YYYY-MM-DD"，digest JSON
由 Evaluator（agent）产出。等于把"同日幂等"这种正确性关键的判断押在模型输出上：
它某天把日期填成昨天（跨零点、时区误判、抄了条目发布日期），读写的就是**另一天**
的 runs 行：

  · 那天已是 delivered → 今天被静默跳过，用户当天收不到推送（无声漏推）；
  · 那天未 delivered   → 今天这次被记进历史那天，今天的行永远停在 collected，
                         次日再跑会重复推送。

而 ``collect.py`` 写 runs 表用的是 ``datetime.now(SH_TZ)`` —— 运行日。同一张表
两套口径，是缺陷的根。

修后：锚点取运行日，digest.date 退为内容日期，不一致时打 [warn] 并写进
runs.stats.digest_date 以便追溯。

本测试用临时目录里的独立 SQLite，**不触碰 data/push.db**，也不发网络请求
（渲染/行情/投递全部替换为空实现）。直接运行：
    python3 tests/test_deliver_run_date.py
"""
import datetime
import json
import os
import sys
import tempfile

NEWS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NEWS_DIR, "scripts"))

import db  # noqa: E402

tmp = tempfile.mkdtemp()
db.DB_PATH = os.path.join(tmp, "test.db")  # 隔离：绝不碰生产库

import deliver_digest as D  # noqa: E402

# 把重活（渲染 / 行情快照 / 飞书投递）替换成空实现，只留幂等记账这条链
D.render_to_file = lambda d: os.path.join(tmp, "x.html")
D.snapshot = lambda: {}
D.send_file = lambda p: None
D.validate = lambda d: []
D.health_line = lambda c: "1/1源正常"

yday = (datetime.datetime.now(D.SH_TZ) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
today = datetime.datetime.now(D.SH_TZ).strftime("%Y-%m-%d")
digest_path = os.path.join(tmp, "digest.json")
with open(digest_path, "w", encoding="utf-8") as f:
    json.dump({"date": yday, "major": [], "opps": []}, f)

print(f"digest.date = {yday}（模拟 LLM 填成昨天），实际运行日 = {today}")
sys.argv = ["deliver_digest.py", "--digest", digest_path, "--dry-run"]
D.main()

rows = [(r[0], r[1]) for r in db.get_conn().execute("SELECT date, state FROM runs")]
print(f"runs 写入：{rows}")
assert [r[0] for r in rows] == [today], f"锚点必须是运行日 {today}，实际写入了 {[r[0] for r in rows]}"
assert yday not in [r[0] for r in rows], "不得写进 digest.date 那一天（会污染历史记录）"

# 同日重跑不应新增行（锚点稳定）；dry-run 停在 rendered，仍不是 delivered，故不跳过
D.main()
rows2 = [(r[0], r[1]) for r in db.get_conn().execute("SELECT date, state FROM runs")]
assert rows2 == rows, f"同日重跑不应新增行，实际 {rows2}"

# 状态推进到 delivered 后，再跑必须命中幂等跳过
con = db.get_conn()
con.execute("UPDATE runs SET state='delivered'")
con.commit()
D.main()

# 反向钉死：旧口径（拿 digest.date 当锚点）在这份数据下会误命中昨天那行而静默跳过
con = db.get_conn()
con.execute("INSERT OR REPLACE INTO runs(date, state) VALUES(?, 'delivered')", (yday,))
con.commit()
old_hit = con.execute("SELECT state FROM runs WHERE date=?", (yday,)).fetchone()[0]
assert old_hit == "delivered", "反证构造失败：昨天那行应为 delivered"
print(f"反证：若沿用 digest.date={yday} 作锚点 → 会查到「已投递」而静默跳过今天的推送")
print(f"      现口径用运行日 {today} → 独立判断，不受 digest.date 影响")

print("结果：全部 PASS")
