#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""金叉 v2（定稿 2026-09-21）：单向斜率带 + MA60 走平带 + 分段止盈状态机。

信号（1d）：
  金叉: MA20 上穿 MA60
  斜率: 0 <= slope_MA20 <= 0.5 %/根;  -0.10 < slope_MA60 <= 0.25 %/根
        —— MA20 不得为负；MA60 对称走平带（2026-09-21 用户定稿）：微跌 ≤0.10%/根 视为走平=0°
        （拐头初期单日符号是噪音；实测复活 BTC 2020-10-18 f20+28.8%/完整+113.5%、BTC 2023-10-07 +21.2%
         等主升浪起点，n16→22，完整均值 26.1%→25.0% 基本持平；替代方案 W-back/W-both 窗口判据已回测劣化）
  冷却 10 天；T0 建仓（信号日涨幅>5% 顺延）；双边 0.1%

卖出状态机（全程 close 口径）：
  硬止损: close <= entry*0.90 → 清仓（全程有效）
  gate 前浮盈 <100%: close < MA20 → 卖 50%（一次性）；close < MA60 → 清剩余
                     （同根双破时 MA60 优先=直接清仓）
  gate（历史最高浮盈 >= +100%）后永久切换: 移动止盈 = 从最高浮盈回吐 1/3 → 清仓
        例：最高 +150% → 跌回 +100% 清仓；最高 +300% → 跌回 +200% 清仓
