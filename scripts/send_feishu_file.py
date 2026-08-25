#!/usr/bin/env python3
"""把文件以 file 消息发到飞书会话（走飞书开放接口，QwenPaw CLI 只支持文本）。

凭据运行时从 QwenPaw 配置读取（~/.qwenpaw/config.json → channels.feishu），
绝不硬编码、绝不打印。目标为「飞书专用频道」P2P 会话。

用法：python3 scripts/send_feishu_file.py <file_path>
"""
import json
import os
import sys

import requests

CONFIG = os.path.expanduser("~/.qwenpaw/config.json")
TARGET_OPEN_ID = "ou_152f981a4053515ca573ab8159e9e09b"  # 晓道友 P2P 会话
BASE = "https://open.feishu.cn/open-apis"


def main() -> None:
    path = sys.argv[1]
    name = os.path.basename(path)

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
        json={"receive_id": TARGET_OPEN_ID, "msg_type": "file",
              "content": json.dumps({"file_key": file_key})},
        timeout=30)
    sent = r.json()
    if sent.get("code") != 0:
        raise RuntimeError(f"发送失败: {sent.get('code')} {sent.get('msg')}")
    print(f"OK 已发送 {name} (file_key={file_key[:12]}…)")


if __name__ == "__main__":
    main()
