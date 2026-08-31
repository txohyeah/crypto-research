# crypto-research

加密资产研究合并库 —— 三个子系统的统一仓库：

| 子系统 | 目录 | 职责 |
|--------|------|------|
| **crypto 技术分析**（原 stock-research crypto 三件） | `src/crypto_research/` | Binance spot K 线同步、技术指标评级（golden bull）、图表、信号快照、回测、纸交易；统一 CLI |
| **BTC 链上监控**（原 btc_analysis / crypto_trading） | `btc/` | BTC 每日采集（恐惧贪婪/ETF/MVRV/永续资金费率/未平仓量）、筑底/见顶清单、HTML 报告 + 飞书推送 |
| **Web3 新闻早报**（原 crypto-news） | `news/` | 每日早报流水线：RSS 采集 → digest 提取 → 渲染 HTML → 飞书投递 |

## 快速开始

```bash
# venv（已安装 pandas/matplotlib/requests/feedparser/tech-indicators 等）
./venv/bin/python -m crypto_research.cli --help

# crypto 技术分析（Binance 走 Clash 代理；见 scripts/crypto_sync.sh 环境变量）
PYTHONPATH=src ./venv/bin/python -m crypto_research.cli crypto sync --symbol BTCUSDT --timeframe 4h --lookback-bars 600
PYTHONPATH=src ./venv/bin/python -m crypto_research.cli crypto rate --symbol BTCUSDT --timeframe 1d --confirm-timeframe 4h
PYTHONPATH=src ./venv/bin/python -m crypto_research.cli crypto chart --symbol BTCUSDT --timeframe 4h --output /tmp/btc.html
PYTHONPATH=src ./venv/bin/python -m crypto_research.cli crypto signal-sync --symbol BTCUSDT --timeframe 4h
PYTHONPATH=src ./venv/bin/python -m crypto_research.cli crypto backtest --symbol BTCUSDT --timeframe 4h
PYTHONPATH=src ./venv/bin/python -m crypto_research.cli crypto paper-trade --symbol BTCUSDT --timeframe 4h

# BTC 链上监控（完整采集 + 报告推送飞书）
python3 btc/scripts/btc_daily_collect.py --report

# Web3 早报流水线（从 news/ 目录执行，原样保留）
cd news && python3 scripts/collect.py
```

全部 crypto 命令输出单行 JSON（`{"ok":true,...}` / `{"ok":false,"error_code":...}`）。

## 数据与存储

- **统一 sqlite 单库** `data/crypto.db`（原 MySQL 已废弃，2026-08-31 全量迁移）：
  - `crypto_asset` / `crypto_ohlcv` / `crypto_signal_snapshot`（Binance K 线与信号快照）
  - 表结构与 tushare 无关，遵循原 crypto 语义（BIGINT open_time_ms / REAL 价格 / ISO 时间字符串，UTC）
- `news/data/push.db`：早报采集条目与投递记账
- `btc/data/btc_monitor.db` + `crypto_monitor.db`：BTC 链上指标
- 时间约定：crypto 库时间字段为 **UTC**；btc/news 为本地日（Asia/Shanghai）

## 定时任务

| 任务 | 调度 | 位置 |
|------|------|------|
| crypto 4h 同步 | 每天 00/04/08/12/16/20 点 +5 分（系统 crontab） | `scripts/crypto_sync.sh 4h` |
| crypto 1d 同步 | 每天 08:05（系统 crontab） | `scripts/crypto_sync.sh 1d` |
| Web3 早报 | 每天 08:00（QwenPaw cron） | `news/`（cd 后执行流水线） |
| BTC 采集+报告 | 每天 08:10（QwenPaw cron） | `btc/scripts/btc_daily_collect.py --report` |

## 迁移与兼容说明

- Binance 请求需走 Clash 代理 + `SSL_CERT_FILE=/etc/pki/tls/cert.pem`（venv python 默认 CA 为空）——见 `scripts/crypto_sync.sh`
- crypto 存储层由 SQLAlchemy/MySQL 重写为 sqlite3 标准库，`CryptoRepository` 方法签名与语义保持一致
- chart/indicators/strategies 依赖公共技术包 `tech-indicators`（editable 安装于 venv）
- 旧仓路径（stock-research / crypto_trading / btc_analysis / crypto-news）在切换并验证后退役