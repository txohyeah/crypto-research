#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""金叉 v3：双线平行上行金叉（MA20/MA60 斜率均 0~60°，且斜率相差 ≤15%）。

信号（1d）：
  金叉: MA20 上穿 MA60
  斜率带: 0 < s_pct <= tan(60°)=1.732 %/根（双线；角度制 = 用户映射 atan(s_pct)，0.5≈30°）
  斜率差（三口径对比）:
    rel   : |s20-s60| / max(s20,s60) <= 0.15   （相对差，用户写 "%" 的字面义）
    angle : |atan(s20)-atan(s60)| <= 15°       （角度差，延续用户角度口径）
    abs   : |s20-s60| <= 0.15 %/根             （绝对差，最严）
  冷却 10 天
卖出：沿用 v2 状态机（-10% 硬止损 + 破MA20卖半 + 破MA60清仓 + 100%后 trail 1/3）。
增量基线：MA20>MA60 且双线斜率 ∈ (0, 1.732] 的状态窗口内任意日买入 f20。
"""
import json
import math
import os
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']
S_HI = math.tan(math.radians(60))          # 1.732 %/根
GATE, GIVE, HARD, FEE = 1.0, 1 / 3, -0.10, 0.002


def slopes(c):
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    s20 = ma20.diff() / ma20.shift(1) * 100
    s60 = ma60.diff() / ma60.shift(1) * 100
    return ma20, ma60, s20, s60


def signal_mask(c, mode):
    ma20, ma60, s20, s60 = slopes(c)
    cross = (ma20.shift(1) <= ma60.shift(1)) & (ma20 > ma60)
    band = (s20 > 0) & (s20 <= S_HI) & (s60 > 0) & (s60 <= S_HI)
    if mode == 'rel':
        diff = (s20 - s60).abs() <= 0.15 * pd.concat([s20, s60], axis=1).max(axis=1)
    elif mode == 'angle':
        # 角度制：把斜率百分数当 tan（用户映射 0.5≈30°）；|角20 - 角60| <= 15°
        deg20 = np.degrees(np.arctan(s20))
        deg60 = np.degrees(np.arctan(s60))
        diff = (deg20 - deg60).abs() <= 15
    elif mode == 'abs':
        diff = (s20 - s60).abs() <= 0.15
    m = cross & band & diff
    idxs = np.flatnonzero(m.to_numpy())
    events, last = [], -10**9
    for i in idxs:
        if i - last < 10:
            continue
        last = i
        events.append(int(i))
    return events, ma20.to_numpy(), ma60.to_numpy()


def run_trade(cs, ma20, ma60, i):
    """v2 卖出状态机（同 bt_goldencross_v2.run_trade）。"""
    sig_chg = cs[i] / cs[i - 1] - 1 if i > 0 else 0
    buy_i = i + 2 if sig_chg > 0.05 else i + 1
    n = len(cs)
    if buy_i >= n:
        return None
    cost = cs[buy_i]
    half_sold, gated, highest = False, False, 0.0
    half_pnl = rest_pnl = None
    exit_i, reason = None, None
    for t in range(buy_i + 1, n):
        px = cs[t]
        gain = px / cost - 1
        highest = max(highest, gain)
        if not gated and highest >= GATE:
            gated = True
        if px <= cost * (1 + HARD):
            if half_sold:
                rest_pnl = gain * 100 - FEE * 100
            exit_i, reason = t, 'stop'
            break
        if gated:
            if gain <= highest - highest * GIVE:
                if half_sold:
                    rest_pnl = gain * 100 - FEE * 100
                exit_i, reason = t, 'trail'
                break
        else:
            if not np.isfinite(ma60[t]):
                continue
            if px < ma60[t]:
                if half_sold:
                    rest_pnl = gain * 100 - FEE * 100
                exit_i, reason = t, 'ma60'
                break
            if px < ma20[t] and not half_sold:
                half_sold = True
                half_pnl = gain * 100 - FEE * 100
    if exit_i is None:
        exit_i, reason = n - 1, 'eod'
        if half_sold:
            rest_pnl = (cs[-1] / cost - 1) * 100 - FEE * 100
    if half_sold:
        if rest_pnl is None:
            rest_pnl = (cs[-1] / cost - 1) * 100 - FEE * 100
        total = 0.5 * half_pnl + 0.5 * rest_pnl
    else:
        total = (cs[exit_i] / cost - 1) * 100 - FEE * 100
    f20_i = min(buy_i + 20, n - 1)
    return {'buy_i': buy_i, 'pnl': total, 'hold': exit_i - buy_i, 'reason': reason,
            'f20': (cs[f20_i] / cost - 1) * 100}


def st(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 'n=0'
    v = sorted(vals)
    win = sum(1 for x in v if x > 0) / len(v) * 100
    return f'n={len(v):3d} 均值{sum(v)/len(v):+7.2f}% 中位{v[len(v)//2]:+7.2f}% 胜率{win:4.1f}%'


def main():
    data = json.load(open(os.path.join(HERE, 'data', 'klines_ohlc_1d.json')))

    # ---- 三口径总览（f20 事件研究 + v2 卖法完整交易）----
    print('===== 金叉 v3 三口径对比（双线 0~60°，斜率差≤15%的三种读法）=====')
    for mode in ['rel', 'angle', 'abs']:
        f_all, p_all, dates_all = [], [], []
        for s in SYMS:
            rows = data[s]
            c = pd.Series([r[4] for r in rows], dtype=float)
            dates = [r[0] for r in rows]
            ev, ma20, ma60 = signal_mask(c, mode)
            cs = c.to_numpy()
            for i in ev:
                t = run_trade(cs, ma20, ma60, i)
                if t:
                    f_all.append(t['f20'])
                    p_all.append(t['pnl'])
                    dates_all.append((s, dates[t['buy_i']]))
        print(f'  [{mode:5s}] 信号{len(dates_all):3d}  f20[{st(f_all)}]')
        print(f'          v2卖法完整[{st(p_all)}]')
        for s, d in dates_all[-6:]:
            print(f'            {s:9s} {d}')

    # ---- 主口径 rel：明细 + 增量 ----
    print('\n===== 主口径 rel（相对差≤15%）明细 =====')
    all_tr = []
    for s in SYMS:
        rows = data[s]
        c = pd.Series([r[4] for r in rows], dtype=float)
        dates = [r[0] for r in rows]
        ev, ma20, ma60, = signal_mask(c, 'rel')
        _, _, s20, s60 = slopes(c)
        cs = c.to_numpy()
        # 增量基线：MA20>MA60 且双线斜率 (0,60°] 状态窗口随机买 f20
        state = ((ma20 > ma60) & (s20 > 0) & (s20 <= S_HI) & (s60 > 0) & (s60 <= S_HI)).to_numpy()
        f20arr = np.full(len(c), np.nan)
        f20arr[:len(c) - 20] = cs[20:len(c)] / cs[:len(c) - 20] * 100 - 100
        base = np.nanmean(f20arr[state & ~np.isnan(f20arr)])
        trs = []
        for i in ev:
            t = run_trade(cs, ma20, ma60, i)
            if t:
                t['sym'], t['year'], t['date'] = s, dates[t['buy_i']][:4], dates[t['buy_i']]
                trs.append(t)
        nav = 1.0
        for t in trs:
            nav *= 1 + t['pnl'] / 100
        sgv = [t['f20'] for t in trs]
        sg = sum(sgv) / len(sgv) if sgv else float('nan')
        print(f'  {s:9s} 信号{len(ev):3d}  f20[{st(sgv)}]  状态基线{base:+6.2f}%  增量{sg - base:+6.2f}%  v2卖法净值{nav:.2f}x')
        all_tr += trs
    nav = 1.0
    for t in all_tr:
        nav *= 1 + t['pnl'] / 100
    print(f'\n  全体 完整交易[{st([t["pnl"] for t in all_tr])}] 净值{nav:.2f}x 平均持有{sum(t["hold"] for t in all_tr)/max(len(all_tr),1):.0f}根')
    print(f'  出场分布{ {r: sum(1 for t in all_tr if t["reason"]==r) for r in set(t["reason"] for t in all_tr)} }')
    print('\n  分年:')
    by_year = defaultdict(list)
    for t in all_tr:
        by_year[t['year']].append(t)
    for y in sorted(by_year):
        sub = by_year[y]
        nav1 = 1.0
        for t in sub:
            nav1 *= 1 + t['pnl'] / 100
        print(f'    {y}: n={len(sub):3d} f20[{st([t["f20"] for t in sub])}]  完整[{st([t["pnl"] for t in sub])}] 净值{nav1:.2f}x')
    print('\n  全部交易明细 (rel):')
    for t in sorted(all_tr, key=lambda x: (x['sym'], x['buy_i'])):
        print(f'    {t["sym"]:9s} {t["date"]}  pnl{t["pnl"]:+8.2f}%  hold{t["hold"]:4d}根  {t["reason"]}')


if __name__ == '__main__':
    main()
