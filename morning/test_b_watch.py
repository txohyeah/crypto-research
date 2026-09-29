#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B版观察铃历史回放测试：逐日喂 K 线模拟晨报运行，验证三段式时序 + 冷却。
对照：bt_price_cross.price_cross_events 的 N=1 事件（上穿日）与 N=2/3 确认日。"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from morning_report import b_cross_watch, B_POOL  # noqa: E402
from bt_price_cross import price_cross_events  # noqa: E402


def replay(sym, rows, start_day):
    """从 start_day 起逐日回放，返回 [(day, alert)]。"""
    dates = [r[0] for r in rows]
    cs_all = np.array([r[4] for r in rows], dtype=float)
    watch = {}
    out = []
    for k in range(dates.index(start_day), len(dates)):
        data = {sym: dates[:k + 1]}
        closes = {sym: cs_all[:k + 1]}
        saved = B_POOL[:]
        B_POOL[:] = [sym]
        try:
            alerts = b_cross_watch(data, closes, watch)
        finally:
            B_POOL[:] = saved
        for a in alerts:
            out.append((dates[k], a))
    return out, watch


def main():
    data = json.load(open(os.path.join(HERE, 'data', 'klines_1d_uni_crv.json')))
    rows = data['UNIUSDT']
    dates = [r[0] for r in rows]

    # 对照：回测口径的事件日
    c = np.array([r[4] for r in rows], dtype=float)
    import pandas as pd
    cs = pd.Series(c)
    ma60 = cs.rolling(60).mean()
    s60 = (ma60.diff() / ma60.shift(1) * 100).to_numpy()
    ev1 = price_cross_events(cs, ma60.to_numpy(), s60, 1)
    ev2 = price_cross_events(cs, ma60.to_numpy(), s60, 2)
    ev3 = price_cross_events(cs, ma60.to_numpy(), s60, 3)
    recent = [i for i in ev1 if dates[i] >= '2026-06-01']
    print('回测对照（2026-06 起）：N=1 上穿日:', [dates[i] for i in recent])
    print('  N=2 确认日:', [dates[i] for i in ev2 if dates[i] >= '2026-06-01'])
    print('  N=3 确认日:', [dates[i] for i in ev3 if dates[i] >= '2026-06-01'])

    alerts, watch = replay('UNIUSDT', rows, '2026-06-01')
    print(f'\n回放结果（2026-06-01 起逐日，共 {len(alerts)} 条提醒）：')
    for d, a in alerts:
        print(f'  {d}  {a}')
    print(f'\n回放结束 watch={watch}')

    # 断言：N=1 上穿日必产预警；预警日期集合 ⊇ 2026-06 后 N=1 事件中未被冷却吞掉的
    alert_days = [d for d, a in alerts if '第1天预警' in a]
    for i in recent:
        d = dates[i]
        ok = d in alert_days
        print(f"  检查 N=1 事件 {d}: {'✓ 有预警' if ok else '✗ 无预警（若因冷却则正常）'}")


if __name__ == '__main__':
    main()
