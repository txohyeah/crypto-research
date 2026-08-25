# crypto-news

Web3 每日信息推送：重大事件 + 新机会雷达，每天 08:00 推送一条一分钟读完的早报。

完整设计方案见 [docs/design.md](docs/design.md)。

## 当前状态：P0（采集入库验证）

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # 填写飞书 webhook（P0 阶段可先留空）
python3 scripts/collect.py
```

## 结构

```
config/sources.json   # 源注册表（含每个源的实测状态）
scripts/db.py         # SQLite schema（WAL）
scripts/collect.py    # P0 采集器：直连优先/代理重试、入库即去重、源健康记账
```

## 已知问题

- 中文快讯源（律动/Odaily/PANews）2026-08-25 实测全部失效，见 sources.json 各 note，
  修复路径：注册 BlockBeats 开放平台 key，或自建 RSSHub。