收益合并：卖过一半的笔 = 0.5*half_pnl + 0.5*rest_pnl。
"""
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']
S20_HI, S60_HI, GATE, GIVE, HARD, FEE = 0.5, 0.25, 1.0, 1 / 3, -0.10, 0.002


def signals(c, dates, s60_floor=-0.10):
    """金叉 v2 事件（默认=定稿口径：MA60 对称走平带 -0.10，2026-09-21 拍板）。
    s60_floor=0.0 可取回旧版严格口径（仅存档对照用）。"""
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    s20 = ma20.diff() / ma20.shift(1) * 100
    s60 = ma60.diff() / ma60.shift(1) * 100
    cross = (ma20.shift(1) <= ma60.shift(1)) & (ma20 > ma60)
    m = cross & (s20 >= 0) & (s20 <= S20_HI) & (s60 >= s60_floor) & (s60 <= S60_HI)
    idxs = np.flatnonzero(m.to_numpy())
    events, last = [], -10**9
    for i in idxs:
        if i - last < 10:
            continue
        last = i
        events.append(int(i))
    return events, ma20.to_numpy(), ma60.to_numpy()


def run_trade(cs, ma20, ma60, i):
    """单笔状态机。返回 (总pnl%, half_pnl, rest_pnl, exit_i, reason, gated, sell_events)。
    sell_events: [(t, type)]，type ∈ half_ma20 / stop / ma60 / trail / eod"""
    sig_chg = cs[i] / cs[i - 1] - 1 if i > 0 else 0
    buy_i = i + 2 if sig_chg > 0.05 else i + 1
    n = len(cs)
    if buy_i >= n:
        return None
    cost = cs[buy_i]
    half_sold, gated, highest = False, False, 0.0
    half_pnl = rest_pnl = None
    exit_i, reason = None, None
    sell_events = []
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
            sell_events.append((t, 'stop'))
            break
        if gated:
            if gain <= highest - highest * GIVE:
                if half_sold:
                    rest_pnl = gain * 100 - FEE * 100
                exit_i, reason = t, 'trail'
                sell_events.append((t, 'trail'))
                break
        else:
            if not np.isfinite(ma60[t]):
                continue
            if px < ma60[t]:
                if half_sold:
                    rest_pnl = gain * 100 - FEE * 100
                exit_i, reason = t, 'ma60'
                sell_events.append((t, 'ma60'))
                break
            if px < ma20[t] and not half_sold:
                half_sold = True
                half_pnl = gain * 100 - FEE * 100
                sell_events.append((t, 'half_ma20'))
    if exit_i is None:
        exit_i, reason = n - 1, 'eod'
        gain = cs[-1] / cost - 1
        if half_sold:
            rest_pnl = gain * 100 - FEE * 100
        sell_events.append((n - 1, 'eod'))
    if half_sold:
        if rest_pnl is None:
            rest_pnl = (cs[-1] / cost - 1) * 100 - FEE * 100
        total = 0.5 * half_pnl + 0.5 * rest_pnl
    else:
        total = (cs[exit_i] / cost - 1) * 100 - FEE * 100
    f20_i = min(buy_i + 20, n - 1)
    return {'buy_i': buy_i, 'pnl': total, 'half': half_pnl, 'rest': rest_pnl,
            'hold': exit_i - buy_i, 'reason': reason, 'gated': gated,
            'f20': (cs[f20_i] / cost - 1) * 100, 'sells': sell_events}


def st(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 'n=0'
    v = sorted(vals)
    win = sum(1 for x in v if x > 0) / len(v) * 100
    return f'n={len(v):3d} 均值{sum(v)/len(v):+7.2f}% 中位{v[len(v)//2]:+7.2f}% 胜率{win:4.1f}%'


def main():
    data = json.load(open(os.path.join(HERE, 'data', 'klines_ohlc_1d.json')))
    all_tr, out = [], {}
    print('===== 金叉 v2 定稿（MA20 0~0.5 / MA60 走平带(-0.10, 0.25] %/根 + 分段止盈）=====')
    for s in SYMS:
        rows = data[s]
        c = pd.Series([r[4] for r in rows], dtype=float)
        dates = [r[0] for r in rows]
        ev, ma20, ma60 = signals(c, dates)
        cs = c.to_numpy()
        trs = []
        for i in ev:
            t = run_trade(cs, ma20, ma60, i)
            if t:
                t['sym'], t['year'], t['date'] = s, dates[t['buy_i']][:4], dates[t['buy_i']]
                trs.append(t)
        out[s] = {'dates': [dates[i] for i in ev], 'buy_dates': [t['date'] for t in trs],
                  'sells': [{'d': dates[t_i], 'type': tp} for tr in trs for t_i, tp in tr['sells']]}
        nav = 1.0
        for t in trs:
            nav *= 1 + t['pnl'] / 100
        hs = sum(1 for t in trs if t['half'] is not None)
        print(f'  {s:9s} 信号{len(ev):3d}  完整交易[{st([t["pnl"] for t in trs])}]  '
              f'净值{nav:.2f}x  卖半笔数{hs}  出场分布{ {r: sum(1 for t in trs if t["reason"]==r) for r in set(t["reason"] for t in trs)} }')
        all_tr += trs
    nav = 1.0
    for t in all_tr:
        nav *= 1 + t['pnl'] / 100
    print(f'\n  全体 完整交易[{st([t["pnl"] for t in all_tr])}]  净值{nav:.2f}x  '
          f'平均持有{sum(t["hold"] for t in all_tr)/max(len(all_tr),1):.0f}根')
    print(f'  f20[{st([t["f20"] for t in all_tr])}]')
    print(f'  卖出一半的那半段收益[{st([t["half"] for t in all_tr if t["half"] is not None])}]')
    print(f'  gate 触发笔数 {sum(1 for t in all_tr if t["gated"])}/{len(all_tr)}')
    print('\n  分年: 完整交易 | f20')
    by_year = defaultdict(list)
    for t in all_tr:
        by_year[t['year']].append(t)
    for y in sorted(by_year):
        sub = by_year[y]
        nav1 = 1.0
        for t in sub:
            nav1 *= 1 + t['pnl'] / 100
        print(f'    {y}: n={len(sub):3d} 完整[{st([t["pnl"] for t in sub])}] 净值{nav1:.2f}x  f20[{st([t["f20"] for t in sub])}]')
    print('\n  全部交易明细:')
    for t in sorted(all_tr, key=lambda x: (x['sym'], x['buy_i'])):
        half_s = f' half{t["half"]:+.1f}%' if t['half'] is not None else ''
        print(f'    {t["sym"]:9s} {t["date"]}  pnl{t["pnl"]:+8.2f}%  hold{t["hold"]:4d}根  '
              f'{t["reason"]:5s}  gate{"✓" if t["gated"] else "×"}{half_s}')
    json.dump(out, open(os.path.join(HERE, 'data', 'goldencross_v2_signals.json'), 'w'))
    print('\nsaved goldencross_v2_signals.json')


if __name__ == '__main__':
    main()
