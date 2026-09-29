#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UNI / CRV 金叉体系分析（2026-09-29）。
口径 = 2026-09-21 定稿：
  v2: MA20 上穿 MA60, s20∈[0,0.5], s60∈(-0.10,0.25] %/根, 冷却10, T0=次根收盘(信号日>5%顺延),
      卖出状态机(-10%硬止损 / <100%浮盈破MA20卖半·破MA60清仓 / 曾≥100% trail 回吐1/3), 双边0.1%
  v3: 粘合金叉 θ=2.0 观察层（无穿越、与 v2 互斥）
对照：状态基线 = MA20>MA60 状态内任意日买入 f20。
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bt_goldencross_v2 import signals, run_trade, st  # noqa: E402


def gc_v3_events(c, dates):
    """v3 粘合金叉（θ=2.0 定稿，与 morning_report.py 同口径）。"""
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    s20 = ma20.diff() / ma20.shift(1) * 100
    gap = (ma20 - ma60) / ma60 * 100
    no_die = (ma20 - ma60).rolling(60, min_periods=60).min() >= 0
    pin = gap.rolling(15, min_periods=1).min() <= 2.0
    rev = (gap > gap.shift(1)) & (gap.shift(1) > gap.shift(2)) & (s20 > 0) & (s20 <= 0.5) & (gap <= 2.0)
    idxs = np.flatnonzero((no_die & pin & rev).to_numpy())
    ev, last = [], -10**9
    for i in idxs:
        if i - last < 10:
            continue
        last = i
        ev.append(int(i))
    return ev


def bare_cross(c):
    """裸金叉（不带任何筛子）事件，作参考。"""
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    cross = (ma20.shift(1) <= ma60.shift(1)) & (ma20 > ma60)
    idxs = np.flatnonzero(cross.to_numpy())
    ev, last = [], -10**9
    for i in idxs:
        if i - last < 10:
            continue
        last = i
        ev.append(int(i))
    return ev, ma20, ma60


def state_baseline(c):
    """状态基线：MA20>MA60 的日子里随便买，20 天后收益（不含费用）。"""
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    state = (ma20 > ma60) & ma20.notna() & ma60.notna()
    f20 = c.shift(-20) / c - 1
    vals = (f20[state] * 100).dropna()
    return vals


