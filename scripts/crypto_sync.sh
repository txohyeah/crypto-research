#!/usr/bin/env bash
# crypto_sync.sh — 同步 Binance spot K 线到本地 sqlite（crypto-research 合并库）
# 用法: crypto_sync.sh [4h|1d]
# 不传参数则同时跑 4h 和 1d

set -euo pipefail

PYTHON="/home/application/crypto-research/venv/bin/python"
cd /home/application/crypto-research

# Clash 代理（Binance API 在大陆需要走代理）
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
export no_proxy=localhost,127.0.0.1
# venv python 默认 CA 为空，指向含 Clash 证书的系统 bundle
export SSL_CERT_FILE=/etc/pki/tls/cert.pem

SYMBOLS="BTCUSDT ETHUSDT BNBUSDT SOLUSDT"
MODE="${1:-all}"
DATABASE="${CRYPTO_DB:-data/crypto.db}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

sync_symbol() {
  local symbol="$1" timeframe="$2" bars="$3"
  log "sync ${symbol} ${timeframe} (bars=${bars})"
  PYTHONPATH=src "$PYTHON" -m crypto_research.cli crypto sync \
    --symbol "$symbol" \
    --timeframe "$timeframe" \
    --lookback-bars "$bars" \
    --database "$DATABASE" \
    2>&1 | tail -1
}

for sym in $SYMBOLS; do
  if [[ "$MODE" == "4h" || "$MODE" == "all" ]]; then
    sync_symbol "$sym" "4h" 600
  fi
  if [[ "$MODE" == "1d" || "$MODE" == "all" ]]; then
    sync_symbol "$sym" "1d" 180
  fi
done

log "done (${MODE})"