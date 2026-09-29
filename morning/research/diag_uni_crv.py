#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：UNI/CRV 为什么在 v2 定稿口径下 0 信号。
1) 年化波动率对比（UNI/CRV vs 原5币）
2) 全史裸金叉 f20 表现（不过筛子）
3) 裸金叉日的 s20/s60 分布 vs v2 允许带
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def ann_vol(c):
    r = np.log(c / c.shift(1)).dropna()
    return r.std() * np.sqrt(365) * 100


def cross_diag(sym, rows):
    c = pd.Series([r[4] for r in rows], dtype=float)
    dates = [r[0] for r in rows]
    cs = c.to_numpy()
    n = len(cs)
    ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean()
    s20 = (ma20.diff() / ma20.shift(1) * 100).to_numpy()
    s60 = (ma60.diff() / ma60.shift(1) * 100).to_numpy()
    cross = (ma20.shift(1) <= ma60.shift(1)) & (ma20 > ma60)
    idxs = np.flatnonzero(cross.to_numpy())
    ev, last = [], -10**9
    for i in idxs:
        if i - last < 10:
            continue
        last = i
        ev.append(int(i))
    f20s, rows_out = [], []
    for i in ev:
        if i + 1 >= n:
            continue
        f20 = (cs[min(i + 21, n - 1)] / cs[i + 1] - 1) * 100
        f20s.append(f20)
        rows_out.append((dates[i], s20[i], s60[i], f20))
    v = ann_vol(c)
    print(f'\n### {sym}  年化波动率 {v:.0f}%')
    print(f'  裸金叉(冷却10) {len(rows_out)} 笔  f20 均值{np.mean(f20s):+.2f}% 中位{np.median(f20s):+.2f}%'
          f'  胜率{sum(1 for x in f20s if x > 0) / len(f20s) * 100:.0f}%')
    in_s20 = sum(1 for _, a, b, _ in rows_out if 0 <= a <= 0.5)
    in_s60 = sum(1 for _, a, b, _ in rows_out if -0.10 < b <= 0.25)
    both = sum(1 for _, a, b, _ in rows_out if 0 <= a <= 0.5 and -0.10 < b <= 0.25)
    print(f'  金叉日 s20∈[0,0.5]: {in_s20}/{len(rows_out)}   s60∈(-0.10,0.25]: {in_s60}/{len(rows_out)}'
          f'   双带同时过: {both}/{len(rows_out)}')
    for d, a, b, f in rows_out:
        print(f'    {d}  s20={a:+7.3f}  s60={b:+7.3f}  f20={f:+8.1f}%')
    return v


def main():
    data5 = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_ohlc_1d.json')))
    print('===== 原5币年化波动率 =====')
    for s in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']:
        c = pd.Series([r[4] for r in data5[s]], dtype=float)
        print(f'  {s:9s} {ann_vol(c):5.0f}%')
    data = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_1d_uni_crv.json')))
    print('\n===== UNI/CRV 裸金叉诊断 =====')
    for s in ['UNIUSDT', 'CRVUSDT']:
        cross_diag(s, data[s])


if __name__ == '__main__':
    main()
