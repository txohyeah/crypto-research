#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MA20 金叉 MA60（双线斜率平缓）回测：5 币 1d。

信号定义：
  金叉: MA20 上穿 MA60（前根 MA20<=MA60 且当根 MA20>MA60）
  斜率: slope = (MA[t]-MA[t-1])/MA[t-1]*100 （%/根，归一化——价格刻度斜率跨币不可比）
  约束: abs(slope_MA20) < TH 且 abs(slope_MA60) < TH，TH 默认 0.5（用户口径 ~30°）
  冷却 10 天（与超跌一致）
卖出同尺 C2：max(硬亏-10%, 30日滚动低点)，T0 建仓（信号日涨幅>5% 顺延），双边 0.1%。
增量对照：MA20>MA60 状态窗口内任意日买入的 f20 基线（金叉扳机相对趋势状态的贡献）。
"""
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']
TH, HARD, FEE = 0.5, -0.10, 0.002


def signal_mask(c, th):
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    s20 = ma20.diff() / ma20.shift(1) * 100
    s60 = ma60.diff() / ma60.shift(1) * 100
    cross = (ma20.shift(1) <= ma60.shift(1)) & (ma20 > ma60)
    return cross & (s20.abs() < th) & (s60.abs() < th), ma20, ma60, s20, s60


def backtest(c, events_mask, cool=10):
    idxs = np.flatnonzero(events_mask.to_numpy())
    events, last = [], -10**9
    for i in idxs:
        if i - last < cool:
            continue
        last = i
        events.append(int(i))
    rollmin = c.rolling(30).min().shift(1).to_numpy()
    cs = c.to_numpy()
    trades = []
    for i in events:
        sig_chg = cs[i] / cs[i - 1] - 1 if i > 0 else 0
        buy_i = i + 2 if sig_chg > 0.05 else i + 1
        if buy_i >= len(cs):
            continue
        buy = cs[buy_i]
        stopline = np.maximum(buy * (1 + HARD), rollmin)
        hit = np.flatnonzero(cs[buy_i + 1:] < stopline[buy_i + 1:])
        j = buy_i + 1 + hit[0] if len(hit) else len(cs) - 1
        pnl = (cs[j] / buy - 1) * 100 - FEE * 100
        f20_i = min(buy_i + 20, len(cs) - 1)
        mae_i = min(buy_i + 20, len(cs) - 1)
        trades.append({'i': i, 'buy_i': buy_i, 'pnl': pnl, 'hold': j - buy_i,
                       'f20': (cs[f20_i] / buy - 1) * 100,
                       'mae': (cs[buy_i:mae_i + 1].min() / buy - 1) * 100})
    return events, trades


def st(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 'n=0'
    v = sorted(vals)
    win = sum(1 for x in v if x > 0) / len(v) * 100
    return f'n={len(v):3d} 均值{sum(v)/len(v):+7.2f}% 中位{v[len(v)//2]:+7.2f}% 胜率{win:4.1f}%'


def main():
    data = json.load(open(os.path.join(HERE, 'data', 'klines_ohlc_1d.json')))

    # ---- 阈值敏感性（全体合并）----
    print('===== 斜率阈值敏感性（5 币合并，f20 事件研究）=====')
    for th in [0.1, 0.2, 0.3, 0.5, 1.0, 99.0]:
        n_all, f_all = [], []
        for s in SYMS:
            rows = data[s]
            c = pd.Series([r[4] for r in rows], dtype=float)
            dates = [r[0] for r in rows]
            m, *_ = signal_mask(c, th)
            ev, tr = backtest(c, m)
            f_all += [t['f20'] for t in tr]
            n_all += [(s, dates[i]) for i in ev]
        print(f'  TH={th:4.1f}%/根: 信号{len(n_all):3d}  f20[{st(f_all)}]')

    # ---- 主口径 TH=0.5 明细 ----
    print(f'\n===== 主口径 TH=0.5：金叉(MA20上穿MA60)+双线|斜率|<0.5%/根 =====')
    all_tr, all_sig = [], []
    per_sym = {}
    for s in SYMS:
        rows = data[s]
        c = pd.Series([r[4] for r in rows], dtype=float)
        dates = [r[0] for r in rows]
        m, ma20, ma60, s20, s60 = signal_mask(c, TH)
        # 增量基线：MA20>MA60 状态窗口内任意日买入 f20
        state = (ma20 > ma60).to_numpy()
        f20arr = np.full(len(c), np.nan)
        cs_ = c.to_numpy()
        f20arr[:len(c) - 20] = cs_[20:len(c)] / cs_[:len(c) - 20] * 100 - 100
        base = np.nanmean(f20arr[state & ~np.isnan(f20arr)])
        ev, tr = backtest(c, m)
        for t in tr:
            t['sym'], t['year'], t['date'] = s, dates[t['buy_i']][:4], dates[t['buy_i']]
        per_sym[s] = {'base': base, 'sig': [dates[i] for i in ev],
                      'f20': [t['f20'] for t in tr], 'n_state': int(state.sum())}
        all_tr += tr
        all_sig += [(s, dates[i]) for i in ev]
        nav = 1.0
        for t in tr:
            nav *= 1 + t['pnl'] / 100
        sgv = [t['f20'] for t in tr]
        sg = sum(sgv) / len(sgv) if sgv else float('nan')
        print(f'  {s:9s} 信号{len(ev):3d}  f20[{st(sgv)}]  状态基线{base:+6.2f}%  增量{sg - base:+6.2f}%  C2净值{nav:.2f}x')
    sgv = [t['f20'] for t in all_tr]
    pnls = [t['pnl'] for t in all_tr]
    nav = 1.0
    for t in all_tr:
        nav *= 1 + t['pnl'] / 100
    print(f'  {"全体":9s} 信号{len(all_sig):3d}  f20[{st(sgv)}]')
    print(f'           C2[{st(pnls)}]  平均持有{sum(t["hold"] for t in all_tr)/max(len(all_tr),1):.0f}根  MAE[{st([t["mae"] for t in all_tr])}]  净值{nav:.2f}x')

    print('\n  分年（全体合并）: f20 | C2')
    by_year = defaultdict(list)
    for t in all_tr:
        by_year[t['year']].append(t)
    for y in sorted(by_year):
        sub = by_year[y]
        nav1 = 1.0
        for t in sub:
            nav1 *= 1 + t['pnl'] / 100
        print(f'    {y}: f20[{st([t["f20"] for t in sub])}]  C2[{st([t["pnl"] for t in sub])}] 净值{nav1:.2f}x')

    json.dump(per_sym, open(os.path.join(HERE, 'data', 'goldencross_stats.json'), 'w'))
    print('\nsaved goldencross_stats.json')


if __name__ == '__main__':
    main()
