#!/usr/bin/env python3
"""飞书文本消息推送工具

发送文本消息到飞书用户。
用法:
    python feishu_send_text.py "消息内容" [--user <open_id>]
"""

import sys
import os
import json
import json5
import requests
from pathlib import Path


def get_feishu_config():
    """读取飞书配置：环境变量优先，回退 ~/.qwenpaw/config.json

    环境变量: FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_OPEN_ID
    Returns: (app_id, app_secret, default_open_id)，缺失项为 None
    """
    app_id = os.environ.get('FEISHU_APP_ID')
    app_secret = os.environ.get('FEISHU_APP_SECRET')
    open_id = os.environ.get('FEISHU_OPEN_ID')
    if not (app_id and app_secret):
        config_path = Path.home() / '.qwenpaw' / 'config.json'
        with open(config_path) as f:
            cfg = json5.load(f)
        feishu = cfg.get('channels', {}).get('feishu', {})
        app_id = app_id or feishu.get('app_id')
        app_secret = app_secret or feishu.get('app_secret')
        open_id = open_id or feishu.get('open_id')
    return app_id, app_secret, open_id


def get_tenant_token(app_id, app_secret):
    """获取 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    data = resp.json()
    if data.get('code') != 0:
        raise Exception(f"获取 token 失败: {data}")
    return data['tenant_access_token']


def send_text_message(token, receive_id, text, id_type='open_id'):
    """发送文本消息"""
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    result = resp.json()
    if result.get('code') != 0:
        raise Exception(f"发送失败: {result}")
    
    msg_id = result['data']['message_id']
    print(f"✅ 发送成功: msg_id={msg_id}")
    return msg_id


def main():
    import argparse
    parser = argparse.ArgumentParser(description='飞书文本推送')
    parser.add_argument('text', help='要发送的文本内容')
    parser.add_argument('--user', default=None,
                        help='接收人 open_id (未指定时使用 FEISHU_OPEN_ID 配置)')
    parser.add_argument('--id-type', default='open_id',
                        help='ID 类型 (open_id/chat_id)')
    args = parser.parse_args()
    
    # 获取凭证
    app_id, app_secret, default_open_id = get_feishu_config()
    if not app_id or not app_secret:
        print("❌ 飞书凭证未配置 (FEISHU_APP_ID/FEISHU_APP_SECRET 或 ~/.qwenpaw/config.json)")
        sys.exit(1)
    user = args.user or default_open_id
    if not user:
        print("❌ 未指定接收人: 请传 --user 或设置 FEISHU_OPEN_ID")
        sys.exit(1)
    
    # 执行流程
    token = get_tenant_token(app_id, app_secret)
    send_text_message(token, user, args.text, args.id_type)
    
    print(f"🎉 完成！文本已发送到飞书")


if __name__ == '__main__':
    main()
