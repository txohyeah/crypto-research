#!/usr/bin/env python3
"""飞书文件推送工具

将本地文件上传到飞书并发送给指定用户。
用法:
    python feishu_send_file.py <file_path> [--user <open_id>]
    
读取 qwenpaw config 中的飞书凭证，无需手动配置。
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


def upload_file(token, file_path, file_type='stream'):
    """上传文件到飞书，返回 file_key"""
    url = "https://open.feishu.cn/open-apis/im/v1/files"
    path = Path(file_path)
    
    # 根据扩展名确定 file_type
    ext = path.suffix.lower()
    type_map = {
        '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.gif': 'image',
        '.mp4': 'mp4', '.mov': 'mp4',
    }
    file_type = type_map.get(ext, 'stream')
    
    headers = {"Authorization": f"Bearer {token}"}
    with open(path, 'rb') as f:
        files = {'file': (path.name, f)}
        data = {
            'file_type': file_type,
            'file_name': path.name,
        }
        resp = requests.post(url, headers=headers, data=data, files=files, timeout=30)
    
    result = resp.json()
    if result.get('code') != 0:
        raise Exception(f"上传失败: {result}")
    
    file_key = result['data']['file_key']
    print(f"✅ 上传成功: file_key={file_key[:24]}...")
    return file_key, file_type


def send_file_message(token, receive_id, file_key, file_type='stream', id_type='open_id'):
    """发送文件消息"""
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}"
    
    # 构建消息内容
    if file_type == 'image':
        content = json.dumps({"image_key": file_key})
        msg_type = "image"
    else:
        content = json.dumps({"file_key": file_key})
        msg_type = "file"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": content,
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
    parser = argparse.ArgumentParser(description='飞书文件推送')
    parser.add_argument('file_path', help='要发送的文件路径')
    parser.add_argument('--user', default=None,
                        help='接收人 open_id (未指定时使用 FEISHU_OPEN_ID 配置)')
    parser.add_argument('--id-type', default='open_id',
                        help='ID 类型 (open_id/chat_id)')
    args = parser.parse_args()
    
    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}")
        sys.exit(1)
    
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
    print(f"📤 准备发送: {file_path.name} ({file_path.stat().st_size / 1024:.1f} KB)")
    
    token = get_tenant_token(app_id, app_secret)
    file_key, file_type = upload_file(token, file_path)
    send_file_message(token, user, file_key, file_type, args.id_type)
    
    print(f"🎉 完成！文件已发送到飞书")


if __name__ == '__main__':
    main()
