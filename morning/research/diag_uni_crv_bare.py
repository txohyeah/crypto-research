#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""裸金叉（不筛斜率，其余口径不变：冷却10/T0建仓/信号日>5%顺延/v2卖出状态机/双边0.1%）
完整交易统计：UNI/CRV vs 原5币。"""
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bt_goldencross_v2 import run_trade, st  # noqa: E402


def bare_cross(c):
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
    return ev, ma20.to_numpy(), ma60.to_numpy()


def analyze(sym, rows):
    c = pd.Series([r[4] for r in rows], dtype=float)
    dates = [r[0] for r in rows]
    cs = c.to_numpy()
    ev, ma20, ma60 = bare_cross(c)
    trs = []
    for i in ev:
        t = run_trade(cs, ma20, ma60, i)
        if t:
            t['year'], t['date'] = dates[t['buy_i']][:4], dates[t['buy_i']]
            trs.append(t)
    nav = 1.0
    for t in trs:
        nav *= 1 + t['pnl'] / 100
    print(f'\n### {sym}  裸金叉 {len(ev)} 笔  净值 {nav:.2f}x')
    print(f'  完整交易[{st([t["pnl"] for t in trs])}]')
    print(f'  f20[{st([t["f20"] for t in trs])}]')
    print(f'  gate触发 {sum(1 for t in trs if t["gated"])}/{len(trs)}'
          f'  出场分布 { {r: sum(1 for t in trs if t["reason"] == r) for r in set(t["reason"] for t in trs)} }')
    by_year = defaultdict(list)
    for t in trs:
        by_year[t['year']].append(t)
    print('  分年(完整):')
    for y in sorted(by_year):
        sub = by_year[y]
        nav1 = 1.0
        for t in sub:
            nav1 *= 1 + t['pnl'] / 100
        print(f'    {y}: n={len(sub):3d} [{st([t["pnl"] for t in sub])}] 净值{nav1:.2f}x')
    print('  明细:')
    for t in sorted(trs, key=lambda x: x['buy_i']):
        half_s = f' half{t["half"]:+.1f}%' if t['half'] is not None else ''
        print(f'    {t["date"]}  pnl{t["pnl"]:+8.2f}%  hold{t["hold"]:4d}根  {t["reason"]:5s}'
              f'  gate{"✓" if t["gated"] else "×"}{half_s}')
    return trs


def main():
    data = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_1d_uni_crv.json')))
    data5 = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_ohlc_1d.json')))
    allu, all5 = [], []
    print('===== UNI/CRV 裸金叉 + v2 卖出状态机 =====')
    for s in ['UNIUSDT', 'CRVUSDT']:
        allu += analyze(s, data[s])
    print('\n===== 原5币同口径对照（裸金叉 + v2 卖出状态机）=====')
    for s in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']:
        all5 += analyze(s, data5[s])
    for tag, lst in [('UNI/CRV', allu), ('原5币', all5)]:
        nav = 1.0
        for t in lst:
            nav *= 1 + t['pnl'] / 100
        print(f'\n===== {tag} 合计 {len(lst)} 笔 净值 {nav:.2f}x '
              f'完整[{st([t["pnl"] for t in lst])}] f20[{st([t["f20"] for t in lst])}]')


if __name__ == '__main__':
    main()