def analyze(sym, rows):
    c = pd.Series([r[4] for r in rows], dtype=float)
    dates = [r[0] for r in rows]
    cs = c.to_numpy()
    n = len(cs)
    print(f'\n{"=" * 72}\n### {sym}   {dates[0]} ~ {dates[-1]}  ({n} 根日线)')

    # ---- 当前技术位 ----
    last = n - 1
    ma20, ma60, s20, s60 = None, None, None, None
    ev_all, ma20s, ma60s = bare_cross(c)
    ma20 = ma20s.to_numpy(); ma60 = ma60s.to_numpy()
    s20 = (c.rolling(20).mean().diff() / c.rolling(20).mean().shift(1) * 100).to_numpy()
    s60 = (c.rolling(60).mean().diff() / c.rolling(60).mean().shift(1) * 100).to_numpy()
    gap = (ma20[last] - ma60[last]) / ma60[last] * 100
    hi60, lo60 = cs[last - 59:last + 1].max(), cs[last - 59:last + 1].min()
    hi20, lo20 = cs[last - 19:last + 1].max(), cs[last - 19:last + 1].min()
    print(f'\n[当前技术位] 收盘 {cs[-1]:.4f}')
    print(f'  MA20={ma20[last]:.4f} ({c.iloc[-1]/ma20[last]-1:+.1%})  MA60={ma60[last]:.4f} ({c.iloc[-1]/ma60[last]-1:+.1%})')
    print(f'  gap(MA20-MA60)/MA60={gap:+.2f}%   s20={s20[last]:+.3f} %/根  s60={s60[last]:+.3f} %/根')
    print(f'  排列: {"多头(MA20在MA60上)" if ma20[last] > ma60[last] else "空头(MA20在MA60下)"}'
          f'   距60日高 {cs[-1]/hi60-1:+.1%} / 距60日低 {cs[-1]/lo60-1:+.1%}'
          f'   距20日高 {cs[-1]/hi20-1:+.1%} / 距20日低 {cs[-1]/lo20-1:+.1%}')
    print(f'  全史裸金叉 {len(ev_all)} 次（无筛子）')

    # ---- v2 定稿回测 ----
    ev2, _, _ = signals(c, dates)  # 默认 s60_floor=-0.10 定稿口径
    trs = []
    for i in ev2:
        t = run_trade(cs, ma20, ma60, i)
        if t:
            t['year'], t['date'] = dates[t['buy_i']][:4], dates[t['buy_i']]
            trs.append(t)
    nav = 1.0
    for t in trs:
        nav *= 1 + t['pnl'] / 100
    print(f'\n[v2 定稿] 信号 {len(ev2)} 笔  净值 {nav:.2f}x')
    print(f'  完整交易[{st([t["pnl"] for t in trs])}]')
    print(f'  f20（买入后拿20天不动，不含费用）[{st([t["f20"] for t in trs])}]')
    hs = [t['half'] for t in trs if t['half'] is not None]
    print(f'  卖半那半段[{st(hs)}]  gate触发 {sum(1 for t in trs if t["gated"])}/{len(trs)}'
          f'  出场分布 { {r: sum(1 for t in trs if t["reason"] == r) for r in set(t["reason"] for t in trs)} }')

    # ---- v3 粘合观察层 ----
    ev3 = gc_v3_events(c, dates)
    trs3 = []
    for i in ev3:
        t = run_trade(cs, ma20, ma60, i)
        if t:
            t['year'], t['date'] = dates[t['buy_i']][:4], dates[t['buy_i']]
            trs3.append(t)
    print(f'\n[v3 粘合 θ=2.0 观察层] 事件 {len(ev3)} 笔')
    for t in trs3:
        half_s = f' half{t["half"]:+.1f}%' if t['half'] is not None else ''
        print(f'  {t["date"]}  pnl{t["pnl"]:+8.2f}%  hold{t["hold"]:4d}根  {t["reason"]:5s}'
              f'  gate{"✓" if t["gated"] else "×"}{half_s}')
    both = trs + trs3
    nav2 = 1.0
    for t in both:
        nav2 *= 1 + t['pnl'] / 100
    if ev3:
        overlap = set(t['buy_i'] for t in trs) & set(t['buy_i'] for t in trs3)
        print(f'  与 v2 买入日重叠 {len(overlap)}；合并 {len(both)} 笔 净值 {nav2:.2f}x'
              f'  完整[{st([t["pnl"] for t in both])}]')

    # ---- 分年（v2）----
    by_year = defaultdict(list)
    for t in trs:
        by_year[t['year']].append(t)
    print('\n[v2 分年] 完整交易 | f20')
    for y in sorted(by_year):
        sub = by_year[y]
        nav1 = 1.0
        for t in sub:
            nav1 *= 1 + t['pnl'] / 100
        print(f'  {y}: n={len(sub):3d} 完整[{st([t["pnl"] for t in sub])}] 净值{nav1:.2f}x'
              f'  f20[{st([t["f20"] for t in sub])}]')

    # ---- 明细（v2）----
    print('\n[v2 明细]')
    for t in sorted(trs, key=lambda x: x['buy_i']):
        half_s = f' half{t["half"]:+.1f}%' if t['half'] is not None else ''
        print(f'  {t["date"]}  信号日收盘{cs[t["buy_i"]-1]:.4f}  pnl{t["pnl"]:+8.2f}%  hold{t["hold"]:4d}根'
              f'  {t["reason"]:5s}  gate{"✓" if t["gated"] else "×"}{half_s}')

    # ---- 增量对照 ----
    base = state_baseline(c)
    ev2_f20 = [t['f20'] for t in trs]
    b_mean = base.mean() if len(base) else float('nan')
    e_mean = np.mean(ev2_f20) if ev2_f20 else float('nan')
    print(f'\n[增量对照] 状态基线(MA20>MA60随便买 f20) n={len(base)} 均值{b_mean:+.2f}%'
          f'   vs v2 信号 f20 均值{e_mean:+.2f}%   增量{e_mean - b_mean:+.2f}pp')

    # ---- 被筛子杀掉的裸金叉（近 3 年），看筛子在此币上过滤了什么 ----
    ev2_set = set(ev2)
    recent = [i for i in ev_all if dates[i] >= '2024-01-01' and i not in ev2_set]
    if recent:
        print(f'\n[2024 以来被 v2 筛子杀掉的裸金叉]（当日 s20/s60/gap → 后20天）')
        for i in recent:
            f20 = (cs[min(i + 21, n - 1)] / cs[i + 1] - 1) * 100 if i + 1 < n else float("nan")
            why = []
            if not (0 <= s20[i] <= 0.5):
                why.append(f's20={s20[i]:+.2f}出带')
            if not (-0.10 < s60[i] <= 0.25):
                why.append(f's60={s60[i]:+.2f}出带')
            print(f'  {dates[i]}  s20={s20[i]:+.3f} s60={s60[i]:+.3f}  [{",".join(why)}]  后20天{f20:+.1f}%')

    # ---- 近 30 天均线状态（判断离信号多远）----
    print(f'\n[近30天均线状态] date / close / MA20 / MA60 / gap% / s20 / s60')
    for i in range(last - 29, last + 1):
        g = (ma20[i] - ma60[i]) / ma60[i] * 100
        print(f'  {dates[i]}  {cs[i]:9.4f}  {ma20[i]:9.4f}  {ma60[i]:9.4f}  {g:+6.2f}%  {s20[i]:+.3f}  {s60[i]:+.3f}')


def main():
    data = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_1d_uni_crv.json')))
    for sym in ['UNIUSDT', 'CRVUSDT']:
        analyze(sym, data[sym])


if __name__ == '__main__':
    main()
