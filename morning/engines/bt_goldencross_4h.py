#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""金叉 v2 → 4h 平移回测（2026-09-21）。

平移原则：
  MA20/MA60 均线长度不变（20/60 根 4h ≈ 3.3 天 / 10 天——4h 金叉本质=更小尺度的金叉）
  斜率带 %/根 按时间尺度缩放：1d 上限 0.5 → sqrt 缩放 0.5/√6≈0.204（波动 ∝ √t）→ 线性 0.083
  走平带 (-0.10, 0.25] 同法缩放
  冷却：10 根（直接平移=1.7 天）vs 60 根（等价 1d 的 10 天）
  C2 状态机原样（T0 顺延 5%、硬止损 -10%、GATE 100%、回吐 1/3、双边 0.1%）——价格尺度不平移
  f20=20 根（≈3.3 天）；f120=120 根（等价 1d 的 f20=20 天）
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bt_goldencross_v2 import run_trade, st

HERE = os.path.dirname(os.path.abspath(__file__))
SYMS5 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']


def sig4(c, s20_hi, s60_lo, s60_hi, cooldown):
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    s20 = ma20.diff() / ma20.shift(1) * 100
    s60 = ma60.diff() / ma60.shift(1) * 100
    cross = (ma20.shift(1) <= ma60.shift(1)) & (ma20 > ma60)
    m = cross & (s20 >= 0) & (s20 <= s20_hi) & (s60 >= s60_lo) & (s60 <= s60_hi)
    idxs = np.flatnonzero(m.to_numpy())
    ev, last = [], -10**9
    for i in idxs:
        if i - last < cooldown:
            continue
        last = i
        ev.append(int(i))
    return ev, ma20.to_numpy(), ma60.to_numpy()


def run_config(data, syms, s20_hi, s60_lo, s60_hi, cooldown, tag):
    trs, n_sig = [], 0
    for s in syms:
        c = pd.Series([r[1] for r in data[s]], dtype=float)
        dates = [r[0] for r in data[s]]
        cs = c.to_numpy()
        ev, ma20, ma60 = sig4(c, s20_hi, s60_lo, s60_hi, cooldown)
        n_sig += len(ev)
        for i in ev:
            t = run_trade(cs, ma20, ma60, i)
            if t:
                f120_i = min(t['buy_i'] + 120, len(cs) - 1)
                t['f120'] = (cs[f120_i] / cs[t['buy_i']] - 1) * 100
                t['sym'], t['year'], t['date'] = s, dates[t['buy_i']][:4], dates[t['buy_i']]
                trs.append(t)
    nav = 1.0
    for t in trs:
        nav *= 1 + t['pnl'] / 100
    print(f'\n== {tag} ==  原始信号 {n_sig} → 冷却后交易 {len(trs)} 笔  净值 {nav:.2f}x')
    print(f'   完整[{st([t["pnl"] for t in trs])}]')
    print(f'   f20(20根≈3.3天)[{st([t["f20"] for t in trs])}]')
    print(f'   f120(120根≈20天)[{st([t["f120"] for t in trs])}]')
    print(f'   平均持有 {sum(t["hold"] for t in trs)/max(len(trs),1):.1f} 根({sum(t["hold"] for t in trs)/max(len(trs),1)*4/24:.1f} 天)  '
          f'出场分布 { {r: sum(1 for t in trs if t["reason"]==r) for r in set(t["reason"] for t in trs)} }')
    return trs, nav


def yearly(trs, tag):
    print(f'\n   -- {tag} 分年 --')
    by = defaultdict(list)
    for t in trs:
        by[t['year']].append(t)
    for y in sorted(by):
        sub = by[y]
        nav1 = 1.0
        for t in sub:
            nav1 *= 1 + t['pnl'] / 100
        print(f'   {y}: n={len(sub):3d} 完整[{st([t["pnl"] for t in sub])}] 净值{nav1:.2f}x')


def main():
    data = json.load(open(os.path.join(HERE, 'data', 'klines_4h.json')))
    print('===== 金叉 v2 → 4h 平移矩阵（5 币：BTC/ETH/SOL/BNB/AR）=====')
    print('（1d 基线：22 笔 / 净值 21.23x / 完整均值 +25.0% / 平均持有 ~40 天）')

    results = {}
    # A: sqrt 缩放斜率带 + 等价冷却 60 根（公平对比 1d 的主方案）
    trs, nav = run_config(data, SYMS5, 0.204, -0.041, 0.102, 60,
                          'A: 斜率√缩放 0.204/(−0.041,0.102] + 冷却60根(=10天)')
    results['A'] = (trs, nav)
    yearly(trs, 'A')
    # B: 斜率直接平移 0.5/(−0.10,0.25] + 冷却 10 根
    trs, nav = run_config(data, SYMS5, 0.5, -0.10, 0.25, 10,
                          'B: 斜率直接平移 0.5/(−0.10,0.25] + 冷却10根(=1.7天)')
    results['B'] = (trs, nav)
    yearly(trs, 'B')
    # C: sqrt 缩放斜率带 + 冷却 10 根
    trs, nav = run_config(data, SYMS5, 0.204, -0.041, 0.102, 10,
                          'C: 斜率√缩放 + 冷却10根')
    results['C'] = (trs, nav)
    # D: 线性缩放 0.083 + 冷却 60 根
    trs, nav = run_config(data, SYMS5, 0.083, -0.017, 0.042, 60,
                          'D: 斜率线性缩放 0.083/(−0.017,0.042] + 冷却60根')
    results['D'] = (trs, nav)

    # 体系外三币顺带看（主推参数 A 口径）
    run_config(data, ['XRPUSDT', 'DOGEUSDT', 'LINKUSDT'], 0.204, -0.041, 0.102, 60,
               '体系外顺带(XRP/DOGE/LINK): A 口径')

    # 最优组分币
    best = max(results, key=lambda k: results[k][1])
    print(f'\n===== 最优组 {best} 分币明细 =====')
    for s in SYMS5:
        sub = [t for t in results[best][0] if t['sym'] == s]
        if not sub:
            print(f'  {s}: 0 笔')
            continue
        nav1 = 1.0
        for t in sub:
            nav1 *= 1 + t['pnl'] / 100
        print(f'  {s:9s} n={len(sub):3d} 完整[{st([t["pnl"] for t in sub])}] 净值{nav1:.2f}x')
    print('\n最差 8 笔（看坑的结构）:')
    for t in sorted(results[best][0], key=lambda x: x['pnl'])[:8]:
        print(f'  {t["sym"][:3]} {t["date"]}  pnl{t["pnl"]:+7.2f}%  hold{t["hold"]:4d}根  {t["reason"]}')
    print('\n最好 8 笔:')
    for t in sorted(results[best][0], key=lambda x: -x['pnl'])[:8]:
        print(f'  {t["sym"][:3]} {t["date"]}  pnl{t["pnl"]:+7.2f}%  hold{t["hold"]:4d}根  {t["reason"]}')


if __name__ == '__main__':
    main()
