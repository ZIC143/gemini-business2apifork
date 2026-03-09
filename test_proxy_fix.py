#!/usr/bin/env python3
"""验证代理修复"""
import httpx
from core import storage, node_manager

print("=" * 50)
print("代理修复验证")
print("=" * 50)

# 1. 检查配置
proxy_cfg = storage.load_proxy_control_sync()
print(f"\n✓ 代理端口: {proxy_cfg.get('port')}")
print(f"✓ Auth启用: {proxy_cfg.get('auth_enabled')}")
print(f"✓ Chat启用: {proxy_cfg.get('chat_enabled')}")

# 2. 检查节点
nodes = storage.load_nodes_sync()
enabled = [n for n in nodes if n.get("enabled", True)]
print(f"\n✓ 节点总数: {len(nodes)}")
print(f"✓ 启用节点: {len(enabled)}")
if enabled:
    print(f"✓ 第一个节点: {enabled[0]['name']}")
    print(f"✓ 节点URL: {enabled[0]['url']}")
    print(f"✓ use_for_auth: {enabled[0].get('use_for_auth')}")
    print(f"✓ use_for_chat: {enabled[0].get('use_for_chat')}")

# 3. 测试代理连接
port = proxy_cfg.get('port', 7890)
proxy_url = f"http://127.0.0.1:{port}"
print(f"\n测试代理连接: {proxy_url}")

try:
    with httpx.Client(proxies={"http://": proxy_url, "https://": proxy_url}, timeout=10) as client:
        resp = client.get("https://www.google.com/generate_204")
        if resp.status_code == 204:
            print("✅ 代理连接成功")

            # 获取代理IP
            ip_resp = client.get("https://api.ipify.org?format=json", timeout=5)
            if ip_resp.status_code == 200:
                ip = ip_resp.json().get("ip", "未知")
                print(f"✅ 当前代理IP: {ip}")
        else:
            print(f"❌ 代理响应异常: {resp.status_code}")
except Exception as e:
    print(f"❌ 代理连接失败: {e}")

print("\n" + "=" * 50)
print("验证完成")
print("=" * 50)
