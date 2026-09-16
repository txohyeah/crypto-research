#!/usr/bin/env python3
"""把文件以 file 消息发到飞书会话（走飞书开放接口，QwenPaw CLI 只支持文本）。

凭据与接收人运行时从配置读取（环境变量 > 仓库根 .env > ~/.qwenpaw/config.json），
绝不硬编码、绝不打印。目标为「飞书专用频道」P2P 会话。

用法：python3 scripts/send_feishu_file.py <file_path>
"""
import json
import os
import sys

import requests

CONFIG = os.path.expanduser("~/.qwenpaw/config.json")
BASE = "https://open.feishu.cn/open-apis"


def _load_env_file() -> dict:
    """读取仓库根目录 `.env`（.gitignore 已排除，不入库）。"""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), ".env")
    values: dict = {}
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _resolve_target_open_id() -> str:
    """接收人 open_id：环境变量 > 仓库根 .env > config.json(channels.feishu.open_id)。

    2026-09-16 起不再硬编码在源码里（本仓库是公开仓库）；
    QwenPaw 规范化 config.json 时可能清掉非标准键 open_id，故保留 .env 兜底。
    """
    env_file = _load_env_file()
    open_id = os.environ.get("FEISHU_OPEN_ID") or env_file.get("FEISHU_OPEN_ID")
    if not open_id:
        try:
            with open(CONFIG, encoding="utf-8") as fh:
                open_id = json.load(fh).get("channels", {}).get("feishu", {}).get("open_id")
        except (OSError, ValueError):
            open_id = None
    if not open_id:
        raise RuntimeError("未配置飞书接收人：请在仓库根 .env 设置 FEISHU_OPEN_ID")
    return open_id


def send_file(path: str) -> str:
    """上传并投递文件到飞书 P2P 会话，成功返回 file_key。失败抛异常。

    纯 Channel 职责：只管送达。push_log/runs 记账由 deliver_digest 统一负责。
    """
    name = os.path.basename(path)
    target_open_id = _resolve_target_open_id()  # 晓道友 P2P 会话（配置读取，不写死）
    cfg = json.load(open(CONFIG))["channels"]["feishu"]
    sess = requests.Session()
    sess.trust_env = False  # 国内接口，禁用环境代理直连
    r = sess.post(f"{BASE}/auth/v3/tenant_access_token/internal",
                  json={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]},
                  timeout=15)
    tok = r.json()
    if tok.get("code") != 0:
        raise RuntimeError(f"token失败: {tok.get('code')} {tok.get('msg')}")
    h = {"Authorization": f"Bearer {tok['tenant_access_token']}"}

    with open(path, "rb") as f:
        r = sess.post(f"{BASE}/im/v1/files", headers=h,
                      data={"file_type": "stream", "file_name": name},
                      files={"file": (name, f)}, timeout=60)
    up = r.json()
    if up.get("code") != 0:
        raise RuntimeError(f"上传失败: {up.get('code')} {up.get('msg')}")
    file_key = up["data"]["file_key"]

    r = sess.post(
        f"{BASE}/im/v1/messages", params={"receive_id_type": "open_id"},
        headers={**h, "Content-Type": "application/json"},
        json={"receive_id": target_open_id, "msg_type": "file",
              "content": json.dumps({"file_key": file_key})},
        timeout=30)
    sent = r.json()
    if sent.get("code") != 0:
        raise RuntimeError(f"发送失败: {sent.get('code')} {sent.get('msg')}")
    return file_key


def main() -> None:
    path = sys.argv[1]
    file_key = send_file(path)
    print(f"OK 已发送 {os.path.basename(path)} (file_key={file_key[:12]}…)")


if __name__ == "__main__":
    main()
