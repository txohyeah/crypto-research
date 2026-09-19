#!/usr/bin/env python3
"""实时价快照采集：三源兜底（CoinGecko → Binance → OKX）→ 本地 crypto.db price_spot 表。

背景：cf-crypto-site 的 /api/price（worker 三源代理）是即用即弃的，不落任何盘；
本脚本把同一批上游报价以分钟级粒度在本地 crypto.db 留一份历史。

用法：
  python3 scripts/price_snapshot.py                  # 采集一次并入库
  python3 scripts/price_snapshot.py --dry-run        # 只打印将写入的行，不写库
  python3 scripts/price_snapshot.py --retention-days 365   # 自定义保留天数（默认 365）

约定：
  - 标的取 crypto.db crypto_asset（active=1）的 base_asset，与 crypto_ohlcv 口径一致
  - 时间为 UTC（crypto 库约定），格式 'YYYY-MM-DD HH:MM:SS'
  - 外网请求走本地 Clash 代理（直连超时）
  - 每行记实际供给源（coingecko|binance|okx），按源逐级补缺、每币只取一次
"""
import argparse
import datetime
import json
import ssl
import sqlite3
import sys
import urllib.request

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    import os
    _p = '/etc/pki/tls/cert.pem'
    _CTX = ssl.create_default_context(cafile=_p if os.path.exists(_p) else None)

PROXY = 'http://127.0.0.1:7890'
DB_PATH = '/home/application/crypto-research/data/crypto.db'
UA = 'crypto-price-snapshot/1.0'
TIMEOUT = 15

# base_asset → CoinGecko id（与 cf-crypto-site worker 的 CG2EX 同口径，扩展到本地 5 币）
CG_IDS = {'BTC': 'bitcoin', 'ETH': 'ethereum', 'SOL': 'solana', 'BNB': 'binancecoin', 'AR': 'arweave'}


def http_json(url):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}),
        urllib.request.HTTPSHandler(context=_CTX))
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with opener.open(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def fetch_coingecko(symbols):
    """→ {sym: (price_usd, change24h百分数)}"""
    ids = ','.join(CG_IDS[s] for s in symbols if s in CG_IDS)
    if not ids:
        return {}
    try:
        data = http_json('https://api.coingecko.com/api/v3/simple/price?ids=' + ids +
                         '&vs_currencies=usd&include_24hr_change=true')
        id2sym = {v: k for k, v in CG_IDS.items()}
        out = {}
        for k, v in (data or {}).items():
            sym = id2sym.get(k, k)
            if sym in symbols and isinstance(v, dict) and v.get('usd') is not None:
                out[sym] = (float(v['usd']), v.get('usd_24h_change'))
        return out
    except Exception as e:
        print(f'[warn] coingecko 失败: {e}')
        return {}


def fetch_binance(symbols):
    out = {}
    if not symbols:
        return out
    try:
        url = ('https://api.binance.com/api/v3/ticker/24hr?symbols=' +
               json.dumps([s + 'USDT' for s in symbols]))
        for t in http_json(url) or []:
            sym = (t.get('symbol') or '').replace('USDT', '')
            last, pct = t.get('lastPrice'), t.get('priceChangePercent')
            if sym in symbols and last is not None:
                out[sym] = (float(last), float(pct) if pct is not None else None)
    except Exception as e:
        print(f'[warn] binance 失败: {e}')
    return out


def fetch_okx(symbols):
    out = {}
    for s in symbols:
        try:
            d = (http_json('https://www.okx.com/api/v5/market/ticker?instId=' + s + '-USDT')
                 or {}).get('data') or []
            if d and d[0].get('last') is not None:
                out[s] = (float(d[0]['last']), None)  # OKX 该接口无 24h 涨跌幅
        except Exception as e:
            print(f'[warn] okx {s} 失败: {e}')
    return out


SOURCES = [('coingecko', fetch_coingecko), ('binance', fetch_binance), ('okx', fetch_okx)]


def ensure_table(con):
    con.execute('''CREATE TABLE IF NOT EXISTS price_spot (
        ts_utc TEXT NOT NULL,                -- 'YYYY-MM-DD HH:MM:SS' UTC（对齐 crypto_ohlcv 口径）
        symbol TEXT NOT NULL,                -- base asset：BTC/ETH/SOL/BNB/AR
        price_usd REAL NOT NULL,
        change_24h REAL,                     -- 24h 涨跌（百分数）；OKX 源为 NULL
        source TEXT NOT NULL,                -- coingecko | binance | okx（实际供给源）
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (ts_utc, symbol)
    )''')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--retention-days', type=int, default=365)
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    ensure_table(con)
    symbols = [r['base_asset'] for r in con.execute(
        'SELECT base_asset FROM crypto_asset WHERE active = 1 ORDER BY symbol')]
    if not symbols:
        print('[error] crypto_asset 无 active 标的')
        return 1

    # 三源逐级补缺：每币只被取一次，归属即调用时的源
    rows, tried, remaining = [], [], list(symbols)
    for name, getter in SOURCES:
        got = getter(remaining) if remaining else {}
        tried.append(f'{name}:{len(got)}')
        for s, (price, chg) in got.items():
            rows.append((s, price, chg, name))
        remaining = [s for s in remaining if s not in got]

    if not rows:
        print(f'[error] 三源均不可用 tried={tried}')
        return 1

    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime('%Y-%m-%d %H:%M:%S')
    fetched_at = now.isoformat(timespec='seconds')

    print(f'[{ts}Z] tried={tried} 写入 {len(rows)}/{len(symbols)} 币（缺: {remaining}）')
    for s, price, chg, src in sorted(rows):
        print(f'  {s:<4} {price:>12.6g} USD  24h={chg}  src={src}')

    if args.dry_run:
        print('--- dry-run，未写库 ---')
        return 0

    con.executemany('INSERT OR REPLACE INTO price_spot (ts_utc, symbol, price_usd, change_24h, source, fetched_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    [(ts, s, p, c, src, fetched_at) for s, p, c, src in rows])
    # 保留期清理：ts_utc 是 PK 前缀，走索引
    cutoff = (now - datetime.timedelta(days=args.retention_days)).strftime('%Y-%m-%d %H:%M:%S')
    con.execute('DELETE FROM price_spot WHERE ts_utc < ?', (cutoff,))
    con.commit()
    n = con.execute('SELECT COUNT(*) FROM price_spot').fetchone()[0]
    print(f'[OK] 已入库，price_spot 现有 {n} 行（保留 {args.retention_days} 天）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
