#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""7 币 B 版（价格上穿 MA60 + s60>=0 + 防抖N + 破MA60止损）紧凑数字表。"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bt_price_cross import make_series, price_cross_events, run_simple, baseline  # noqa: E402

FMT = f'{"币":8s} {"N":2s} {"信号":>4s} {"净值":>8s} {"完整均值":>8s} {"中位":>7s} {"胜率":>6s} {"f20均值":>8s} {"f20中位":>8s} {"f20胜率":>7s} {"基线均值":>8s}'


def one(sym, rows):
    c, ma20, ma60, s60 = make_series(rows)
    bl = baseline(c, s60).mean()
    for nc in (1, 2, 3):
        evs = price_cross_events(c, ma60, s60, nc)
        trs = [run_simple(c, ma60, i) for i in evs]
        trs = [t for t in trs if t]
        nav = 1.0
        for t in trs:
            nav *= 1 + t['pnl'] / 100
        pn = [t['pnl'] for t in trs]
        f2 = [t['f20'] for t in trs]
        if not pn:
            print(f'{sym:8s} {nc:<2d} {len(evs):4d} {"-":>8s}   无信号')
            continue
        w = sum(1 for x in pn if x > 0) / len(pn) * 100
        wf = sum(1 for x in f2 if x > 0) / len(f2) * 100
        print(f'{sym:8s} {nc:<2d} {len(evs):4d} {nav:7.2f}x {np.mean(pn):+7.2f}% {np.median(pn):+6.2f}%'
              f' {w:5.1f}% {np.mean(f2):+7.2f}% {np.median(f2):+7.2f}% {wf:6.1f}% {bl:+7.2f}%')


def main():
    print(FMT)
    data = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_1d_uni_crv.json')))
    data5 = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_ohlc_1d.json')))
    for s in ['UNIUSDT', 'CRVUSDT']:
        one(s, data[s])
    for s in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']:
        one(s, data5[s])


if __name__ == '__main__':
    main()
