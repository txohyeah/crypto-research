#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""价格上穿均线策略（用户 2026-09-29 提案）：
  A 版：收盘价上穿 MA20 买入；止损 = 收盘价下穿 MA20
  B 版：收盘价上穿 MA60 买入；止损 = 收盘价下穿 MA60
  共同闸门：MA60 斜率 s60 >= 0（确认日）
  防抖参数：上穿后连续 N 根收盘站上均线才确认（N=1/2/3 扫描，N=1=无防抖）
  冷却 10 根（确认日间隔）；确认日收盘买入（无前视）；费用双边 0.1%
  对照基线：s60>=0 的日子随便买 f20。"""
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.002
COOLDOWN = 10


def make_series(rows):
    c = pd.Series([r[4] for r in rows], dtype=float)
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    s60 = ma60.diff() / ma60.shift(1) * 100
    return c.to_numpy(), ma20.to_numpy(), ma60.to_numpy(), s60.to_numpy()


def price_cross_events(c, ma, s60, n_confirm):
    """收盘价上穿 ma + 连续 n_confirm 根站上 + 确认日 s60>=0。返回确认日索引。"""
    n = len(c)
    above = c > ma  # NaN 比较 = False，天然排除均线未成型期
    evs, last = [], -10**9
    i = 1
    while i < n:
        if above[i] and not above[i - 1]:
            # 上穿起点，向后数连续站上
            j = i
            while j < n and above[j]:
                j += 1
            run = j - i  # 连续在上方根数
            if run >= n_confirm:
                confirm = i + n_confirm - 1
                if confirm - last >= COOLDOWN and np.isfinite(s60[confirm]) and s60[confirm] >= 0:
                    evs.append(confirm)
                    last = confirm
            i = j
        else:
            i += 1
    return evs


def run_simple(c, ma, i):
    """确认日 i 收盘买入；之后任一收盘 < ma[t] → 当日收盘卖出。"""
    n = len(c)
    cost = c[i]
    for t in range(i + 1, n):
        if c[t] < ma[t]:
            pnl = (c[t] / cost - 1) * 100 - FEE * 100
            return {'buy_i': i, 'exit_i': t, 'pnl': pnl, 'hold': t - i, 'reason': 'stop',
                    'f20': (c[min(i + 20, n - 1)] / cost - 1) * 100, 'cost': cost, 'exit': c[t]}
    pnl = (c[-1] / cost - 1) * 100 - FEE * 100
    return {'buy_i': i, 'exit_i': n - 1, 'pnl': pnl, 'hold': n - 1 - i, 'reason': 'eod',
            'f20': (c[min(i + 20, n - 1)] / cost - 1) * 100, 'cost': cost, 'exit': c[-1]}


def st(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 'n=0'
    v = sorted(vals)
    win = sum(1 for x in v if x > 0) / len(v) * 100
    return f'n={len(v):3d} 均值{sum(v)/len(v):+7.2f}% 中位{v[len(v)//2]:+7.2f}% 胜率{win:4.1f}%'


def baseline(c, s60):
    """s60>=0 的日子随便买，f20 均值（不含费用）。"""
    f20 = pd.Series(c).shift(-20) / pd.Series(c) - 1
    m = (s60 >= 0) & np.isfinite(s60)
    vals = (f20[m] * 100).dropna()
    return vals


def run_sym(sym, rows, detail=False):
    c, ma20, ma60, s60 = make_series(rows)
    dates = [r[0] for r in rows]
    print(f'\n{"=" * 74}\n### {sym}  ({dates[0]} ~ {dates[-1]})')
    bl = baseline(c, s60)
    print(f'  基线: s60>=0 日子随便买 f20  n={len(bl)} 均值{bl.mean():+.2f}% 中位{bl.median():+.2f}%')
    results = {}
    for tag, ma, name in [('A上穿MA20', ma20, 'A'), ('B上穿MA60', ma60, 'B')]:
        for nc in (1, 2, 3):
            evs = price_cross_events(c, ma, s60, nc)
            trs = []
            for i in evs:
                t = run_simple(c, ma, i)
                t['date'] = dates[i]
                t['year'] = dates[i][:4]
                trs.append(t)
            nav = 1.0
            for t in trs:
                nav *= 1 + t['pnl'] / 100
            f20s = [t['f20'] for t in trs]
            pnls = [t['pnl'] for t in trs]
            results[(tag, nc)] = (evs, trs, nav)
            print(f'\n  [{tag} 防抖N={nc}] 信号{len(evs)}  净值{nav:.2f}x')
            print(f'    完整(含费)[{st(pnls)}]   f20(不含费)[{st(f20s)}]'
                  f'   出场 { {r: sum(1 for t in trs if t["reason"] == r) for r in set(t["reason"] for t in trs)} }')
            if detail and trs:
                by_year = defaultdict(list)
                for t in trs:
                    by_year[t['year']].append(t)
                for y in sorted(by_year):
                    sub = by_year[y]
                    nav1 = 1.0
                    for t in sub:
                        nav1 *= 1 + t['pnl'] / 100
                    print(f'    {y}: n={len(sub):3d} [{st([t["pnl"] for t in sub])}] 净值{nav1:.2f}x')
                print('    明细:')
                for t in trs:
                    print(f'      {t["date"]}  买{t["cost"]:.4f} → 卖{t["exit"]:.4f}  pnl{t["pnl"]:+8.2f}%'
                          f'  hold{t["hold"]:4d}根  {t["reason"]}')
    return results


def main():
    data = json.load(open(os.path.join(HERE, 'data', 'klines_1d_uni_crv.json')))
    data5 = json.load(open(os.path.join(HERE, 'data', 'klines_ohlc_1d.json')))
    summary = defaultdict(list)
    for s in ['UNIUSDT', 'CRVUSDT']:
        res = run_sym(s, data[s], detail=True)
        for k, (evs, trs, nav) in res.items():
            summary[k] += trs
    print(f'\n\n{"#" * 74}\n# 原5币同口径对照（汇总）\n')
    for s in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']:
        res = run_sym(s, data5[s], detail=False)
        for k, (evs, trs, nav) in res.items():
            summary[k] += trs
    print(f'\n{"#" * 74}\n# 汇总（UNI/CRV + 原5币 = 7 币合计）')
    for (tag, nc), trs in sorted(summary.items()):
        nav = 1.0
        for t in trs:
            nav *= 1 + t['pnl'] / 100
        print(f'  [{tag} N={nc}] 合计 净值{nav:.2f}x  完整[{st([t["pnl"] for t in trs])}]'
              f'  f20[{st([t["f20"] for t in trs])}]')


if __name__ == '__main__':
    main()
