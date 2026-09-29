#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取扩展主流币 1d K 线（Binance 公开 API，对齐 klines_ohlc_1d.json 结构 [date,o,h,l,c]）"""
import json
import time
import urllib.request

SYMS = ['DOGEUSDT', 'SUIUSDT', 'TONUSDT', 'XRPUSDT', 'ADAUSDT',
        'AVAXUSDT', 'LINKUSDT', 'DOTUSDT', 'LTCUSDT', 'TRXUSDT']
BASE = 'https://data-api.binance.vision/api/v3/klines?symbol={}&interval=1d&limit=1000{}'


def fetch(sym):
    out, end = [], ''
    while True:
        url = BASE.format(sym, f'&endTime={end}' if end else '')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read())
        if not rows:
            break
        out = rows + out
        if len(rows) < 1000:
            break
        end = rows[0][0] - 1
        time.sleep(0.3)
    return out


def main():
    data = {}
    for s in SYMS:
        try:
            rows = fetch(s)
            data[s] = [[time.strftime('%Y-%m-%d', time.gmtime(r[0] / 1000)),
                        float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows]
            print(f'{s}: {len(data[s])} 根, {data[s][0][0]} ~ {data[s][-1][0]}')
        except Exception as e:
            print(f'{s}: FAILED {e}')
    json.dump(data, open('klines_1d_ext.json', 'w'))
    print(f'saved klines_1d_ext.json ({len(data)} symbols)')


if __name__ == '__main__':
    main()
