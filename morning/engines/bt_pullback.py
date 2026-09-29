#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""趋势回踩起爆 crypto 版（补全起爆点家族）。

原设计（A 股）四条件 → crypto 因果化平移：
  1) 趋势健康：close > SMA(60根)（金牛通道的纯因果近似——金牛居中平均有未来函数前科，弃用）
  2) 浅回撤：距 60 根滚动最高收盘 -5% ~ -25%（幅度原样）
  3) 起爆：RSI(6天等价) 上穿 50（回踩阈值 50，超跌是 40）
  4) 量能确认：当日量 >= 前 5 根均量 x 1.2（shift(1) 不含当根）
  冷却 10 天等价。卖出与超跌同尺：C2 简化 max(-10%, 滚动 30 天等价低点)，双边 0.1%。
周期：1d / 1w 主测（1M 结构不存在；4h/1h 顺带）。
对照基准：A 股因果化后 +0.18%/胜率 41.3%（机械无优势——用户 edge 在选股）。
"""
import json
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'ARUSDT']
TF_HOURS = {'1d': 24, '4h': 4, '1h': 1, '1w': 168}
DROP_LO, DROP_HI, RSI_TH, VOLX, HARD, FEE = -0.25, -0.05, 50, 1.2, -0.10, 0.002


def scaled(tf, days):
    return max(2, round(days * 24 / TF_HOURS[tf]))


def backtest_pullback(closes, volumes, tf):
    c = pd.Series(closes, dtype=float)
    v = pd.Series(volumes, dtype=float)
    n60, n6 = scaled(tf, 60), scaled(tf, 6)
    cool, f20n, roll_n = scaled(tf, 10), scaled(tf, 20), scaled(tf, 30)

    ch = c.diff()
    rsi = (ch.clip(lower=0).ewm(alpha=1 / n6, adjust=False).mean()
           / ch.abs().ewm(alpha=1 / n6, adjust=False).mean() * 100)
    hi60 = c.rolling(n60).max().shift(1)
    drop = c / hi60 - 1
    trend_ok = c > c.rolling(n60).mean()                       # 因果化趋势健康
    vol_ok = v >= v.rolling(5).mean().shift(1) * VOLX          # 量能确认，不含当根
    cross = (rsi.shift(1) < RSI_TH) & (rsi >= RSI_TH)
    cand = trend_ok & (drop >= DROP_LO) & (drop <= DROP_HI) & cross & vol_ok

    idxs = np.flatnonzero(cand.to_numpy())
    events, last = [], -10**9
    for i in idxs:
        if i - last < cool:
            continue
        last = i
        events.append(int(i))

    rollmin = c.rolling(roll_n).min().shift(1).to_numpy()
    cs = c.to_numpy()
    trades = []
    for i in events:
        sig_chg = cs[i] / cs[i - 1] - 1 if i > 0 else 0
        buy_i = i + 2 if sig_chg > 0.05 else i + 1
        if buy_i >= len(cs):
            continue
        buy = cs[buy_i]
        stopline = np.maximum(buy * (1 + HARD), rollmin)
        seg = cs[buy_i + 1:] < stopline[buy_i + 1:]
        hit = np.flatnonzero(seg)
        if len(hit):
            j = buy_i + 1 + hit[0]
            exit_px = cs[j]
        else:
            j, exit_px = len(cs) - 1, cs[-1]
        pnl = (exit_px / buy - 1) * 100 - FEE * 100
        f20_i = min(buy_i + f20n, len(cs) - 1)
        trades.append({'sym': '', 'year': '', 'f20': (cs[f20_i] / buy - 1) * 100,
                       'pnl': pnl, 'hold': j - buy_i, 'buy_i': buy_i})
    return events, trades


def st(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 'n=0'
    v = sorted(vals)
    win = sum(1 for x in v if x > 0) / len(v) * 100
    return f'n={len(v):4d} 均值{sum(v)/len(v):+7.2f}% 中位{v[len(v)//2]:+7.2f}% 胜率{win:4.1f}%'


def main():
    for tf, path in [('1d', 'klines_vol_1d.json'), ('1w', 'klines_vol_1w.json'), ('4h', None), ('1h', None)]:
        if path:
            data = json.load(open(os.path.join(HERE, path)))
            rows_by_sym = {s: [(r[0], r[1], r[2]) for r in data[s]] for s in SYMBOLS}
        else:   # 4h/1h 无 volume 缓存——超跌引擎的 klines_tf 也没有 volume，跳过并注明
            print(f'{tf}: 无成交量缓存，跳过（回踩的量能确认是四条件之一，不可省）')
            continue
        all_ev, all_tr = [], []
        for sym in SYMBOLS:
            rows = rows_by_sym[sym]
            closes = [r[1] for r in rows]
            vols = [r[2] for r in rows]
            days = [r[0] for r in rows]
            if len(closes) < scaled(tf, 70) + 10:
                continue
            ev, tr = backtest_pullback(closes, vols, tf)
            for t in tr:
                t['sym'], t['year'] = sym, days[t['buy_i']][:4]
            all_ev += [(sym, days[i]) for i in ev]
            all_tr += tr
        print(f'\n===== 回踩起爆 {tf} =====')
        print(f'信号总数 {len(all_ev)}')
        print(f'事件研究 f20: {st([t["f20"] for t in all_tr])}')
        print(f'C2 推进:      {st([t["pnl"] for t in all_tr])}  平均持有 {sum(t["hold"] for t in all_tr)/max(len(all_tr),1):.0f} 根')
        nav = 1.0
        for t in all_tr:
            nav *= 1 + t['pnl'] / 100
        print(f'串行净值 {nav:.2f}x')
        print('分币 f20:')
        for s in SYMBOLS:
            sub = [t for t in all_tr if t['sym'] == s]
            if sub:
                print(f'  {s:9s} {st([t["f20"] for t in sub])}')
        print('分年 f20 | C2:')
        import collections
        by_year = collections.defaultdict(list)
        for t in all_tr:
            by_year[t['year']].append(t)
        for y in sorted(by_year):
            sub = by_year[y]
            nav1 = 1.0
            for t in sub:
                nav1 *= 1 + t['pnl'] / 100
            f = st([t['f20'] for t in sub])
            p = st([t['pnl'] for t in sub])
            print(f'  {y}: f20[{f}]  C2[{p}] 净值{nav1:.2f}x')


if __name__ == '__main__':
    main()
